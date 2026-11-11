# ABOUTME: Tests that OpenEO job status writes are conditional, so racing writers (cancel vs
# ABOUTME: finish, two starts, an edit during a run) can't overwrite each other.
import threading
import time
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text

from openeo_app.registers import jobs
from openeo_fastapi.api.types import Status


@pytest.fixture()
def table(monkeypatch, tmp_path):
    """A jobs table in SQLite, reached through the same engine lookup the register uses.

    A file, so each thread gets its own connection and SQLite's lock orders racing writes. One
    in-memory connection shared between threads failed about one run in seven with "cannot commit
    transaction - SQL statements in progress"."""
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}", connect_args={"timeout": 10})
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL)"))
    monkeypatch.setattr("openeo_fastapi.client.psql.engine.get_engine", lambda: engine)

    def put(status):
        job_id = str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO jobs VALUES (:id, :status)"), {"id": job_id, "status": status})
        return job_id

    def status_of(job_id):
        with engine.begin() as connection:
            return connection.execute(text("SELECT status FROM jobs WHERE job_id = :id"), {"id": job_id}).scalar()

    return SimpleNamespace(put=put, status=status_of)


def test_every_move_targets_a_known_status():
    statuses = {status.value for status in Status}
    assert set(jobs.JOB_MOVES) == statuses
    for targets in jobs.JOB_MOVES.values():
        assert targets <= statuses


@pytest.mark.parametrize("current,target,applies", [
    ("created", "queued", True),
    ("finished", "queued", True),
    ("queued", "queued", False),     # a second start
    ("running", "queued", False),
    ("queued", "running", True),
    ("canceled", "running", False),  # cancelled while waiting for a slot
    ("running", "finished", True),
    ("canceled", "finished", False), # cancel landed as it finished
    ("finished", "canceled", False), # finish landed as it was cancelled
    ("running", "canceled", True),
])
def test_moves_apply_only_from_allowed_states(table, current, target, applies):
    job_id = table.put(current)
    assert jobs._move_job_status(job_id, target) is applies
    assert table.status(job_id) == (target if applies else current)


