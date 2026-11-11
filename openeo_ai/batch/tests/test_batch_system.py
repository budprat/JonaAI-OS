# ABOUTME: Unit tests for the batch analysis system.
# Tests template CRUD, batch job lifecycle, scheduler, and parameter substitution.

"""
Tests for the Batch/Repeated Analysis Mode.

Uses in-memory SQLite for isolation. Tests cover:
- Template creation, listing, versioning, auto-detection
- Batch job creation, item claiming, status transitions
- Schedule creation, due detection, run recording
- Parameter substitution in process graphs
- Resource estimation
"""

import json
import pytest
from datetime import datetime, timedelta

from openeo_ai.batch.models import (
    WorkflowTemplateCreate,
    TemplateParameter,
    ParameterType,
    BatchJobCreate,
    LocationInput,
    BatchStatus,
    ScheduleFrequency,
    ScheduleStatus,
    ScheduleCreate,
    TriggerType,
    init_batch_db,
)
from openeo_ai.batch.template_store import TemplateStore
from openeo_ai.batch.batch_store import BatchStore
from openeo_ai.batch.scheduler import ScheduleStore, _compute_next_run


# ── Fixtures ──

@pytest.fixture
def db_path(tmp_path):
    """Use a temp SQLite DB for each test."""
    path = str(tmp_path / "test_batch.db")
    init_batch_db(path)
    return path


@pytest.fixture
def template_store(db_path):
    return TemplateStore(db_path=db_path)


@pytest.fixture
def batch_store(db_path):
    return BatchStore(db_path=db_path)


@pytest.fixture
def schedule_store(db_path):
    return ScheduleStore(db_path=db_path)


@pytest.fixture
def sample_process_graph():
    """A realistic OpenEO process graph for NDVI analysis."""
    return {
        "load": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {"$parameter": "spatial_extent"},
                "temporal_extent": {"$parameter": "temporal_extent"},
                "bands": ["B04", "B08"],
            },
        },
        "ndvi": {
            "process_id": "ndvi",
            "arguments": {
                "data": {"from_node": "load"},
                "nir": "B08",
                "red": "B04",
            },
        },
        "reduce": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": "ndvi"},
                "dimension": "t",
                "reducer": {"process_graph": {"mean": {"process_id": "mean", "arguments": {"data": {"from_parameter": "data"}}}}},
            },
        },
        "save": {
            "process_id": "save_result",
            "arguments": {
                "data": {"from_node": "reduce"},
                "format": "GTiff",
            },
            "result": True,
        },
    }


@pytest.fixture
def sample_template(template_store, sample_process_graph):
    """Create and return a sample template."""
    data = WorkflowTemplateCreate(
        name="NDVI Crop Health",
        description="Monthly NDVI analysis for crop health monitoring",
        process_graph=sample_process_graph,
        parameters=[
            TemplateParameter(
                name="spatial_extent",
                type=ParameterType.SPATIAL_EXTENT,
                description="Farm bounding box",
                required=True,
            ),
            TemplateParameter(
                name="temporal_extent",
                type=ParameterType.TEMPORAL_EXTENT,
                description="Analysis date range",
                required=True,
                default_value=["2025-01-01", "2025-02-01"],
            ),
        ],
        tags=["agriculture", "ndvi", "insurance"],
        source_query="Calculate NDVI for my farm for January 2025",
        collection_id="sentinel-2-l2a",
        index_type="NDVI",
    )
    return template_store.create_template(data, user_id="test-user")


@pytest.fixture
def sample_locations():
    return [
        LocationInput(
            name="Farm Alpha",
            spatial_extent={"west": 11.0, "south": 46.0, "east": 11.5, "north": 46.5},
            metadata={"policy_id": "INS-001", "area_hectares": 120},
        ),
        LocationInput(
            name="Farm Beta",
            spatial_extent={"west": 12.0, "south": 47.0, "east": 12.5, "north": 47.5},
            metadata={"policy_id": "INS-002", "area_hectares": 85},
        ),
        LocationInput(
            name="Farm Gamma",
            spatial_extent={"west": 13.0, "south": 48.0, "east": 13.5, "north": 48.5},
            metadata={"policy_id": "INS-003", "area_hectares": 200},
        ),
    ]


