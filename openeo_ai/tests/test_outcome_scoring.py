"""Tests for outcome-based scoring — Phase 1 of outcome evaluation system."""

import json
import os

import pytest

from openeo_ai.evaluation.trajectory_store import ToolCall, Trajectory
from openeo_ai.evaluation.trajectory_scorer import (
    DEFAULT_WEIGHTS,
    GroundTruth,
    OutcomeSpec,
    TOOL_CAPABILITIES,
    TrajectoryScore,
    load_ground_truths,
    score_outcome_quality,
    score_trajectory,
)


# --- Helper to build trajectories ---

def _make_trajectory(
    tool_names: list,
    all_success: bool = True,
    final_success: bool = True,
    fail_indices: list = None,
) -> Trajectory:
    """Build a Trajectory with the given tool names."""
    fail_set = set(fail_indices or [])
    return Trajectory(
        id="test-traj",
        session_id="test-session",
        user_prompt="test prompt",
        domain="vegetation",
        tool_calls=[
            ToolCall(
                tool_name=name,
                parameters={},
                success=(i not in fail_set) if not all_success or fail_set else True,
                duration_ms=100,
                timestamp="2024-01-01T00:00:00",
            )
            for i, name in enumerate(tool_names)
        ],
        final_success=final_success,
    )


# --- Outcome Scoring Tests ---

