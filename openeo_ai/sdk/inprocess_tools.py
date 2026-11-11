"""In-process MCP server for OpenEO tools using create_sdk_mcp_server().

Replaces the subprocess-based mcp_stdio_server.py with an in-process MCP
server that runs within the same Python process. This eliminates:
  - Subprocess spawning overhead per session
  - JSON-RPC serialization/deserialization latency
  - Process management complexity

All 22 tool handlers are reused from openeo_ai/tools/*.py — no logic
duplication. Tool schemas come from TOOL_DEFINITIONS in client.py.

Usage:
    from openeo_ai.sdk.inprocess_tools import create_openeo_mcp_server
    config = create_openeo_mcp_server()
    # Returns McpSdkServerConfig for ClaudeAgentOptions.mcp_servers
"""

import asyncio
import json
import os
import traceback
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from claude_agent_sdk import SdkMcpTool, create_sdk_mcp_server

from openeo_ai.sdk.client import (
    CORE_TOOL_NAMES,
    TOOL_DEFINITIONS,
    OpenEOAIConfig,
    PLANNER_TOOL_NAMES,
    SPECIALIST_CONFIGS,
)
from openeo_ai.tools.openeo_tools import create_openeo_tools
from openeo_ai.tools.job_tools import create_job_tools
from openeo_ai.tools.geoai_tools import create_geoai_tools
from openeo_ai.tools.viz_tools import create_viz_tools
from openeo_ai.tools.saved_jobs_tools import create_saved_jobs_tools
from openeo_ai.tools.monitor_tools import create_monitor_tools
from openeo_ai.tools.result_analysis_tools import create_result_analysis_tools
from openeo_ai.tools.spectral_index_tools import create_spectral_index_tools
from openeo_ai.tools.temporal_analysis_tools import create_temporal_analysis_tools
from openeo_ai.tools.export_tools import create_export_tools
from openeo_ai.tools.comparison_tools import create_comparison_tools
from openeo_ai.tools.discovery_tools import create_discovery_tools
from openeo_ai.tools.fusion_tools import create_fusion_tools
from openeo_ai.tools.documentation_tools import create_documentation_tools
from openeo_ai.tools.earth_agent_tools import create_earth_agent_tools
# NOTE: goal_tools, quality_tools, validation_tools are imported lazily inside
# _create_all_handlers() to avoid circular import:
#   goal_tools → sdk.domain_knowledge → sdk.__init__ → claude_sdk_bridge → inprocess_tools → goal_tools


def _create_all_handlers(config: OpenEOAIConfig) -> Dict[str, Any]:
    """Create all tool handler functions from the 14 tool modules.

    Returns:
        Dict mapping tool_name -> async handler(args) -> dict
    """
    handlers: Dict[str, Any] = {}
    handlers.update(create_openeo_tools(config))
    handlers.update(create_job_tools(config))
    handlers.update(create_geoai_tools(config))
    handlers.update(create_viz_tools(config))
    handlers.update(create_saved_jobs_tools(config))
    handlers.update(create_monitor_tools(config))
    handlers.update(create_result_analysis_tools(config))
    handlers.update(create_spectral_index_tools(config))
    handlers.update(create_temporal_analysis_tools(config))
    handlers.update(create_export_tools(config))
    handlers.update(create_comparison_tools(config))
    handlers.update(create_discovery_tools(config))
    handlers.update(create_fusion_tools(config))
    handlers.update(create_documentation_tools(config))
    handlers.update(create_earth_agent_tools(config))

    # Graph builder, workflow executor, interpretation tools
    from openeo_ai.tools.graph_builder_tools import create_graph_builder_tools
    from openeo_ai.tools.workflow_executor import create_workflow_executor_tools
    from openeo_ai.tools.interpretation import create_interpretation_tools

    handlers.update(create_graph_builder_tools(config))
    handlers.update(create_interpretation_tools(config))

    # Workflow executor needs access to other tool handlers for chaining
    # Steps run through the wrapped handlers, so they get each tool's access checks.
    # Filled in below, once every tool has been created.
    wrapped: Dict[str, Any] = {}
    handlers.update(create_workflow_executor_tools(config, tool_handlers=wrapped))

    # Query intelligence tools (lazy imports to avoid circular dependency)
    from openeo_ai.tools.goal_tools import create_goal_tools
    from openeo_ai.tools.quality_tools import create_quality_tools
    from openeo_ai.tools.validation_tools import create_validation_tools
    from openeo_ai.tools.strategy_tools import create_strategy_tools

    goal_handlers, _ = create_goal_tools()
    handlers.update(goal_handlers)
    quality_handlers, _ = create_quality_tools()
    handlers.update(quality_handlers)
    validation_handlers, _ = create_validation_tools()
    handlers.update(validation_handlers)
    strategy_handlers, _ = create_strategy_tools()
    handlers.update(strategy_handlers)

    # Agentic-EO decision-grade query tool (natural language -> decision report)
    from openeo_ai.agentic_eo.sdk_tool import create_decision_query_tools
    handlers.update(create_decision_query_tools(config))

    wrapped.update({name: _wrap_handler(name, handler) for name, handler in handlers.items()})
    return handlers


