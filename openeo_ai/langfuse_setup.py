# ABOUTME: Langfuse observability initialization for JonaAI (Phase 16).
# Wires OpenTelemetry tracing from Claude Agent SDK → LangSmith → Langfuse.

"""
Langfuse observability setup.

JonaAI uses the Claude Agent SDK (via `claude-agent-sdk` Python package) as the
LLM runtime. Langfuse captures traces via OpenTelemetry: the langsmith package
provides the Claude Agent SDK instrumentation, which emits OTel spans; those
spans are exported to Langfuse's OTLP endpoint at $LANGFUSE_HOST/api/public/otel.

Initialization order (important):
    1. Set LANGSMITH_OTEL_ENABLED / LANGSMITH_OTEL_ONLY / LANGSMITH_TRACING env
       BEFORE importing claude_agent_sdk (those env vars are read at import
       time by langsmith's OTel bridge)
    2. Import langfuse + call get_client() to initialize the Langfuse SDK
    3. Call configure_claude_agent_sdk() to attach the instrumentation

Call `init_langfuse()` once at FastAPI startup. It's idempotent and safe to
call multiple times. Failures are logged but do not crash the server — if
Langfuse credentials are missing or the cloud endpoint is unreachable, the
app continues without tracing.

Per-request user/session attribution is done in web_interface.py using
`propagate_attributes(user_id=..., session_id=...)` inside the WebSocket
handler's _run_query function.

FAIL-OPEN INVARIANT (do not break this):
    Every public helper in this module MUST be safe to call from the user
    query path even when Langfuse is misconfigured, offline, or buggy.
    Telemetry failures must never turn into user-facing errors.

    Concretely this means:
      - `get_langfuse_client()`  → returns None on failure
      - `current_trace_id()`     → returns None on failure
      - `set_root_trace_name()`  → internal try/except, logs debug
      - `annotate_tool_span()`   → internal try/except, logs debug
      - `emit_score()`           → returns bool (never raises)
      - `mask_tool_args()`       → returns error dict on scrub failure

    Audited 2026-04-11 across web_interface, claude_sdk_bridge,
    earth_agent, evals, observability API, and tool handlers — every call
    site either uses a helper that self-protects OR wraps the call in its
    own try/except. When adding a new telemetry call site, preserve this
    invariant: either wrap the call or use a self-protecting helper.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Module-level flag to ensure we only run configure_claude_agent_sdk once
_initialized = False


def init_langfuse() -> bool:
    """Initialize Langfuse + Claude Agent SDK OTel instrumentation.

    Returns:
        True if initialization succeeded and tracing is active.
        False if credentials missing or initialization failed — server
        continues to work without tracing in that case.
    """
    global _initialized
    if _initialized:
        return True

    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public_key or not secret_key:
        # Tracing is off by choice here, so its per-call auth warnings are only noise.
        logging.getLogger("langfuse").setLevel(logging.ERROR)

    if not public_key or not secret_key:
        logger.info(
            "[langfuse] LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set — "
            "observability disabled. Set them in .env to enable tracing."
        )
        return False

    # Langfuse v4 reads the host from LANGFUSE_BASE_URL, but the langsmith
    # Claude Agent SDK docs still reference LANGFUSE_HOST. Accept both so the
    # integration works regardless of which name the user set.
    host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL")
    if host:
        # Export both so langfuse SDK and langsmith bridge both see it
        os.environ["LANGFUSE_HOST"] = host
        os.environ["LANGFUSE_BASE_URL"] = host

    # Ensure the langsmith OTel bridge env vars are set. The langsmith package
    # reads these at import time, so setting them here is a no-op if already
    # set in .env but prevents silent mis-configuration.
    os.environ.setdefault("LANGSMITH_OTEL_ENABLED", "true")
    os.environ.setdefault("LANGSMITH_OTEL_ONLY", "true")
    os.environ.setdefault("LANGSMITH_TRACING", "true")

    try:
        from langfuse import get_client

        client = get_client()
        if client.auth_check():
            logger.info("[langfuse] authenticated OK, host=%s", host or "cloud.langfuse.com")
        else:
            logger.warning(
                "[langfuse] auth_check failed — credentials may be invalid or "
                "host unreachable. Continuing without tracing."
            )
            return False
    except Exception as exc:  # noqa: BLE001 — we want to swallow any init error
        logger.warning("[langfuse] client init failed: %s", exc)
        return False

    # Attach the Claude Agent SDK OTel instrumentation. This must happen
    # BEFORE any ClaudeSDKClient or Claude Agent SDK tool is used. JonaAI
    # imports ClaudeSDKClient lazily (inside sdk/client.py functions), so
    # calling this at startup is sufficient.
    try:
        from langsmith.integrations.claude_agent_sdk import configure_claude_agent_sdk

        configure_claude_agent_sdk()
        logger.info("[langfuse] Claude Agent SDK OTel instrumentation active")
    except ImportError as exc:
        logger.warning(
            "[langfuse] langsmith.integrations.claude_agent_sdk not available: %s — "
            "install with `pip install 'langsmith[claude-agent-sdk]'`",
            exc,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("[langfuse] configure_claude_agent_sdk failed: %s", exc)
        return False

    _initialized = True
    return True


def get_langfuse_client():
    """Return the initialized Langfuse client, or None if not available.

    Useful for callers that want to emit scores / annotations outside the
    auto-instrumented Claude Agent SDK path (e.g., quality metric scores).
    """
    if not _initialized:
        return None
    try:
        from langfuse import get_client

        return get_client()
    except Exception:
        return None


def current_trace_id() -> Optional[str]:
    """Return the currently-active Langfuse trace_id, or None if no trace.

    Used by scoring endpoints to attach scores to the in-flight trace
    without the caller needing to pass IDs around manually.
    """
    client = get_langfuse_client()
    if client is None:
        return None
    try:
        return client.get_current_trace_id()
    except Exception:
        return None


_SENSITIVE_KEY_PATTERNS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "token",
    "authorization",
    "bearer",
    "credentials",
)
_TRUNCATE_VALUE_AT = 500  # chars per string field


def _truncate_with_meta(text: str, limit: int = _TRUNCATE_VALUE_AT) -> Any:
    """Truncate a string and return a dict with orig_len + sha256 metadata.

    Borrowed from the Langfuse Claude-Code integration pattern. Instead of
    silently cutting long payloads to the head N chars (which loses any
    signal that a truncation happened), emit a structured object that the
    Langfuse UI can display as "truncated" with the original length and a
    stable hash. The hash allows dedupe of identical large payloads across
    traces even after truncation.

    Returns:
        - The original string if len(text) <= limit
        - A dict `{text, _truncated, _orig_len, _kept_len, _sha256}` otherwise
    """
    if text is None:
        return text
    if len(text) <= limit:
        return text
    return {
        "text": text[:limit],
        "_truncated": True,
        "_orig_len": len(text),
        "_kept_len": limit,
        "_sha256": hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16],
    }


def mask_tool_args(tool_name: str, args: Any) -> Any:
    """Redact sensitive fields from tool arguments before tracing.

    - Removes any key whose name matches a sensitive pattern (case-insensitive)
    - Summarizes bounding boxes to compact `{west, south, east, north}` tuples
      (vs the full nested dict form) so spans stay readable
    - Truncates long string values with metadata: `{text, _truncated: True,
      _orig_len, _kept_len, _sha256}` so the UI shows the original size and
      identical large payloads dedupe by hash.
    - Converts non-serializable values to their string repr

    Returns a copy — never mutates the input.
    """
    def _scrub(val: Any, depth: int = 0) -> Any:
        if depth > 5:  # hard depth limit to avoid cycles
            return "<…depth limit…>"
        if val is None or isinstance(val, (bool, int, float)):
            return val
        if isinstance(val, str):
            return _truncate_with_meta(val)
        if isinstance(val, dict):
            out: dict[str, Any] = {}
            for k, v in val.items():
                k_str = str(k)
                # Redact sensitive keys outright
                if any(p in k_str.lower() for p in _SENSITIVE_KEY_PATTERNS):
                    out[k_str] = "<redacted>"
                    continue
                # Compact bounding boxes
                if k_str in ("spatial_extent", "extent", "bbox") and isinstance(v, dict):
                    keys = set(v.keys())
                    if {"west", "south", "east", "north"}.issubset(keys):
                        out[k_str] = {
                            "west": v["west"],
                            "south": v["south"],
                            "east": v["east"],
                            "north": v["north"],
                        }
                        continue
                out[k_str] = _scrub(v, depth + 1)
            return out
        if isinstance(val, (list, tuple)):
            if len(val) > 20:
                return [_scrub(x, depth + 1) for x in val[:20]] + [f"<…+{len(val) - 20} items>"]
            return [_scrub(x, depth + 1) for x in val]
        # Fallback: coerce via repr and truncate via the metadata helper
        return _truncate_with_meta(repr(val))

    try:
        return _scrub(args)
    except Exception:
        return {"_mask_error": "failed to scrub", "tool": tool_name}


def set_root_trace_name(name: str, input_data: Any = None, output_data: Any = None) -> None:
    """Set name/input/output on the currently-active Langfuse span.

    Safe no-op if Langfuse isn't initialized or no span is active. Used at
    entry points (WebSocket _run_query) to give traces descriptive names
    and curated input/output instead of auto-captured function args.
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        update_kwargs: dict[str, Any] = {"name": name}
        if input_data is not None:
            update_kwargs["input"] = input_data
        if output_data is not None:
            update_kwargs["output"] = output_data
        client.update_current_span(**update_kwargs)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[langfuse] set_root_trace_name failed: %s", exc)


