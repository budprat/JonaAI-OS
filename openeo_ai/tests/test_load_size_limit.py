# ABOUTME: The load-size guard must be configurable per host: city-scale jobs passed the
# ABOUTME: fixed 250M limit and overwhelmed a 1.2 GB service during the 2026-11-06 audit.
import threading
import time

import pytest

from openeo_app.processes.load_collection import _load_element_limit


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv("OPENEO_MAX_LOAD_ELEMENTS", raising=False)
    assert _load_element_limit() == 250_000_000


def test_uses_configured_limit(monkeypatch):
    monkeypatch.setenv("OPENEO_MAX_LOAD_ELEMENTS", "40000000")
    assert _load_element_limit() == 40_000_000


@pytest.mark.parametrize("value", ["", "not-a-number", "0", "-5"])
def test_bad_values_keep_a_limit(monkeypatch, value):
    monkeypatch.setenv("OPENEO_MAX_LOAD_ELEMENTS", value)
    assert _load_element_limit() == 250_000_000


class TestJobSlots:
    def _register(self, calls):
        from openeo_app.registers import jobs

        register = jobs.ExecutableJobsRegister.__new__(jobs.ExecutableJobsRegister)
        register._run_job = lambda job_id, cancel_event: calls.append(job_id)
        return jobs, register

    def test_job_waits_for_a_slot(self, monkeypatch):
        calls = []
        jobs, register = self._register(calls)
        slots = threading.BoundedSemaphore(1)
        monkeypatch.setattr(jobs, "_JOB_SLOTS", slots)
        slots.acquire()
        worker = threading.Thread(target=register._execute_job_background, args=("j1", threading.Event()))
        worker.start()
        time.sleep(0.2)
        assert calls == []
        slots.release()
        worker.join(timeout=5)
        assert calls == ["j1"]

    def test_slot_released_when_job_raises(self, monkeypatch):
        jobs, register = self._register([])
        slots = threading.BoundedSemaphore(1)
        monkeypatch.setattr(jobs, "_JOB_SLOTS", slots)

        def boom(job_id, cancel_event):
            raise RuntimeError("job failed")

        register._run_job = boom
        with pytest.raises(RuntimeError):
            register._execute_job_background("j1", threading.Event())
        assert slots.acquire(blocking=False)

    def test_cancel_while_waiting_reaches_the_job_without_a_slot(self, monkeypatch):
        calls = []
        jobs, register = self._register(calls)
        slots = threading.BoundedSemaphore(1)
        monkeypatch.setattr(jobs, "_JOB_SLOTS", slots)
        slots.acquire()
        cancel = threading.Event()
        worker = threading.Thread(target=register._execute_job_background, args=("j1", cancel))
        worker.start()
        cancel.set()
        worker.join(timeout=5)
        assert calls == ["j1"]
        slots.release()  # the waiting job took no slot, so this one is still ours


def test_a_load_refusal_keeps_its_explanation():
    from openeo_app.core.exceptions import UserFacingError, classify_error

    assert "too large" in classify_error(UserFacingError("Requested load is too large"))["user_message"]
    assert classify_error(ValueError("/internal/path bad input"))["user_message"] == "An unexpected error occurred."
    assert classify_error(RuntimeError("internal detail"))["user_message"] == "An unexpected error occurred."
