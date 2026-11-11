"""Query limits and clean endings: idle sessions, spend, and errors that won't clear on retry."""
import asyncio
import time
import weakref
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import openeo_ai.sdk.claude_sdk_bridge as bridge_module
from openeo_ai.sdk.claude_sdk_bridge import (
    ClaudeSDKBridge,
    _ModelAccessError,
    _apply_reasoning,
    _model_access_problem,
    _query_spend,
    _record_spend,
)
from openeo_ai.sdk.method_gate import forbids_execution, graph_fingerprint


class _Socket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data, **kwargs):
        self.sent.append(data)


@pytest.fixture()
def bridge():
    instance = ClaudeSDKBridge.__new__(ClaudeSDKBridge)
    instance._sessions = {}
    instance._busy_sessions = set()
    instance._user_refs, instance._project_refs, instance._prompt_refs = {}, {}, {}
    instance._prompt_refs = {}
    instance._reaper_task = None
    instance._client_costs = weakref.WeakKeyDictionary()
    return instance


class _Client:
    def __init__(self):
        self.disconnect = AsyncMock()


def _session(idle_seconds=0):
    return {"client": _Client(), "last_active": time.monotonic() - idle_seconds}


@pytest.mark.parametrize(
    "detail,expected",
    [
        ("API Error: 400 You have reached your specified API usage limits. You will regain access on 2026-12-01",
         "You will regain access on 2026-12-01"),
        ("Your credit balance is too low", "credit balance"),
        ("Prompt is too long", "too long"),
        ("authentication_error: invalid x-api-key", "credentials"),
    ],
)
def test_errors_that_recur_are_recognized(detail, expected):
    assert expected in _model_access_problem(detail)


def test_other_errors_are_left_to_normal_handling():
    assert _model_access_problem("Overloaded") is None
    assert _model_access_problem(None) is None


def test_budget_stop_is_recognized():
    assert "spending limit" in _model_access_problem(None, "error_max_budget_usd")


def test_idle_session_ends_but_a_running_one_does_not(bridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "_SDK_SESSION_TTL_SECONDS", 60)
    bridge._sessions = {"idle": _session(120), "running": _session(120), "recent": _session(5)}
    idle_client = bridge._sessions["idle"]["client"]
    bridge._busy_sessions.add("running")
    asyncio.run(bridge._cleanup_expired_sessions())
    assert set(bridge._sessions) == {"running", "recent"}
    idle_client.disconnect.assert_awaited_once()


def test_session_limit_evicts_longest_idle_and_never_a_running_one(bridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "_SDK_SESSION_MAX_COUNT", 2)
    bridge._sessions = {"oldest": _session(50), "older": _session(40), "new": _session(1), "busy": _session(99)}
    bridge._busy_sessions.add("busy")
    asyncio.run(bridge._cleanup_expired_sessions())
    assert set(bridge._sessions) == {"new", "busy"}


def test_entry_without_a_client_does_not_break_cleanup(bridge):
    bridge._sessions = {"partial": {"last_active": 0}}
    asyncio.run(bridge._cleanup_expired_sessions())
    assert bridge._sessions == {}


def test_running_totals_become_one_querys_spend():
    token = _query_spend.set({"usd": 0.0})
    holder = {}
    _record_spend(holder, SimpleNamespace(total_cost_usd=1.0))
    _record_spend(holder, SimpleNamespace(total_cost_usd=1.5))
    _record_spend({}, SimpleNamespace(total_cost_usd=2.0))
    assert _query_spend.get()["usd"] == pytest.approx(3.5)
    _query_spend.reset(token)


def test_each_client_gets_what_remains_of_the_query_limit(monkeypatch):
    monkeypatch.setenv("JONA_MAX_QUERY_USD", "10")
    token = _query_spend.set({"usd": 4.0})
    opts = SimpleNamespace(model="claude-opus-5-5", thinking=None, effort=None, max_budget_usd=None)
    _apply_reasoning(opts)
    assert opts.max_budget_usd == pytest.approx(6.0)
    _query_spend.get()["usd"] = 10.0
    with pytest.raises(_ModelAccessError):
        _apply_reasoning(opts)
    _query_spend.reset(token)