def _extract_tool_insights(tool_name: str, args: Any, result: Any) -> dict:
    """Extract per-tool-type insights for the span metadata.

    Rather than treating every tool as a generic blob, peek into the
    result shape of known tools to surface the single most useful data
    point for each (graph node count, cloud coverage, quality grade,
    etc.). All extraction is defensive — unknown tools fall through
    to an empty dict.
    """
    insights: dict[str, Any] = {}

    def _get(obj, *keys):
        for k in keys:
            if isinstance(obj, dict) and k in obj:
                return obj[k]
        return None

    # Unwrap MCP content envelope { "content": [{"type":"text","text":"<json>"}] }
    def _unwrap(r):
        if not isinstance(r, dict):
            return r
        content = r.get("content")
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    import json as _j
                    try:
                        return _j.loads(item.get("text", ""))
                    except (ValueError, TypeError):
                        return item.get("text", "")
        return r

    try:
        r = _unwrap(result)

        # openeo_raw_graph / openeo_generate_graph → count process nodes
        if tool_name in ("openeo_raw_graph", "openeo_generate_graph"):
            pg = _get(r, "process_graph", "processGraph")
            if isinstance(pg, dict):
                insights["graph_node_count"] = len(pg)
                insights["graph_processes"] = sorted({
                    n.get("process_id") for n in pg.values() if isinstance(n, dict) and n.get("process_id")
                })[:15]

        # openeo_execute → output path, shape, file size estimate
        elif tool_name == "openeo_execute":
            insights["result_path"] = str(_get(r, "result_path", "output_path", "path"))[:200]
            insights["save_id"] = _get(r, "save_id")

        # openeo_quality_metrics → cloud %, quality grade, scene count
        elif tool_name == "openeo_quality_metrics":
            insights["overall_quality_score"] = _get(r, "overall_quality_score")
            insights["quality_grade"] = _get(r, "quality_grade")
            cloud = _get(r, "cloud_coverage") or {}
            if isinstance(cloud, dict):
                insights["cloud_cover_pct"] = _get(cloud, "estimated_cloud_cover_pct")
                insights["usable_scenes"] = _get(cloud, "usable_scenes_estimate")
            temporal = _get(r, "temporal_coverage") or {}
            if isinstance(temporal, dict):
                insights["expected_acquisitions"] = _get(temporal, "expected_acquisitions")

        # openeo_resolve_location → bbox dims, confidence
        elif tool_name == "openeo_resolve_location":
            insights["location_name"] = _get(r, "name") or _get(r, "display_name", "display_name")
            insights["resolve_confidence"] = _get(r, "confidence")
            bbox = _get(r, "bbox") or _get(r, "spatial_extent")
            if isinstance(bbox, dict) and {"west", "east", "south", "north"}.issubset(bbox.keys()):
                width = bbox["east"] - bbox["west"]
                height = bbox["north"] - bbox["south"]
                insights["bbox_size_deg"] = round(max(width, height), 4)

        # openeo_parse_temporal → parsed temporal extent
        elif tool_name == "openeo_parse_temporal":
            insights["temporal_extent"] = _get(r, "temporal_extent")
            insights["temporal_confidence"] = _get(r, "confidence")

        # extract_analysis_goal → analysis type + confidence
        elif tool_name == "extract_analysis_goal":
            insights["analysis_type"] = _get(r, "analysis_type")
            insights["index"] = _get(r, "index")
            insights["complexity"] = _get(r, "complexity")
            conf = _get(r, "confidence")
            if isinstance(conf, dict):
                insights["confidence_avg"] = round(
                    sum(float(v) for v in conf.values() if isinstance(v, (int, float))) / max(len(conf), 1), 3
                )

        # validate_pre_execution → errors + warnings
        elif tool_name == "validate_pre_execution":
            insights["valid"] = _get(r, "valid")
            insights["errors"] = _get(r, "errors")
            insights["warnings"] = _get(r, "warnings")

        # discovery_collections → collection count
        elif tool_name in ("openeo_discovery_collections", "discovery_collections"):
            collections = _get(r, "collections")
            if isinstance(collections, list):
                insights["collection_count"] = len(collections)

        # openeo_compose_graph → composed nodes, bands, temporal window
        elif tool_name == "openeo_compose_graph":
            pg = _get(r, "process_graph", "processGraph")
            if isinstance(pg, dict):
                insights["graph_node_count"] = len(pg)
            insights["collection"] = _get(r, "collection", "collection_id")
            insights["bands"] = _get(r, "bands")
            insights["temporal_extent"] = _get(r, "temporal_extent")
            insights["index"] = _get(r, "index", "index_type")

        # openeo_validate_graph → errors + warnings
        elif tool_name == "openeo_validate_graph":
            insights["valid"] = _get(r, "valid", "is_valid")
            errs = _get(r, "errors") or []
            warns = _get(r, "warnings") or []
            insights["error_count"] = len(errs) if isinstance(errs, list) else errs
            insights["warning_count"] = len(warns) if isinstance(warns, list) else warns

        # openeo_validate_geospatial → extent validity + CRS
        elif tool_name == "openeo_validate_geospatial":
            insights["valid"] = _get(r, "valid", "is_valid")
            insights["crs"] = _get(r, "crs", "projection")

        # openeo_estimate_extent → estimated file size + pixel count
        elif tool_name == "openeo_estimate_extent":
            insights["estimated_size_mb"] = _get(r, "estimated_size_mb", "size_mb")
            insights["num_pixels"] = _get(r, "num_pixels")
            insights["num_bands"] = _get(r, "num_bands")
            insights["num_timesteps"] = _get(r, "num_timesteps")

        # openeo_aggregate_spatial → aggregation method + output shape
        elif tool_name == "openeo_aggregate_spatial":
            insights["method"] = _get(r, "method", "reducer")
            insights["output_shape"] = _get(r, "shape")

        # openeo_apply_kernel → kernel size/type
        elif tool_name == "openeo_apply_kernel":
            insights["kernel"] = _get(r, "kernel_name", "kernel")

        # openeo_algorithm → algorithm name + parameters
        elif tool_name == "openeo_algorithm":
            insights["algorithm"] = _get(r, "algorithm", "algorithm_name")

        # openeo_list_collections / openeo_get_collection_info
        elif tool_name in ("openeo_list_collections", "openeo_get_collection_info"):
            cols = _get(r, "collections")
            if isinstance(cols, list):
                insights["collection_count"] = len(cols)
            insights["collection_id"] = _get(r, "id", "collection_id")

        # openeo job lifecycle tools → job_id + status
        elif tool_name in (
            "openeo_create_job", "openeo_start_job", "openeo_get_job_status",
            "openeo_get_results", "openeo_list_jobs"
        ):
            insights["job_id"] = _get(r, "job_id", "id")
            insights["status"] = _get(r, "status")
            if tool_name == "openeo_list_jobs":
                jobs = _get(r, "jobs")
                if isinstance(jobs, list):
                    insights["job_count"] = len(jobs)

        # viz_show_map / viz_show_ndvi_map → map bounds + title
        elif tool_name in ("viz_show_map", "viz_show_ndvi_map", "viz_show_dashboard"):
            insights["title"] = _get(r, "title")
            insights["colormap"] = _get(r, "colormap")
            bounds = _get(r, "bounds", "bbox")
            if isinstance(bounds, (list, tuple)) and len(bounds) >= 4:
                insights["has_bounds"] = True

        # viz_show_time_series → series length
        elif tool_name == "viz_show_time_series":
            insights["title"] = _get(r, "title")
            data = _get(r, "data", "series", "values")
            if isinstance(data, list):
                insights["series_length"] = len(data)

        # viz_compare_images → number of images compared
        elif tool_name == "viz_compare_images":
            images = _get(r, "images") or []
            if isinstance(images, list):
                insights["image_count"] = len(images)

        # saved_jobs_* → save_id + count
        elif tool_name == "saved_jobs_list":
            jobs = _get(r, "jobs")
            if isinstance(jobs, list):
                insights["saved_count"] = len(jobs)
        elif tool_name in ("saved_jobs_load", "saved_jobs_delete", "result_load"):
            insights["save_id"] = _get(r, "save_id", "id")
            insights["title"] = _get(r, "title")

        # save_workflow_template → template id + name
        elif tool_name == "save_workflow_template":
            insights["template_id"] = _get(r, "id", "template_id")
            insights["template_name"] = _get(r, "name")

        # spectral index tools → index name + formula
        elif tool_name.startswith("spectral_index") or "spectral" in tool_name:
            insights["index"] = _get(r, "index", "name")
            insights["formula"] = str(_get(r, "formula", "expression") or "")[:100]

        # temporal_analysis_* → aggregation window + trend
        elif tool_name.startswith("temporal_analysis") or tool_name.startswith("temporal_"):
            insights["window"] = _get(r, "window", "aggregation_window")
            insights["trend"] = _get(r, "trend", "slope")

        # fusion_* → source collections + fusion method
        elif tool_name.startswith("fusion"):
            insights["sources"] = _get(r, "source_collections", "sources")
            insights["method"] = _get(r, "method", "fusion_method")

        # comparison_* → compared items + metric
        elif tool_name.startswith("comparison") or tool_name.startswith("compare"):
            insights["metric"] = _get(r, "metric", "comparison_metric")
            insights["items_compared"] = _get(r, "items_compared", "num_items")

        # interpret_result / generate_report → summary length + type
        elif tool_name in ("interpret_result", "generate_report"):
            insights["report_type"] = _get(r, "report_type", "type")
            summary = _get(r, "summary", "interpretation", "report") or ""
            if isinstance(summary, str):
                insights["summary_length"] = len(summary)

        # earth_agent_analyze → EA execution summary
        elif tool_name == "earth_agent_analyze":
            insights["steps_executed"] = _get(r, "steps_executed", "num_steps")
            insights["tools_used"] = _get(r, "tools_used")
            insights["ea_model"] = _get(r, "model", "model_name")
            insights["ea_success"] = _get(r, "success")
            insights["ea_total_tokens"] = _get(r, "total_tokens")

        # get_analysis_strategies → strategy count
        elif tool_name == "get_analysis_strategies":
            strats = _get(r, "strategies")
            if isinstance(strats, list):
                insights["strategy_count"] = len(strats)

        # ea_* prefixed (Earth-Agent bridge tools) → pass-through + mark source
        elif tool_name.startswith("ea_"):
            insights["ea_tool"] = True
            insights["ea_tool_name"] = tool_name
            # Try common result shapes
            insights["ea_output_files"] = _get(r, "output_files", "files")
            insights["ea_summary"] = str(_get(r, "summary", "result") or "")[:200]

        # geoai_* tools → model name, inference shape
        elif tool_name.startswith("geoai"):
            insights["model"] = _get(r, "model", "model_name")
            insights["inference_shape"] = _get(r, "output_shape", "shape")

        # export_* tools → format + destination
        elif tool_name.startswith("export"):
            insights["format"] = _get(r, "format", "export_format")
            insights["destination"] = str(_get(r, "destination", "path") or "")[:200]

        # notebook_* → cell count
        elif tool_name.startswith("notebook"):
            cells = _get(r, "cells")
            if isinstance(cells, list):
                insights["cell_count"] = len(cells)

        # discovery_* → item count
        elif tool_name.startswith("discovery"):
            items = _get(r, "items", "results")
            if isinstance(items, list):
                insights["item_count"] = len(items)

        # ── Graph construction layer (SCF builders) ──────────────────────
        # graph_add_* tools mutate an in-flight SCF process graph.
        elif tool_name.startswith("graph_add_"):
            insights["op"] = tool_name.removeprefix("graph_add_")
            insights["graph_id"] = _get(r, "graph_id", "id")
            pg = _get(r, "process_graph", "graph")
            if isinstance(pg, dict):
                insights["graph_node_count"] = len(pg)
            insights["target_band"] = _get(r, "band", "target_band")

        # graph_validate_full → full SCF validation results
        elif tool_name == "graph_validate_full":
            insights["valid"] = _get(r, "valid", "is_valid")
            errs = _get(r, "errors") or []
            warns = _get(r, "warnings") or []
            insights["error_count"] = len(errs) if isinstance(errs, list) else errs
            insights["warning_count"] = len(warns) if isinstance(warns, list) else warns
            insights["validation_layers"] = _get(r, "layers_checked", "validators")

        # openeo_compile_scf → SCF → graph compilation
        elif tool_name == "openeo_compile_scf":
            pg = _get(r, "process_graph", "compiled_graph")
            if isinstance(pg, dict):
                insights["graph_node_count"] = len(pg)
            insights["scf_version"] = _get(r, "scf_version", "version")
            insights["collection"] = _get(r, "collection", "collection_id")

        # openeo_process_node → single-node graph addition
        elif tool_name == "openeo_process_node":
            insights["process_id"] = _get(r, "process_id", "process")
            insights["node_id"] = _get(r, "node_id", "id")

        # openeo_graph_inspect → graph analysis summary
        elif tool_name == "openeo_graph_inspect":
            pg = _get(r, "process_graph")
            if isinstance(pg, dict):
                insights["graph_node_count"] = len(pg)
            insights["processes_used"] = _get(r, "processes_used", "unique_processes")
            insights["depth"] = _get(r, "max_depth", "depth")

        # ── Process registry introspection ───────────────────────────────
        # openeo_list_processes → count of available processes
        elif tool_name == "openeo_list_processes":
            procs = _get(r, "processes")
            if isinstance(procs, list):
                insights["process_count"] = len(procs)

        # openeo_get_process_spec → single process schema
        elif tool_name == "openeo_get_process_spec":
            insights["process_id"] = _get(r, "id", "process_id")
            insights["parameter_count"] = (
                len(_get(r, "parameters") or [])
                if isinstance(_get(r, "parameters"), list)
                else None
            )

        # ── Strategy / planning ──────────────────────────────────────────
        # analysis_strategy_planner → selected strategy
        elif tool_name == "analysis_strategy_planner":
            insights["strategy"] = _get(r, "strategy", "recommended_strategy")
            insights["confidence"] = _get(r, "confidence")
            insights["num_steps"] = _get(r, "num_steps", "step_count")

        # recommend_collection → recommended collection + confidence
        elif tool_name == "recommend_collection":
            insights["recommended"] = _get(r, "collection", "recommended_collection")
            insights["confidence"] = _get(r, "confidence")
            alts = _get(r, "alternatives")
            if isinstance(alts, list):
                insights["alternative_count"] = len(alts)

        # explain_collection_bands → explained band count
        elif tool_name == "explain_collection_bands":
            insights["collection_id"] = _get(r, "collection_id", "id")
            bands = _get(r, "bands", "band_info")
            if isinstance(bands, list):
                insights["band_count"] = len(bands)

        # validate_goal_completeness → goal coverage score
        elif tool_name == "validate_goal_completeness":
            insights["complete"] = _get(r, "complete", "is_complete")
            insights["missing_items"] = _get(r, "missing", "missing_items")
            insights["coverage_pct"] = _get(r, "coverage", "coverage_pct")

        # generate_validation_checklist → checklist item count
        elif tool_name == "generate_validation_checklist":
            items = _get(r, "checklist", "items")
            if isinstance(items, list):
                insights["checklist_item_count"] = len(items)
            insights["validation_type"] = _get(r, "type", "validation_type")

        # ── Execution ────────────────────────────────────────────────────
        # execute_workflow → workflow execution status
        elif tool_name == "execute_workflow":
            insights["status"] = _get(r, "status", "state")
            insights["job_id"] = _get(r, "job_id", "id")
            insights["result_path"] = str(_get(r, "result_path", "output_path") or "")[:200]

        # ── Result analysis (non-viz) ────────────────────────────────────
        # raster_statistics → summary stats shape
        elif tool_name == "raster_statistics":
            insights["min"] = _get(r, "min")
            insights["max"] = _get(r, "max")
            insights["mean"] = _get(r, "mean")
            insights["pixel_count"] = _get(r, "count", "pixel_count")

        # result_inspect → basic raster shape
        elif tool_name == "result_inspect":
            insights["shape"] = _get(r, "shape", "dimensions")
            insights["dtype"] = _get(r, "dtype")
            insights["nodata"] = _get(r, "nodata")
            insights["crs"] = _get(r, "crs")

        # result_histogram → bin count + range
        elif tool_name == "result_histogram":
            bins = _get(r, "bins", "histogram")
            if isinstance(bins, list):
                insights["bin_count"] = len(bins)
            insights["value_range"] = _get(r, "range", "value_range")

        # result_correlation → correlation coefficient
        elif tool_name == "result_correlation":
            insights["correlation"] = _get(r, "correlation", "coefficient", "r")
            insights["p_value"] = _get(r, "p_value")

        # result_difference → difference stats
        elif tool_name == "result_difference":
            insights["mean_diff"] = _get(r, "mean_difference", "mean_diff", "mean")
            insights["max_diff"] = _get(r, "max_difference", "max_diff", "max")

        # result_extract_point → sampled value at point
        elif tool_name == "result_extract_point":
            insights["value"] = _get(r, "value")
            insights["coordinates"] = _get(r, "coordinates", "point")

        # result_extract_temporal_layers → num layers extracted
        elif tool_name == "result_extract_temporal_layers":
            layers = _get(r, "layers")
            if isinstance(layers, list):
                insights["layer_count"] = len(layers)
            insights["time_range"] = _get(r, "time_range")

        # result_zonal_stats → per-zone stats
        elif tool_name == "result_zonal_stats":
            zones = _get(r, "zones", "zonal_stats")
            if isinstance(zones, list):
                insights["zone_count"] = len(zones)
            insights["stat_type"] = _get(r, "statistic", "stat")

        # threshold_classify → number of classes + pixel breakdown
        elif tool_name == "threshold_classify":
            insights["num_classes"] = _get(r, "num_classes", "class_count")
            insights["threshold"] = _get(r, "threshold", "thresholds")
            classes = _get(r, "class_pixel_counts", "pixel_counts")
            if isinstance(classes, dict):
                insights["class_counts"] = {str(k): v for k, v in list(classes.items())[:10]}

        # analyze_result_image → multimodal analysis summary
        elif tool_name == "analyze_result_image":
            insights["image_type"] = _get(r, "image_type", "type")
            analysis = _get(r, "analysis", "summary") or ""
            if isinstance(analysis, str):
                insights["analysis_length"] = len(analysis)

        # assess_result_quality → quality assessment
        elif tool_name == "assess_result_quality":
            insights["quality_score"] = _get(r, "quality_score", "score")
            insights["quality_grade"] = _get(r, "grade", "quality_grade")
            issues = _get(r, "issues")
            if isinstance(issues, list):
                insights["issue_count"] = len(issues)

    except Exception:
        pass  # Insights are best-effort — never break tracing

    return insights


