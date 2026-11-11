# ABOUTME: Unit tests for the ReAct workflow executor tool.
# Tests step execution, auto-threading, adapters, error handling, and end-to-end flows.

"""
Unit tests for openeo_ai.tools.workflow_executor.

Tests:
- Plan adapters (planner JSON, strategy output)
- Auto-threading rules (result key resolution)
- WorkflowExecutor engine (linear flow, errors, validation gates)
- End-to-end workflow scenarios

Uses plain async handler functions to test the executor's orchestration
without requiring the OpenEO backend or network calls.
"""

import json
import pytest

from openeo_ai.tools.workflow_executor import (
    AUTO_THREAD_RULES,
    WorkflowExecutor,
    adapt_planner_plan,
    adapt_strategy_plan,
    create_workflow_executor_tools,
    _parse_tool_result,
    _find_threaded_value,
    WORKFLOW_EXECUTOR_TOOL_DEFINITIONS,
)


# ── Test Helpers ──
# Plain async functions returning MCP-format results.
# These exercise the executor's orchestration logic directly.


def _mcp_result(data: dict) -> dict:
    """Create an MCP-format tool result wrapping JSON data."""
    return {"content": [{"type": "text", "text": json.dumps(data)}]}


def _handler_returning(result_data: dict):
    """Create an async handler that returns MCP-format result and records calls."""
    calls = []

    async def handler(args):
        calls.append(dict(args))
        return _mcp_result(result_data)

    handler.calls = calls
    return handler


def _handler_raising(error_msg: str = "Something went wrong"):
    """Create an async handler that raises RuntimeError and records calls."""
    calls = []

    async def handler(args):
        calls.append(dict(args))
        raise RuntimeError(error_msg)

    handler.calls = calls
    return handler


class RecordingWebSocket:
    """WebSocket that records sent messages for assertion."""

    def __init__(self):
        self.messages = []

    async def send_text(self, text: str):
        self.messages.append(json.loads(text))


# ============================================================================
# Test: _parse_tool_result
# ============================================================================

class TestParseToolResult:
    """Tests for MCP result parsing."""

    def test_parse_mcp_json_result(self):
        result = _mcp_result({"graph": {"load": {}}, "id": "job-123"})
        parsed = _parse_tool_result(result)
        assert parsed == {"graph": {"load": {}}, "id": "job-123"}

    def test_parse_mcp_text_result(self):
        result = {"content": [{"type": "text", "text": "Some plain text"}]}
        parsed = _parse_tool_result(result)
        assert parsed == {"raw_text": "Some plain text"}

    def test_parse_direct_dict(self):
        result = {"graph": {"load": {}}}
        parsed = _parse_tool_result(result)
        assert parsed == {"graph": {"load": {}}}

    def test_parse_none(self):
        assert _parse_tool_result(None) is None

    def test_parse_string_json(self):
        result = json.dumps({"key": "value"})
        parsed = _parse_tool_result(result)
        assert parsed == {"key": "value"}

    def test_parse_plain_string(self):
        parsed = _parse_tool_result("not json")
        assert parsed == {"raw_text": "not json"}


# ============================================================================
# Test: adapt_planner_plan
# ============================================================================