def test_bad_limit_value_keeps_the_default(monkeypatch):
    monkeypatch.setenv("JONA_MAX_QUERY_USD", "lots")
    assert bridge_module._max_query_usd() == 10.0
    monkeypatch.setenv("JONA_MAX_QUERY_USD", "-1")
    assert bridge_module._max_query_usd() == 10.0


def _run_query(bridge, prompt="hi", inner=None):
    socket = _Socket()
    bridge._query = inner or AsyncMock()

    async def scenario():
        await bridge.query(prompt, socket, "s1", auth_user_id=None)
        bridge._reaper_task.cancel()

    asyncio.run(scenario())
    return socket


def test_fatal_error_ends_the_turn_with_error_then_done(bridge):
    socket = _run_query(bridge, inner=AsyncMock(side_effect=_ModelAccessError("nope")))
    assert socket.sent == [{"type": "error", "content": "nope"}, {"type": "done"}]
    assert "s1" not in bridge._busy_sessions


def test_oversized_prompt_is_refused_before_any_model_call(bridge):
    inner = AsyncMock()
    socket = _run_query(bridge, prompt="x" * 2_000_001, inner=inner)
    inner.assert_not_awaited()
    assert "too long" in socket.sent[0]["content"]
    assert socket.sent[-1] == {"type": "done"}


def test_daily_limit_blocks_a_new_query(bridge, monkeypatch):
    monkeypatch.setenv("JONA_USER_DAILY_USD", "5")
    monkeypatch.setattr(bridge_module.budget, "user_spend_today", lambda user: 6.0)
    inner = AsyncMock()
    socket = _run_query(bridge, inner=inner)
    inner.assert_not_awaited()
    assert "usage limit" in socket.sent[0]["content"]


def test_spend_is_recorded_for_the_user(bridge, monkeypatch):
    recorded = []
    monkeypatch.setattr(bridge_module.budget, "user_spend_today", lambda user: 0.0)
    monkeypatch.setattr(bridge_module.budget, "record_llm_cost", lambda user, usd, session_id=None: recorded.append((user, usd)))

    async def inner(*args):
        _query_spend.get()["usd"] = 1.25

    socket = _Socket()
    bridge._query = inner

    async def scenario():
        await bridge.query("hi", socket, "s1", auth_user_id="u1")
        bridge._reaper_task.cancel()

    asyncio.run(scenario())
    assert recorded == [("u1", 1.25)]


def test_specialist_is_never_reaped_while_running(bridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "_SDK_SESSION_TTL_SECONDS", 1)
    monkeypatch.setattr(bridge_module, "_SDK_SESSION_MAX_COUNT", 1)
    bridge._sessions = {"s1": {"last_active": time.monotonic()}, "s1_data": _session(100), "other": _session(1)}
    bridge._busy_sessions.update({"s1", "s1_data"})
    asyncio.run(bridge._cleanup_expired_sessions())
    assert "s1_data" in bridge._sessions


def test_turn_notes_do_not_count_toward_the_session_limit(bridge, monkeypatch):
    monkeypatch.setattr(bridge_module, "_SDK_SESSION_MAX_COUNT", 1)
    bridge._sessions = {"notes": {"last_active": time.monotonic()}, "real": _session(1)}
    asyncio.run(bridge._cleanup_expired_sessions())
    assert "real" in bridge._sessions


def test_long_running_client_is_replaced_before_its_total_can_stop_a_question(bridge, monkeypatch):
    monkeypatch.setenv("JONA_MAX_QUERY_USD", "10")
    monkeypatch.setattr(bridge_module.budget, "user_spend_today", lambda user: 0.0)
    entry = _session()
    bridge._sessions["s1"] = entry
    bridge._client_costs[entry["client"]] = {"reported_cost": 6.0}
    _run_query(bridge)
    assert "client" not in bridge._sessions["s1"]
    entry["client"].disconnect.assert_awaited_once()


