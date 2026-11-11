# ABOUTME: Tests the declared status lifecycles and that each record's writers obey them:
# ABOUTME: settled states stay settled, timestamps follow status, and races can't double-run work.
import asyncio
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from openeo_ai import lifecycle
from openeo_ai.batch.batch_store import BatchStore
from openeo_ai.batch.executor import BatchExecutor
from openeo_ai.batch.models import (
    BatchJobCreate,
    BatchStatus,
    LocationInput,
    ScheduleCreate,
    ScheduleFrequency,
    ScheduleStatus,
    WorkflowTemplateCreate,
    init_batch_db,
)
from openeo_ai.batch.scheduler import ScheduleStore
from openeo_ai.batch.template_store import TemplateStore
from openeo_ai.intelligence import repository as repo_mod
from openeo_ai.intelligence.repository import EarthIntelligenceRepository
from openeo_ai.intelligence.schemas import DeliveryUpdate, ObservationUpdate, SignalUpdate, WatchUpdate
from openeo_ai.lifecycle import IllegalTransition
from openeo_ai.storage import job_archive


# ── The declarations themselves ──

@pytest.mark.parametrize("machine", lifecycle.ALL, ids=lambda m: m.name)
def test_every_move_targets_a_declared_state(machine):
    for state, targets in machine.moves.items():
        assert targets <= machine.states, f"{machine.name}.{state} moves to an undeclared state"


@pytest.mark.parametrize("machine", lifecycle.ALL, ids=lambda m: m.name)
def test_every_state_is_reachable_or_initial(machine):
    reached = set().union(*machine.moves.values())
    initial = next(iter(machine.moves))
    assert machine.states - reached <= {initial}


def test_user_settable_statuses_are_declared():
    by_name = {machine.name: machine for machine in lifecycle.ALL}
    for name, statuses in lifecycle.USER_SETTABLE.items():
        assert statuses <= by_name[name].states


def test_illegal_move_names_the_allowed_ones():
    with pytest.raises(IllegalTransition) as raised:
        lifecycle.DELIVERY.check("sent", "pending")
    assert "it is final" in str(raised.value)
    with pytest.raises(IllegalTransition) as raised:
        lifecycle.OBSERVATION.check("queued", "completed")
    assert "cancelled, running" in str(raised.value)


def test_staying_put_is_always_allowed():
    for machine in lifecycle.ALL:
        for state in machine.states:
            assert machine.can(state, state)


@pytest.mark.parametrize("status,outcome", [
    ("finished", "finished"), ("completed", "finished"), ("error", "failed"),
    ("canceled", "cancelled"), ("cancelled", "cancelled"), ("running", "pending"),
    ("queued", "pending"), (None, "pending"),
])
def test_openeo_outcome_maps_every_spelling(status, outcome):
    assert lifecycle.openeo_outcome(status) == outcome


# ── API inputs ──

def test_update_schemas_accept_only_settable_statuses():
    assert ObservationUpdate(status="canceled").status == "cancelled"
    assert SignalUpdate(status="resolved").status == "resolved"
    assert WatchUpdate(status="paused").status == "paused"
    for schema, status in ((ObservationUpdate, "completed"), (DeliveryUpdate, "sent"),
                           (SignalUpdate, "closed"), (WatchUpdate, "Active")):
        with pytest.raises(ValidationError):
            schema(status=status)


def test_delivery_update_ignores_dispatcher_fields():
    update = DeliveryUpdate(status="pending", attempt_count=0, sent_at=datetime.utcnow())
    assert update.model_dump(exclude_unset=True) == {"status": "pending"}


# ── Earth Intelligence records ──

def _repo() -> EarthIntelligenceRepository:
    job_archive._engine = create_engine("sqlite:///:memory:")
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    repo_mod._tables_ready = False
    return EarthIntelligenceRepository()


def _observation(repo):
    asset = repo.create_asset("u1", {"name": "Field", "bbox": [1.0, 2.0, 1.1, 2.1]})
    watch = repo.create_watch("u1", {"asset_id": asset["id"], "name": "W", "process_graph": {}})
    return watch, repo.create_observation_for_watch("u1", watch["id"], {})