# ══════════════════════════════════════════════════════════════
# TEMPLATE TESTS
# ══════════════════════════════════════════════════════════════

class TestTemplateStore:

    def test_create_template(self, template_store, sample_process_graph):
        data = WorkflowTemplateCreate(
            name="Test Template",
            description="A test",
            process_graph=sample_process_graph,
            tags=["test"],
        )
        result = template_store.create_template(data, user_id="user-1")

        assert result.name == "Test Template"
        assert result.version == 1
        assert result.use_count == 0
        assert result.user_id == "user-1"
        assert result.process_graph == sample_process_graph

    def test_get_template(self, template_store, sample_template):
        loaded = template_store.get_template(sample_template.id)
        assert loaded is not None
        assert loaded.name == "NDVI Crop Health"
        assert len(loaded.parameters) == 2

    def test_list_templates(self, template_store, sample_template):
        templates, total = template_store.list_templates("test-user")
        assert total == 1
        assert templates[0].id == sample_template.id

    def test_list_templates_filter_by_tag(self, template_store, sample_template):
        templates, total = template_store.list_templates("test-user", tag="agriculture")
        assert total == 1
        templates, total = template_store.list_templates("test-user", tag="nonexistent")
        assert total == 0

    def test_update_template_bumps_version(self, template_store, sample_template):
        updated = template_store.update_template(
            sample_template.id,
            "test-user",
            name="Updated NDVI",
        )
        assert updated.version == 2
        assert updated.name == "Updated NDVI"

    def test_delete_template(self, template_store, sample_template):
        assert template_store.delete_template(sample_template.id, "test-user")
        assert template_store.get_template(sample_template.id) is None

    def test_delete_template_wrong_user(self, template_store, sample_template):
        assert not template_store.delete_template(sample_template.id, "other-user")

    def test_increment_use_count(self, template_store, sample_template):
        template_store.increment_use_count(sample_template.id)
        template_store.increment_use_count(sample_template.id)
        loaded = template_store.get_template(sample_template.id)
        assert loaded.use_count == 2


class TestParameterSubstitution:

    def test_apply_parameters(self, sample_process_graph, sample_template):
        result = TemplateStore.apply_parameters(
            sample_process_graph,
            sample_template.parameters,
            {
                "spatial_extent": {"west": 10.0, "south": 45.0, "east": 10.5, "north": 45.5},
                "temporal_extent": ["2025-06-01", "2025-07-01"],
            },
        )

        # spatial_extent should be substituted
        load_args = result["load"]["arguments"]
        assert load_args["spatial_extent"]["west"] == 10.0

        # temporal_extent should be substituted
        assert load_args["temporal_extent"] == ["2025-06-01", "2025-07-01"]

    def test_apply_parameters_uses_default(self, sample_process_graph, sample_template):
        # Only provide spatial_extent, temporal_extent should use default
        result = TemplateStore.apply_parameters(
            sample_process_graph,
            sample_template.parameters,
            {
                "spatial_extent": {"west": 10.0, "south": 45.0, "east": 10.5, "north": 45.5},
            },
        )
        load_args = result["load"]["arguments"]
        assert load_args["temporal_extent"] == ["2025-01-01", "2025-02-01"]

    def test_apply_parameters_missing_required(self, sample_process_graph, sample_template):
        with pytest.raises(ValueError, match="spatial_extent"):
            TemplateStore.apply_parameters(
                sample_process_graph,
                sample_template.parameters,
                {},  # spatial_extent is required with no default
            )

    def test_auto_detect_parameters(self, sample_process_graph):
        params = TemplateStore.auto_detect_parameters(sample_process_graph)
        names = {p.name for p in params}
        assert "spatial_extent" in names
        assert "temporal_extent" in names


# ══════════════════════════════════════════════════════════════
# BATCH JOB TESTS
# ══════════════════════════════════════════════════════════════