# Module-level cache: handlers are stateless so one set is sufficient
_cached_handlers: Dict[str, Any] = {}
_cached_config_id: int = 0


def _get_handlers(config: OpenEOAIConfig) -> Dict[str, Any]:
    """Return cached handlers, creating them once per config identity."""
    global _cached_handlers, _cached_config_id
    cfg_id = id(config)
    if not _cached_handlers or _cached_config_id != cfg_id:
        _cached_handlers = _create_all_handlers(config)
        _cached_config_id = cfg_id
    return _cached_handlers


_INPUT_PATH_ARGS = (
    "file_path", "file_path_a", "file_path_b", "input_path", "geotiff_path",
    "before_path", "after_path", "path", "source_file", "input_files",
)


async def _access_denied(args: Dict[str, Any]) -> Optional[str]:
    """Why the current user may not use these arguments, or None if they may.

    A custom output_path is moved into the user's folder rather than refused, so an
    export can't write into another user's folder and doesn't fail needlessly. With no
    current user (scripts, tests) there is nobody to check against, unless
    JONA_REQUIRE_TOOL_USER is set, as a deployment should: there every tool call comes from
    a signed-in chat, so a missing user means the context was lost and the call is refused.
    """
    from ..storage.result_access import can_read_job, can_read_path, user_output_dir
    from .user_context import get_current_user_id

    user_id = get_current_user_id()
    if not user_id and os.environ.get("JONA_REQUIRE_TOOL_USER", "").lower() in ("1", "true", "yes"):
        return "Access denied: this tool call has no signed-in user."
    if not user_id or not isinstance(args, dict):
        return None

    output_path = args.get("output_path")
    if isinstance(output_path, str) and output_path:
        args["output_path"] = str(user_output_dir(user_id) / Path(output_path).name)

    for key in _INPUT_PATH_ARGS:
        value = args.get(key)
        for item in value if isinstance(value, (list, tuple)) else [value]:
            if isinstance(item, str) and item and not await asyncio.to_thread(can_read_path, item, user_id):
                return f"Access denied: this file does not belong to the current user ({key})."

    job_id = args.get("job_id")
    if isinstance(job_id, str) and job_id and not await asyncio.to_thread(can_read_job, job_id, user_id):
        return "Access denied: this job does not belong to the current user."
    return None


def _append_raster_warning(args: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Warn when a tool analyzed a raster holding only part of the job's output."""
    path = args.get("file_path") if isinstance(args, dict) else None
    if not isinstance(path, str) or not path.lower().endswith((".tif", ".tiff")):
        return
    from ..tools.raster_provenance import load_notes_warning, time_slice_warning

    if not isinstance(result.get("content"), list):
        return
    for warning in (time_slice_warning(path), load_notes_warning(path)):
        if warning:
            result["content"].append({"type": "text", "text": warning})


def _wrap_handler(name: str, handler):
    """Wrap a tool handler with error handling matching mcp_stdio_server.py."""
    async def safe_handler(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            denied = await _access_denied(args)
            if denied:
                return {"content": [{"type": "text", "text": json.dumps({"error": denied})}], "isError": True}
            result = await handler(args)
            # Handlers return {"content": [{"type": "text", "text": "..."}]}
            if isinstance(result, dict) and "content" in result:
                _append_raster_warning(args, result)
                return result
            # Wrap unexpected formats
            text = json.dumps(result) if isinstance(result, (dict, list)) else str(result)
            return {"content": [{"type": "text", "text": text}]}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return {
                "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                "isError": True,
            }
    return safe_handler


def create_openeo_mcp_server(config: OpenEOAIConfig = None):
    """Create an in-process MCP server with all 22 OpenEO tools.

    Args:
        config: OpenEOAIConfig instance. Created from env vars if None.

    Returns:
        McpSdkServerConfig dict for use in ClaudeAgentOptions.mcp_servers
    """
    if config is None:
        config = OpenEOAIConfig()

    # Build all handlers (cached — created once, shared across MCP servers)
    handlers = _get_handlers(config)

    # Build SdkMcpTool objects from TOOL_DEFINITIONS + handlers
    tools = []
    for td in TOOL_DEFINITIONS:
        name = td["name"]
        if name not in handlers:
            print(f"[InProcess MCP] Warning: no handler for tool '{name}'", file=sys.stderr)
            continue
        tools.append(SdkMcpTool(
            name=name,
            description=td["description"],
            input_schema=td["input_schema"],
            handler=_wrap_handler(name, handlers[name]),
        ))

    tools.append(_ask_user_question_tool())

    print(f"[InProcess MCP] Registered {len(tools)} tools", file=sys.stderr)
    return create_sdk_mcp_server("openeo", "1.0.0", tools)


def _ask_user_question_tool() -> SdkMcpTool:
    """AskUserQuestion — interactive clarification tool.

    The can_use_tool callback in claude_sdk_bridge intercepts this tool
    call, routes questions to the frontend, waits for answers, then
    injects them into updated_input. The handler simply returns the answers.
    """
    async def ask_user_handler(args: Dict[str, Any]) -> Dict[str, Any]:
        answers = args.get("answers", {})
        if not answers:
            return {"content": [{"type": "text", "text": (
                "The user gave no answer. Do not treat your recommended option as their "
                "choice; state any assumption you make."
            )}]}
        return {"content": [{"type": "text", "text": json.dumps(answers)}]}

    return SdkMcpTool(
        name="AskUserQuestion",
        description=(
            "Ask the user clarification questions before running expensive operations. "
            "MUST use when: (1) spatial extent is completely missing, (2) analysis type is ambiguous, "
            "(3) user says 'compare' without specifying what, (4) 'before and after' without dates. "
            "SHOULD use when: cloud masking preference matters, multiple valid indices apply, "
            "or resolution choice matters (Sentinel-2 10m vs Landsat 30m). "
            "Keep to 1-2 questions with 2-3 options each. Mark the best option with '(Recommended)'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {"type": "string", "description": "The question to ask the user"},
                            "header": {"type": "string", "description": "Short label (max 12 chars)"},
                            "options": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"},
                                    },
                                    "required": ["label", "description"],
                                },
                            },
                            "multiSelect": {"type": "boolean", "default": False},
                        },
                        "required": ["question", "header", "options"],
                    },
                },
            },
            "required": ["questions"],
        },
        handler=ask_user_handler,
    )


