# ABOUTME: Dead-Man Tick — worker heartbeat + due-lag detector that alarms as a
# ABOUTME: system Signal through the existing delivery pipe, with an anti-spam gate.
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from openeo_ai.intelligence import repository as repo_mod
from openeo_ai.intelligence.deadman import (
    SYSTEM_USER_ID,
    assess_liveness,
    run_deadman_check,
)
from openeo_ai.intelligence.repository import EarthIntelligenceRepository
from openeo_ai.storage import job_archive

GRACE = 900


def _memory_repo() -> EarthIntelligenceRepository:
    job_archive._engine = create_engine("sqlite:///:memory:")
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    repo_mod._tables_ready = False
    return EarthIntelligenceRepository()


def _check(repo, *, last_tick_at, now, targets=None, enabled=True, dispatch=None):
    return asyncio.run(run_deadman_check(
        repo, {"last_tick_at": last_tick_at}, targets or [],
        now=now, grace_seconds=GRACE, worker_enabled=enabled, dispatch=dispatch,
    ))


def _system_signals(repo, status=None):
    return [s for s in repo.list_signals(SYSTEM_USER_ID, status=status)
            if s.get("signal_type") == "system"]


def test_healthy_worker_over_many_checks_creates_no_signals():
    repo = _memory_repo()
    now = datetime.utcnow()
    for i in range(20):
        _check(repo, last_tick_at=(now - timedelta(seconds=30)).isoformat(),
               now=now + timedelta(seconds=i))
    assert _system_signals(repo) == []


def test_stale_heartbeat_creates_system_signal():
    repo = _memory_repo()
    now = datetime.utcnow()
    stale = (now - timedelta(seconds=3 * GRACE)).isoformat()
    out = _check(repo, last_tick_at=stale, now=now)
    signals = _system_signals(repo, status="open")
    assert len(signals) == 1
    assert signals[0]["metric"] == "worker_heartbeat"
    assert signals[0]["severity"] == "critical"
    assert signals[0]["user_id"] == SYSTEM_USER_ID
    assert out["signals_created"] == 1


def test_overdue_watch_produces_due_lag_signal():
    repo = _memory_repo()
    now = datetime.utcnow()
    asset = repo.create_asset("user-1", {"name": "Farm", "bbox": [76.0, 8.0, 76.1, 8.1]})
    watch = repo.create_watch("user-1", {
        "asset_id": asset["id"], "name": "W", "cadence": "1d",
        "process_graph": {"load": {"process_id": "load_collection"}},
    })
    repo.update_watch("user-1", watch["id"],
                      {"next_run_at": now - timedelta(seconds=3 * GRACE)})
    # heartbeat looks fresh — a wedged scheduler shows up as due-lag alone
    _check(repo, last_tick_at=now.isoformat(), now=now)
    signals = _system_signals(repo, status="open")
    assert len(signals) == 1
    assert signals[0]["metric"] == "watch_due_lag"


def test_second_check_does_not_duplicate_open_signal_or_delivery():
    repo = _memory_repo()
    now = datetime.utcnow()
    stale = (now - timedelta(seconds=3 * GRACE)).isoformat()
    targets = [{"channel": "webhook", "target": "https://example.test/alarm"}]
    _check(repo, last_tick_at=stale, now=now, targets=targets)
    _check(repo, last_tick_at=stale, now=now + timedelta(seconds=60), targets=targets)
    assert len(_system_signals(repo, status="open")) == 1
    claimed = repo.claim_pending_deliveries(limit=10, user_id=SYSTEM_USER_ID)
    assert len(claimed) == 1


def test_recovery_resolves_open_system_signal():
    repo = _memory_repo()
    now = datetime.utcnow()
    stale = (now - timedelta(seconds=3 * GRACE)).isoformat()
    _check(repo, last_tick_at=stale, now=now)
    assert len(_system_signals(repo, status="open")) == 1
    out = _check(repo, last_tick_at=now.isoformat(), now=now + timedelta(seconds=60))
    assert out["signals_resolved"] == 1
    assert _system_signals(repo, status="open") == []
    assert len(_system_signals(repo, status="resolved")) == 1


def test_disabled_or_never_ticked_worker_raises_no_false_alarm():
    repo = _memory_repo()
    now = datetime.utcnow()
    # intentional silence: worker disabled on purpose
    _check(repo, last_tick_at=None, now=now, enabled=False)
    # cold start: enabled but no tick yet and nothing overdue
    _check(repo, last_tick_at=None, now=now)
    assert _system_signals(repo) == []


def test_detector_dispatches_deliveries_itself():
    # A dead worker cannot send its own alarm — the check must invoke the
    # delivery dispatch directly, and only when it created a signal.
    repo = _memory_repo()
    now = datetime.utcnow()
    calls = []

    async def _dispatch(user_id=None, **kwargs):
        calls.append(user_id)
        return []

    _check(repo, last_tick_at=now.isoformat(), now=now, dispatch=_dispatch)
    assert calls == []
    stale = (now - timedelta(seconds=3 * GRACE)).isoformat()
    _check(repo, last_tick_at=stale, now=now,
           targets=[{"channel": "webhook", "target": "https://example.test/a"}],
           dispatch=_dispatch)
    assert calls == [SYSTEM_USER_ID]


def test_assess_liveness_is_pure_and_graded():
    now = datetime.utcnow()
    fresh = now - timedelta(seconds=10)
    stale = now - timedelta(seconds=3 * GRACE)
    assert assess_liveness(fresh, None, now, GRACE) == []
    faults = assess_liveness(stale, 3 * GRACE, now, GRACE)
    assert {f.metric for f in faults} == {"worker_heartbeat", "watch_due_lag"}
    assert all(f.severity == "critical" for f in faults)
    assert assess_liveness(stale, None, now, GRACE, worker_enabled=False) == []
