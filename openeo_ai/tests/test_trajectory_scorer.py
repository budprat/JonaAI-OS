"""Tests for trajectory scorer — 5 scoring algorithms + composite score."""

import pytest

from openeo_ai.evaluation.trajectory_store import ToolCall, Trajectory
from openeo_ai.evaluation.trajectory_scorer import (
    DEFAULT_WEIGHTS,
    GroundTruth,
    OutcomeSpec,
    TOOL_CAPABILITIES,
    TrajectoryScore,
    find_matching_ground_truth,
    load_ground_truths,
    score_outcome_quality,
    score_parameter_accuracy,
    score_tool_any_order,
    score_tool_exact_match,
    score_tool_in_order,
    score_trajectory,
)


# --- Tool-Any-Order Tests ---

class TestToolAnyOrder:
    def test_perfect_match(self):
        actual = ["recommend", "resolve", "generate", "create", "get_results"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        assert score_tool_any_order(actual, expected) == 1.0

    def test_partial_match(self):
        actual = ["recommend", "resolve", "generate"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        assert score_tool_any_order(actual, expected) == pytest.approx(0.6)

    def test_empty_actual(self):
        assert score_tool_any_order([], ["recommend", "resolve"]) == 0.0

    def test_extra_tools_dont_reduce_score(self):
        actual = ["extra1", "recommend", "resolve", "extra2", "generate", "create", "get_results"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        assert score_tool_any_order(actual, expected) == 1.0

    def test_both_empty(self):
        assert score_tool_any_order([], []) == 1.0

    def test_no_overlap(self):
        actual = ["tool_x", "tool_y"]
        expected = ["recommend", "resolve"]
        assert score_tool_any_order(actual, expected) == 0.0


# --- Tool-In-Order Tests ---

class TestToolInOrder:
    def test_perfect_order(self):
        actual = ["recommend", "resolve", "generate", "create", "get_results"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        assert score_tool_in_order(actual, expected) == 1.0

    def test_reversed_order(self):
        actual = ["get_results", "create", "generate", "resolve", "recommend"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        # Reversed: LCS is 1 (just one element in order)
        assert score_tool_in_order(actual, expected) == pytest.approx(0.2)

    def test_with_extra_tools_between(self):
        actual = ["recommend", "extra1", "resolve", "extra2", "generate", "create", "get_results"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        # All expected tools present in correct order with extras between
        assert score_tool_in_order(actual, expected) == 1.0

    def test_partial_order(self):
        actual = ["recommend", "generate", "resolve", "create"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        # LCS: recommend, generate, create (3) or recommend, resolve, create (3)
        assert score_tool_in_order(actual, expected) == pytest.approx(0.6)

    def test_empty_expected(self):
        assert score_tool_in_order(["a", "b"], []) == 1.0

    def test_empty_actual(self):
        assert score_tool_in_order([], ["a", "b"]) == 0.0


# --- Tool-Exact-Match Tests ---

class TestToolExactMatch:
    def test_perfect_match(self):
        actual = ["recommend", "resolve", "generate", "create", "get_results"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        assert score_tool_exact_match(actual, expected) == 1.0

    def test_diverges_at_step_3(self):
        actual = ["recommend", "resolve", "wrong_tool", "create", "get_results"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        # Common prefix: 2 out of 5
        assert score_tool_exact_match(actual, expected) == pytest.approx(0.4)

    def test_no_match(self):
        actual = ["wrong_start", "resolve", "generate"]
        expected = ["recommend", "resolve", "generate"]
        assert score_tool_exact_match(actual, expected) == 0.0

    def test_empty_actual(self):
        assert score_tool_exact_match([], ["a", "b", "c"]) == 0.0

    def test_empty_expected(self):
        assert score_tool_exact_match(["a", "b"], []) == 1.0

    def test_actual_shorter_but_matching(self):
        actual = ["recommend", "resolve"]
        expected = ["recommend", "resolve", "generate", "create", "get_results"]
        # Prefix of 2 out of 5
        assert score_tool_exact_match(actual, expected) == pytest.approx(0.4)


# --- Parameter Accuracy Tests ---

class TestParameterAccuracy:
    def test_perfect_match(self):
        calls = [
            ToolCall(tool_name="resolve_location",
                     parameters={"location_name": "Berlin"},
                     success=True, duration_ms=100, timestamp="t"),
            ToolCall(tool_name="openeo_generate_graph",
                     parameters={"description": "NDVI analysis", "collection": "sentinel-2-l2a", "cloud_mask": True},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "resolve_location": {"location_name": "Berlin"},
            "openeo_generate_graph": {"description": "NDVI", "collection": "sentinel-2-l2a", "cloud_mask": True},
        }
        assert score_parameter_accuracy(calls, expected) == 1.0

    def test_partial_params_wrong(self):
        calls = [
            ToolCall(tool_name="resolve_location",
                     parameters={"location_name": "Paris"},  # Wrong location
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "resolve_location": {"location_name": "Berlin"},
        }
        # "Berlin" not in "Paris" -> 0/1 params match
        assert score_parameter_accuracy(calls, expected) == 0.0

    def test_case_insensitive_string(self):
        calls = [
            ToolCall(tool_name="openeo_generate_graph",
                     parameters={"description": "Calculate NDVI for vegetation analysis"},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "openeo_generate_graph": {"description": "ndvi"},
        }
        # "ndvi" in "calculate ndvi for vegetation analysis" (case-insensitive)
        assert score_parameter_accuracy(calls, expected) == 1.0

    def test_numeric_tolerance(self):
        calls = [
            ToolCall(tool_name="test_tool",
                     parameters={"threshold": 0.52},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "test_tool": {"threshold": 0.5},
        }
        # 0.52 is 4% deviation from 0.5 — clearly within 10% tolerance
        assert score_parameter_accuracy(calls, expected) == 1.0

    def test_numeric_outside_tolerance(self):
        calls = [
            ToolCall(tool_name="test_tool",
                     parameters={"threshold": 0.8},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "test_tool": {"threshold": 0.5},
        }
        # 0.8 is not within 10% of 0.5
        assert score_parameter_accuracy(calls, expected) == 0.0

    def test_boolean_exact_match(self):
        calls = [
            ToolCall(tool_name="openeo_generate_graph",
                     parameters={"cloud_mask": True},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "openeo_generate_graph": {"cloud_mask": True},
        }
        assert score_parameter_accuracy(calls, expected) == 1.0

    def test_boolean_mismatch(self):
        calls = [
            ToolCall(tool_name="openeo_generate_graph",
                     parameters={"cloud_mask": False},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "openeo_generate_graph": {"cloud_mask": True},
        }
        assert score_parameter_accuracy(calls, expected) == 0.0

    def test_tool_not_called(self):
        """When an expected tool isn't called at all, score is 0 for that tool."""
        calls = [
            ToolCall(tool_name="other_tool",
                     parameters={"x": 1},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        expected = {
            "resolve_location": {"location_name": "Berlin"},
        }
        assert score_parameter_accuracy(calls, expected) == 0.0

    def test_empty_expected_params(self):
        calls = [
            ToolCall(tool_name="test", parameters={"x": 1},
                     success=True, duration_ms=100, timestamp="t"),
        ]
        assert score_parameter_accuracy(calls, {}) == 1.0


# --- Composite Score Tests ---

class TestCompositeScore:
    @pytest.fixture
    def ground_truth(self):
        return GroundTruth(
            query="Show NDVI for Berlin",
            domain="vegetation",
            expected_tools=["recommend", "resolve", "generate", "create", "get_results"],
            expected_params={
                "resolve": {"location_name": "Berlin"},
                "generate": {"description": "NDVI", "cloud_mask": True},
            },
        )

    def test_composite_with_default_weights(self, ground_truth):
        trajectory = Trajectory(
            id="t1",
            session_id="s1",
            user_prompt="Show NDVI for Berlin",
            domain="vegetation",
            tool_calls=[
                ToolCall(tool_name="recommend", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="resolve", parameters={"location_name": "Berlin"}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="generate", parameters={"description": "NDVI analysis", "cloud_mask": True}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="create", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="get_results", parameters={}, success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )

        result = score_trajectory(trajectory, ground_truth)

        assert result.tool_any_order == 1.0
        assert result.tool_in_order == 1.0
        assert result.tool_exact_match == 1.0
        assert result.param_accuracy == 1.0
        assert result.composite == 1.0

    def test_composite_custom_weights(self, ground_truth):
        trajectory = Trajectory(
            id="t1",
            session_id="s1",
            user_prompt="Show NDVI for Berlin",
            domain="vegetation",
            tool_calls=[
                ToolCall(tool_name="recommend", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="resolve", parameters={"location_name": "Berlin"}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="generate", parameters={"description": "NDVI analysis", "cloud_mask": True}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="create", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="get_results", parameters={}, success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )

        custom_weights = {
            "tool_any_order": 0.25,
            "tool_in_order": 0.25,
            "tool_exact_match": 0.25,
            "param_accuracy": 0.25,
        }
        result = score_trajectory(trajectory, ground_truth, weights=custom_weights)
        # All scores are 1.0, so composite is 1.0 regardless of weights
        assert result.composite == 1.0

    def test_composite_imperfect_trajectory(self, ground_truth):
        """Trajectory with missing tools and wrong params should have lower composite."""
        trajectory = Trajectory(
            id="t2",
            session_id="s1",
            user_prompt="Show NDVI for Berlin",
            domain="vegetation",
            tool_calls=[
                ToolCall(tool_name="recommend", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="resolve", parameters={"location_name": "Paris"}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="generate", parameters={"description": "EVI", "cloud_mask": False}, success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )

        result = score_trajectory(trajectory, ground_truth)

        # 3/5 tools present
        assert result.tool_any_order == pytest.approx(0.6)
        # 3/5 in correct order
        assert result.tool_in_order == pytest.approx(0.6)
        # 3/5 prefix match
        assert result.tool_exact_match == pytest.approx(0.6)
        # resolve: wrong location (0/1), generate: wrong desc and cloud_mask (0/2) -> avg = 0/3 -> 0
        assert result.param_accuracy < 0.5
        # Composite should be between 0 and 1
        assert 0.0 < result.composite < 1.0

    def test_score_trajectory_returns_trajectory_score(self, ground_truth):
        trajectory = Trajectory(
            id="t3", session_id="s1", user_prompt="test",
            tool_calls=[], final_success=True,
        )
        result = score_trajectory(trajectory, ground_truth)
        assert isinstance(result, TrajectoryScore)
        assert result.trajectory_id == "t3"
        assert result.ground_truth_query == "Show NDVI for Berlin"


class TestAcceptableAlternatives:
    def test_alternative_sequence_scored(self):
        ground_truth = GroundTruth(
            query="Show NDVI",
            domain="vegetation",
            expected_tools=["recommend", "resolve", "generate_graph", "create", "get_results"],
            expected_params={},
            acceptable_alternatives=[
                ["recommend", "resolve", "compose_graph", "create", "get_results"],
            ],
        )

        # Trajectory matches the alternative, not the primary
        trajectory = Trajectory(
            id="t1", session_id="s1", user_prompt="Show NDVI",
            tool_calls=[
                ToolCall(tool_name="recommend", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="resolve", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="compose_graph", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="create", parameters={}, success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="get_results", parameters={}, success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )

        result = score_trajectory(trajectory, ground_truth)
        # Should match the alternative perfectly
        assert result.tool_any_order == 1.0
        assert result.tool_in_order == 1.0
        assert result.tool_exact_match == 1.0


# --- Ground Truth Loading Tests ---

class TestGroundTruthLoading:
    def test_load_bundled_ground_truths(self):
        ground_truths = load_ground_truths()
        assert len(ground_truths) == 10
        assert ground_truths[0].query == "Show NDVI for Berlin in 2024"
        assert ground_truths[0].domain == "vegetation"
        assert len(ground_truths[0].expected_tools) > 0

    def test_find_matching_ground_truth(self):
        ground_truths = load_ground_truths()
        match = find_matching_ground_truth("Show me NDVI for Berlin area in 2024", ground_truths)
        assert match is not None
        assert "Berlin" in match.query

    def test_find_no_match_for_unrelated_query(self):
        ground_truths = load_ground_truths()
        match = find_matching_ground_truth("What is the weather today?", ground_truths)
        assert match is None


# --- Outcome Quality Integration Tests ---

class TestOutcomeQualityInScoreTrajectory:
    def test_outcome_included_when_ground_truth_has_spec(self):
        """score_trajectory includes outcome when expected_outcome present."""
        gt = GroundTruth(
            query="Show NDVI",
            domain="vegetation",
            expected_tools=["resolve_location", "openeo_generate_graph",
                            "openeo_create_job", "openeo_get_results"],
            expected_params={},
            expected_outcome=OutcomeSpec(
                min_tool_calls=3,
                max_tool_calls=10,
                required_capabilities=["location_resolution", "graph_building",
                                       "job_execution", "result_retrieval"],
            ),
        )
        traj = Trajectory(
            id="t1", session_id="s1", user_prompt="Show NDVI",
            domain="vegetation",
            tool_calls=[
                ToolCall(tool_name="resolve_location", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="openeo_generate_graph", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="openeo_create_job", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="openeo_get_results", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )
        result = score_trajectory(traj, gt)
        assert result.outcome_quality > 0.0
        assert result.outcome_quality == pytest.approx(1.0)

    def test_backward_compat_no_outcome(self):
        """GroundTruth without expected_outcome -> outcome_quality stays 0.0."""
        gt = GroundTruth(
            query="Show NDVI",
            domain="vegetation",
            expected_tools=["recommend", "resolve"],
            expected_params={},
            # No expected_outcome
        )
        traj = Trajectory(
            id="t1", session_id="s1", user_prompt="Show NDVI",
            tool_calls=[
                ToolCall(tool_name="recommend", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
                ToolCall(tool_name="resolve", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )
        result = score_trajectory(traj, gt)
        assert result.outcome_quality == 0.0

    def test_tool_capabilities_mapping_complete(self):
        """All tools in TOOL_CAPABILITIES have valid non-empty capabilities."""
        for tool_name, capability in TOOL_CAPABILITIES.items():
            assert isinstance(capability, str)
            assert len(capability) > 0

    def test_outcome_spec_none_means_zero(self):
        """None expected_outcome -> outcome score stays 0.0."""
        gt = GroundTruth(
            query="Test",
            expected_tools=["tool_a"],
            expected_outcome=None,
        )
        traj = Trajectory(
            id="t1", session_id="s1", user_prompt="Test",
            tool_calls=[
                ToolCall(tool_name="tool_a", parameters={},
                         success=True, duration_ms=100, timestamp="t"),
            ],
            final_success=True,
        )
        result = score_trajectory(traj, gt)
        assert result.outcome_quality == 0.0
