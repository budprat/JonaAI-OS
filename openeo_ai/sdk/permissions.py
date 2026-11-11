# ABOUTME: Permission callbacks controlling tool access and validation.
# Blocks dangerous operations, validates spatial extent sizes,
# enforces rate limits, and detects suspicious input patterns.

"""
Permission callbacks for OpenEO AI tools.

Defines which tools require user confirmation and validates
tool arguments before execution. Also provides rate-limit
configuration and input sanitisation patterns.
"""

import re
import math
from typing import Any, Dict, List, Optional, Set, Union

# Read-only tools that don't require confirmation
READ_ONLY_TOOLS = {
    "openeo_list_collections",
    "openeo_get_collection_info",
    "openeo_validate_graph",
    "openeo_generate_graph",
    "openeo_get_job_status",
    "openeo_list_jobs",
    "openeo_resolve_location",
    "openeo_parse_temporal",
    "openeo_estimate_extent",
    "openeo_quality_metrics",
    "openeo_validate_geospatial",
    "viz_show_map",
    "viz_show_time_series",
    "viz_show_ndvi_map",
    "viz_show_dashboard",
    "viz_compare_images",
    "saved_jobs_list",
    "saved_jobs_load",
    "result_inspect",
    "result_histogram",
    "result_zonal_stats",
    "result_extract_point",
    "result_load",
    "result_extract_temporal_layers",
    "raster_statistics",
    "spectral_index_search",
    "temporal_anomaly_detect",
    "temporal_trend_analysis",
    "temporal_phenology",
    "compare_collections",
    "result_difference",
    "result_correlation",
    "recommend_collection",
    "explain_collection_bands",
    # Graph building (read-only: builds graph JSON, doesn't execute)
    "openeo_compose_graph",
    "openeo_algorithm",
    "openeo_raw_graph",
    "openeo_apply_kernel",
    "openeo_aggregate_spatial",
    "graph_add_band_filter",
    "graph_add_logic",
    "graph_add_mask",
    "graph_add_math",
    "graph_add_merge",
    "graph_add_threshold",
    "graph_validate_full",
    # Discovery and documentation
    "openeo_list_processes",
    "openeo_get_process_spec",
    "fusion_list_presets",
    # Strategy and validation (read-only analysis)
    "analysis_strategy_planner",
    "extract_analysis_goal",
    "validate_goal_completeness",
    "validate_pre_execution",
    "assess_result_quality",
    "interpret_result",
    "generate_validation_checklist",
    "export_workflow_doc",
    "generate_report",
    "threshold_classify",
}

# Tools that modify state but are generally safe
SAFE_MODIFY_TOOLS = {
    "openeo_create_job",
    "openeo_start_job",
    "openeo_get_results",
    "geoai_segment",
    "geoai_detect_change",
    "geoai_estimate_canopy_height",
    "saved_jobs_delete",
    "spectral_index_calculate",
    "export_styled_geotiff",
    "export_report",
    "export_csv",
    "execute_workflow",
    "analyze_result_image",
    "fusion_multi_sensor",
}

# Tools that are blocked by default
BLOCKED_TOOLS = {
    "openeo_delete_all_jobs",
    "openeo_delete_all_graphs",
}

# Generic Claude Agent SDK built-in tools a domain EO agent must NEVER use.
# Two observed failure modes (2026-07-08): (1) with Bash/Write it FABRICATED a
# collections/indices catalog instead of calling openeo_* tools; (2) with
# Glob/Grep/Read it WANDERED the source tree ("Glob **/*.py", "Read
# openeo_tools.py") instead of running the requested NDVI analysis, finishing
# 190s later having computed nothing. The domain agent's only real tools are the
# in-process openeo_* MCP tools; it has no legitimate use for a shell, file I/O,
# subagents, web, or filesystem search. Passed to
# ClaudeAgentOptions.disallowed_tools (removes them from the toolset) AND denied
# in the pre_tool_use hook as defense-in-depth. ToolSearch and the MCP-resource
# tools are intentionally absent — they load the deferred openeo_* tools. Blocking
# built-in Task does not affect JonaAI's own specialist orchestration (that uses
# separate ClaudeSDKClients, not the SDK Task tool).
GENERIC_BUILTIN_TOOLS_DENY = frozenset({
    "Bash", "BashOutput", "KillBash", "KillShell",
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "Read", "Glob", "Grep", "LS",
    "Task", "WebSearch", "WebFetch",
})

