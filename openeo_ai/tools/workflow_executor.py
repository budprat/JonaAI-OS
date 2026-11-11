# ABOUTME: ReAct workflow executor tool for step-by-step plan execution.
# Executes structured multi-step EO analysis workflows with automatic result
# threading, validation gates, progress reporting, and error recovery.

"""
Workflow executor for structured multi-step EO analysis plans.

Takes a list of steps (from analysis_strategy_planner or planner/worker output)
and executes them sequentially. Each step's output automatically feeds the next
step's input via smart auto-threading rules.

Features:
- Result threading: create_job auto-receives process_graph from previous step
- Validation gates: graph_validate_full runs before job submission
- Error recovery: skip/retry/stop policies per step
- Adapter functions normalize planner JSON and strategy output formats
"""

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Auto-Threading Rules ──
# Maps: (current_tool) -> (source_result_key, inject_as_arg_key)
# The executor searches backwards through completed steps for a result
# containing source_result_key and injects it as inject_as_arg_key.

AUTO_THREAD_RULES: Dict[str, List[Dict[str, str]]] = {
    "openeo_create_job": [
        {"source_key": "graph", "inject_as": "process_graph"},
        {"source_key": "process_graph", "inject_as": "process_graph"},
    ],
    "openeo_start_job": [
        {"source_key": "id", "inject_as": "job_id", "source_tool": "openeo_create_job"},
    ],
    "openeo_get_results": [
        {"source_key": "id", "inject_as": "job_id", "source_tool": "openeo_create_job"},
    ],
    "openeo_get_job_status": [
        {"source_key": "id", "inject_as": "job_id", "source_tool": "openeo_create_job"},
    ],
    "result_inspect": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "viz_show_map": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "spectral_index_calculate": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "interpret_result": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "result_histogram": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "result_zonal_stats": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "result_extract_point": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "export_styled_geotiff": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "export_report": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "temporal_trend_analysis": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "temporal_anomaly_detect": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
    "temporal_phenology": [
        {"source_key": "output_path", "inject_as": "file_path"},
        {"source_key": "file_path", "inject_as": "file_path"},
    ],
}

# Tools that produce process graphs (for validation gate detection)
GRAPH_PRODUCING_TOOLS = {
    "openeo_generate_graph", "openeo_compose_graph",
    "openeo_raw_graph", "openeo_algorithm",
}


def _raise_if_tool_failed(result: Any) -> Any:
    """Checked handlers report a failure in the result, so a step's error handling sees it."""
    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or [{}]
        raise RuntimeError(str(content[0].get("text", "Tool failed")))
    return result


def _parse_tool_result(result: Any) -> Optional[dict]:
    """Extract JSON dict from MCP tool result format.

    Tool handlers return: {"content": [{"type": "text", "text": "..."}]}
    The text field often contains JSON. Parse it if possible.
    """
    if result is None:
        return None
    if isinstance(result, dict):
        # Already a plain dict (e.g., from direct call)
        if "content" in result:
            for block in result.get("content", []):
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    try:
                        return json.loads(text)
                    except (json.JSONDecodeError, TypeError):
                        return {"raw_text": text}
        # Direct dict result (no MCP wrapper)
        return result
    if isinstance(result, str):
        try:
            return json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return {"raw_text": result}
    return {"raw_value": str(result)}


def _find_threaded_value(
    results: Dict[str, dict],
    step_order: List[str],
    rules: List[Dict[str, str]],
) -> Optional[Dict[str, Any]]:
    """Search backward through completed step results for auto-threading match.

    Returns dict: {inject_as: value} or None if no match found.
    """
    for rule in rules:
        source_key = rule["source_key"]
        inject_as = rule["inject_as"]
        source_tool = rule.get("source_tool")

        # Search backward through completed steps
        for step_id in reversed(step_order):
            step_result = results.get(step_id)
            if step_result is None:
                continue

            result_data = step_result.get("data")
            if result_data is None:
                continue

            # If source_tool specified, only match steps using that tool
            if source_tool and step_result.get("tool") != source_tool:
                continue

            # Check if the result contains the source key
            if isinstance(result_data, dict) and source_key in result_data:
                return {inject_as: result_data[source_key]}

    return None


