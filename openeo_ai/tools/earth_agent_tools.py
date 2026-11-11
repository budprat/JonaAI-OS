# ABOUTME: Earth-Agent meta-tool and direct MCP tool bridge for JonaAI.

"""Expose Earth-Agent analysis tools through JonaAI's tool registry."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Tuple

from ..earth_agent.agent_runner import EarthAgentRunner
from ..earth_agent.grpo_strategies import get_relevant_strategies
from ..earth_agent.mcp_client import EarthAgentMCPClient
from ..earth_agent.result_mapper import to_visualizations
from ..earth_agent.tool_registry import RegisteredEarthAgentTool, load_earth_agent_registry_sync

# Defensive Langfuse @observe — no-op when langfuse isn't installed, ensures
# the earth_agent_analyze tool call wraps the subprocess in an explicit span
# so `current_trace_id()` has a live trace to forward to Earth-Agent.
try:
    from langfuse import observe as _lf_observe  # type: ignore
except Exception:  # noqa: BLE001
    def _lf_observe(*dargs, **dkwargs):  # type: ignore[no-redef]
        def _decorator(fn):
            return fn
        if dargs and callable(dargs[0]) and not dkwargs:
            return dargs[0]
        return _decorator

logger = logging.getLogger(__name__)

EA_DEFAULT_MAX_STEPS = 50
EA_MAX_STEPS_CAP = 100


EARTH_AGENT_ANALYZE_TOOL_DEFINITION = {
    "name": "earth_agent_analyze",
    "description": (
        "Run comprehensive Earth Observation analysis on job output files using "
        "Earth-Agent's specialized tools and ReAct orchestration. Use this for "
        "complex multi-step analyses that require chaining multiple tools."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language analysis request.",
            },
            "input_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Paths to GeoTIFF files from JonaAI job results to analyze.",
            },
            "data_context": {
                "type": "object",
                "description": (
                    "Optional data context such as data_type, crs, bounds, bands, "
                    "time_range, original_query, process_applied, and statistics."
                ),
            },
            "analysis_mode": {
                "type": "string",
                "enum": ["auto", "instruct"],
                "default": "auto",
                "description": "auto: Earth-Agent plans its own tool chain. instruct: follow prescribed steps.",
            },
            "max_steps": {
                "type": "integer",
                "default": 50,
                "description": "Maximum number of Earth-Agent tool calls to allow.",
            },
            "job_id": {
                "type": "string",
                "description": "Optional JonaAI job identifier for output storage.",
            },
        },
        "required": ["query", "input_files"],
    },
}

GET_ANALYSIS_STRATEGIES_TOOL_DEFINITION = {
    "name": "get_analysis_strategies",
    "description": (
        "Retrieve Earth-Agent's learned analysis strategies for a specific EO task. "
        "Call this before Earth-Agent post-processing on job output GeoTIFFs to get "
        "recommended tool-chaining patterns and QA guidance."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query_type": {
                "type": "string",
                "enum": [
                    "vegetation",
                    "water",
                    "urban",
                    "change_detection",
                    "climate",
                    "disaster",
                    "agriculture",
                    "terrain",
                    "object_detection",
                    "general",
                ],
                "description": "Category of analysis to retrieve strategies for.",
            },
            "data_type": {
                "type": "string",
                "enum": ["spectral_index", "rgb", "multi_temporal", "thermal", "sar", "general"],
                "description": "Type of input data being analyzed.",
                "default": "general",
            },
        },
        "required": ["query_type"],
    },
}


_DIRECT_TOOL_DEFINITIONS: list[dict[str, Any]] | None = None
_DIRECT_TOOL_INDEX: dict[str, RegisteredEarthAgentTool] | None = None


def get_earth_agent_tool_definitions() -> List[Dict[str, Any]]:
    """Return Earth-Agent tool definitions for SDK registration.

    Direct tool discovery (ea_* tools) is deferred — it requires MCP servers
    which can't be started during synchronous module import. The meta-tool
    and strategy tool are offered only where Earth-Agent is installed and not disabled,
    matching create_earth_agent_tools, so the model is never offered a tool with no handler.
    """
    if not _earth_agent_meta_enabled():
        return []
    definitions = [
        GET_ANALYSIS_STRATEGIES_TOOL_DEFINITION,
        EARTH_AGENT_ANALYZE_TOOL_DEFINITION,
    ]

    if _earth_agent_direct_tools_enabled():
        try:
            direct_defs, _ = _load_direct_tool_registry()
            definitions.extend(direct_defs)
        except Exception as e:
            # asyncio.run() fails inside an already-running event loop (uvicorn),
            # or MCP servers may not be available. Direct tools deferred.
            logger.warning(f"Earth-Agent direct tool discovery deferred: {type(e).__name__}: {e}")
    return definitions


def create_earth_agent_tools(config=None) -> Dict[str, Any]:
    """Create Earth-Agent tool handlers for direct and SDK execution paths."""
    handlers: Dict[str, Any] = {}
    if not _earth_agent_meta_enabled():
        return handlers

    runner = EarthAgentRunner()

    async def get_analysis_strategies(args: Dict[str, Any]) -> Dict[str, Any]:
        strategy_text = get_relevant_strategies(
            query_type=args["query_type"],
            data_type=args.get("data_type"),
        )
        return {"content": [{"type": "text", "text": strategy_text or "No matching Earth-Agent strategies found."}]}

    @_lf_observe(name="tool.earth_agent_analyze")
    async def earth_agent_analyze(args: Dict[str, Any]) -> Dict[str, Any]:
        raw_steps = int(args.get("max_steps", EA_DEFAULT_MAX_STEPS))
        capped_steps = min(max(raw_steps, 1), EA_MAX_STEPS_CAP)
        # Annotate the observe span with masked args + the input file count
        # so the Langfuse UI shows what EA was asked to do without leaking
        # full process graphs or bbox coords.
        try:
            from ..langfuse_setup import mask_tool_args
            from langfuse import get_client as _lf_get_client  # type: ignore

            _lf = _lf_get_client()
            if _lf is not None:
                _lf.update_current_span(
                    input=mask_tool_args("earth_agent_analyze", args),
                    metadata={
                        "component": "earth-agent",
                        "input_file_count": len(args.get("input_files") or []),
                        "max_steps": capped_steps,
                        "analysis_mode": args.get("analysis_mode", "auto"),
                    },
                )
        except Exception:  # noqa: BLE001 — telemetry must never fail the tool
            pass

        result = await runner.analyze(
            query=args["query"],
            input_files=args["input_files"],
            data_context=args.get("data_context"),
            analysis_mode=args.get("analysis_mode", "auto"),
            max_steps=capped_steps,
            job_id=args.get("job_id"),
        )
        viz_seed = dict(result)
        if result.get("output_files") and "output_path" not in viz_seed:
            viz_seed["output_path"] = result["output_files"][0]

        # Surface the EA answer summary (truncated) on the span as output.
        try:
            from langfuse import get_client as _lf_get_client  # type: ignore

            _lf = _lf_get_client()
            if _lf is not None:
                answer_text = str(result.get("answer") or "")
                _lf.update_current_span(
                    output={
                        "answer_preview": answer_text[:500],
                        "output_file_count": len(result.get("output_files") or []),
                        "trajectory_steps": len(result.get("trajectory") or []),
                    }
                )
        except Exception:  # noqa: BLE001
            pass

        return _build_response(viz_seed, tool_name="earth_agent_analyze")

    handlers["get_analysis_strategies"] = get_analysis_strategies
    handlers["earth_agent_analyze"] = earth_agent_analyze

    if _earth_agent_direct_tools_enabled():
        mcp_client = EarthAgentMCPClient()
        _, tool_index = _load_direct_tool_registry()
        for prefixed_name, metadata in tool_index.items():
            handlers[prefixed_name] = _make_direct_tool_handler(
                prefixed_name=prefixed_name,
                metadata=metadata,
                mcp_client=mcp_client,
            )

    return handlers


def _make_direct_tool_handler(
    *,
    prefixed_name: str,
    metadata: RegisteredEarthAgentTool,
    mcp_client: EarthAgentMCPClient,
):
    async def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
        result_info = await mcp_client.call_tool(
            server_name=metadata.server_name,
            tool_name=metadata.source_name,
            arguments=args,
            job_id=args.get("job_id"),
        )
        payload = result_info["result"]
        # Merge output_files into payload so downstream viz/storage can find the path
        if result_info.get("output_files") and "output_path" not in payload:
            payload["output_path"] = result_info["output_files"][0]
        return _build_response(payload, tool_name=prefixed_name)

    return _handler


def _build_response(payload: Any, *, tool_name: str) -> Dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("content"), list):
        content = list(payload["content"])
        viz_source = payload
    else:
        content = [{"type": "text", "text": json.dumps(payload)}]
        viz_source = payload

    visualizations = to_visualizations(viz_source, tool_name=tool_name)
    for viz in visualizations:
        component = _to_sdk_visualization_component(viz)
        if component:
            content.append({"type": "visualization", "component": component})

    response = {"content": content}
    if isinstance(payload, dict) and payload.get("isError"):
        response["isError"] = True
    return response


def _to_sdk_visualization_component(viz: Dict[str, Any]) -> Dict[str, Any] | None:
    viz_type = viz.get("type")
    data = viz.get("data", {})
    title = viz.get("title", "")

    if viz_type == "map":
        return {
            "type": "map",
            "title": title,
            "mapType": data.get("type", "raster"),
            "url": data.get("url", ""),
            "bounds": data.get("bounds"),
            "colormap": data.get("colormap", "viridis"),
            "opacity": data.get("opacity", 0.6),
            "source": data.get("source"),
            "geojson": data.get("geojson"),
            "vmin": data.get("vmin"),
            "vmax": data.get("vmax"),
        }
    if viz_type == "chart":
        return {
            "type": "chart",
            "title": title,
            "chartType": data.get("chartType", "line"),
            "xAxis": data.get("xAxis", {}),
            "yAxis": data.get("yAxis", {}),
            "series": data.get("series", []),
        }
    if viz_type == "table":
        return {
            "type": "table",
            "title": title,
            "headers": data.get("headers", []),
            "rows": data.get("rows", []),
        }
    if viz_type == "classification":
        return {
            "type": "classification",
            "title": title,
            "label": data.get("label", ""),
            "confidence": data.get("confidence", 0),
            "categories": data.get("categories", []),
        }
    return None


def _load_direct_tool_registry() -> tuple[list[dict[str, Any]], dict[str, RegisteredEarthAgentTool]]:
    global _DIRECT_TOOL_DEFINITIONS, _DIRECT_TOOL_INDEX
    if _DIRECT_TOOL_DEFINITIONS is not None and _DIRECT_TOOL_INDEX is not None:
        return list(_DIRECT_TOOL_DEFINITIONS), dict(_DIRECT_TOOL_INDEX)

    try:
        definitions, index = load_earth_agent_registry_sync()
    except Exception as exc:
        logger.warning("Earth-Agent direct tool discovery unavailable: %s", exc)
        definitions, index = [], {}

    _DIRECT_TOOL_DEFINITIONS = list(definitions)
    _DIRECT_TOOL_INDEX = dict(index)
    return list(_DIRECT_TOOL_DEFINITIONS), dict(_DIRECT_TOOL_INDEX)


def _earth_agent_meta_enabled() -> bool:
    if os.environ.get("DISABLE_EARTH_AGENT_INTEGRATION", "").lower() in {"1", "true", "yes"}:
        return False
    return _earth_agent_installed()


def _earth_agent_installed() -> bool:
    """Whether Earth-Agent can run here: its checkout exists, and an interpreter with its
    dependencies does. Without both every call fails, so the tools aren't offered at all.

    Mirrors EarthAgentRunner: the default root is the Earth-Agent folder beside the repo, and
    the interpreter is EARTH_AGENT_PYTHON, the checkout's own .venv, or this one.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "Earth-Agent"
    if not root.is_dir():
        return False
    if os.environ.get("EARTH_AGENT_PYTHON") or any(
            (root / ".venv" / "bin" / name).exists() for name in ("python3", "python")):
        return True
    return all(importlib.util.find_spec(name) is not None
               for name in ("langgraph", "langchain_core", "langchain_mcp_adapters"))


def _earth_agent_direct_tools_enabled() -> bool:
    if not _earth_agent_meta_enabled():
        return False
    return os.environ.get("ENABLE_EARTH_AGENT_DIRECT_TOOLS", "").lower() in {"1", "true", "yes"}