# Maximum allowed spatial extent (degrees)
MAX_SPATIAL_EXTENT = 10.0  # 10 degrees in any direction

# --- Rate Limiting Configuration ---
# Per-query limits: {tool_name_suffix: max_calls_per_query}
RATE_LIMITS: Dict[str, int] = {
    "openeo_create_job": 8,       # Max 8 job creations per query (complex multi-index queries need 6+)
    "openeo_start_job": 8,        # Max 8 job starts per query
    "openeo_delete_all_jobs": 0,  # Redundant with BLOCKED_TOOLS, belt-and-suspenders
    "geoai_segment": 3,           # Expensive GPU ops
    "geoai_detect_change": 3,
    "geoai_estimate_canopy_height": 3,
}
RATE_LIMIT_DEFAULT = 50  # Max total tool calls per query (any tool)

# --- Suspicious Input Patterns ---
# Regex patterns that should never appear in tool inputs.
# Matched against all string values in the tool input dict (recursive).
SUSPICIOUS_PATTERNS: List[re.Pattern] = [
    re.compile(r"\.\./"),                          # Path traversal
    re.compile(r"\.\\.\\"),                        # Windows path traversal
    re.compile(r"^/(?:etc|proc|sys|dev|root)/"),   # Absolute system paths
    re.compile(r"[;|&`$]"),                        # Shell metacharacters
    re.compile(r"<script", re.IGNORECASE),         # XSS attempt
    re.compile(r"\bexec\s*\("),                    # Code injection
    re.compile(r"\beval\s*\("),                    # Code injection
    re.compile(r"__import__"),                     # Python import injection
]


# Fields that legitimately contain free text, code, regex, or markdown.
# These are exempt from the shell-metacharacter pattern ([;|&`$]) but
# still checked against all other suspicious patterns (path traversal,
# XSS, code injection).
_FREE_TEXT_FIELDS = frozenset({
    "content", "text", "description", "report", "markdown", "body",
    "pattern", "query", "summary", "title", "message",
    # Fields in export_workflow_doc / generate_report that carry markdown/rich text
    "objective", "results_summary", "quality_info", "findings",
    "recommendations", "notes", "interpretation", "assessment",
    # Fields in export_csv that carry formatted data
    "data", "header", "value", "label", "output",
})

# Index of the shell-metacharacter pattern in SUSPICIOUS_PATTERNS
_SHELL_META_PATTERN = re.compile(r"[;|&`$]")
# Shell metacharacters only mean something in values used as paths or identifiers. Tools
# don't pass arguments to a shell, and ordinary text ("R&D", "$5", "a; b") is full of them.
_IDENTIFIER_FIELD_RE = re.compile(r"(^|_)(path|file|filename|dir|directory|url|uri|id|command|cmd)$", re.IGNORECASE)


def check_suspicious_input(args: Dict[str, Any]) -> str | None:
    """Recursively scan tool input for suspicious patterns.

    Free-text fields (content, pattern, query, etc.) are exempt from the
    shell-metacharacter check but still scanned for path traversal, XSS,
    and code injection patterns.  The exemption propagates to all children
    of a free-text field (e.g., nested dicts/lists inside results_summary).

    Returns the matched pattern description if found, None if clean.
    """
    def _scan(value: Any, key: str = "", parent_is_free: bool = False) -> str | None:
        if isinstance(value, str):
            is_free_text = parent_is_free or key.lower() in _FREE_TEXT_FIELDS
            for pattern in SUSPICIOUS_PATTERNS:
                # Skip shell-metacharacter check for free-text fields
                if pattern.pattern == _SHELL_META_PATTERN.pattern and (
                    is_free_text or not _IDENTIFIER_FIELD_RE.search(key)
                ):
                    continue
                if pattern.search(value):
                    return f"Suspicious pattern matched: {pattern.pattern!r}"
        elif isinstance(value, dict):
            for k, v in value.items():
                child_free = parent_is_free or k.lower() in _FREE_TEXT_FIELDS
                hit = _scan(v, key=k, parent_is_free=child_free)
                if hit:
                    return hit
        elif isinstance(value, (list, tuple)):
            for v in value:
                hit = _scan(v, key=key, parent_is_free=parent_is_free)
                if hit:
                    return hit
        return None

    return _scan(args)