def test_signal_timestamps_follow_status():
    repo = _repo()
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    acked = repo.update_signal("u1", signal["id"], {"acknowledged": True})
    assert acked["status"] == "acknowledged" and acked["acknowledged_at"]
    resolved = repo.update_signal("u1", signal["id"], {"resolved": True})
    assert resolved["status"] == "resolved" and resolved["resolved_at"]

    # Unresolving goes back to acknowledged, not to a "resolved" signal with no resolved time.
    unresolved = repo.update_signal("u1", signal["id"], {"resolved": False})
    assert unresolved["status"] == "acknowledged" and unresolved["resolved_at"] is None

    reopened = repo.update_signal("u1", signal["id"], {"status": "open"})
    assert reopened["acknowledged_at"] is None and reopened["resolved_at"] is None


def test_delivery_cannot_be_faked_as_sent_or_revived_after_sending():
    repo = _repo()
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    delivery = repo.create_delivery("u1", {"signal_id": signal["id"], "channel": "webhook",
                                           "target": "https://example.test/hook"})
    [claimed] = repo.claim_pending_deliveries(limit=1)
    repo.finish_delivery(claimed["id"], success=True)
    with pytest.raises(IllegalTransition):
        repo.update_delivery("u1", delivery["id"], {"status": "pending"})


def test_failed_delivery_retry_starts_a_fresh_attempt_budget():
    repo = _repo()
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    delivery = repo.create_delivery("u1", {"signal_id": signal["id"], "channel": "webhook",
                                           "target": "https://example.test/hook"})
    repo.claim_pending_deliveries(limit=1)
    repo.finish_delivery(delivery["id"], success=False, error="boom", retryable=False)
    retried = repo.update_delivery("u1", delivery["id"], {"status": "pending"})
    assert retried["status"] == "pending"
    assert retried["attempt_count"] == 0 and retried["last_error"] is None


def test_finishing_an_unclaimed_delivery_changes_nothing():
    repo = _repo()
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    delivery = repo.create_delivery("u1", {"signal_id": signal["id"], "channel": "webhook",
                                           "target": "https://example.test/hook"})
    result = repo.finish_delivery(delivery["id"], success=True)
    assert result["status"] == "pending" and result["attempt_count"] == 0


def test_observation_rejects_moves_its_lifecycle_forbids():
    repo = _repo()
    _, observation = _observation(repo)
    with pytest.raises(IllegalTransition):
        repo.update_observation("u1", observation["id"], {"status": "completed"})
    cancelled = repo.update_observation("u1", observation["id"], {"status": "canceled"})
    assert cancelled["status"] == "cancelled"


def test_fresh_claim_of_a_long_running_observation_is_not_stale():
    repo = _repo()
    _, observation = _observation(repo)
    first = repo.claim_next_observation("worker-a")
    # A deferral puts it back to queued but keeps its first start time.
    repo.update_observation("u1", first["id"], {"status": "queued",
                                                "started_at": datetime.utcnow() - timedelta(hours=3)},
                            by_user=False)
    again = repo.claim_next_observation("worker-a")
    assert again["id"] == observation["id"]
    # worker-a has just claimed it, so worker-b must not take it as stale.
    assert repo.claim_next_observation("worker-b") is None


def test_manual_start_refuses_an_observation_a_worker_holds():
    repo = _repo()
    _, observation = _observation(repo)
    assert repo.claim_next_observation("worker-a")
    assert repo.start_observation("u1", observation["id"], "manual") is None


def test_watch_status_follows_its_lifecycle():
    repo = _repo()
    watch, _ = _observation(repo)
    assert repo.update_watch("u1", watch["id"], {"status": "paused"})["status"] == "paused"
    assert repo.update_watch("u1", watch["id"], {"status": "active"})["status"] == "active"


# ── Batches and schedules ──

@pytest.fixture
def stores(tmp_path):
    path = str(tmp_path / "batch.db")
    init_batch_db(path)
    templates = TemplateStore(db_path=path)
    template = templates.create_template(
        WorkflowTemplateCreate(name="T", description="", process_graph={"x": {"process_id": "ndvi",
                                                                               "arguments": {}, "result": True}},
                               parameters=[]),
        user_id="u1",
    )
    return templates, BatchStore(db_path=path), ScheduleStore(db_path=path), template