class WorkflowExecutor:
    """Executes a structured workflow plan step-by-step.

    Each step calls a tool handler directly (same async functions from
    _create_tools()), threads results between steps, and applies
    validation gates and error policies.
    """

    def __init__(
        self,
        tool_handlers: Dict[str, Callable],
        websocket: Any = None,
    ):
        self.tools = tool_handlers
        self.ws = websocket
        self.results: Dict[str, dict] = {}  # step_id -> {tool, data, error, duration}
        self._step_order: List[str] = []     # ordered list of step_ids

    async def _send_progress(
        self, step_num: int, total: int, tool: str, purpose: str
    ) -> None:
        """Send workflow step progress via WebSocket (if available)."""
        if self.ws is None:
            return
        try:
            msg = json.dumps({
                "type": "workflow_step",
                "step": step_num,
                "total": total,
                "tool": tool,
                "purpose": purpose,
            })
            await self.ws.send_text(msg)
        except Exception:
            pass  # Non-critical: don't fail workflow on WS errors

    def _resolve_args(self, step: dict, step_id: str) -> dict:
        """Resolve step arguments by merging explicit args with auto-threaded values."""
        args = dict(step.get("args", {}))

        # Explicit input_from threading
        input_from = step.get("input_from")
        if input_from:
            source = self.results.get(input_from, {}).get("data", {})
            if isinstance(source, dict):
                input_key = step.get("input_key")
                inject_as = step.get("inject_as", input_key)
                if input_key and inject_as and input_key in source:
                    args.setdefault(inject_as, source[input_key])

        # Auto-threading: apply rules if arg not already set
        tool_name = step.get("tool", "")
        rules = AUTO_THREAD_RULES.get(tool_name)
        if rules:
            threaded = _find_threaded_value(
                self.results, self._step_order, rules
            )
            if threaded:
                for key, value in threaded.items():
                    args.setdefault(key, value)

        return args

    async def _run_validation_gate(self, args: dict) -> Optional[str]:
        """Run graph_validate_full on process_graph if validator is available.

        Returns error string or None if valid.
        """
        graph = args.get("process_graph")
        if not graph:
            return None

        validator = self.tools.get("graph_validate_full")
        if not validator:
            return None

        try:
            result = await validator({"graph": graph})
            parsed = _parse_tool_result(result)
            if parsed and isinstance(parsed, dict):
                if parsed.get("ready_to_submit") is False:
                    errors = parsed.get("errors", parsed.get("issues", []))
                    return f"Graph validation failed: {errors}"
            return None
        except Exception as e:
            logger.warning(f"Validation gate error: {e}")
            return None  # Non-blocking: validation failure shouldn't stop execution

    async def execute(
        self,
        steps: List[dict],
        context: Optional[dict] = None,
        validate_graphs: bool = True,
        stop_on_error: bool = False,
    ) -> dict:
        """Execute steps sequentially with result threading.

        Args:
            steps: List of step dicts with tool, purpose, args, etc.
            context: Optional pre-resolved values injected into all steps.
            validate_graphs: Auto-validate graphs before job submission.
            stop_on_error: Stop on first error (overrides per-step on_error).

        Returns:
            Summary dict with completed/failed/skipped counts, results, errors.
        """
        self.results = {}
        self._step_order = []

        total = len(steps)
        completed = 0
        failed = 0
        skipped = 0
        errors: List[dict] = []
        final_output_path: Optional[str] = None
        workflow_tools: List[str] = []
        start_time = time.time()

        for i, step in enumerate(steps):
            step_num = i + 1
            step_id = step.get("id", f"step_{step_num}")
            tool_name = step.get("tool", "")
            purpose = step.get("purpose", step.get("reason", f"Execute {tool_name}"))
            on_error = step.get("on_error", "skip")

            self._step_order.append(step_id)
            workflow_tools.append(tool_name)

            # Progress report
            await self._send_progress(step_num, total, tool_name, purpose)
            logger.info(f"Workflow step {step_num}/{total}: {tool_name} — {purpose}")

            # Check tool availability
            handler = self.tools.get(tool_name)
            if handler is None:
                error_msg = f"Unknown tool: {tool_name}"
                logger.warning(f"Step {step_num}: {error_msg}")
                errors.append({"step": step_num, "step_id": step_id, "tool": tool_name, "error": error_msg})
                self.results[step_id] = {"tool": tool_name, "data": None, "error": error_msg}
                if stop_on_error:
                    failed += 1
                    break
                skipped += 1
                continue

            # Resolve arguments
            args = self._resolve_args(step, step_id)

            # Inject context values as defaults
            if context:
                for key, val in context.items():
                    args.setdefault(key, val)

            # Validation gate before job submission
            if validate_graphs and tool_name == "openeo_create_job":
                val_error = await self._run_validation_gate(args)
                if val_error:
                    logger.warning(f"Step {step_num}: Validation gate: {val_error}")
                    errors.append({
                        "step": step_num, "step_id": step_id,
                        "tool": "graph_validate_full", "error": val_error,
                    })
                    # Validation failure is a warning, not a stop — proceed with submission

            # Execute tool
            step_start = time.time()
            try:
                result = _raise_if_tool_failed(await handler(args))
                parsed = _parse_tool_result(result)
                duration = round(time.time() - step_start, 2)

                self.results[step_id] = {
                    "tool": tool_name,
                    "data": parsed,
                    "error": None,
                    "duration_s": duration,
                }
                completed += 1

                # Track final output path for convenience
                if parsed and isinstance(parsed, dict):
                    for path_key in ("output_path", "file_path", "path"):
                        if path_key in parsed:
                            final_output_path = parsed[path_key]
                            break

                logger.info(f"Step {step_num}: {tool_name} completed in {duration}s")

            except Exception as e:
                duration = round(time.time() - step_start, 2)
                error_msg = f"{type(e).__name__}: {e}"
                logger.error(f"Step {step_num}: {tool_name} failed: {error_msg}")

                self.results[step_id] = {
                    "tool": tool_name,
                    "data": None,
                    "error": error_msg,
                    "duration_s": duration,
                }
                errors.append({
                    "step": step_num, "step_id": step_id,
                    "tool": tool_name, "error": error_msg,
                })

                if stop_on_error or on_error == "stop":
                    failed += 1
                    break
                elif on_error == "retry":
                    # Single retry attempt
                    try:
                        result = _raise_if_tool_failed(await handler(args))
                        parsed = _parse_tool_result(result)
                        self.results[step_id] = {
                            "tool": tool_name,
                            "data": parsed,
                            "error": None,
                            "duration_s": round(time.time() - step_start, 2),
                        }
                        completed += 1
                        errors.pop()  # Remove the error since retry succeeded
                        logger.info(f"Step {step_num}: {tool_name} succeeded on retry")
                    except Exception as retry_e:
                        retry_msg = f"Retry failed: {type(retry_e).__name__}: {retry_e}"
                        self.results[step_id]["error"] = retry_msg
                        errors[-1]["error"] = retry_msg
                        failed += 1
                else:
                    # skip: continue to next step
                    skipped += 1

        total_duration = round(time.time() - start_time, 2)

        return {
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "total": total,
            "duration_s": total_duration,
            "results": {
                sid: {
                    "tool": r["tool"],
                    "success": r["error"] is None,
                    "error": r["error"],
                    "duration_s": r.get("duration_s"),
                }
                for sid, r in self.results.items()
            },
            "errors": errors,
            "final_output_path": final_output_path,
            "workflow_graph": " -> ".join(workflow_tools),
        }


