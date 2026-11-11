from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from openeo_ai.intelligence import repository as repo_mod
from openeo_ai.intelligence.catalog import CatalogDiscoveryService
from openeo_ai.intelligence.repository import EarthIntelligenceRepository
from openeo_ai.intelligence.worker import EarthIntelligenceWorker
from openeo_ai.storage import job_archive


def _memory_repo() -> EarthIntelligenceRepository:
    job_archive._engine = create_engine("sqlite:///:memory:")
    job_archive._SessionLocal = sessionmaker(bind=job_archive._engine)
    job_archive._tables_ready = False
    repo_mod._tables_ready = False
    return EarthIntelligenceRepository()


def test_catalog_feature_normalization_and_observation_idempotency():
    repo = _memory_repo()
    asset = repo.create_asset("user-1", {"name": "Farm Alpha", "bbox": [76.0, 8.0, 76.1, 8.1]})
    watch = repo.create_watch(
        "user-1",
        {
            "asset_id": asset["id"],
            "name": "NDVI Watch",
            "process_graph": {"load": {"process_id": "load_collection"}},
        },
    )

    item = CatalogDiscoveryService().normalize_feature(
        {
            "id": "S2A_001",
            "collection": "sentinel-2-l2a",
            "bbox": [76.0, 8.0, 76.1, 8.1],
            "geometry": {"type": "Polygon", "coordinates": []},
            "properties": {"datetime": "2026-05-01T10:00:00Z", "eo:cloud_cover": 12.5},
            "assets": {"visual": {"href": "https://example.test/visual.tif"}},
        }
    )
    stored = repo.upsert_catalog_items([item])

    first = repo.create_observations_for_catalog_items("user-1", watch["id"], [stored[0]["id"]])
    second = repo.create_observations_for_catalog_items("user-1", watch["id"], [stored[0]["id"]])

    assert stored[0]["stac_id"] == "S2A_001"
    assert stored[0]["cloud_cover"] == 12.5
    assert len(first) == 1
    assert second == []
    assert first[0]["source_catalog_item_ids"] == [stored[0]["id"]]


def test_worker_signal_updates_baseline_and_queues_delivery():
    repo = _memory_repo()
    asset = repo.create_asset("user-1", {"name": "Mine Site", "bbox": [10.0, 20.0, 10.1, 20.1]})
    watch = repo.create_watch(
        "user-1",
        {
            "asset_id": asset["id"],
            "name": "NDVI Watch",
            "process_graph": {"load": {"process_id": "load_collection"}},
            "quality_policy": {
                "metric": "NDVI",
                # Deliveries now gate on a breach; this plumbing test opts
                # info-level signals into delivery explicitly.
                "min_severity": "info",
                "alert_targets": [{"channel": "webhook", "target": "https://example.test/hook"}],
            },
        },
    )
    observation = repo.create_observation_for_watch("user-1", watch["id"], {})

    worker = EarthIntelligenceWorker(repository=repo)
    signal = worker._create_signal(
        "user-1",
        observation["id"],
        asset,
        watch,
        {"validation": {"warnings": []}},
        {"mean": 0.64, "nan_percent": 5.0},
        save_id="save1234",
    )

    baselines = repo.list_baselines("user-1", asset_id=asset["id"], watch_id=watch["id"], metric="NDVI")
    deliveries = repo.list_deliveries("user-1", signal_id=signal["id"])

    assert signal["metric"] == "NDVI"
    assert signal["value"] == 0.64
    assert baselines[0]["sample_count"] == 1
    assert baselines[0]["stats"]["last_value"] == 0.64
    assert deliveries[0]["status"] == "pending"
    assert deliveries[0]["channel"] == "webhook"


def test_claim_next_observation_recovers_stale_locked_running_observation():
    repo = _memory_repo()
    asset = repo.create_asset("user-1", {"name": "Farm Alpha", "bbox": [76.0, 8.0, 76.1, 8.1]})
    watch = repo.create_watch(
        "user-1",
        {
            "asset_id": asset["id"],
            "name": "NDVI Watch",
            "process_graph": {"load": {"process_id": "load_collection"}},
        },
    )
    observation = repo.create_observation_for_watch("user-1", watch["id"], {})
    stale_at = datetime.utcnow() - timedelta(minutes=10)
    repo.update_observation(
        "user-1",
        observation["id"],
        {
            "status": "running",
            "started_at": stale_at,
            "locked_by": "old-worker",
            "locked_at": stale_at,
        },
    )

    claimed = repo.claim_next_observation("new-worker", stale_after_seconds=60)

    assert claimed is not None
    assert claimed["id"] == observation["id"]
    assert claimed["locked_by"] == "new-worker"
    assert claimed["attempt_count"] == 1
    assert claimed["quality_metrics"]["worker"]["recovered_stale"] is True