class TestAdaptPlannerPlan:
    """Tests for planner JSON to executor step conversion."""

    def test_basic_conversion(self):
        plan = {
            "steps": [
                {"tool": "openeo_generate_graph", "purpose": "Generate NDVI graph"},
                {"tool": "openeo_create_job", "purpose": "Submit job"},
                {"tool": "openeo_get_results", "purpose": "Download results"},
            ]
        }
        steps = adapt_planner_plan(plan)
        assert len(steps) == 3
        assert steps[0]["tool"] == "openeo_generate_graph"
        assert steps[0]["id"] == "step_1"
        assert steps[0]["purpose"] == "Generate NDVI graph"
        assert steps[1]["id"] == "step_2"
        assert steps[2]["id"] == "step_3"

    def test_preserves_explicit_threading(self):
        plan = {
            "steps": [
                {"tool": "openeo_generate_graph", "purpose": "Gen graph"},
                {
                    "tool": "openeo_create_job",
                    "purpose": "Submit",
                    "input_from": "step_1",
                    "input_key": "graph",
                    "inject_as": "process_graph",
                },
            ]
        }
        steps = adapt_planner_plan(plan)
        assert steps[1]["input_from"] == "step_1"
        assert steps[1]["input_key"] == "graph"
        assert steps[1]["inject_as"] == "process_graph"

    def test_empty_plan(self):
        assert adapt_planner_plan({}) == []
        assert adapt_planner_plan({"steps": []}) == []

    def test_handles_missing_fields(self):
        plan = {"steps": [{"tool": "openeo_generate_graph"}]}
        steps = adapt_planner_plan(plan)
        assert steps[0]["purpose"] == ""
        assert steps[0]["args"] == {}
        assert steps[0]["on_error"] == "skip"


# ============================================================================
# Test: adapt_strategy_plan
# ============================================================================

class TestAdaptStrategyPlan:
    """Tests for strategy planner output to executor step conversion."""

    def test_basic_conversion(self):
        strategy = {
            "tool_sequence": [
                {"tool": "recommend_collection", "reason": "Pick best collection"},
                {"tool": "openeo_generate_graph", "reason": "Generate NDVI graph"},
                {"tool": "openeo_create_job", "reason": "Submit batch job"},
            ]
        }
        steps = adapt_strategy_plan(strategy)
        assert len(steps) == 3
        assert steps[0]["tool"] == "recommend_collection"
        assert steps[0]["purpose"] == "Pick best collection"

    def test_injects_context(self):
        strategy = {
            "tool_sequence": [
                {"tool": "openeo_resolve_location", "reason": "Geocode"},
                {"tool": "openeo_generate_graph", "reason": "Generate graph"},
            ]
        }
        context = {
            "location": "Mumbai",
            "collection": "sentinel-2-l2a",
            "spatial_extent": {"west": 72, "south": 18, "east": 73, "north": 20},
            "temporal_extent": ["2024-01-01", "2024-06-01"],
        }
        steps = adapt_strategy_plan(strategy, context=context)

        # Location should be injected into resolve_location
        assert steps[0]["args"]["location"] == "Mumbai"

        # Collection/spatial/temporal into graph generator
        assert steps[1]["args"]["collection"] == "sentinel-2-l2a"
        assert steps[1]["args"]["spatial_extent"]["west"] == 72

    def test_empty_strategy(self):
        assert adapt_strategy_plan({}) == []
        assert adapt_strategy_plan({"tool_sequence": []}) == []

    def test_preserves_hint_as_description(self):
        strategy = {
            "tool_sequence": [
                {"tool": "openeo_generate_graph", "reason": "Gen", "hint": "ndvi"},
            ]
        }
        steps = adapt_strategy_plan(strategy)
        assert steps[0]["args"]["description"] == "ndvi"


# ============================================================================
# Test: Auto-Threading (_find_threaded_value)
# ============================================================================

