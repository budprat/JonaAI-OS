# ABOUTME: Regression guard — _run_planner_phase referenced session_id without it being a param,
# ABOUTME: crashing planner + orchestrator with NameError so every complex/moderate query fell to direct.

from __future__ import annotations

import inspect

from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge


def test_run_planner_phase_declares_session_id():
    # It calls self._record_llm_budget(session_id, ...) internally, so session_id MUST be a param.
    params = inspect.signature(ClaudeSDKBridge._run_planner_phase).parameters
    assert "session_id" in params


def test_both_call_sites_forward_session_id():
    # The planner-worker and orchestrator callers both have session_id in scope and must pass it.
    for meth in (ClaudeSDKBridge._planner_worker_query, ClaudeSDKBridge._multi_agent_query):
        src = inspect.getsource(meth)
        assert "_run_planner_phase(prompt, websocket, session_id" in src, (
            f"{meth.__name__} does not forward session_id to _run_planner_phase"
        )
        assert "stop_event=stop_event" in src.split("_run_planner_phase(", 1)[1].split(")", 1)[0], (
            f"{meth.__name__} does not let a stop reach the planner phase"
        )