def test_retryable_worker_failure_requeues_with_backoff():
    repo = _memory_repo()
    asset = repo.create_asset("user-1", {"name": "Farm Alpha", "bbox": [76.0, 8.0, 76.1, 8.1]})
    watch = repo.create_watch(
        "user-1",
        {
            "asset_id": asset["id"],
            "name": "NDVI Watch",
            "process_graph": {"load": {"process_id": "load_collection"}},
        },
    )
    observation = repo.create_observation_for_watch("user-1", watch["id"], {})
    claimed = repo.claim_next_observation("worker-1")
    assert claimed is not None

    worker = EarthIntelligenceWorker(
        repository=repo,
        max_attempts=3,
        retry_base_delay_seconds=60,
        retry_max_delay_seconds=60,
    )
    result = worker._mark_failed(
        "user-1",
        observation["id"],
        "temporary connection timeout",
    )
    updated = repo.get_observation("user-1", observation["id"])
    events = repo.list_observation_events("user-1", observation["id"])

    assert result["status"] == "queued"
    assert result["retry_scheduled"] is True
    assert updated["status"] == "queued"
    assert updated["locked_by"] is None
    assert updated["next_attempt_at"] is not None
    assert repo.claim_next_observation("worker-2") is None
    assert any(event["event_type"] == "retry_scheduled" for event in events)


def test_exhausted_retryable_failure_marks_observation_failed():
    repo = _memory_repo()
    asset = repo.create_asset("user-1", {"name": "Farm Alpha", "bbox": [76.0, 8.0, 76.1, 8.1]})
    watch = repo.create_watch(
        "user-1",
        {
            "asset_id": asset["id"],
            "name": "NDVI Watch",
            "process_graph": {"load": {"process_id": "load_collection"}},
        },
    )
    observation = repo.create_observation_for_watch("user-1", watch["id"], {})
    repo.update_observation(
        "user-1",
        observation["id"],
        {"status": "running", "attempt_count": 3, "locked_by": "worker-1", "locked_at": datetime.utcnow()},
    )

    worker = EarthIntelligenceWorker(repository=repo, max_attempts=3)
    result = worker._mark_failed("user-1", observation["id"], "temporary connection timeout")
    updated = repo.get_observation("user-1", observation["id"])

    assert result["status"] == "failed"
    assert "retry_scheduled" not in result or result["retry_scheduled"] is not True
    assert updated["status"] == "failed"
    assert updated["locked_by"] is None
    assert updated["next_attempt_at"] is None


# Signal confidence → coverage relabel (DB column unchanged)

def test_signal_to_dict_serializes_coverage_not_confidence():
    repo = _memory_repo()
    sig = repo.create_signal("u1", {"signal_type": "metric", "confidence": 0.87})
    assert sig["coverage"] == 0.87
    assert "confidence" not in sig


def test_signal_create_schema_maps_coverage_to_confidence_column():
    from openeo_ai.intelligence.routes import _signal_write_payload
    from openeo_ai.intelligence.schemas import SignalCreate

    body = SignalCreate(coverage=0.9)
    assert body.coverage == 0.9
    d = _signal_write_payload(body)
    # The public 'coverage' field writes the unchanged DB column 'confidence'.
    assert d["confidence"] == 0.9 and "coverage" not in d


def test_asset_with_a_watch_can_be_deleted_only_by_its_owner():
    repo = _memory_repo()
    asset = repo.create_asset("user-1", {"name": "Farm", "bbox": [76.0, 8.0, 76.1, 8.1]})
    repo.create_watch(
        "user-1",
        {"asset_id": asset["id"], "name": "Watch", "process_graph": {"load": {"process_id": "load_collection"}}},
    )
    assert repo.delete_asset("user-2", asset["id"]) is False
    assert repo.get_asset("user-1", asset["id"]) is not None
    assert repo.delete_asset("user-1", asset["id"]) is True
    assert repo.get_asset("user-1", asset["id"]) is None
    assert repo.delete_asset("user-1", asset["id"]) is False