def test_zero_step_plan_runs_without_a_failure_notice(bridge, monkeypatch):
    socket = _Socket()
    bridge._user_result_count = lambda user: 0
    bridge._run_planner_phase = AsyncMock(return_value={"steps": [], "summary": "no tools"})
    bridge._notify_plan_fallback = AsyncMock()
    bridge._build_options = MagicMock(return_value=SimpleNamespace(model=None, thinking=None, effort=None, max_budget_usd=None, max_turns=0))
    bridge._record_llm_budget = AsyncMock()
    bridge._stream_response = AsyncMock()
    bridge._save_plan_trajectory = MagicMock()
    fake_client = SimpleNamespace(connect=AsyncMock(), query=AsyncMock())
    monkeypatch.setattr(bridge_module, "ClaudeSDKClient", lambda options: fake_client)
    asyncio.run(bridge._planner_worker_query("Count these", socket, "s1"))
    bridge._notify_plan_fallback.assert_not_awaited()
    fake_client.query.assert_awaited_once()


def test_missing_plan_still_gets_the_notice(bridge, monkeypatch):
    bridge._user_result_count = lambda user: 0
    bridge._run_planner_phase = AsyncMock(return_value=None)
    bridge._notify_plan_fallback = AsyncMock()
    bridge._build_options = MagicMock(return_value=SimpleNamespace(model=None, thinking=None, effort=None, max_budget_usd=None, max_turns=0))
    bridge._record_llm_budget = AsyncMock()
    bridge._stream_response = AsyncMock()
    bridge._save_plan_trajectory = MagicMock()
    monkeypatch.setattr(bridge_module, "ClaudeSDKClient", lambda options: SimpleNamespace(connect=AsyncMock(), query=AsyncMock()))
    asyncio.run(bridge._planner_worker_query("Count these", _Socket(), "s1"))
    bridge._notify_plan_fallback.assert_awaited_once()


@pytest.mark.parametrize(
    "prompt",
    [
        "From memory, without running anything, tell me the mean NDVI of Iowa",
        "Do not run any satellite job. Count the plots",
        "Do not create any new job. For existing job abc, report its status",
        "Without rerunning anything: what did the odd line say?",
        "don't start a job yet",
        "Don\u2019t run anything",
        "don't re-run anything",
        "without re-running",
        "don't run it again",
        "no new jobs please",
        "don't execute any processing",
        "Don't run anything until I approve the plan.",
        "Answer without running any jobs or queries.",
        "Do not run any analysis or create any jobs.",
        "Do not execute the graph.",
        "Don't run the process graph, just show it.",
        "Don't submit the job yet \u2014 just plan it.",
        "Please do not run anything on the backend.",
        "Do not create a job for this",
    ],
)
def test_requests_not_to_run_are_recognized(prompt):
    assert forbids_execution(prompt)


@pytest.mark.parametrize(
    "prompt",
    [
        "Compute NDVI for Berlin",
        "Tell me why the job did not run",
        "Do not include clouds in the analysis",
        "Run the analysis and do not stop",
        "Compute NDVI over Iowa without running out of memory",
        "Don't run jobs over 1 degree wide; map NDVI for Paris",
        "Do not run analysis on cloudy scenes, map NDVI for Lyon",
        "Use the settings from memory of the last run",
        "Don't run any job over 1 degree wide",
        "Map vegetation over Kenya but do not run any cloud masking.",
        "Show deforestation in Rondonia. Don't create any map legend.",
        "Do not run the analysis for 2020, only 2021.",
        "Don't run it for Paris; run it for Lyon.",
        "Don't run the job twice. NDVI for Lagos 2022.",
        "Without creating a new job for each month, compute monthly NDVI over Berlin.",
        "Compute NDVI over Lima 2024 and don't run anything else.",
        "Run NDVI for Lima. No new jobs after this one.",
        "I've never run a job here before. Can you compute NDVI for Paris?",
        "We don't run jobs like this often. Compute NDVI over Lima 2024.",
    ],
)
def test_ordinary_requests_may_run(prompt):
    assert not forbids_execution(prompt)