# ── Plan Adapters ──


def adapt_planner_plan(plan_json: dict) -> List[dict]:
    """Convert planner JSON (from _planner_worker_query) to executor steps.

    Planner steps have: {"tool": "...", "purpose": "...", "specialist": "..."}
    Missing: args, threading. Adds smart defaults.
    """
    raw_steps = plan_json.get("steps", [])
    if not raw_steps:
        return []

    result = []
    for i, step in enumerate(raw_steps):
        executor_step = {
            "id": f"step_{i + 1}",
            "tool": step.get("tool", ""),
            "purpose": step.get("purpose", step.get("reason", "")),
            "args": step.get("args", {}),
            "on_error": step.get("on_error", "skip"),
        }
        # Preserve explicit threading if present
        if "input_from" in step:
            executor_step["input_from"] = step["input_from"]
        if "input_key" in step:
            executor_step["input_key"] = step["input_key"]
        if "inject_as" in step:
            executor_step["inject_as"] = step["inject_as"]

        result.append(executor_step)
    return result


def adapt_strategy_plan(strategy: dict, context: Optional[dict] = None) -> List[dict]:
    """Convert strategy planner output to executor steps.

    Strategy output has: {"tool_sequence": [{"tool": "...", "reason": "..."}], ...}
    Context provides: {"collection": "...", "spatial_extent": {...}, "temporal_extent": [...]}
    """
    tool_sequence = strategy.get("tool_sequence", [])
    if not tool_sequence:
        return []

    # Extract useful info from strategy for arg injection
    strategy_context = {}
    # Strategy planner returns "collections" (plural array), extract first element.
    # Also check singular "collection" and "recommendation" sub-dict for compatibility.
    collections = strategy.get("collections") or strategy.get("recommendation", {}).get("collections")
    collection = strategy.get("collection") or strategy.get("recommendation", {}).get("collection")
    if collections and isinstance(collections, list):
        strategy_context["collection"] = collections[0]
    elif collection:
        strategy_context["collection"] = collection
    indices = strategy.get("indices") or strategy.get("recommendation", {}).get("indices")
    if indices:
        strategy_context["_indices"] = indices
    colormap = strategy.get("colormap")
    if colormap:
        strategy_context["_colormap"] = colormap

    result = []
    for i, step in enumerate(tool_sequence):
        tool_name = step.get("tool", "")
        executor_step = {
            "id": f"step_{i + 1}",
            "tool": tool_name,
            "purpose": step.get("reason", f"Execute {tool_name}"),
            "args": step.get("args", {}),
            "on_error": step.get("on_error", "skip"),
        }

        # Inject context-derived args for specific tools
        if context:
            if tool_name == "recommend_collection" and "query" not in executor_step["args"]:
                # Pass the original query if available
                if "query" in context:
                    executor_step["args"]["query"] = context["query"]
            elif tool_name == "openeo_resolve_location" and "location" not in executor_step["args"]:
                if "location" in context:
                    executor_step["args"]["location"] = context["location"]
            elif tool_name == "openeo_parse_temporal" and "expression" not in executor_step["args"]:
                if "temporal_expression" in context:
                    executor_step["args"]["expression"] = context["temporal_expression"]
            elif tool_name in GRAPH_PRODUCING_TOOLS:
                # Inject collection, bbox, temporal if available
                if "collection" in context:
                    executor_step["args"].setdefault("collection", context["collection"])
                if "spatial_extent" in context:
                    executor_step["args"].setdefault("spatial_extent", context["spatial_extent"])
                if "temporal_extent" in context:
                    executor_step["args"].setdefault("temporal_extent", context["temporal_extent"])

        # Preserve hint as description if present
        hint = step.get("hint")
        if hint and "description" not in executor_step["args"]:
            executor_step["args"]["description"] = hint

        result.append(executor_step)

    return result