def test_only_one_of_two_concurrent_starts_wins(table):
    job_id = table.put("created")
    results = []
    threads = [threading.Thread(target=lambda: results.append(jobs._move_job_status(job_id, "queued")))
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [False, True]


def _register():
    register = jobs.ExecutableJobsRegister.__new__(jobs.ExecutableJobsRegister)
    register._jobs_lock = threading.Lock()
    register._cancel_events = {}
    register._running_jobs = {}
    register.storage = SimpleNamespace(
        save_log=lambda **_: None,
        deleted=[],
    )
    register.storage.delete_result = register.storage.deleted.append
    return register


def _job(job_id, status):
    return SimpleNamespace(job_id=job_id, user_id="u1", status=Status(status))


def test_cancel_after_the_job_finished_keeps_its_results(table, monkeypatch):
    job_id = table.put("finished")
    register = _register()
    # The register read the job while it was still running, then it finished.
    monkeypatch.setattr(jobs, "get", lambda **_: _job(job_id, "running"))
    with pytest.raises(HTTPException) as raised:
        register.cancel_job(uuid.UUID(job_id), SimpleNamespace(user_id="u1"))
    assert raised.value.status_code == 400
    assert table.status(job_id) == "finished"
    assert register.storage.deleted == []


def test_cancel_without_a_thread_cancels_a_queued_job(table, monkeypatch):
    job_id = table.put("queued")
    register = _register()
    monkeypatch.setattr(jobs, "get", lambda **_: _job(job_id, "queued"))
    response = register.cancel_job(uuid.UUID(job_id), SimpleNamespace(user_id="u1"))
    assert response.status_code == 204
    assert table.status(job_id) == "canceled"


def test_second_start_is_refused(table, monkeypatch):
    job_id = table.put("queued")
    register = _register()
    monkeypatch.setattr(jobs, "get", lambda **_: _job(job_id, "created"))
    with pytest.raises(HTTPException) as raised:
        register.start_job(uuid.UUID(job_id), SimpleNamespace(user_id="u1"))
    assert raised.value.status_code == 400


def test_editing_a_running_job_is_refused(monkeypatch):
    register = _register()
    monkeypatch.setattr(jobs, "get", lambda **_: _job("j1", "running"))
    with pytest.raises(HTTPException) as raised:
        register.update_job(uuid.uuid4(), body=None, user=SimpleNamespace(user_id="u1"))
    assert raised.value.detail.code == "JobLocked"


def test_a_timed_out_job_reads_as_failed_when_it_times_out(table, monkeypatch):
    job_id = table.put("queued")
    register = _register()
    register._job_timeout_seconds = 1
    register.storage.save_result = lambda **_: "unused"
    register.executor = SimpleNamespace(execute=lambda **_: time.sleep(3))
    job = SimpleNamespace(job_id=job_id, user_id="u1", status=Status.queued,
                          process=SimpleNamespace(process_graph={}))
    monkeypatch.setattr(jobs, "get", lambda **_: job)
    runner = threading.Thread(target=register._run_job, args=(uuid.UUID(job_id),), daemon=True)
    runner.start()
    time.sleep(1.8)  # past the 1 s timeout, before the 3 s computation ends
    status_then = table.status(job_id)
    runner.join(5)
    assert table.status(job_id) == "error"
    assert status_then == "error", f"status {status_then!r} 0.8 s after the timeout"


def _slow_job(table, monkeypatch, seconds):
    """A queued job whose computation takes `seconds`, and a register with a 1 s timeout."""
    job_id = table.put("queued")
    register = _register()
    register._job_timeout_seconds = 1
    register.storage.save_result = lambda **_: "unused"
    register.executor = SimpleNamespace(execute=lambda **_: time.sleep(seconds))
    job = SimpleNamespace(job_id=job_id, user_id="u1", status=Status.queued,
                          process=SimpleNamespace(process_graph={}))
    monkeypatch.setattr(jobs, "get", lambda **_: job)
    return job_id, register


def test_a_timed_out_job_keeps_its_slot_until_the_work_ends(table, monkeypatch):
    monkeypatch.setattr(jobs, "_JOB_SLOTS", threading.BoundedSemaphore(1))
    job_id, register = _slow_job(table, monkeypatch, 3)
    runner = threading.Thread(target=register._execute_job_background,
                              args=(uuid.UUID(job_id), threading.Event()), daemon=True)
    runner.start()
    time.sleep(1.8)
    assert table.status(job_id) == "error"
    assert not jobs._JOB_SLOTS.acquire(blocking=False), "slot freed while the computation still runs"
    runner.join(5)
    assert jobs._JOB_SLOTS.acquire(blocking=False)
    jobs._JOB_SLOTS.release()


def test_a_cancelled_job_reads_as_cancelled_at_once(table, monkeypatch):
    job_id, register = _slow_job(table, monkeypatch, 3)
    register._job_timeout_seconds = 60
    cancel = threading.Event()
    runner = threading.Thread(target=register._run_job, args=(uuid.UUID(job_id), cancel), daemon=True)
    runner.start()
    time.sleep(0.3)
    cancel.set()
    time.sleep(1.5)
    status_then = table.status(job_id)
    assert runner.is_alive(), "returned before the computation ended"
    runner.join(5)
    assert status_then == "canceled"


def test_at_one_slot_a_sync_job_waits_behind_a_batch_job_then_503s(table, monkeypatch):
    """Audit closure: with OPENEO_MAX_CONCURRENT_JOBS=1 a sync request queues behind a running
    batch job for the sync timeout (300 s; 1 s here), then is refused with 503."""
    monkeypatch.setattr(jobs, "_JOB_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(jobs, "_SYNC_JOB_TIMEOUT_SECONDS", 1)
    job_id, register = _slow_job(table, monkeypatch, 3)
    register._job_timeout_seconds = 60
    batch = threading.Thread(target=register._execute_job_background,
                             args=(uuid.UUID(job_id), threading.Event()), daemon=True)
    batch.start()
    time.sleep(0.3)
    started = time.monotonic()
    with pytest.raises(HTTPException) as raised:
        register._execute_sync_in_slot({})
    assert raised.value.status_code == 503
    assert time.monotonic() - started >= 1
    batch.join(5)


def test_at_one_slot_a_sync_job_runs_once_the_batch_job_ends(table, monkeypatch):
    monkeypatch.setattr(jobs, "_JOB_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(jobs, "_SYNC_JOB_TIMEOUT_SECONDS", 5)
    job_id, register = _slow_job(table, monkeypatch, 1)
    register._job_timeout_seconds = 60
    batch = threading.Thread(target=register._execute_job_background,
                             args=(uuid.UUID(job_id), threading.Event()), daemon=True)
    batch.start()
    time.sleep(0.3)
    register.executor = SimpleNamespace(execute=lambda **_: "sync result")
    assert register._execute_sync_in_slot({}) == "sync result"
    batch.join(5)
