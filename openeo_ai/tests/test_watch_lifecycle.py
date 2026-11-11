# ABOUTME: Lifecycle tests — create_watch derives next_run_at from cadence
# ABOUTME: (L1) and deliveries fire only on a policy breach (L1b, anti-spam).
from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from openeo_ai.intelligence import repository as repo_mod
from openeo_ai.intelligence.repository import EarthIntelligenceRepository
from openeo_ai.intelligence.worker import EarthIntelligenceWorker
from openeo_ai.storage import job_archive


def _memory_repo() -> EarthIntelligenceRepository:
    job_archive._engine = create_engine("sqlite:///:memory:")
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    repo_mod._tables_ready = False
    return EarthIntelligenceRepository()


def _watch(repo, quality_policy=None, cadence=None, next_run_at=None, trigger_type=None):
    asset = repo.create_asset("user-1", {"name": "Site", "bbox": [10.0, 20.0, 10.1, 20.1]})
    data = {
        "asset_id": asset["id"],
        "name": "NDVI Watch",
        "process_graph": {"load": {"process_id": "load_collection"}},
    }
    if quality_policy is not None:
        data["quality_policy"] = quality_policy
    if cadence is not None:
        data["cadence"] = cadence
    if next_run_at is not None:
        data["next_run_at"] = next_run_at
    if trigger_type is not None:
        data["trigger_type"] = trigger_type
    return asset, repo.create_watch("user-1", data)


# ── L1: create_watch derives scheduling from cadence ─────────────────────────

def test_cadence_sets_next_run_at_and_scheduled():
    repo = _memory_repo()
    _, watch = _watch(repo, cadence="P1W")
    assert watch["next_run_at"] is not None
    assert watch["trigger_type"] == "scheduled"


def test_cadence_scheduled_even_when_caller_passes_manual():
    # api_v1/routes send plain model_dump → trigger_type='manual' default present.
    repo = _memory_repo()
    _, watch = _watch(repo, cadence="P1W", trigger_type="manual")
    assert watch["next_run_at"] is not None
    assert watch["trigger_type"] == "scheduled"


def test_explicit_next_run_at_is_preserved():
    repo = _memory_repo()
    _, watch = _watch(repo, cadence="P1W", next_run_at=datetime(2030, 1, 1, 0, 0, 0))
    assert watch["next_run_at"].startswith("2030-01-01")


def test_no_cadence_leaves_watch_manual_and_unscheduled():
    repo = _memory_repo()
    _, watch = _watch(repo)
    assert watch["next_run_at"] is None
    assert watch["trigger_type"] == "manual"


def test_scheduled_watch_meets_worker_due_predicates():
    # enqueue_due_watches filters on: status active, next_run_at != None, next_run_at <= now.
    repo = _memory_repo()
    _, watch = _watch(repo, cadence="P1W")
    assert watch["status"] == "active"
    assert watch["next_run_at"] is not None
    assert datetime.fromisoformat(watch["next_run_at"]) <= datetime.utcnow()


# ── L1b: deliveries fire only on a policy breach ─────────────────────────────

def _signal(repo, watch, asset, stats, save_id="s1"):
    worker = EarthIntelligenceWorker(repository=repo)
    return worker._create_signal(
        "user-1", repo.create_observation_for_watch("user-1", watch["id"], {})["id"],
        asset, watch, {"validation": {"warnings": []}}, stats, save_id=save_id,
    )


_ALERT = [{"channel": "webhook", "target": "https://example.test/hook"}]


def test_info_signal_does_not_deliver_by_default():
    repo = _memory_repo()
    asset, watch = _watch(repo, quality_policy={"metric": "NDVI", "alert_targets": _ALERT})
    sig = _signal(repo, watch, asset, {"mean": 0.64, "nan_percent": 5.0})
    assert repo.list_deliveries("user-1", signal_id=sig["id"]) == []


def test_warning_signal_delivers_by_default():
    repo = _memory_repo()
    asset, watch = _watch(repo, quality_policy={"metric": "NDVI", "alert_targets": _ALERT})
    sig = _signal(repo, watch, asset, {"mean": 0.64, "nan_percent": 60.0})
    d = repo.list_deliveries("user-1", signal_id=sig["id"])
    assert len(d) == 1 and d[0]["channel"] == "webhook"


def test_min_severity_info_opts_into_info_delivery():
    repo = _memory_repo()
    asset, watch = _watch(repo, quality_policy={"metric": "NDVI", "min_severity": "info", "alert_targets": _ALERT})
    sig = _signal(repo, watch, asset, {"mean": 0.64, "nan_percent": 5.0})
    assert len(repo.list_deliveries("user-1", signal_id=sig["id"])) == 1


def test_direction_threshold_breach_delivers():
    repo = _memory_repo()
    asset, watch = _watch(repo, quality_policy={
        "metric": "NDVI", "direction": "drop", "threshold": 0.1, "alert_targets": _ALERT})
    _signal(repo, watch, asset, {"mean": 0.60, "nan_percent": 5.0}, save_id="a")   # baseline
    sig = _signal(repo, watch, asset, {"mean": 0.45, "nan_percent": 5.0}, save_id="b")  # delta -0.15
    assert len(repo.list_deliveries("user-1", signal_id=sig["id"])) == 1


def test_direction_threshold_no_breach_no_delivery():
    repo = _memory_repo()
    asset, watch = _watch(repo, quality_policy={
        "metric": "NDVI", "direction": "drop", "threshold": 0.1, "alert_targets": _ALERT})
    _signal(repo, watch, asset, {"mean": 0.60, "nan_percent": 5.0}, save_id="a")   # baseline
    sig = _signal(repo, watch, asset, {"mean": 0.58, "nan_percent": 5.0}, save_id="b")  # delta -0.02
    assert repo.list_deliveries("user-1", signal_id=sig["id"]) == []


def test_gate_disabled_env_restores_unconditional_delivery(monkeypatch):
    monkeypatch.setenv("JONA_DELIVERY_THRESHOLD_GATE", "0")
    repo = _memory_repo()
    asset, watch = _watch(repo, quality_policy={"metric": "NDVI", "alert_targets": _ALERT})
    sig = _signal(repo, watch, asset, {"mean": 0.64, "nan_percent": 5.0})  # info
    assert len(repo.list_deliveries("user-1", signal_id=sig["id"])) == 1