class TestOutcomeScoring:
    def test_outcome_perfect_score(self):
        """All sub-scores 1.0 -> outcome 1.0."""
        traj = _make_trajectory(
            ["resolve_location", "openeo_compose_graph",
             "openeo_create_job", "openeo_start_job", "openeo_get_results"],
            final_success=True,
        )
        spec = OutcomeSpec(
            job_succeeded=True,
            min_tool_calls=4,
            max_tool_calls=12,
            required_capabilities=[
                "location_resolution", "graph_building",
                "job_execution", "result_retrieval",
            ],
        )
        score = score_outcome_quality(traj, spec)
        assert score == pytest.approx(1.0)

    def test_outcome_job_failed(self):
        """Failed trajectory -> job_success sub-score 0.0."""
        traj = _make_trajectory(
            ["resolve_location", "openeo_compose_graph", "openeo_create_job"],
            final_success=False,
        )
        spec = OutcomeSpec(
            job_succeeded=True,
            min_tool_calls=2,
            max_tool_calls=10,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # job sub-score = 0.0 (0.25 weight), others contribute
        assert score < 1.0
        # Specifically: job=0, efficiency=1.0, capability=1.0 (no reqs), error_free=1.0
        # = 0 + 0.25 + 0.25 + 0.25 = 0.75
        assert score == pytest.approx(0.75)

    def test_outcome_expected_failure(self):
        """When expected failure matches actual failure -> job_success = 1.0."""
        traj = _make_trajectory(
            ["resolve_location", "openeo_compose_graph"],
            final_success=False,
        )
        spec = OutcomeSpec(
            job_succeeded=False,  # We EXPECT failure
            min_tool_calls=1,
            max_tool_calls=5,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # job=1.0, efficiency=1.0, capability=1.0, error_free=1.0
        assert score == pytest.approx(1.0)

    def test_outcome_efficiency_in_range(self):
        """Tool count in [min, max] -> efficiency 1.0."""
        traj = _make_trajectory(
            ["t1", "t2", "t3", "t4", "t5"],
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=3,
            max_tool_calls=8,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # job=1.0, efficiency=1.0, capability=1.0, error_free=1.0
        assert score == pytest.approx(1.0)

    def test_outcome_efficiency_over_max(self):
        """Too many tools -> efficiency < 1.0."""
        # 10 tools when max is 5
        traj = _make_trajectory(
            [f"t{i}" for i in range(10)],
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=3,
            max_tool_calls=5,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # efficiency: distance=5, range=2, penalty = 1 - 5/2 = -1.5 -> clamped to 0.0
        # job=1, efficiency=0, capability=1, error_free=1 = 0.75
        assert score < 1.0

    def test_outcome_efficiency_under_min(self):
        """Too few tools -> efficiency < 1.0."""
        traj = _make_trajectory(
            ["t1"],
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=4,
            max_tool_calls=12,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # distance=3, range=8, efficiency = 1 - 3/8 = 0.625
        assert score < 1.0

    def test_outcome_capability_full_coverage(self):
        """All capabilities present -> capability score 1.0."""
        traj = _make_trajectory(
            ["resolve_location", "openeo_generate_graph",
             "openeo_create_job", "openeo_get_results"],
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=2,
            max_tool_calls=10,
            required_capabilities=[
                "location_resolution", "graph_building",
                "job_execution", "result_retrieval",
            ],
        )
        score = score_outcome_quality(traj, spec)
        assert score == pytest.approx(1.0)

    def test_outcome_capability_partial(self):
        """2/4 capabilities -> capability score 0.5."""
        traj = _make_trajectory(
            ["resolve_location", "openeo_get_results"],
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=1,
            max_tool_calls=10,
            required_capabilities=[
                "location_resolution", "graph_building",
                "job_execution", "result_retrieval",
            ],
        )
        score = score_outcome_quality(traj, spec)
        # job=1, efficiency=1, capability=0.5, error_free=1 = 0.875
        assert score == pytest.approx(0.875)

    def test_outcome_capability_via_different_tools(self):
        """compose_graph satisfies graph_building capability."""
        traj = _make_trajectory(
            ["openeo_compose_graph"],  # Not generate_graph
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=1,
            max_tool_calls=5,
            required_capabilities=["graph_building"],
        )
        score = score_outcome_quality(traj, spec)
        assert score == pytest.approx(1.0)

    def test_outcome_error_free_rate(self):
        """4/5 successful -> error_free rate 0.8."""
        traj = _make_trajectory(
            ["t1", "t2", "t3", "t4", "t5"],
            all_success=False,
            fail_indices=[2],  # t3 fails
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=3,
            max_tool_calls=8,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # job=1, efficiency=1, capability=1, error_free=4/5=0.8
        # = 0.25 + 0.25 + 0.25 + 0.2 = 0.95
        assert score == pytest.approx(0.95)

    def test_outcome_all_errors(self):
        """0/5 successful -> error_free rate 0.0."""
        traj = _make_trajectory(
            ["t1", "t2", "t3", "t4", "t5"],
            all_success=False,
            fail_indices=[0, 1, 2, 3, 4],
            final_success=True,
        )
        spec = OutcomeSpec(
            min_tool_calls=3,
            max_tool_calls=8,
            required_capabilities=[],
        )
        score = score_outcome_quality(traj, spec)
        # job=1, efficiency=1, capability=1, error_free=0
        # = 0.25 + 0.25 + 0.25 + 0.0 = 0.75
        assert score == pytest.approx(0.75)


class TestCompositeWithOutcome:
    def test_composite_includes_outcome(self):
        """New default weights include outcome in composite."""
        gt = GroundTruth(
            query="Show NDVI for Berlin",
            domain="vegetation",
            expected_tools=["resolve_location", "openeo_generate_graph",
                            "openeo_create_job", "openeo_start_job",
                            "openeo_get_results"],
            expected_params={},
            expected_outcome=OutcomeSpec(
                min_tool_calls=4,
                max_tool_calls=12,
                required_capabilities=[
                    "location_resolution", "graph_building",
                    "job_execution", "result_retrieval",
                ],
            ),
        )
        traj = _make_trajectory(
            ["resolve_location", "openeo_generate_graph",
             "openeo_create_job", "openeo_start_job", "openeo_get_results"],
            final_success=True,
        )
        result = score_trajectory(traj, gt)
        # With default weights including outcome_quality=0.35
        assert result.outcome_quality == pytest.approx(1.0)
        assert result.composite == pytest.approx(1.0)

    def test_composite_backward_compat(self):
        """Old weights (no outcome key) skip outcome scoring."""
        gt = GroundTruth(
            query="Show NDVI for Berlin",
            domain="vegetation",
            expected_tools=["recommend", "resolve", "generate"],
            expected_params={},
            expected_outcome=OutcomeSpec(
                required_capabilities=["location_resolution"],
            ),
        )
        traj = _make_trajectory(
            ["recommend", "resolve", "generate"],
            final_success=True,
        )
        old_weights = {
            "tool_any_order": 0.15,
            "tool_in_order": 0.25,
            "tool_exact_match": 0.20,
            "param_accuracy": 0.40,
            # No "outcome_quality" key
        }
        result = score_trajectory(traj, gt, weights=old_weights)
        # outcome_quality should be 0.0 since weights don't include it
        assert result.outcome_quality == 0.0
        # Composite should work with old weights only
        assert result.composite == pytest.approx(1.0)


class TestOutcomeSpecFromGroundTruth:
    def test_loading_with_expected_outcome(self):
        """Loading from JSON with expected_outcome parses correctly."""
        ground_truths = load_ground_truths()
        assert len(ground_truths) == 10

        # First entry should have expected_outcome
        gt = ground_truths[0]
        assert gt.expected_outcome is not None
        assert gt.expected_outcome.job_succeeded is True
        assert gt.expected_outcome.expected_bands == ["ndvi"]
        assert gt.expected_outcome.value_range == (-1.0, 1.0)
        assert gt.expected_outcome.min_tool_calls == 4
        assert gt.expected_outcome.max_tool_calls == 12
        assert "location_resolution" in gt.expected_outcome.required_capabilities
        assert "graph_building" in gt.expected_outcome.required_capabilities

    def test_discovery_entry_no_temporal(self):
        """Discovery entry has no temporal extent requirement."""
        ground_truths = load_ground_truths()
        # Entry 7 (index 6) is the discovery query
        gt = ground_truths[6]
        assert gt.domain == "discovery"
        assert gt.expected_outcome is not None
        assert gt.expected_outcome.has_temporal_extent is False
        assert gt.expected_outcome.min_tool_calls == 2
        assert gt.expected_outcome.max_tool_calls == 6
        assert "collection_discovery" in gt.expected_outcome.required_capabilities

    def test_fire_entry_has_temporal_resolution(self):
        """Fire/burn severity entry requires temporal_resolution capability."""
        ground_truths = load_ground_truths()
        gt = ground_truths[2]
        assert gt.domain == "fire"
        assert gt.expected_outcome is not None
        assert "temporal_resolution" in gt.expected_outcome.required_capabilities
        assert gt.expected_outcome.value_range == (-2.0, 2.0)


class TestToolCapabilities:
    def test_all_capabilities_have_valid_values(self):
        """All tools in TOOL_CAPABILITIES have non-empty capability strings."""
        for tool, cap in TOOL_CAPABILITIES.items():
            assert isinstance(cap, str) and len(cap) > 0, (
                f"Tool '{tool}' has invalid capability: {cap!r}"
            )

    def test_graph_building_tools_all_map_to_same_capability(self):
        """All graph builders map to 'graph_building'."""
        graph_tools = [
            "openeo_generate_graph", "openeo_compose_graph",
            "openeo_algorithm", "openeo_raw_graph",
        ]
        for tool in graph_tools:
            assert TOOL_CAPABILITIES[tool] == "graph_building"

    def test_discovery_tools_map_to_collection_discovery(self):
        """Collection discovery tools all map correctly."""
        discovery_tools = [
            "recommend_collection", "openeo_search_collections",
            "openeo_list_collections", "openeo_get_collection_info",
        ]
        for tool in discovery_tools:
            assert TOOL_CAPABILITIES[tool] == "collection_discovery"