class TestAutoThreading:
    """Tests for auto-threading rule resolution."""

    def test_create_job_gets_graph(self):
        results = {
            "step_1": {
                "tool": "openeo_generate_graph",
                "data": {"graph": {"load": {}, "save": {}}},
            }
        }
        order = ["step_1"]
        rules = AUTO_THREAD_RULES["openeo_create_job"]
        threaded = _find_threaded_value(results, order, rules)
        assert threaded is not None
        assert "process_graph" in threaded
        assert threaded["process_graph"] == {"load": {}, "save": {}}

    def test_get_results_gets_job_id(self):
        results = {
            "step_1": {
                "tool": "openeo_generate_graph",
                "data": {"graph": {"load": {}}},
            },
            "step_2": {
                "tool": "openeo_create_job",
                "data": {"id": "job-abc-123", "status": "created"},
            },
        }
        order = ["step_1", "step_2"]
        rules = AUTO_THREAD_RULES["openeo_get_results"]
        threaded = _find_threaded_value(results, order, rules)
        assert threaded == {"job_id": "job-abc-123"}

    def test_inspect_gets_file_path(self):
        results = {
            "step_3": {
                "tool": "openeo_get_results",
                "data": {"output_path": "/tmp/openeo_results/result.tif"},
            },
        }
        order = ["step_3"]
        rules = AUTO_THREAD_RULES["result_inspect"]
        threaded = _find_threaded_value(results, order, rules)
        assert threaded == {"file_path": "/tmp/openeo_results/result.tif"}

    def test_viz_gets_file_path(self):
        results = {
            "step_3": {
                "tool": "openeo_get_results",
                "data": {"output_path": "/tmp/result.tif"},
            },
        }
        order = ["step_3"]
        rules = AUTO_THREAD_RULES["viz_show_map"]
        threaded = _find_threaded_value(results, order, rules)
        assert threaded == {"file_path": "/tmp/result.tif"}

    def test_no_match_returns_none(self):
        results = {
            "step_1": {"tool": "openeo_list_collections", "data": {"collections": []}},
        }
        order = ["step_1"]
        rules = AUTO_THREAD_RULES["openeo_create_job"]
        threaded = _find_threaded_value(results, order, rules)
        assert threaded is None

    def test_source_tool_filter(self):
        """get_results should only take job_id from create_job, not any step with 'id'."""
        results = {
            "step_1": {
                "tool": "openeo_generate_graph",
                "data": {"id": "graph-id-not-job"},
            },
            "step_2": {
                "tool": "openeo_create_job",
                "data": {"id": "job-real-123"},
            },
        }
        order = ["step_1", "step_2"]
        rules = AUTO_THREAD_RULES["openeo_get_results"]
        threaded = _find_threaded_value(results, order, rules)
        assert threaded == {"job_id": "job-real-123"}


# ============================================================================
# Test: WorkflowExecutor
# ============================================================================