def _build_core_tools(config: OpenEOAIConfig = None) -> list:
    """Build the curated main-session toolset.

    CORE_TOOL_NAMES keeps the definitions under the CLI's auto-eager
    threshold so sessions load every tool eagerly instead of deferring the
    whole surface behind ToolSearch. AskUserQuestion is always included.
    """
    if config is None:
        config = OpenEOAIConfig()

    handlers = _get_handlers(config)

    tools = []
    for td in TOOL_DEFINITIONS:
        name = td["name"]
        if name not in CORE_TOOL_NAMES:
            continue
        if name not in handlers:
            print(f"[InProcess MCP] Warning: no handler for core tool '{name}'", file=sys.stderr)
            continue
        tools.append(SdkMcpTool(
            name=name,
            description=td["description"],
            input_schema=td["input_schema"],
            handler=_wrap_handler(name, handlers[name]),
        ))
    tools.append(_ask_user_question_tool())
    return tools


def create_core_mcp_server(config: OpenEOAIConfig = None):
    """Create the slimmed main-session MCP server (same 'openeo' server name,
    so mcp__openeo__* tool names and the frontend prefix contract are unchanged).
    """
    tools = _build_core_tools(config)
    print(f"[InProcess MCP] Core server: {len(tools)} tools", file=sys.stderr)
    return create_sdk_mcp_server("openeo", "1.0.0", tools)


def create_planner_mcp_server(config: OpenEOAIConfig = None):
    """Create an MCP server with only discovery/validation tools for the Planner agent.

    The Planner gets read-only tools for collection discovery, location resolution,
    and parameter validation — but NOT execution tools (job creation, graph generation).
    This forces it to plan rather than execute.

    Args:
        config: OpenEOAIConfig instance. Created from env vars if None.

    Returns:
        McpSdkServerConfig dict for use in ClaudeAgentOptions.mcp_servers
    """
    if config is None:
        config = OpenEOAIConfig()

    handlers = _get_handlers(config)

    tools = []
    for td in TOOL_DEFINITIONS:
        name = td["name"]
        if name not in PLANNER_TOOL_NAMES:
            continue
        if name not in handlers:
            continue
        tools.append(SdkMcpTool(
            name=name,
            description=td["description"],
            input_schema=td["input_schema"],
            handler=_wrap_handler(name, handlers[name]),
        ))

    print(f"[InProcess MCP] Planner server: {len(tools)} tools", file=sys.stderr)
    return create_sdk_mcp_server("openeo_planner", "1.0.0", tools)


def create_specialist_mcp_server(specialist_name: str, tool_names: frozenset, config: OpenEOAIConfig = None):
    """Create an MCP server for a specialist agent with a specific tool subset.

    Args:
        specialist_name: Specialist identifier (e.g. "data", "viz", "ml", "qa").
        tool_names: Frozenset of tool names this specialist can use.
        config: OpenEOAIConfig instance. Created from env vars if None.

    Returns:
        McpSdkServerConfig dict for use in ClaudeAgentOptions.mcp_servers
    """
    if config is None:
        config = OpenEOAIConfig()

    handlers = _get_handlers(config)

    tools = []
    for td in TOOL_DEFINITIONS:
        name = td["name"]
        if name not in tool_names:
            continue
        if name not in handlers:
            continue
        tools.append(SdkMcpTool(
            name=name,
            description=td["description"],
            input_schema=td["input_schema"],
            handler=_wrap_handler(name, handlers[name]),
        ))

    mcp_server_name = f"openeo_{specialist_name}"
    print(f"[InProcess MCP] {mcp_server_name} server: {len(tools)} tools", file=sys.stderr)
    return create_sdk_mcp_server(mcp_server_name, "1.0.0", tools)
