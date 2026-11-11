# ABOUTME: Guards the deny-list that stops the domain agent from using generic
# ABOUTME: built-ins (Bash/Write/Task) to fabricate data instead of calling openeo_* tools.

from openeo_ai.sdk.permissions import GENERIC_BUILTIN_TOOLS_DENY


def test_fabrication_vectors_are_denied():
    # These let the model shell out / write files / spawn subagents and invent
    # results instead of calling the real openeo_* tools (observed 2026-07-08).
    for tool in ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "Task"):
        assert tool in GENERIC_BUILTIN_TOOLS_DENY, f"{tool} must be denied"


def test_filesystem_wandering_vectors_are_denied():
    # These let the worker grep/read the source tree instead of running the
    # analysis (observed 2026-07-08: "Glob **/*.py" + "Read openeo_tools.py"
    # burned 190s and computed nothing).
    for tool in ("Read", "Glob", "Grep", "LS"):
        assert tool in GENERIC_BUILTIN_TOOLS_DENY, f"{tool} must be denied"


def test_deferred_tool_loaders_stay_available():
    # ToolSearch and the MCP-resource tools load the deferred openeo_* tools —
    # denying them would break the agent entirely.
    for tool in ("ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool"):
        assert tool not in GENERIC_BUILTIN_TOOLS_DENY, f"{tool} must stay available"


def test_bridge_wires_the_deny_list_into_options():
    # The bridge must import the constant (so disallowed_tools is populated).
    from openeo_ai.sdk import claude_sdk_bridge as bridge

    assert bridge.GENERIC_BUILTIN_TOOLS_DENY is GENERIC_BUILTIN_TOOLS_DENY


def test_monitoring_intent_routes_to_complex():
    # Durable-monitoring requests must reach the Sentinel specialist (complex
    # tier), not the moderate worker that builds a one-off analysis.
    from openeo_ai.sdk.claude_sdk_bridge import _classify_query_complexity as classify

    assert classify("Set up weekly monitoring of NDVI here and alert me if it drops") == "complex"
    assert classify("Notify me when the reservoir level drops") == "complex"
    # ordinary one-off analysis stays moderate
    assert classify("Compute NDVI for Punjab in June 2024") == "moderate"


def test_sentinel_gets_routed_and_stepped():
    # Routing Sentinel is not enough — it also needs a step, or the orchestrator
    # skips it (observed 2026-07-08: "No steps for specialist 'sentinel'").
    from openeo_ai.sdk.claude_sdk_bridge import _inject_sentinel_monitoring

    routing = ["data", "viz"]
    plan = {"steps": [{"specialist": "data", "tool": "openeo_generate_graph"}]}
    injected = _inject_sentinel_monitoring(routing, plan, "set up monitoring and alert me")

    assert injected is True
    assert routing[0] == "sentinel"
    sentinel_steps = [s for s in plan["steps"] if s.get("specialist") == "sentinel"]
    assert len(sentinel_steps) == 1
    assert sentinel_steps[0]["tool"] == "monitor_create_watch"


def test_sentinel_injection_is_noop_for_oneoff_analysis():
    from openeo_ai.sdk.claude_sdk_bridge import _inject_sentinel_monitoring

    routing = ["data", "viz"]
    plan = {"steps": []}
    assert _inject_sentinel_monitoring(routing, plan, "compute ndvi for punjab june 2024") is False
    assert "sentinel" not in routing
    assert plan["steps"] == []
    # idempotent when sentinel already present
    routing2 = ["sentinel", "data"]
    assert _inject_sentinel_monitoring(routing2, {"steps": []}, "set up monitoring") is False


def test_sentinel_injection_synthesizes_plan_when_orchestrator_returns_nothing():
    # The no-plan fallback path passes an empty routing and a plan with no steps
    # (setdefault-created). The helper must still route + step Sentinel.
    from openeo_ai.sdk.claude_sdk_bridge import _inject_sentinel_monitoring

    routing = []
    plan = {}
    plan.setdefault("steps", [])
    assert _inject_sentinel_monitoring(routing, plan, "set up weekly monitoring, alert me") is True
    assert routing == ["sentinel"]
    assert any(s.get("specialist") == "sentinel" for s in plan["steps"])