# ── Tool Definition ──

WORKFLOW_EXECUTOR_TOOL_DEFINITIONS = [
    {
        "name": "execute_workflow",
        "description": (
            "Execute a multi-step EO analysis workflow with automatic result threading, "
            "validation gates, and error recovery. Pass the tool_sequence from "
            "analysis_strategy_planner or steps from the planner. Each step's output "
            "automatically feeds the next step's input (e.g., process_graph flows to "
            "create_job, job_id flows to get_results, file_path flows to inspect/viz). "
            "Use this instead of calling tools manually for complex multi-step analyses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "description": (
                        "Ordered list of steps to execute. Each step: "
                        '{"tool": "tool_name", "purpose": "description", '
                        '"args": {"key": "value"}, "on_error": "skip|retry|stop"}'
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "tool": {
                                "type": "string",
                                "description": "Name of the tool to call",
                            },
                            "purpose": {
                                "type": "string",
                                "description": "Human-readable description of this step",
                            },
                            "args": {
                                "type": "object",
                                "description": "Arguments to pass to the tool (optional — auto-threaded values fill gaps)",
                            },
                            "input_from": {
                                "type": "string",
                                "description": "Step ID to thread result from (e.g., 'step_2')",
                            },
                            "input_key": {
                                "type": "string",
                                "description": "Key from previous step's result to extract",
                            },
                            "inject_as": {
                                "type": "string",
                                "description": "Key name to inject extracted value as in this step's args",
                            },
                            "on_error": {
                                "type": "string",
                                "enum": ["skip", "retry", "stop"],
                                "description": "Error policy: skip (default), retry once, or stop workflow",
                            },
                        },
                        "required": ["tool"],
                    },
                },
                "context": {
                    "type": "object",
                    "description": (
                        "Pre-resolved context values injected into step args as defaults. "
                        "Useful for collection, spatial_extent, temporal_extent, location."
                    ),
                },
                "validate_graphs": {
                    "type": "boolean",
                    "description": "Auto-validate process graphs before job submission (default: true)",
                },
                "stop_on_error": {
                    "type": "boolean",
                    "description": "Stop on first error instead of skipping (default: false)",
                },
                "source": {
                    "type": "string",
                    "enum": ["direct", "planner", "strategy"],
                    "description": (
                        "Source format of steps. 'direct' = already executor format, "
                        "'planner' = from planner JSON, 'strategy' = from analysis_strategy_planner. "
                        "Default: 'direct'"
                    ),
                },
            },
            "required": ["steps"],
        },
    }
]


