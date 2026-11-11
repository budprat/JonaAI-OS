"""Tests for trajectory store — capture and persist tool call trajectories."""

import json
import os
import time

import pytest

from openeo_ai.evaluation.trajectory_store import (
    ToolCall,
    Trajectory,
    TrajectoryStore,
)


@pytest.fixture
def store(tmp_path):
    """Create a TrajectoryStore with a dedicated SQLite database."""
    db_path = str(tmp_path / "test_traj.db")
    return TrajectoryStore(db_path=db_path)


@pytest.fixture
def sample_tool_call():
    """Create a sample ToolCall for testing."""
    return ToolCall(
        tool_name="openeo_generate_graph",
        parameters={"description": "NDVI Berlin", "collection": "sentinel-2-l2a"},
        result_summary="Graph generated successfully",
        success=True,
        duration_ms=150,
        timestamp="2024-01-01T00:00:00Z",
    )


class TestStartTrajectory:
    def test_start_trajectory_returns_id(self, store):
        tid = store.start_trajectory("session-1", "Show NDVI for Berlin", "vegetation")
        assert tid is not None
        assert isinstance(tid, str)
        assert len(tid) == 36  # UUID format

    def test_start_trajectory_unique_ids(self, store):
        tid1 = store.start_trajectory("s1", "prompt 1")
        tid2 = store.start_trajectory("s2", "prompt 2")
        assert tid1 != tid2


class TestRecordToolCall:
    def test_record_tool_call_appends(self, store, sample_tool_call):
        tid = store.start_trajectory("s1", "test prompt")
        store.record_tool_call(tid, sample_tool_call)
        # Not persisted yet — still in memory
        assert tid in store._active
        assert len(store._active[tid].tool_calls) == 1
        assert store._active[tid].tool_calls[0].tool_name == "openeo_generate_graph"

    def test_record_multiple_tool_calls(self, store):
        tid = store.start_trajectory("s1", "test prompt")
        for i in range(5):
            store.record_tool_call(tid, ToolCall(
                tool_name=f"tool_{i}",
                parameters={"step": i},
                success=True,
                duration_ms=100,
                timestamp=f"2024-01-01T00:0{i}:00Z",
            ))
        assert len(store._active[tid].tool_calls) == 5

    def test_record_to_nonexistent_trajectory(self, store, sample_tool_call):
        """Recording to a nonexistent trajectory ID is a safe no-op."""
        store.record_tool_call("nonexistent-id", sample_tool_call)
        # Should not raise


class TestFinishTrajectory:
    def test_finish_persists_to_sqlite(self, store, sample_tool_call):
        tid = store.start_trajectory("s1", "test prompt", "vegetation")
        store.record_tool_call(tid, sample_tool_call)
        store.finish_trajectory(tid, success=True)

        # Should be removed from active
        assert tid not in store._active

        # Should be in SQLite
        loaded = store.get_trajectory(tid)
        assert loaded is not None
        assert loaded.user_prompt == "test prompt"
        assert loaded.domain == "vegetation"
        assert loaded.final_success is True
        assert len(loaded.tool_calls) == 1

    def test_finish_sets_total_duration(self, store):
        tid = store.start_trajectory("s1", "test")
        time.sleep(0.05)  # At least 50ms
        store.finish_trajectory(tid, success=True)

        loaded = store.get_trajectory(tid)
        assert loaded.total_duration_ms >= 40  # Allow some tolerance

    def test_finish_records_failure(self, store):
        tid = store.start_trajectory("s1", "test")
        store.finish_trajectory(tid, success=False)

        loaded = store.get_trajectory(tid)
        assert loaded.final_success is False

    def test_finish_nonexistent_trajectory(self, store):
        """Finishing a nonexistent trajectory is a safe no-op."""
        store.finish_trajectory("nonexistent-id", success=True)
        # Should not raise


class TestGetTrajectory:
    def test_get_trajectory_loads_from_sqlite(self, store, sample_tool_call):
        tid = store.start_trajectory("s1", "test prompt", "vegetation")
        store.record_tool_call(tid, sample_tool_call)
        store.finish_trajectory(tid, success=True)

        loaded = store.get_trajectory(tid)
        assert loaded is not None
        assert loaded.id == tid
        assert loaded.session_id == "s1"
        assert loaded.user_prompt == "test prompt"
        assert loaded.domain == "vegetation"
        assert len(loaded.tool_calls) == 1
        tc = loaded.tool_calls[0]
        assert tc.tool_name == "openeo_generate_graph"
        assert tc.parameters["collection"] == "sentinel-2-l2a"
        assert tc.success is True
        assert tc.duration_ms == 150

    def test_get_missing_trajectory_returns_none(self, store):
        result = store.get_trajectory("nonexistent-id")
        assert result is None


class TestListTrajectories:
    def test_list_trajectories_returns_recent(self, store):
        for i in range(5):
            tid = store.start_trajectory(f"s{i}", f"prompt {i}", "vegetation")
            store.finish_trajectory(tid, success=True)

        results = store.list_trajectories()
        assert len(results) == 5

    def test_list_with_domain_filter(self, store):
        tid1 = store.start_trajectory("s1", "ndvi query", "vegetation")
        store.finish_trajectory(tid1, success=True)

        tid2 = store.start_trajectory("s2", "flood query", "water")
        store.finish_trajectory(tid2, success=True)

        veg_results = store.list_trajectories(domain="vegetation")
        assert len(veg_results) == 1
        assert veg_results[0].domain == "vegetation"

        water_results = store.list_trajectories(domain="water")
        assert len(water_results) == 1
        assert water_results[0].domain == "water"

    def test_list_with_limit(self, store):
        for i in range(10):
            tid = store.start_trajectory(f"s{i}", f"prompt {i}")
            store.finish_trajectory(tid, success=True)

        results = store.list_trajectories(limit=3)
        assert len(results) == 3