def _batch(batch_store, template, count=2):
    locations = [LocationInput(name=f"L{i}", spatial_extent={"west": 1, "south": 1, "east": 1.1, "north": 1.1})
                 for i in range(count)]
    return batch_store.create_batch_job(BatchJobCreate(template_id=template.id, locations=locations), user_id="u1")


def test_cancelled_items_stay_cancelled_when_their_executor_reports_late(stores):
    _, batch_store, _, template = stores
    batch = _batch(batch_store, template)
    item = batch_store.get_batch_items(batch.id)[0]
    batch_store.update_item_status(item.id, BatchStatus.RUNNING)
    batch_store.cancel_batch_job(batch.id, "u1")

    assert batch_store.update_item_status(item.id, BatchStatus.COMPLETED) is False
    assert all(i.status == BatchStatus.CANCELLED for i in batch_store.get_batch_items(batch.id))
    assert batch_store.get_batch_job(batch.id).status == BatchStatus.CANCELLED


def test_settled_batch_cannot_be_cancelled(stores):
    _, batch_store, _, template = stores
    batch = _batch(batch_store, template, count=1)
    item = batch_store.get_batch_items(batch.id)[0]
    batch_store.update_item_status(item.id, BatchStatus.RUNNING)
    batch_store.update_item_status(item.id, BatchStatus.COMPLETED)
    with pytest.raises(IllegalTransition):
        batch_store.cancel_batch_job(batch.id, "u1")


def _executor_with_outcome(batch_store, templates, final_status):
    executor = BatchExecutor(batch_store=batch_store, template_store=templates)

    async def submit(**_kwargs):
        return {"id": "job-1"}

    async def poll(_job_id, **_kwargs):
        return {"status": final_status}

    executor._submit_openeo_job = submit
    executor._poll_job = poll
    return executor


@pytest.mark.parametrize("final_status,expected", [
    ("canceled", BatchStatus.CANCELLED),
    ("running", BatchStatus.FAILED),  # polling gave up: no result, so not completed
])
def test_items_record_what_actually_happened_to_their_job(stores, monkeypatch, final_status, expected):
    templates, batch_store, _, template = stores
    monkeypatch.setattr("openeo_ai.batch.executor.RATE_LIMIT_DELAY", 0)
    batch = _batch(batch_store, template, count=1)
    item = batch_store.get_batch_items(batch.id)[0]
    executor = _executor_with_outcome(batch_store, templates, final_status)
    asyncio.run(executor._process_item(item.id, "L0", item.spatial_extent, template, {}))
    assert batch_store.get_batch_items(batch.id)[0].status == expected


def test_resuming_an_expired_or_cancelled_schedule_is_refused(stores):
    _, _, schedules, template = stores
    schedule = schedules.create_schedule(
        ScheduleCreate(template_id=template.id, frequency=ScheduleFrequency.DAILY,
                       locations=[LocationInput(name="L", spatial_extent={"west": 1, "south": 1,
                                                                          "east": 1.1, "north": 1.1})]),
        user_id="u1",
    )
    assert schedules.update_schedule_status(schedule.id, "u1", ScheduleStatus.PAUSED)
    assert schedules.update_schedule_status(schedule.id, "u1", ScheduleStatus.CANCELLED)
    with pytest.raises(IllegalTransition):
        schedules.update_schedule_status(schedule.id, "u1", ScheduleStatus.ACTIVE)


# ── Database guards (migration phase19) ──