class TestWorkflowExecutor:
    """Tests for the WorkflowExecutor engine."""

    @pytest.mark.asyncio
    async def test_linear_workflow(self):
        """Execute 3 steps linearly, all succeed."""
        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {"load": {}, "save": {}}}),
            "openeo_create_job": _handler_returning({"id": "job-001", "status": "created"}),
            "openeo_get_results": _handler_returning({"output_path": "/tmp/result.tif"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "step_1", "tool": "openeo_generate_graph", "purpose": "Gen graph"},
            {"id": "step_2", "tool": "openeo_create_job", "purpose": "Submit job"},
            {"id": "step_3", "tool": "openeo_get_results", "purpose": "Download"},
        ]

        result = await executor.execute(steps, validate_graphs=False)

        assert result["completed"] == 3
        assert result["failed"] == 0
        assert result["skipped"] == 0
        assert result["total"] == 3
        assert result["final_output_path"] == "/tmp/result.tif"
        assert result["workflow_graph"] == "openeo_generate_graph -> openeo_create_job -> openeo_get_results"

    @pytest.mark.asyncio
    async def test_auto_threading_in_execution(self):
        """Verify create_job receives graph from generate_graph via auto-threading."""
        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {"load_collection": {}}}),
            "openeo_create_job": _handler_returning({"id": "job-002"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "step_1", "tool": "openeo_generate_graph", "purpose": "Gen"},
            {"id": "step_2", "tool": "openeo_create_job", "purpose": "Submit"},
        ]

        await executor.execute(steps, validate_graphs=False)

        # Verify create_job was called with process_graph from generate_graph
        assert len(handlers["openeo_create_job"].calls) == 1
        call_args = handlers["openeo_create_job"].calls[0]
        assert "process_graph" in call_args
        assert call_args["process_graph"] == {"load_collection": {}}

    @pytest.mark.asyncio
    async def test_error_skip_policy(self):
        """Step 2 fails with skip policy - step 3 still executes."""
        handlers = {
            "step_a": _handler_returning({"result": "ok"}),
            "step_b": _handler_raising("boom"),
            "step_c": _handler_returning({"output_path": "/tmp/out.tif"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "step_a", "purpose": "A", "on_error": "skip"},
            {"id": "s2", "tool": "step_b", "purpose": "B", "on_error": "skip"},
            {"id": "s3", "tool": "step_c", "purpose": "C"},
        ]

        result = await executor.execute(steps, validate_graphs=False)
        assert result["completed"] == 2
        assert result["skipped"] == 1
        assert result["failed"] == 0
        assert len(result["errors"]) == 1
        assert result["errors"][0]["tool"] == "step_b"

    @pytest.mark.asyncio
    async def test_error_stop_policy(self):
        """Step 2 fails with stop_on_error - step 3 never runs."""
        handlers = {
            "step_a": _handler_returning({"result": "ok"}),
            "step_b": _handler_raising("critical failure"),
            "step_c": _handler_returning({"result": "should not run"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "step_a", "purpose": "A"},
            {"id": "s2", "tool": "step_b", "purpose": "B"},
            {"id": "s3", "tool": "step_c", "purpose": "C"},
        ]

        result = await executor.execute(steps, validate_graphs=False, stop_on_error=True)
        assert result["completed"] == 1
        assert result["failed"] == 1
        assert len(handlers["step_c"].calls) == 0

    @pytest.mark.asyncio
    async def test_error_retry_policy(self):
        """Step fails first, succeeds on retry."""
        call_count = 0

        async def flaky_handler(args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient error")
            return _mcp_result({"result": "ok"})

        handlers = {"flaky_tool": flaky_handler}
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "flaky_tool", "purpose": "Retry test", "on_error": "retry"},
        ]

        result = await executor.execute(steps, validate_graphs=False)
        assert result["completed"] == 1
        assert result["failed"] == 0
        assert len(result["errors"]) == 0
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_unknown_tool_skipped(self):
        """Unknown tool name is skipped gracefully."""
        handlers = {"known_tool": _handler_returning({"ok": True})}
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "nonexistent_tool", "purpose": "Bad tool"},
            {"id": "s2", "tool": "known_tool", "purpose": "Good tool"},
        ]

        result = await executor.execute(steps, validate_graphs=False)
        assert result["completed"] == 1
        assert result["skipped"] == 1
        assert result["errors"][0]["error"] == "Unknown tool: nonexistent_tool"

    @pytest.mark.asyncio
    async def test_empty_steps(self):
        """Empty step list completes immediately."""
        executor = WorkflowExecutor(tool_handlers={})
        result = await executor.execute([], validate_graphs=False)
        assert result["completed"] == 0
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_validation_gate(self):
        """Validation gate runs before openeo_create_job."""
        validation_called = False

        async def validator(args):
            nonlocal validation_called
            validation_called = True
            return _mcp_result({"valid": True, "errors": []})

        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {"load": {}}}),
            "graph_validate_full": validator,
            "openeo_create_job": _handler_returning({"id": "job-v1"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "openeo_generate_graph", "purpose": "Gen"},
            {"id": "s2", "tool": "openeo_create_job", "purpose": "Submit"},
        ]

        result = await executor.execute(steps, validate_graphs=True)
        assert validation_called
        assert result["completed"] == 2

    @pytest.mark.asyncio
    async def test_validation_gate_failure_warns_but_continues(self):
        """Validation gate failure produces warning but doesn't block submission."""
        async def failing_validator(args):
            return _mcp_result({"ready_to_submit": False, "issues": ["Missing save_result node"]})

        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {"load": {}}}),
            "graph_validate_full": failing_validator,
            "openeo_create_job": _handler_returning({"id": "job-v2"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "openeo_generate_graph", "purpose": "Gen"},
            {"id": "s2", "tool": "openeo_create_job", "purpose": "Submit"},
        ]

        result = await executor.execute(steps, validate_graphs=True)
        # Validation error is logged but job still submitted
        assert result["completed"] == 2
        assert any("graph_validate_full" in e.get("tool", "") for e in result["errors"])

    @pytest.mark.asyncio
    async def test_context_injection(self):
        """Context values are injected as defaults into step args."""
        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {"load": {}}}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "openeo_generate_graph", "purpose": "Gen", "args": {"description": "ndvi"}},
        ]
        context = {"collection": "sentinel-2-l2a", "cloud_cover": True}

        await executor.execute(steps, context=context, validate_graphs=False)

        call_args = handlers["openeo_generate_graph"].calls[0]
        assert call_args["description"] == "ndvi"  # Explicit arg preserved
        assert call_args["collection"] == "sentinel-2-l2a"  # Context injected
        assert call_args["cloud_cover"] is True

    @pytest.mark.asyncio
    async def test_explicit_args_override_context(self):
        """Explicit step args take priority over context."""
        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {}}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "openeo_generate_graph", "purpose": "Gen",
             "args": {"collection": "landsat-8"}},
        ]
        context = {"collection": "sentinel-2-l2a"}

        await executor.execute(steps, context=context, validate_graphs=False)

        call_args = handlers["openeo_generate_graph"].calls[0]
        assert call_args["collection"] == "landsat-8"  # Explicit wins

    @pytest.mark.asyncio
    async def test_websocket_progress(self):
        """Progress messages sent via WebSocket."""
        ws = RecordingWebSocket()

        handlers = {"test_tool": _handler_returning({"ok": True})}
        executor = WorkflowExecutor(tool_handlers=handlers, websocket=ws)

        steps = [{"id": "s1", "tool": "test_tool", "purpose": "Test step"}]
        await executor.execute(steps, validate_graphs=False)

        assert len(ws.messages) == 1
        msg = ws.messages[0]
        assert msg["type"] == "workflow_step"
        assert msg["step"] == 1
        assert msg["total"] == 1
        assert msg["tool"] == "test_tool"