class TestToolCallOrdering:
    def test_tool_calls_maintain_insertion_order(self, store):
        tid = store.start_trajectory("s1", "test")
        tools = ["recommend", "resolve", "parse", "generate", "create"]
        for name in tools:
            store.record_tool_call(tid, ToolCall(
                tool_name=name, parameters={}, success=True,
                duration_ms=100, timestamp="2024-01-01T00:00:00Z",
            ))
        store.finish_trajectory(tid, success=True)

        loaded = store.get_trajectory(tid)
        loaded_names = [tc.tool_name for tc in loaded.tool_calls]
        assert loaded_names == tools


class TestEmptyTrajectory:
    def test_empty_trajectory_finishes_ok(self, store):
        tid = store.start_trajectory("s1", "test")
        store.finish_trajectory(tid, success=True)

        loaded = store.get_trajectory(tid)
        assert loaded is not None
        assert len(loaded.tool_calls) == 0
        assert loaded.final_success is True


class TestParameterSerialization:
    def test_complex_params_survive_roundtrip(self, store):
        complex_params = {
            "process_graph": {
                "loadcollection1": {
                    "process_id": "load_collection",
                    "arguments": {
                        "id": "sentinel-2-l2a",
                        "spatial_extent": {
                            "west": 13.0, "south": 52.0,
                            "east": 13.5, "north": 52.5,
                        },
                        "temporal_extent": ["2024-01-01", "2024-06-01"],
                        "bands": ["B04", "B08"],
                    },
                }
            },
            "nested_list": [1, 2, [3, 4]],
            "bool_val": True,
            "null_val": None,
        }

        tid = store.start_trajectory("s1", "test")
        store.record_tool_call(tid, ToolCall(
            tool_name="openeo_raw_graph",
            parameters=complex_params,
            success=True,
            duration_ms=200,
            timestamp="2024-01-01T00:00:00Z",
        ))
        store.finish_trajectory(tid, success=True)

        loaded = store.get_trajectory(tid)
        tc = loaded.tool_calls[0]
        assert tc.parameters["process_graph"]["loadcollection1"]["process_id"] == "load_collection"
        assert tc.parameters["nested_list"] == [1, 2, [3, 4]]
        assert tc.parameters["bool_val"] is True
        assert tc.parameters["null_val"] is None


class TestResultSummaryTruncation:
    def test_long_result_truncated_to_500(self, store):
        long_result = "x" * 1000
        tid = store.start_trajectory("s1", "test")
        store.record_tool_call(tid, ToolCall(
            tool_name="test_tool",
            parameters={},
            result_summary=long_result,
            success=True,
            duration_ms=100,
            timestamp="2024-01-01T00:00:00Z",
        ))

        # Check it was truncated in memory
        tc = store._active[tid].tool_calls[0]
        assert len(tc.result_summary) == 500

        store.finish_trajectory(tid, success=True)
        loaded = store.get_trajectory(tid)
        assert len(loaded.tool_calls[0].result_summary) == 500


class TestSchemaInitialization:
    def test_table_created_on_first_use(self, tmp_path):
        db_path = str(tmp_path / "fresh.db")
        assert not os.path.exists(db_path)

        store = TrajectoryStore(db_path=db_path)
        assert os.path.exists(db_path)

        # Verify table exists by inserting and reading back
        tid = store.start_trajectory("s1", "test")
        store.finish_trajectory(tid, success=True)
        loaded = store.get_trajectory(tid)
        assert loaded is not None


class TestMultipleTrajectories:
    def test_concurrent_trajectories_dont_interfere(self, store):
        tid1 = store.start_trajectory("s1", "prompt 1", "vegetation")
        tid2 = store.start_trajectory("s2", "prompt 2", "water")

        store.record_tool_call(tid1, ToolCall(
            tool_name="tool_a", parameters={"for": "traj1"},
            success=True, duration_ms=100, timestamp="2024-01-01T00:00:00Z",
        ))
        store.record_tool_call(tid2, ToolCall(
            tool_name="tool_b", parameters={"for": "traj2"},
            success=True, duration_ms=200, timestamp="2024-01-01T00:00:01Z",
        ))
        store.record_tool_call(tid1, ToolCall(
            tool_name="tool_c", parameters={"for": "traj1"},
            success=True, duration_ms=150, timestamp="2024-01-01T00:00:02Z",
        ))

        store.finish_trajectory(tid1, success=True)
        store.finish_trajectory(tid2, success=False)

        t1 = store.get_trajectory(tid1)
        t2 = store.get_trajectory(tid2)

        assert len(t1.tool_calls) == 2
        assert t1.tool_calls[0].tool_name == "tool_a"
        assert t1.tool_calls[1].tool_name == "tool_c"
        assert t1.final_success is True

        assert len(t2.tool_calls) == 1
        assert t2.tool_calls[0].tool_name == "tool_b"
        assert t2.final_success is False