def openeo_permission_callback(
    tool_name: str,
    args: Dict[str, Any]
) -> Union[bool, Dict[str, Any]]:
    """
    Permission callback for OpenEO AI tools.

    Determines whether a tool call should be:
    - Allowed immediately (return True)
    - Blocked (return False)
    - Require confirmation (return dict with confirmation request)

    Args:
        tool_name: Name of the tool being called
        args: Arguments passed to the tool

    Returns:
        True if allowed, False if blocked, or dict for confirmation
    """
    # Block dangerous operations
    if tool_name in BLOCKED_TOOLS:
        return False

    # Allow read-only tools
    if tool_name in READ_ONLY_TOOLS:
        return True

    # For modification tools, check arguments
    if tool_name in SAFE_MODIFY_TOOLS:
        # Check for large spatial extent in process graphs
        validation_result = _validate_extent(args)
        if validation_result is not True:
            return validation_result

        return True

    # Unknown tools - block by default
    return False


# Structural faults that make a graph invalid under any OpenEO backend. Deliberately
# NOT the spec-dependent ones: GraphValidator reports UnknownProcess/MissingParameter
# from a registry built out of openeo_processes_dask, whose loader logs a warning and
# returns {} if that import ever fails. Enforcing those would turn one optional
# dependency into "no job can be created". These five need no registry at all, so the
# gate is written standalone rather than by filtering the validator's codes — it cannot
# be emptied. test_graph_structure_gate.py pins it against GraphValidator so the two
# cannot drift.
def _validate_graph_structure(args: Dict[str, Any]) -> Optional[str]:
    """Return a refusal reason for a structurally impossible graph, else None.

    Silent when there is no graph to check, so it is safe for every job-start tool.
    """
    from .method_gate import parse_graph

    raw = args.get("process_graph")
    if raw is None:
        return None
    nodes = parse_graph(raw)
    if not isinstance(nodes, dict) or not nodes:
        return "The process graph is empty. Build the graph before submitting it."

    deps: Dict[str, Set[str]] = {}
    for node_id, node in nodes.items():
        if not isinstance(node, dict):
            return f"Node '{node_id}' is not an object."
        if not node.get("process_id"):
            return f"Node '{node_id}' has no process_id."
        found: Set[str] = set()
        _collect_node_refs(node.get("arguments", {}), found)
        missing = sorted(ref for ref in found if ref not in nodes)
        if missing:
            return (
                f"Node '{node_id}' refers to {', '.join(repr(m) for m in missing)}, "
                "which is not in the graph."
            )
        deps[node_id] = found & set(nodes)

    # Kahn: anything left after the sweep sits on a cycle.
    in_degree = {nid: len(d) for nid, d in deps.items()}
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    seen = 0
    while queue:
        current = queue.pop()
        seen += 1
        for nid, d in deps.items():
            if current in d:
                in_degree[nid] -= 1
                if in_degree[nid] == 0:
                    queue.append(nid)
    if seen != len(nodes):
        return "The process graph contains a cycle, so it can never run."
    return None


def _collect_node_refs(value: Any, found: "Set[str]") -> None:
    """Gather every from_node reference nested anywhere inside an argument value."""
    if isinstance(value, dict):
        ref = value.get("from_node")
        if isinstance(ref, str):
            found.add(ref)
        for nested in value.values():
            _collect_node_refs(nested, found)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _collect_node_refs(nested, found)


_BBOX_KEYS = ("west", "south", "east", "north")


def _bbox_is_malformed(extent: Any) -> bool:
    """True when an extent means to be a bbox but its numbers cannot be trusted.

    extent_to_lonlat returns None for two very different reasons: the numbers are
    unusable, or the CRS cannot be resolved. _validate_extent used to skip both, so the
    same global area that is blocked with real numbers sailed through with north=inf,
    with north missing, or with west="abc" — the size limit simply never ran.

    Only the bad-numbers case is reported here. An extent carrying none of the bbox keys
    is a different shape (a GeoJSON geometry, say) and is left alone, and an unresolvable
    CRS stays a skip: failing closed there would turn a missing pyproj into "no request
    with a CRS can run", the same outage shape rejected for the process-spec registry.
    """
    if not isinstance(extent, dict):
        return False
    if not any(key in extent for key in _BBOX_KEYS):
        return False
    for key in _BBOX_KEYS:
        if key not in extent:
            return True
        try:
            if not math.isfinite(float(extent[key])):
                return True
        except (TypeError, ValueError):
            return True
    return False