# ============================================================================
# Test: End-to-End Workflows
# ============================================================================

class TestEndToEnd:
    """End-to-end workflow tests with realistic step sequences."""

    @pytest.mark.asyncio
    async def test_ndvi_workflow(self):
        """Full NDVI workflow: generate -> create -> get_results -> inspect -> viz."""
        handlers = {
            "openeo_generate_graph": _handler_returning({
                "graph": {"load": {}, "ndvi": {}, "save": {}},
            }),
            "openeo_create_job": _handler_returning({"id": "job-ndvi-001", "status": "created"}),
            "openeo_get_results": _handler_returning({
                "output_path": "/tmp/openeo_results/ndvi.tif",
                "format": "GTiff",
            }),
            "result_inspect": _handler_returning({
                "bands": 1, "crs": "EPSG:4326",
                "stats": {"band_1": {"min": -0.2, "max": 0.9, "mean": 0.45}},
            }),
            "viz_show_map": _handler_returning({"map_url": "/render/ndvi.png"}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "openeo_generate_graph", "purpose": "NDVI graph",
             "args": {"description": "ndvi", "collection": "sentinel-2-l2a"}},
            {"id": "s2", "tool": "openeo_create_job", "purpose": "Submit"},
            {"id": "s3", "tool": "openeo_get_results", "purpose": "Download"},
            {"id": "s4", "tool": "result_inspect", "purpose": "Inspect raster"},
            {"id": "s5", "tool": "viz_show_map", "purpose": "Visualize on map"},
        ]

        result = await executor.execute(steps, validate_graphs=False)
        assert result["completed"] == 5
        assert result["failed"] == 0
        assert result["final_output_path"] is not None

        # Verify auto-threading worked
        create_args = handlers["openeo_create_job"].calls[0]
        assert "process_graph" in create_args

        inspect_args = handlers["result_inspect"].calls[0]
        assert inspect_args["file_path"] == "/tmp/openeo_results/ndvi.tif"

        viz_args = handlers["viz_show_map"].calls[0]
        assert viz_args["file_path"] == "/tmp/openeo_results/ndvi.tif"

    @pytest.mark.asyncio
    async def test_strategy_plan_execution(self):
        """Execute workflow from strategy planner output via adapter."""
        strategy = {
            "tool_sequence": [
                {"tool": "recommend_collection", "reason": "Find best collection"},
                {"tool": "openeo_generate_graph", "reason": "Generate NDVI", "hint": "ndvi"},
                {"tool": "openeo_create_job", "reason": "Submit"},
                {"tool": "openeo_get_results", "reason": "Download"},
            ]
        }
        context = {
            "query": "NDVI analysis of Mumbai",
            "collection": "sentinel-2-l2a",
        }

        steps = adapt_strategy_plan(strategy, context=context)
        assert len(steps) == 4
        assert steps[0]["tool"] == "recommend_collection"
        assert steps[0]["args"]["query"] == "NDVI analysis of Mumbai"
        assert steps[1]["args"]["description"] == "ndvi"
        assert steps[1]["args"]["collection"] == "sentinel-2-l2a"

    @pytest.mark.asyncio
    async def test_partial_failure_recovers(self):
        """Workflow continues after a mid-step failure."""
        handlers = {
            "openeo_generate_graph": _handler_returning({"graph": {"load": {}}}),
            "openeo_create_job": _handler_returning({"id": "job-pf"}),
            "openeo_get_results": _handler_raising("Job failed: OOM"),
            "result_inspect": _handler_returning({"stats": {}}),
        }
        executor = WorkflowExecutor(tool_handlers=handlers)

        steps = [
            {"id": "s1", "tool": "openeo_generate_graph", "purpose": "Gen"},
            {"id": "s2", "tool": "openeo_create_job", "purpose": "Submit"},
            {"id": "s3", "tool": "openeo_get_results", "purpose": "Download", "on_error": "skip"},
            {"id": "s4", "tool": "result_inspect", "purpose": "Inspect"},
        ]

        result = await executor.execute(steps, validate_graphs=False)
        assert result["completed"] == 3  # s1, s2, s4
        assert result["skipped"] == 1   # s3
        assert len(result["errors"]) == 1