def annotate_tool_span(
    tool_name: str,
    args: Any,
    result: Any,
    error: Optional[str] = None,
) -> None:
    """Emit a `tool.<name>` observation for a completed Claude Agent SDK tool call.

    Called from the PostToolUse hook in claude_sdk_bridge. The Claude Agent
    SDK already emits its own span for each tool invocation (e.g.
    `mcp__openeo__export_workflow_doc`); this function adds a companion
    observation that captures our masked args, result summary, tool.category,
    and per-tool insights for the Langfuse UI.

    IMPLEMENTATION NOTE — DO NOT revert to `update_current_span(name=...)`.
    At PostToolUse time the SDK's tool span has already closed; the
    currently-active span is the parent conversation/agent span.
    `update_current_span(name="tool.X")` would rename that parent, causing
    every subsequent observation in the trace to nest as a child of
    `tool.X`. The live audit on 2026-04-11 caught exactly this bug:
    `tool.export_workflow_doc` became the parent of 80+ sibling spans in
    the `jonaai.chat.query` trace.

    Instead, we use `start_observation` to create a sibling observation
    under the current parent, tagged `as_type="tool"`, and immediately close
    it with `.end()`. This gives each tool call its own dedicated node in
    the Langfuse UI without clobbering the parent span's name.

    Args:
        tool_name: the original tool name (e.g. "openeo_execute")
        args: the raw tool input dict
        result: the raw tool output (dict, list, or string)
        error: optional error message if the tool raised
    """
    client = get_langfuse_client()
    if client is None:
        return
    try:
        masked_args = mask_tool_args(tool_name, args)
        # Classify the tool by prefix
        category = "other"
        for prefix in ("openeo", "viz", "quality", "job", "graph", "temporal",
                       "fusion", "spectral", "discovery", "geoai", "export",
                       "earth_agent", "validation", "notebook", "udf",
                       "comparison", "interpretation", "goal", "saved_jobs",
                       "strategy", "result_analysis", "documentation", "workflow"):
            if tool_name.startswith(prefix):
                category = prefix
                break

        # Result size summary
        try:
            result_size = len(str(result)) if result is not None else 0
        except Exception:
            result_size = -1

        # Per-tool insights (graph nodes, cloud %, quality grade, etc.)
        insights = _extract_tool_insights(tool_name, args, result)

        metadata: dict[str, Any] = {
            "tool_name": tool_name,
            "tool_category": category,
            "result_size_chars": result_size,
            "errored": error is not None,
        }
        if insights:
            metadata["insights"] = insights

        # Result output summary — lightweight, never the full payload.
        output_summary: Any
        if error:
            output_summary = {"error": error[:500]}
        elif isinstance(result, dict):
            output_summary = {
                "_shape": "dict",
                "_keys": list(result.keys())[:10],
                "_size_chars": result_size,
            }
        elif isinstance(result, (list, tuple)):
            output_summary = {
                "_shape": "list",
                "_len": len(result),
                "_size_chars": result_size,
            }
        else:
            output_summary = {
                "_shape": type(result).__name__,
                "_size_chars": result_size,
            }

        # Create a child observation under the currently-active parent span,
        # tagged as a `tool` observation so the Langfuse UI renders it with
        # the tool icon and allows filtering by observation type. Explicit
        # `.end()` in a finally-style block so the observation always closes
        # even if the LangfuseTool object raises during update.
        obs = client.start_observation(
            name=f"tool.{tool_name}",
            as_type="tool",
            input=masked_args,
            output=output_summary,
            metadata=metadata,
            level="ERROR" if error else None,
        )
        try:
            obs.end()
        except Exception:  # noqa: BLE001
            pass
    except Exception as exc:  # noqa: BLE001 — tracing never breaks user flow
        logger.debug("[langfuse] annotate_tool_span failed for %s: %s", tool_name, exc)