# ── Factory ──


def create_workflow_executor_tools(
    config: Any = None,
    tool_handlers: Optional[Dict[str, Callable]] = None,
) -> Dict[str, Callable]:
    """Create workflow executor tools.

    Args:
        config: OpenEOAIConfig (unused for now, reserved for future settings).
        tool_handlers: Dict of tool_name -> async handler from _create_tools().
            Required for the executor to call other tools directly.

    Returns:
        Dict mapping tool name to async handler.
    """
    if tool_handlers is None:
        tool_handlers = {}

    async def _execute_workflow(args: Dict[str, Any]) -> Dict[str, Any]:
        """Handle execute_workflow tool call."""
        steps_raw = args.get("steps", [])
        context = args.get("context")
        validate_graphs = args.get("validate_graphs", True)
        stop_on_error = args.get("stop_on_error", False)
        source = args.get("source", "direct")

        if not steps_raw:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "error": "No steps provided",
                        "completed": 0, "failed": 0, "skipped": 0, "total": 0,
                    }),
                }]
            }

        # Adapt steps based on source format
        if source == "planner":
            steps = adapt_planner_plan({"steps": steps_raw})
        elif source == "strategy":
            steps = adapt_strategy_plan(
                {"tool_sequence": steps_raw}, context=context
            )
        else:
            # Direct format — ensure step IDs
            steps = []
            for i, s in enumerate(steps_raw):
                step = dict(s)
                step.setdefault("id", f"step_{i + 1}")
                steps.append(step)

        executor = WorkflowExecutor(
            tool_handlers=tool_handlers,
            websocket=None,  # v1: no direct WS; hooks handle per-tool progress
        )

        result = await executor.execute(
            steps=steps,
            context=context,
            validate_graphs=validate_graphs,
            stop_on_error=stop_on_error,
        )

        return {
            "content": [{
                "type": "text",
                "text": json.dumps(result, default=str),
            }]
        }

    return {"execute_workflow": _execute_workflow}
