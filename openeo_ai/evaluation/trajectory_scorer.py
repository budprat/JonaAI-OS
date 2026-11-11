"""Score agent tool call trajectories against ground truth references.

Five scoring algorithms:
1. Tool-Any-Order: Set intersection — are the right tools used?
2. Tool-In-Order: Longest Common Subsequence — correct ordering?
3. Tool-Exact-Match: Prefix match — exact sequence from the start?
4. Parameter-Accuracy: Key parameter correctness per tool.
5. Outcome-Quality: Did the agent produce a correct outcome? (NEW)

Plus a weighted composite score combining all five.

Usage:
    from openeo_ai.evaluation.trajectory_scorer import score_trajectory
    from openeo_ai.evaluation.trajectory_store import Trajectory

    result = score_trajectory(actual_trajectory, ground_truth)
    print(f"Composite score: {result.composite:.2f}")
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .trajectory_store import ToolCall, Trajectory


# --- Data Models ---

@dataclass
class OutcomeSpec:
    """Expected outcome properties for a ground truth query.

    Defines WHAT the result should look like, not HOW to get there.
    This enables scoring trajectories by outcome quality rather than
    compliance with prescribed tool sequences.
    """

    job_succeeded: bool = True                        # Did the job complete?
    expected_format: str = "GTiff"                    # Output format
    expected_bands: List[str] = field(default_factory=list)  # e.g. ["ndvi"]
    value_range: Optional[Tuple[float, float]] = None  # e.g. (-1.0, 1.0) for NDVI
    has_spatial_extent: bool = True                    # Result covers a spatial area
    has_temporal_extent: bool = True                   # Result covers a time range
    min_tool_calls: int = 3                           # Minimum viable tools used
    max_tool_calls: int = 15                          # Upper bound (penalize bloated trajectories)
    required_capabilities: List[str] = field(         # Abstract capabilities, not specific tools
        default_factory=list                           # e.g. ["location_resolution", "graph_building"]
    )


@dataclass
class GroundTruth:
    """Reference trajectory for a known query."""

    query: str                                            # e.g. "Show me NDVI for Berlin in 2024"
    domain: str = ""                                      # e.g. "vegetation"
    expected_tools: List[str] = field(default_factory=list)  # Ordered tool sequence
    expected_params: Dict[str, Dict[str, Any]] = field(   # tool_name -> key params
        default_factory=dict
    )
    acceptable_alternatives: List[List[str]] = field(     # Alternative valid sequences
        default_factory=list
    )
    expected_outcome: Optional[OutcomeSpec] = None        # Outcome-based scoring spec


@dataclass
class TrajectoryScore:
    """Evaluation result for one trajectory."""

    trajectory_id: str = ""
    ground_truth_query: str = ""
    tool_any_order: float = 0.0       # 0.0 - 1.0
    tool_in_order: float = 0.0        # 0.0 - 1.0
    tool_exact_match: float = 0.0     # 0.0 - 1.0
    param_accuracy: float = 0.0       # 0.0 - 1.0
    outcome_quality: float = 0.0      # 0.0 - 1.0 (NEW: outcome-based score)
    composite: float = 0.0            # 0.0 - 1.0 (weighted average)


# Default composite weights — rebalanced to include outcome scoring
DEFAULT_WEIGHTS: Dict[str, float] = {
    "tool_any_order": 0.10,     # Was 0.15 — demote sequence metrics
    "tool_in_order": 0.15,      # Was 0.25
    "tool_exact_match": 0.10,   # Was 0.20
    "param_accuracy": 0.30,     # Was 0.40
    "outcome_quality": 0.35,    # NEW — outcome is most important
}


# --- Tool → Capability Mapping ---
# Maps concrete tool names to abstract capabilities.
# This lets the agent use ANY tool that satisfies a capability.

TOOL_CAPABILITIES: Dict[str, str] = {
    "recommend_collection": "collection_discovery",
    "openeo_search_collections": "collection_discovery",
    "openeo_list_collections": "collection_discovery",
    "openeo_get_collection_info": "collection_discovery",
    "resolve_location": "location_resolution",
    "openeo_parse_temporal": "temporal_resolution",
    "openeo_generate_graph": "graph_building",
    "openeo_compose_graph": "graph_building",
    "openeo_algorithm": "graph_building",
    "openeo_raw_graph": "graph_building",
    "openeo_create_job": "job_execution",
    "openeo_start_job": "job_execution",
    "openeo_get_results": "result_retrieval",
    "viz_show_map": "visualization",
    "interpret_result": "interpretation",
    "temporal_trend_analysis": "interpretation",
    "result_inspect": "result_retrieval",
    "spectral_index_calculate": "index_calculation",
}

# Key parameters to score per tool (only these are evaluated)
KEY_PARAMETERS: Dict[str, List[str]] = {
    "openeo_generate_graph": ["description", "collection", "cloud_mask"],
    "openeo_compose_graph": ["description", "collection", "cloud_mask", "index"],
    "openeo_algorithm": ["algorithm", "collection"],
    "openeo_raw_graph": ["process_graph"],
    "resolve_location": ["location_name"],
    "openeo_parse_temporal": ["description"],
    "openeo_create_job": ["title"],
    "spectral_index_calculate": ["index_name"],
    "recommend_collection": ["description"],
}

# Numeric tolerance for parameter comparison (10%)
_NUMERIC_TOLERANCE = 0.10


# --- Outcome Scoring ---

def score_outcome_quality(
    trajectory: Trajectory,
    outcome: OutcomeSpec,
) -> float:
    """Score trajectory by outcome quality, not sequence compliance.

    Sub-scores (equally weighted at 0.25 each):
    1. Job success: did trajectory.final_success match outcome.job_succeeded?
    2. Efficiency: tool count within [min_tool_calls, max_tool_calls] range
    3. Capabilities: did the trajectory cover all required_capabilities?
    4. No-error rate: fraction of tool calls that succeeded

    Args:
        trajectory: The recorded trajectory to evaluate.
        outcome: Expected outcome properties.

    Returns:
        Score from 0.0 to 1.0.
    """
    # Sub-score 1: Job success (binary)
    s_job = 1.0 if trajectory.final_success == outcome.job_succeeded else 0.0

    # Sub-score 2: Efficiency — tool count in [min, max] range
    tool_count = len(trajectory.tool_calls)
    if outcome.min_tool_calls <= tool_count <= outcome.max_tool_calls:
        s_efficiency = 1.0
    else:
        range_size = max(outcome.max_tool_calls - outcome.min_tool_calls, 1)
        if tool_count < outcome.min_tool_calls:
            distance = outcome.min_tool_calls - tool_count
        else:
            distance = tool_count - outcome.max_tool_calls
        s_efficiency = max(0.0, 1.0 - distance / range_size)

    # Sub-score 3: Capability coverage
    if outcome.required_capabilities:
        # Map each tool call to its capability, deduplicate
        actual_capabilities = set()
        for tc in trajectory.tool_calls:
            cap = TOOL_CAPABILITIES.get(tc.tool_name)
            if cap:
                actual_capabilities.add(cap)
        required = set(outcome.required_capabilities)
        intersection = actual_capabilities & required
        s_capability = len(intersection) / len(required)
    else:
        s_capability = 1.0  # No capabilities required

    # Sub-score 4: Error-free rate
    if trajectory.tool_calls:
        successful = sum(1 for tc in trajectory.tool_calls if tc.success)
        s_error_free = successful / len(trajectory.tool_calls)
    else:
        s_error_free = 0.0  # No tool calls = no success

    # Equal weighting of all 4 sub-scores
    return 0.25 * s_job + 0.25 * s_efficiency + 0.25 * s_capability + 0.25 * s_error_free


# --- Scoring Algorithms ---

def score_tool_any_order(actual: List[str], expected: List[str]) -> float:
    """Tool-Any-Order Score: set intersection of tool names.

    |intersection(actual, expected)| / |expected|

    Extra tools in actual don't reduce the score.
    Measures: did the agent use the right set of tools?

    Args:
        actual: Tool names from the trajectory (in order).
        expected: Expected tool names from ground truth.

    Returns:
        Score from 0.0 to 1.0.
    """
    if not expected:
        return 1.0 if not actual else 0.0

    actual_set = set(actual)
    expected_set = set(expected)
    intersection = actual_set & expected_set
    return len(intersection) / len(expected_set)


def score_tool_in_order(actual: List[str], expected: List[str]) -> float:
    """Tool-In-Order Score: Longest Common Subsequence.

    LCS(actual, expected) / |expected|

    Measures: did the agent maintain the expected ordering?
    Extra tools between expected ones don't reduce the score.

    Args:
        actual: Tool names from the trajectory (in order).
        expected: Expected tool names from ground truth.

    Returns:
        Score from 0.0 to 1.0.
    """
    if not expected:
        return 1.0  # Nothing expected, any trajectory is valid

    n, m = len(actual), len(expected)
    # DP table for LCS
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if actual[i - 1] == expected[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

    lcs_length = dp[n][m]
    return lcs_length / len(expected)


def score_tool_exact_match(actual: List[str], expected: List[str]) -> float:
    """Tool-Exact-Match Score: common prefix length.

    length_of_common_prefix(actual, expected) / |expected|

    Strictest metric: penalizes any deviation from expected order.

    Args:
        actual: Tool names from the trajectory (in order).
        expected: Expected tool names from ground truth.

    Returns:
        Score from 0.0 to 1.0.
    """
    if not expected:
        return 1.0  # Nothing expected, any trajectory is valid

    prefix_len = 0
    for a, e in zip(actual, expected):
        if a == e:
            prefix_len += 1
        else:
            break

    return prefix_len / len(expected)


def score_parameter_accuracy(
    actual_calls: List[ToolCall],
    expected_params: Dict[str, Dict[str, Any]],
) -> float:
    """Parameter Accuracy Score: key parameter correctness.

    Average parameter match across tools that appear in both actual and expected.

    Matching rules:
    - Strings: case-insensitive containment (actual contains expected substring)
    - Numbers: within 10% tolerance
    - Booleans: exact match
    - Dicts: structural match via sorted JSON comparison

    Only KEY_PARAMETERS per tool are scored (not every argument).

    Args:
        actual_calls: Tool calls from the trajectory.
        expected_params: tool_name -> {param_name: expected_value}.

    Returns:
        Score from 0.0 to 1.0.
    """
    if not expected_params:
        return 1.0

    # Build lookup: tool_name -> first matching ToolCall
    actual_by_tool: Dict[str, ToolCall] = {}
    for tc in actual_calls:
        if tc.tool_name not in actual_by_tool:
            actual_by_tool[tc.tool_name] = tc

    tool_scores: List[float] = []

    for tool_name, params in expected_params.items():
        if not params:
            continue

        actual_tc = actual_by_tool.get(tool_name)
        if actual_tc is None:
            # Tool not called at all -> 0 for this tool
            tool_scores.append(0.0)
            continue

        # Only score key parameters for this tool
        key_params = KEY_PARAMETERS.get(tool_name, list(params.keys()))
        scorable_params = {
            k: v for k, v in params.items() if k in key_params
        }

        if not scorable_params:
            continue

        param_matches = 0
        param_total = len(scorable_params)

        for param_name, expected_val in scorable_params.items():
            actual_val = actual_tc.parameters.get(param_name)
            if _values_match(actual_val, expected_val):
                param_matches += 1

        tool_scores.append(param_matches / param_total if param_total > 0 else 0.0)

    if not tool_scores:
        return 1.0

    return sum(tool_scores) / len(tool_scores)


def _values_match(actual: Any, expected: Any) -> bool:
    """Compare two parameter values with type-appropriate matching.

    - Strings: case-insensitive containment
    - Numbers: within 10% tolerance
    - Booleans: exact match
    - Dicts: sorted JSON string comparison
    - None: actual is None -> False
    """
    if actual is None:
        return expected is None

    if expected is None:
        return actual is None

    # Boolean comparison (must check before numbers since bool is subclass of int)
    if isinstance(expected, bool):
        return actual is expected or actual == expected

    # Numeric comparison with tolerance
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if expected == 0:
            return abs(actual) < _NUMERIC_TOLERANCE
        return abs(actual - expected) / abs(expected) <= _NUMERIC_TOLERANCE

    # String comparison: case-insensitive containment
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.lower() in actual.lower()

    # Dict comparison: structural match via sorted JSON
    if isinstance(expected, dict) and isinstance(actual, dict):
        try:
            return (
                json.dumps(actual, sort_keys=True, default=str)
                == json.dumps(expected, sort_keys=True, default=str)
            )
        except (TypeError, ValueError):
            return actual == expected

    # List comparison
    if isinstance(expected, list) and isinstance(actual, list):
        return actual == expected

    # Fallback: equality
    return actual == expected


# --- Composite Scoring ---

def score_trajectory(
    actual: Trajectory,
    ground_truth: GroundTruth,
    weights: Optional[Dict[str, float]] = None,
) -> TrajectoryScore:
    """Compute all 5 scores + weighted composite for a trajectory.

    If the ground truth has acceptable_alternatives, scores against all
    alternatives and returns the best composite score.

    Backward compatible: if weights don't contain "outcome_quality" key,
    or ground truth has no expected_outcome, outcome scoring is skipped
    and the existing 4-metric composite is used.

    Args:
        actual: The recorded trajectory to evaluate.
        ground_truth: The reference trajectory to score against.
        weights: Optional custom weights (default: DEFAULT_WEIGHTS).

    Returns:
        TrajectoryScore with all 6 fields (0.0 - 1.0).
    """
    w = weights or DEFAULT_WEIGHTS

    actual_tools = [tc.tool_name for tc in actual.tool_calls]

    # Compute outcome score once (independent of tool sequence comparison)
    s_outcome = 0.0
    has_spec = ground_truth.expected_outcome is not None and "outcome_quality" in w
    if has_spec:
        s_outcome = score_outcome_quality(actual, ground_truth.expected_outcome)

    # Score against primary expected sequence
    best_score = _compute_score(
        actual_tools, actual.tool_calls,
        ground_truth.expected_tools, ground_truth.expected_params,
        actual.id, ground_truth.query, w, s_outcome,
        has_outcome_spec=has_spec,
    )

    # Score against acceptable alternatives, keep the best composite
    for alt_tools in ground_truth.acceptable_alternatives:
        alt_score = _compute_score(
            actual_tools, actual.tool_calls,
            alt_tools, ground_truth.expected_params,
            actual.id, ground_truth.query, w, s_outcome,
            has_outcome_spec=has_spec,
        )
        if alt_score.composite > best_score.composite:
            best_score = alt_score

    return best_score


def _compute_score(
    actual_tools: List[str],
    actual_calls: List[ToolCall],
    expected_tools: List[str],
    expected_params: Dict[str, Dict[str, Any]],
    trajectory_id: str,
    query: str,
    weights: Dict[str, float],
    outcome_score: float = 0.0,
    has_outcome_spec: bool = False,
) -> TrajectoryScore:
    """Compute scores for a single expected tool sequence.

    When has_outcome_spec is False, the outcome_quality weight is excluded
    and the remaining weights are re-normalized so they sum to 1.0.
    """
    s_any = score_tool_any_order(actual_tools, expected_tools)
    s_order = score_tool_in_order(actual_tools, expected_tools)
    s_exact = score_tool_exact_match(actual_tools, expected_tools)
    s_param = score_parameter_accuracy(actual_calls, expected_params)

    w_any = weights.get("tool_any_order", 0.10)
    w_order = weights.get("tool_in_order", 0.15)
    w_exact = weights.get("tool_exact_match", 0.10)
    w_param = weights.get("param_accuracy", 0.30)
    w_outcome = weights.get("outcome_quality", 0.0) if has_outcome_spec else 0.0

    # Re-normalize when outcome is excluded so composite still reaches 1.0
    total_w = w_any + w_order + w_exact + w_param + w_outcome
    if total_w > 0:
        norm = 1.0 / total_w
    else:
        norm = 1.0

    composite = (
        w_any * s_any
        + w_order * s_order
        + w_exact * s_exact
        + w_param * s_param
        + w_outcome * outcome_score
    ) * norm

    return TrajectoryScore(
        trajectory_id=trajectory_id,
        ground_truth_query=query,
        tool_any_order=s_any,
        tool_in_order=s_order,
        tool_exact_match=s_exact,
        param_accuracy=s_param,
        outcome_quality=outcome_score,
        composite=composite,
    )


# --- Ground Truth Loading ---

def load_ground_truths(path: str = "") -> List[GroundTruth]:
    """Load ground truth trajectories from JSON file.

    Args:
        path: Path to ground_truth.json. Defaults to the bundled file.

    Returns:
        List of GroundTruth objects.
    """
    if not path:
        import os
        path = os.path.join(
            os.path.dirname(__file__), "ground_truth.json"
        )

    with open(path, "r") as f:
        data = json.load(f)

    results = []
    for entry in data.get("ground_truths", []):
        # Parse expected_outcome if present
        expected_outcome = None
        if "expected_outcome" in entry:
            eo = entry["expected_outcome"]
            expected_outcome = OutcomeSpec(
                job_succeeded=eo.get("job_succeeded", True),
                expected_format=eo.get("expected_format", "GTiff"),
                expected_bands=eo.get("expected_bands", []),
                value_range=tuple(eo["value_range"]) if "value_range" in eo else None,
                has_spatial_extent=eo.get("has_spatial_extent", True),
                has_temporal_extent=eo.get("has_temporal_extent", True),
                min_tool_calls=eo.get("min_tool_calls", 3),
                max_tool_calls=eo.get("max_tool_calls", 15),
                required_capabilities=eo.get("required_capabilities", []),
            )
        results.append(GroundTruth(
            query=entry["query"],
            domain=entry.get("domain", ""),
            expected_tools=entry.get("expected_tools", []),
            expected_params=entry.get("expected_params", {}),
            acceptable_alternatives=entry.get("acceptable_alternatives", []),
            expected_outcome=expected_outcome,
        ))
    return results


def find_matching_ground_truth(
    prompt: str, ground_truths: List[GroundTruth]
) -> Optional[GroundTruth]:
    """Find the best matching ground truth for a user prompt.

    Uses keyword overlap scoring to find the most relevant reference.
    Returns None if no reasonable match is found.
    """
    if not ground_truths:
        return None

    prompt_lower = prompt.lower()
    prompt_words = set(prompt_lower.split())

    best_match = None
    best_overlap = 0

    for gt in ground_truths:
        gt_words = set(gt.query.lower().split())
        overlap = len(prompt_words & gt_words)
        # Require at least 3 keyword matches to avoid false positives
        if overlap > best_overlap and overlap >= 3:
            best_overlap = overlap
            best_match = gt

    return best_match