def emit_score(
    name: str,
    value: float,
    trace_id: Optional[str] = None,
    comment: Optional[str] = None,
    data_type: str = "NUMERIC",
) -> bool:
    """Emit a Langfuse score on a trace.

    Args:
        name: Score dimension (e.g., "user_feedback", "quality_health",
              "relevance"). Langfuse groups scores by name in the UI.
        value: Numeric score. For user feedback: 1.0 = thumbs-up, -1.0 = down.
               For quality metrics: whatever scale the metric uses.
        trace_id: Target trace. If None, uses the currently-active trace.
        comment: Optional free-text note (e.g., the user's original prompt
                 or a reviewer comment).
        data_type: Langfuse score type — NUMERIC, CATEGORICAL, or BOOLEAN.

    Returns:
        True if the score was dispatched to Langfuse, False if no client
        was available or the call errored (score emission never raises).
    """
    client = get_langfuse_client()
    if client is None:
        return False
    try:
        target = trace_id or current_trace_id()
        if not target:
            logger.debug("[langfuse] emit_score: no active trace_id, skipping")
            return False
        client.create_score(
            name=name,
            value=value,
            trace_id=target,
            comment=comment,
            data_type=data_type,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — scoring never breaks user flow
        logger.warning("[langfuse] emit_score failed: %s", exc)
        return False