def _phase19():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "openeo_app/psql/alembic/versions/phase19_status_lifecycles.py"
    spec = importlib.util.spec_from_file_location("phase19", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_constraints_match_the_lifecycles():
    checks = _phase19().STATUS_CHECKS
    by_table = {
        "observations": lifecycle.OBSERVATION, "signals": lifecycle.SIGNAL,
        "deliveries": lifecycle.DELIVERY, "watches": lifecycle.WATCH,
        "batch_items": lifecycle.BATCH_ITEM, "batch_jobs": lifecycle.BATCH_JOB,
        "schedules": lifecycle.SCHEDULE,
    }
    assert set(checks) == set(by_table)
    for table, machine in by_table.items():
        assert set(checks[table]) == machine.states, table


def test_database_refuses_an_undeclared_status():
    from sqlalchemy.exc import IntegrityError

    repo = _repo()
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    session = repo._session()
    try:
        row = session.get(repo_mod.EarthSignal, signal["id"])
        row.status = "closed"
        with pytest.raises(IntegrityError):
            session.commit()
    finally:
        session.rollback()
        session.close()


# ── Delivery claims and retries ──

def _delivery(repo):
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    return repo.create_delivery("u1", {"signal_id": signal["id"], "channel": "webhook",
                                       "target": "https://example.test/hook"})


def test_retry_waits_for_its_backoff():
    repo = _repo()
    delivery = _delivery(repo)
    repo.claim_pending_deliveries(limit=1)
    retried = repo.finish_delivery(delivery["id"], success=False, error="503", retryable=True)
    assert retried["status"] == "pending" and retried["next_attempt_at"]
    assert repo.claim_pending_deliveries(limit=1) == []


def test_backoff_grows_and_is_capped():
    assert repo_mod.delivery_retry_delay(1) == timedelta(minutes=1)
    assert repo_mod.delivery_retry_delay(3) == timedelta(minutes=4)
    assert repo_mod.delivery_retry_delay(20) == timedelta(hours=1)


def test_an_old_delivery_claimed_just_now_is_not_reclaimed():
    repo = _repo()
    delivery = _delivery(repo)
    session = repo._session()
    try:
        row = session.get(repo_mod.Delivery, delivery["id"])
        row.created_at = datetime.utcnow() - timedelta(hours=2)
        session.commit()
    finally:
        session.close()
    assert len(repo.claim_pending_deliveries(limit=1)) == 1
    # Old by creation, but claimed a moment ago: another tick must not send it again.
    assert repo.claim_pending_deliveries(limit=1) == []


def test_a_caller_cannot_requeue_an_observation_a_worker_is_running():
    repo = _repo()
    _, observation = _observation(repo)
    assert repo.claim_next_observation("worker-a")["status"] == "running"
    with pytest.raises(IllegalTransition) as refused:
        repo.update_observation("u1", observation["id"], {"status": "queued"})
    assert "queued" not in refused.value.allowed
    # The worker's own retry still goes back to queued.
    assert repo.update_observation("u1", observation["id"], {"status": "queued"}, by_user=False)["status"] == "queued"


def test_a_caller_can_requeue_a_failed_or_cancelled_observation():
    repo = _repo()
    _, observation = _observation(repo)
    repo.update_observation("u1", observation["id"], {"status": "cancelled"})
    assert repo.update_observation("u1", observation["id"], {"status": "queued"})["status"] == "queued"


def test_a_caller_cannot_requeue_a_delivery_being_sent():
    repo = _repo()
    signal = repo.create_signal("u1", {"signal_type": "metric"})
    delivery = repo.create_delivery("u1", {"signal_id": signal["id"], "channel": "webhook",
                                           "target": "https://example.test/hook"})
    assert repo.claim_pending_deliveries(limit=1)[0]["status"] == "sending"
    with pytest.raises(IllegalTransition):
        repo.update_delivery("u1", delivery["id"], {"status": "pending"})


def test_the_409_answer_lists_only_what_the_caller_may_do(monkeypatch):
    import asyncio
    import json

    monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "false")
    from openeo_ai.lifecycle import OBSERVATION, check_user_move
    from openeo_ai.web_interface import _illegal_transition

    with pytest.raises(IllegalTransition) as refused:
        check_user_move(OBSERVATION, "running", "queued")
    body = json.loads(asyncio.run(_illegal_transition(None, refused.value)).body)
    assert body["allowed"] == [] and body["current"] == "running"


def test_a_signal_cannot_name_another_users_asset_watch_or_observation():
    repo = _repo()
    watch, observation = _observation(repo)  # u1's
    for key, value in (("asset_id", watch["asset_id"]), ("watch_id", watch["id"]),
                       ("observation_id", observation["id"])):
        assert repo.create_signal("u2", {"signal_type": "metric", key: value}) is None, key
        assert repo.create_signal("u1", {"signal_type": "metric", key: value})[key] == value