def _lon_span_degrees(west: float, east: float) -> float:
    """Longitude span in degrees, correct across the antimeridian.

    `extent_to_lonlat` returns bounds as given and does not normalise the dateline,
    so a plain `abs(east - west)` reads a 20-degree box spanning 170E..170W as 340
    degrees and would hard-block a legitimate request. When east < west the extent
    crosses the antimeridian, and the true span is (east + 360) - west. Latitude has
    no wraparound, so height stays a simple difference.
    """
    span = east - west
    if span < 0:
        span += 360.0
    return abs(span)


def _validate_extent(args: Dict[str, Any]) -> Union[bool, Dict[str, Any]]:
    """
    Validate spatial extent in tool arguments.

    Args:
        args: Tool arguments

    Returns:
        True if valid, or dict with warning/confirmation request
    """
    from openeo_app.core.extent import extent_to_lonlat

    from .method_gate import parse_graph

    # Every place a caller can put an extent, not only a process graph. The risk is
    # the same whichever argument carries it: one oversized load against a 1200 MB
    # MemoryMax. Previously only the graph form was inspected, so the tools that pass
    # a top-level bbox were unguarded — and adding them to a tool set would NOT have
    # helped, because this function never looked at their arguments.
    candidates = []

    process_graph = parse_graph(args.get("process_graph", {}))
    for node_id, node in process_graph.items():
        if not isinstance(node, dict):
            continue
        if node.get("process_id") != "load_collection":
            continue
        spatial_extent = node.get("arguments", {}).get("spatial_extent", {})
        if spatial_extent:
            candidates.append(spatial_extent)

    # openeo_aggregate_spatial / openeo_apply_kernel: args["spatial_extent"]
    top_level = args.get("spatial_extent")
    if isinstance(top_level, dict) and top_level:
        candidates.append(top_level)

    # agentic_decision_query: args["aoi"] = [west, south, east, north] in WGS84
    aoi = args.get("aoi")
    if isinstance(aoi, (list, tuple)) and len(aoi) == 4:
        try:
            candidates.append(
                {"west": float(aoi[0]), "south": float(aoi[1]),
                 "east": float(aoi[2]), "north": float(aoi[3])}
            )
        except (TypeError, ValueError):
            pass

    for spatial_extent in candidates:
        if _bbox_is_malformed(spatial_extent):
            return {
                "type": "invalid_extent",
                "message": (
                    "The spatial extent is not a usable bounding box: west, south, east "
                    "and north must all be present and finite numbers. Fix the extent "
                    "and retry; the area limit cannot be applied to it as written."
                ),
            }
        # Calculate extent size in degrees, whatever CRS the extent is written in
        lonlat = extent_to_lonlat(spatial_extent)
        if lonlat is None:
            continue
        width = _lon_span_degrees(lonlat["west"], lonlat["east"])
        height = abs(lonlat["north"] - lonlat["south"])

        # Check if extent is extremely large (likely error) — MUST be checked first
        if width > 50 or height > 50:
            return False  # Block global queries

        # Check if extent is very large (warn but allow with confirmation).
        # C22: contestable — the model asks the user in chat and, only after the
        # user explicitly agrees, retries the same call with confirm_large_extent.
        if width > MAX_SPATIAL_EXTENT or height > MAX_SPATIAL_EXTENT:
            if args.get("confirm_large_extent") is True:
                continue
            return {
                "type": "confirmation_required",
                "message": (
                    f"Large spatial extent detected ({width:.1f}° x {height:.1f}°) — "
                    f"this may be slow or time out. Either narrow the AOI, or ask the "
                    f"user to confirm and, only after they explicitly agree in chat, "
                    f"retry this exact call with confirm_large_extent=true."
                ),
                "severity": "warning"
            }

    return True