# ============================================================================
# Test: Tool Definition & Factory
# ============================================================================

class TestToolDefinitionAndFactory:
    """Tests for tool registration and factory function."""

    def test_tool_definition_exists(self):
        assert len(WORKFLOW_EXECUTOR_TOOL_DEFINITIONS) == 1
        defn = WORKFLOW_EXECUTOR_TOOL_DEFINITIONS[0]
        assert defn["name"] == "execute_workflow"
        assert "input_schema" in defn
        assert "steps" in defn["input_schema"]["properties"]

    def test_factory_returns_handler(self):
        tools = create_workflow_executor_tools(config=None, tool_handlers={})
        assert "execute_workflow" in tools
        assert callable(tools["execute_workflow"])

    @pytest.mark.asyncio
    async def test_factory_handler_empty_steps(self):
        tools = create_workflow_executor_tools(config=None, tool_handlers={})
        result = await tools["execute_workflow"]({"steps": []})
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["error"] == "No steps provided"

    @pytest.mark.asyncio
    async def test_factory_handler_executes_steps(self):
        inner_handlers = {
            "test_tool": _handler_returning({"result": "done"}),
        }
        tools = create_workflow_executor_tools(config=None, tool_handlers=inner_handlers)

        result = await tools["execute_workflow"]({
            "steps": [{"tool": "test_tool", "purpose": "Test"}],
        })
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["completed"] == 1
        assert parsed["total"] == 1

    @pytest.mark.asyncio
    async def test_factory_handler_strategy_source(self):
        """Factory handler respects source='strategy' adapter."""
        inner_handlers = {
            "recommend_collection": _handler_returning({"collection": "sentinel-2-l2a"}),
        }
        tools = create_workflow_executor_tools(config=None, tool_handlers=inner_handlers)

        result = await tools["execute_workflow"]({
            "steps": [{"tool": "recommend_collection", "reason": "Pick collection"}],
            "source": "strategy",
            "context": {"query": "NDVI in Mumbai"},
        })
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["completed"] == 1