class TestBatchStore:

    def test_create_batch_job(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            name="January Assessment",
            locations=sample_locations,
            parameter_overrides={"temporal_extent": ["2025-01-01", "2025-02-01"]},
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")

        assert batch.status == BatchStatus.PENDING
        assert batch.total_items == 3
        assert batch.completed_items == 0

    def test_get_batch_items(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations,
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")
        items = batch_store.get_batch_items(batch.id)

        assert len(items) == 3
        assert all(i.status == BatchStatus.PENDING for i in items)
        names = {i.location_name for i in items}
        assert names == {"Farm Alpha", "Farm Beta", "Farm Gamma"}

    def test_claim_pending_items(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations,
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")

        # Claim 2 items
        claimed = batch_store.get_next_pending_items(batch.id, count=2)
        assert len(claimed) == 2

        # Only 1 pending left
        remaining = batch_store.get_next_pending_items(batch.id, count=5)
        assert len(remaining) == 1

    def test_update_item_status_completed(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations[:1],  # just one item
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")
        items = batch_store.get_batch_items(batch.id)
        item = items[0]

        batch_store.update_item_status(
            item.id,
            BatchStatus.COMPLETED,
            openeo_job_id="openeo-123",
            result_path="/tmp/result.tif",
            result_summary={"mean_ndvi": 0.65, "min_ndvi": 0.3, "max_ndvi": 0.85},
        )

        # Check batch-level rollup
        updated_batch = batch_store.get_batch_job(batch.id)
        assert updated_batch.completed_items == 1
        assert updated_batch.status == BatchStatus.COMPLETED

    def test_partial_completion(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations[:2],
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")
        items = batch_store.get_batch_items(batch.id)

        batch_store.update_item_status(items[0].id, BatchStatus.COMPLETED)
        batch_store.update_item_status(items[1].id, BatchStatus.FAILED, error_message="STAC timeout")

        updated = batch_store.get_batch_job(batch.id)
        assert updated.status == BatchStatus.PARTIALLY_COMPLETED
        assert updated.completed_items == 1
        assert updated.failed_items == 1

    def test_cancel_batch_job(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations,
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")

        assert batch_store.cancel_batch_job(batch.id, "test-user")

        updated = batch_store.get_batch_job(batch.id)
        assert updated.status == BatchStatus.CANCELLED

    def test_cancel_wrong_user(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations,
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")
        assert not batch_store.cancel_batch_job(batch.id, "other-user")

    def test_resource_estimation(self, batch_store):
        est = batch_store.estimate_resources(50)
        assert est.total_items == 50
        assert est.estimated_duration_seconds == 50 * 30
        assert est.warnings == []

        est_large = batch_store.estimate_resources(150)
        assert len(est_large.warnings) == 1
        assert "Large batch" in est_large.warnings[0]

    def test_batch_results_aggregation(self, batch_store, sample_template, sample_locations):
        data = BatchJobCreate(
            template_id=sample_template.id,
            locations=sample_locations[:2],
        )
        batch = batch_store.create_batch_job(data, user_id="test-user")
        items = batch_store.get_batch_items(batch.id)

        batch_store.update_item_status(
            items[0].id, BatchStatus.COMPLETED,
            result_summary={"mean_ndvi": 0.65},
        )
        batch_store.update_item_status(
            items[1].id, BatchStatus.COMPLETED,
            result_summary={"mean_ndvi": 0.45},
        )

        results = batch_store.get_batch_results(batch.id)
        assert results.completed == 2
        assert "mean_ndvi" in results.aggregated_stats
        assert results.aggregated_stats["mean_ndvi"]["min"] == 0.45
        assert results.aggregated_stats["mean_ndvi"]["max"] == 0.65


# ══════════════════════════════════════════════════════════════
# SCHEDULE TESTS
# ══════════════════════════════════════════════════════════════

class TestScheduleStore:

    def test_create_schedule(self, schedule_store, sample_template, sample_locations):
        data = ScheduleCreate(
            template_id=sample_template.id,
            name="Monthly Crop Check",
            frequency=ScheduleFrequency.MONTHLY,
            locations=sample_locations,
        )
        sched = schedule_store.create_schedule(data, user_id="test-user")

        assert sched.frequency == ScheduleFrequency.MONTHLY
        assert sched.status == ScheduleStatus.ACTIVE
        assert sched.locations_count == 3
        assert sched.next_run_at is not None

    def test_pause_resume(self, schedule_store, sample_template, sample_locations):
        data = ScheduleCreate(
            template_id=sample_template.id,
            frequency=ScheduleFrequency.WEEKLY,
            locations=sample_locations[:1],
        )
        sched = schedule_store.create_schedule(data, user_id="test-user")

        assert schedule_store.update_schedule_status(
            sched.id, "test-user", ScheduleStatus.PAUSED
        )
        paused = schedule_store.get_schedule(sched.id)
        assert paused.status == ScheduleStatus.PAUSED

        assert schedule_store.update_schedule_status(
            sched.id, "test-user", ScheduleStatus.ACTIVE
        )
        resumed = schedule_store.get_schedule(sched.id)
        assert resumed.status == ScheduleStatus.ACTIVE

    def test_record_run_increments_count(self, schedule_store, sample_template, sample_locations):
        data = ScheduleCreate(
            template_id=sample_template.id,
            frequency=ScheduleFrequency.MONTHLY,
            locations=sample_locations[:1],
            max_runs=3,
        )
        sched = schedule_store.create_schedule(data, user_id="test-user")

        schedule_store.record_run(sched.id, "batch-001")
        updated = schedule_store.get_schedule(sched.id)
        assert updated.runs_completed == 1
        assert updated.status == ScheduleStatus.ACTIVE

    def test_record_run_expires_at_max(self, schedule_store, sample_template, sample_locations):
        data = ScheduleCreate(
            template_id=sample_template.id,
            frequency=ScheduleFrequency.MONTHLY,
            locations=sample_locations[:1],
            max_runs=2,
        )
        sched = schedule_store.create_schedule(data, user_id="test-user")

        schedule_store.record_run(sched.id, "batch-001")
        schedule_store.record_run(sched.id, "batch-002")

        expired = schedule_store.get_schedule(sched.id)
        assert expired.status == ScheduleStatus.EXPIRED


class TestComputeNextRun:

    def test_daily(self):
        base = datetime(2025, 6, 1, 12, 0, 0)
        nxt = _compute_next_run(ScheduleFrequency.DAILY, base)
        assert nxt == datetime(2025, 6, 2, 12, 0, 0)

    def test_weekly(self):
        base = datetime(2025, 6, 1, 12, 0, 0)
        nxt = _compute_next_run(ScheduleFrequency.WEEKLY, base)
        assert nxt == datetime(2025, 6, 8, 12, 0, 0)

    def test_monthly(self):
        base = datetime(2025, 6, 1, 12, 0, 0)
        nxt = _compute_next_run(ScheduleFrequency.MONTHLY, base)
        assert nxt == datetime(2025, 7, 1, 12, 0, 0)

    def test_manual_returns_none(self):
        assert _compute_next_run(ScheduleFrequency.MANUAL) is None

    def test_satellite_pass_polls_every_six_hours_by_default(self, monkeypatch):
        monkeypatch.delenv("SATELLITE_PASS_POLL_MINUTES", raising=False)
        base = datetime(2025, 6, 1, 12, 0, 0)
        nxt = _compute_next_run(ScheduleFrequency.ON_SATELLITE_PASS, base)
        assert nxt == datetime(2025, 6, 1, 18, 0, 0)

    def test_satellite_pass_poll_interval_is_a_setting(self, monkeypatch):
        monkeypatch.setenv("SATELLITE_PASS_POLL_MINUTES", "30")
        base = datetime(2025, 6, 1, 12, 0, 0)
        nxt = _compute_next_run(ScheduleFrequency.ON_SATELLITE_PASS, base)
        assert nxt == datetime(2025, 6, 1, 12, 30, 0)