def test_identical_graphs_share_a_fingerprint_in_any_form():
    graph = {"a": {"process_id": "load_collection"}, "b": {"process_id": "save_result"}}
    assert graph_fingerprint(graph) == graph_fingerprint({"process_graph": dict(reversed(list(graph.items())))})
    assert graph_fingerprint(graph) != graph_fingerprint({"a": {"process_id": "save_result"}})


def test_dnbr_saved_before_the_fix_carries_a_caution():
    from openeo_ai.web_interface import _dnbr_sign_warning

    old = {"title": "Palisades dNBR", "created_at": "2026-07-24 10:00:00"}
    assert "reversed" in _dnbr_sign_warning(old)
    assert _dnbr_sign_warning({**old, "created_at": "2026-11-06 19:00:00"}) is None
    assert _dnbr_sign_warning({**old, "title": "Pre-Fire NBR"}) is None


def test_public_api_job_with_only_an_error_is_marked_failed(bridge, monkeypatch):
    from openeo_ai.api import router

    job = {"job_id": "j1", "query": "NDVI", "status": "queued", "user_id": "u1"}

    class RefusingBridge:
        async def query(self, prompt, websocket, **kwargs):
            await websocket.send_json({"type": "error", "content": "The AI service can't be used right now."})
            await websocket.send_json({"type": "done"})

    asyncio.run(router._execute_analysis(job, SimpleNamespace(sdk_bridge=RefusingBridge())))
    assert job["status"] == "failed"
    assert "can't be used" in job["error"]["detail"]


def test_failed_connect_leaves_no_client_for_the_next_turn(bridge):
    class FailingClient:
        def __init__(self):
            self.disconnect = AsyncMock()

        async def connect(self):
            raise RuntimeError("agent process failed to start")

    client = FailingClient()
    bridge._sessions["s1"] = {"client": client, "last_trace_id": "t1", "last_active": 0}
    with pytest.raises(RuntimeError):
        asyncio.run(bridge._connect_session_client("s1", client))
    # The next turn starts a fresh client; this turn's notes are kept.
    assert "client" not in bridge._sessions["s1"]
    assert bridge._sessions["s1"]["last_trace_id"] == "t1"
    client.disconnect.assert_awaited()


def test_stop_interrupts_a_planner_that_is_still_thinking(bridge, monkeypatch):
    class HangingPlanner:
        def __init__(self, _options):
            self.interrupted = False
            self.disconnect = AsyncMock()

        async def connect(self):
            pass

        async def query(self, _text):
            pass

        async def interrupt(self):
            self.interrupted = True

        async def receive_response(self):
            await asyncio.sleep(3600)
            yield None

    planners = []
    monkeypatch.setattr(bridge_module, "ClaudeSDKClient", lambda opts: planners.append(HangingPlanner(opts)) or planners[-1])
    monkeypatch.setattr("openeo_ai.sdk.plan_trajectories.retrieve_similar", lambda *a, **k: [])
    bridge._planner_mcp_server_config = {}
    bridge._record_llm_budget = AsyncMock()

    async def scenario():
        stop = asyncio.Event()
        socket = _Socket()
        phase = asyncio.create_task(bridge._run_planner_phase("map NDVI", socket, "s1", stop_event=stop))
        await asyncio.sleep(0.05)
        stop.set()
        plan = await asyncio.wait_for(phase, timeout=5)
        return plan, socket.sent

    plan, sent = asyncio.run(scenario())
    assert plan is None
    assert planners and planners[0].interrupted
    planners[0].disconnect.assert_awaited()
    # Its "planning" spinner is closed rather than left spinning.
    assert any(event.get("thinking_completed") for event in sent)


def test_ending_a_session_frees_its_refs():
    import asyncio

    from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge

    b = object.__new__(ClaudeSDKBridge)
    b._sessions = {"s1": {"client": None}, "s2": {}}
    b._user_refs = {"s1": {"value": "u1"}, "s2": {"value": "u2"}}
    b._project_refs = {"s1": {"value": "p"}}
    b._prompt_refs = {"s1": {"value": "q"}}

    asyncio.run(b.disconnect_session("s1"))

    assert b._user_refs == {"s2": {"value": "u2"}} and b._project_refs == {} and b._prompt_refs == {}
