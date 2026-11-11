# ABOUTME: Per-turn artifact accumulators (viz types, graph processes), the
# ABOUTME: method/deliverable completion contracts, enforcement notices, and the
# change-detection plan injector + prompt pins.

import inspect
import json
import time
from collections import Counter

import pytest
from claude_agent_sdk.types import ResultMessage, StreamEvent

from openeo_ai.sdk import claude_sdk_bridge as bridge_mod
from openeo_ai.sdk.claude_sdk_bridge import (
    ClaudeSDKBridge,
    _class_table_payload,
    _deliverable_gap_payload,
    _grounding_enforce_mode,
    _inject_change_detection,
    _method_gap_payload,
)
from openeo_ai.sdk.client import OpenEOAIConfig
from openeo_ai.sdk.system_prompts import PLANNER_SYSTEM_PROMPT, SYSTEM_PROMPT


class _RecorderWS:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def _bridge_and_hook(ws):
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    opts = bridge._build_options(
        ws_ref={"ws": ws},
        thinking_ids={"tool-1": "thinking-1"},
        session_tools_used=[],
        prompt="ndvi analysis",
    )
    return bridge, opts.hooks["PostToolUse"][0].hooks[0]


def _job_result_input(tool_name="mcp__openeo__openeo_create_job", tool_use_id="tool-1"):
    return {
        "tool_name": tool_name,
        "tool_response": {
            "content": [{
                "type": "text",
                "text": '{"id": "job-1", "status": "finished", '
                        '"output_path": "/tmp/openeo_results/results/job-1/result.tif"}',
            }]
        },
        "tool_use_id": tool_use_id,
    }


# --- Per-turn accumulators -------------------------------------------------


@pytest.mark.asyncio
async def test_viz_types_recorded_on_emission(monkeypatch):
    ws = _RecorderWS()
    bridge, hook = _bridge_and_hook(ws)
    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization",
        lambda *a, **k: [{"type": "map", "data": {"url": "/render"}}],
    )
    await hook(_job_result_input(), None, None)
    assert bridge._sessions["default"]["turn_viz_types"] == ["map"]


@pytest.mark.asyncio
async def test_graph_processes_recorded_from_result_payload(monkeypatch):
    ws = _RecorderWS()
    bridge, hook = _bridge_and_hook(ws)
    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization", lambda *a, **k: []
    )
    graph = {
        "load1": {"process_id": "load_collection", "arguments": {}},
        "merge1": {"process_id": "merge_cubes", "arguments": {}},
    }
    await hook(
        {
            "tool_name": "mcp__openeo__openeo_generate_graph",
            "tool_response": {"content": [{"type": "text", "text": json.dumps(graph)}]},
            "tool_use_id": "tool-3",
        },
        None,
        None,
    )
    procs = bridge._sessions["default"]["turn_graph_processes"]
    assert "merge_cubes" in procs and "load_collection" in procs


@pytest.mark.asyncio
async def test_graph_processes_recorded_from_tool_input(monkeypatch):
    ws = _RecorderWS()
    bridge, hook = _bridge_and_hook(ws)
    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization", lambda *a, **k: []
    )
    graph = {"sub1": {"process_id": "subtract", "arguments": {}}}
    payload = _job_result_input(tool_use_id="tool-4")
    payload["tool_input"] = {"process": {"process_graph": graph}}
    await hook(payload, None, None)
    assert "subtract" in bridge._sessions["default"]["turn_graph_processes"]


@pytest.mark.asyncio
async def test_accumulators_cleared_at_query_turn_start(monkeypatch):
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    ws = _RecorderWS()

    class _IdleClient:
        async def query(self, prompt, session_id=None):
            return None

    bridge._sessions["s1"] = {
        "client": _IdleClient(),
        "ws_ref": {"ws": ws},
        "thinking_ids": {},
        "tools_used": [],
        "tool_call_counts": Counter(),
        "last_active": time.monotonic(),
        "turn_viz_types": ["map", "chart"],
        "turn_graph_processes": ["merge_cubes"],
        "turn_response_texts": ["prose from the previous turn"],
    }

    async def _no_stream(*a, **k):
        return None

    monkeypatch.setattr(bridge, "_stream_response", _no_stream)
    await bridge.query("show ndvi", ws, session_id="s1")
    assert bridge._sessions["s1"]["turn_viz_types"] == []
    assert bridge._sessions["s1"]["turn_graph_processes"] == []
    assert bridge._sessions["s1"]["turn_response_texts"] == []


# --- Completion contracts ---------------------------------------------------


def test_method_gap_fires_on_change_prompt_with_jobs_but_no_difference():
    payload = _method_gap_payload(
        "How did NDVI change between 2020 and 2024 in Punjab?",
        [],
        ["openeo_generate_graph", "openeo_create_job"],
    )
    assert payload is not None
    assert payload["type"] == "method_gap"


def test_method_gap_silent_when_merge_cubes_present():
    assert _method_gap_payload(
        "How did NDVI change between 2020 and 2024 in Punjab?",
        ["load_collection", "merge_cubes"],
        ["openeo_generate_graph"],
    ) is None


def test_method_gap_silent_on_no_tool_turns():
    assert _method_gap_payload(
        "How did NDVI change between 2020 and 2024 in Punjab?", [], []
    ) is None


def test_method_gap_silent_without_change_verb():
    assert _method_gap_payload(
        "show me ndvi for june 2024", [], ["openeo_create_job"]
    ) is None


def test_deliverable_gap_fires_on_map_request_without_map_viz():
    payload = _deliverable_gap_payload(
        "Map the burn severity of the Palisades fire",
        ["chart"],
        ["openeo_algorithm"],
    )
    assert payload is not None
    assert payload["type"] == "deliverable_gap"


def test_deliverable_gap_silent_when_map_emitted():
    assert _deliverable_gap_payload(
        "Map the burn severity of the Palisades fire",
        ["map"],
        ["openeo_algorithm"],
    ) is None


def test_deliverable_gap_silent_when_no_tools_ran():
    assert _deliverable_gap_payload(
        "Map the burn severity of the Palisades fire", [], []
    ) is None


# --- Enforcement -------------------------------------------------------------


def _contract_bridge():
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    bridge._sessions["s1"] = {
        "turn_viz_types": ["chart"],
        "turn_graph_processes": [],
        "turn_tool_results": [],
    }
    return bridge


@pytest.mark.asyncio
async def test_annotate_default_appends_exactly_one_notice_chunk(monkeypatch):
    monkeypatch.delenv("JONA_GROUNDING_ENFORCE", raising=False)
    assert _grounding_enforce_mode() == "annotate"
    ws = _RecorderWS()
    bridge = _contract_bridge()
    await bridge._emit_completion_contracts(
        ws, "s1",
        "Map the burn severity change between 2020 and 2024",
        ["openeo_generate_graph"],
        "The area changed a lot.",
    )
    types = [m["type"] for m in ws.messages]
    assert "method_gap" in types and "deliverable_gap" in types
    deltas = [m for m in ws.messages if m["type"] == "text_delta"]
    assert len(deltas) == 1
    assert "Note:" in deltas[0]["content"]


@pytest.mark.asyncio
async def test_observe_mode_emits_events_but_no_notice(monkeypatch):
    monkeypatch.setenv("JONA_GROUNDING_ENFORCE", "observe")
    ws = _RecorderWS()
    bridge = _contract_bridge()
    await bridge._emit_completion_contracts(
        ws, "s1",
        "Map the burn severity change between 2020 and 2024",
        ["openeo_generate_graph"],
        "The area changed a lot.",
    )
    types = [m["type"] for m in ws.messages]
    assert "method_gap" in types and "deliverable_gap" in types
    assert not [m for m in ws.messages if m["type"] == "text_delta"]


@pytest.mark.asyncio
async def test_enforcement_never_raises_when_internals_throw(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(bridge_mod, "_method_gap_payload", _boom)
    monkeypatch.setattr(bridge_mod, "_deliverable_gap_payload", _boom)
    monkeypatch.setattr(bridge_mod, "_grounding_payload", _boom)
    monkeypatch.setattr(bridge_mod, "_class_table_payload", _boom)
    ws = _RecorderWS()
    bridge = _contract_bridge()
    await bridge._emit_completion_contracts(
        ws, "s1", "map the change", ["openeo_create_job"], "text"
    )  # must not raise
    types = [m["type"] for m in ws.messages]
    assert "method_gap" not in types and "deliverable_gap" not in types
    assert "class_table_gap" not in types


# --- Deterministic change-detection injector ----------------------------------


def test_change_detection_injector_fires_on_two_period_burn_prompt():
    routing = ["data"]
    plan = {"steps": [
        {"specialist": "data", "tool": "openeo_generate_graph",
         "args": {"analysis_type": "nbr"}, "purpose": "snapshot NBR"},
    ]}
    prompt = ("map the burn severity of the palisades fire — compare january 2025 "
              "before the fire vs february 2025 after the fire")
    assert _inject_change_detection(routing, plan, prompt) is True
    tools = [s.get("tool") for s in plan["steps"]]
    assert "openeo_algorithm" in tools
    assert "threshold_classify" in tools
    algo_step = next(s for s in plan["steps"] if s.get("tool") == "openeo_algorithm")
    assert "burn_severity" in str(algo_step)
    assert "ml" in routing


def test_change_detection_injector_silent_on_snapshot_query():
    routing = ["data"]
    plan = {"steps": []}
    assert _inject_change_detection(routing, plan, "show me ndvi for june 2024") is False
    assert plan["steps"] == []


def test_change_detection_injector_silent_when_difference_job_already_planned():
    routing = ["data"]
    plan = {"steps": [
        {"specialist": "data", "tool": "openeo_algorithm",
         "args": {"algorithm": "burn_severity"}, "purpose": "dNBR difference raster"},
    ]}
    prompt = "compare burn severity before the fire vs after the fire"
    assert _inject_change_detection(routing, plan, prompt) is False
    assert len(plan["steps"]) == 1


# --- Prompt pins ------------------------------------------------------------


def test_planner_prompt_pins_difference_raster_rule():
    assert "MUST be planned as ONE difference-raster job" in PLANNER_SYSTEM_PROMPT
    assert "NEVER two snapshot jobs" in PLANNER_SYSTEM_PROMPT
    assert "burn_severity" in PLANNER_SYSTEM_PROMPT


def test_planner_prompt_pins_class_table_rule():
    assert "MUST come from threshold_classify output" in PLANNER_SYSTEM_PROMPT
    assert "NEVER computed in prose" in PLANNER_SYSTEM_PROMPT


def test_system_prompt_pins_per_class_table_clause():
    assert "PER-CLASS TABLE RULE" in SYSTEM_PROMPT
    # Stays inside the single DATA-INTEGRITY block.
    assert SYSTEM_PROMPT.count("=== ABSOLUTE RULE — DATA INTEGRITY ===") == 1
    _block = SYSTEM_PROMPT.split("=== ABSOLUTE RULE — DATA INTEGRITY ===", 1)[1]
    assert "PER-CLASS TABLE RULE" in _block


# --- Review fixes 2026-07-24: contracts on the complex tier + false positives -------


@pytest.mark.asyncio
async def test_turn_tool_names_recorded_on_tool_use(monkeypatch):
    """The complex tier reads turn_tool_names (bare names) for the method
    contract — the per-stream tools_used only covers one specialist."""
    ws = _RecorderWS()
    bridge, hook = _bridge_and_hook(ws)
    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization", lambda *a, **k: [],
    )
    await hook(_job_result_input(), None, None)
    names = bridge._sessions["default"]["turn_tool_names"]
    assert "openeo_create_job" in names  # MCP prefix stripped, matches _OPENEO_JOB_TOOLS


def test_complex_tier_finalize_wires_completion_contracts():
    """CRITICAL: the complex/multi-agent tier (the Palisades path) must run the
    completion contracts at its own turn-final done — _stream_response never
    does for this tier (specialist streams pass send_done=False)."""
    import inspect
    src = inspect.getsource(ClaudeSDKBridge._multi_agent_query)
    assert "_emit_completion_contracts" in src, (
        "complex tier never invokes the completion contracts"
    )


def test_method_gap_silent_on_spatial_vs_comparison():
    """'north vs south field' has a change verb + 'vs' but is SPATIAL, not
    temporal — no two-period signal, so no method gap (false-positive fix)."""
    assert _method_gap_payload(
        "compare the ndvi of the north vs south field",
        [], ["openeo_create_job"],
    ) is None


def test_method_gap_silent_on_bare_since_without_period():
    assert _method_gap_payload(
        "what is the ndvi since it is summer", [], ["openeo_create_job"]
    ) is None


def test_method_gap_fires_on_before_after_burn_prompt():
    """The verbatim Palisades shape: before/after change verb, a job tool ran,
    no difference process — must fire."""
    payload = _method_gap_payload(
        "compare nbr before the fire (2024-12-15) against after (2025-02-15)",
        ["load_collection"], ["openeo_algorithm"],
    )
    assert payload is not None and payload["type"] == "method_gap"


def test_method_gap_silent_when_result_difference_tool_ran():
    """A live Palisades run computed the dNBR via the result_difference tool
    (real per-pixel subtraction of two executed rasters), not an in-graph
    merge_cubes — that is a valid difference, so no method gap (false-pos fix)."""
    assert _method_gap_payload(
        "compare nbr before the fire (2024-12-15) against after (2025-02-15)",
        ["load_collection", "save_result"],  # two snapshots, no merge_cubes
        ["openeo_create_job", "result_difference", "threshold_classify"],
    ) is None


def test_deliverable_gap_silent_on_show_me_value_no_map():
    """'show me the NDVI value' is not a map request (narrowed detection)."""
    assert _deliverable_gap_payload(
        "show me the ndvi value for this field", ["chart"], ["openeo_create_job"]
    ) is None


def test_deliverable_gap_silent_on_planning_confirm_turn():
    """A live run asked 'shall I proceed?' after only discovery/quality tools —
    no analysis job ran, so the map isn't missing, it's pending. Gated on a job
    tool, deliverable_gap must stay silent (false-positive fix, 2026-07-24)."""
    assert _deliverable_gap_payload(
        "map the burn severity of the palisades fire",
        [],  # no viz produced — the agent paused to confirm
        ["extract_analysis_goal", "openeo_resolve_location", "recommend_collection",
         "openeo_quality_metrics"],  # planning tools only, no job
    ) is None


def test_injector_fires_on_broadened_temporal_change_verbs():
    """NU 2026-07-24: deforestation / reservoir-shrunk / urban-growth change
    queries with a two-period signal must trigger the difference-raster steer."""
    for prompt in (
        "map deforestation between 2020 and 2024 in the amazon",
        "has the reservoir shrunk since 2021",
        "show urban growth 2019 vs 2024",
    ):
        routing, plan = ["data"], {"steps": []}
        assert _inject_change_detection(routing, plan, prompt) is True, prompt
        assert any(
            s.get("tool") in ("openeo_generate_graph", "openeo_algorithm")
            for s in plan["steps"]
        ), prompt


def test_injector_silent_on_spatial_between_comparison():
    """'between the north and south fields' is spatial — no temporal anchor,
    so the injector must not fire (false-positive fix)."""
    routing, plan = ["data"], {"steps": []}
    assert _inject_change_detection(
        routing, plan, "compare ndvi between the north and south fields"
    ) is False
    assert plan["steps"] == []


# --- FIX-2 2026-07-24: full-turn prose + class-table integrity ------------------------

# The live Palisades shape: four disjoint severity classes whose percentages sum to
# 134.18% — the table was stitched from several tool outputs, so it is not a partition.
_BAD_CLASS_TABLE = (
    "Burn severity breakdown:\n\n"
    "| Severity | Area (ha) | Percent |\n"
    "|---|---|---|\n"
    "| Unburned | 1200 | 41.30% |\n"
    "| Low | 900 | 30.10% |\n"
    "| Moderate | 700 | 38.20% |\n"
    "| High | 400 | 24.58% |\n"
)

_CLEAN_CLASS_TABLE = (
    "Burn severity breakdown:\n\n"
    "| Severity | Area (ha) | Percent |\n"
    "|---|---|---|\n"
    "| Unburned | 1200 | 40.00% |\n"
    "| Low | 900 | 30.00% |\n"
    "| Moderate | 700 | 20.00% |\n"
    "| High | 400 | 10.00% |\n"
)


class _ScriptedClient:
    """Yields one text block then a ResultMessage — the message shape
    _stream_response consumes for a single specialist stream."""

    def __init__(self, text):
        self._text = text

    async def receive_response(self):
        yield StreamEvent(
            uuid="u1", session_id="sdk-1",
            event={"type": "content_block_start", "content_block": {"type": "text"}},
        )
        yield StreamEvent(
            uuid="u2", session_id="sdk-1",
            event={"type": "content_block_delta",
                   "delta": {"type": "text_delta", "text": self._text}},
        )
        yield ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
            num_turns=1, session_id="sdk-1",
        )


@pytest.mark.asyncio
async def test_turn_response_texts_accumulates_across_specialist_streams():
    """Issue 2 root cause: every specialist stream OVERWRITES last_response_text,
    so an earlier specialist's prose is invisible at the complex-tier finalize.
    The per-turn accumulator must keep all of it."""
    ws = _RecorderWS()
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    bridge._sessions["s1"] = {"turn_response_texts": []}
    for text in ("data specialist prose", "qa specialist prose"):
        await bridge._stream_response(
            _ScriptedClient(text), ws, "s1", {"ws": ws}, {}, [], None,
            "burn severity", send_done=False,
        )
    assert bridge._sessions["s1"]["turn_response_texts"] == [
        "data specialist prose", "qa specialist prose",
    ]
    # last_response_text keeps its existing meaning (the prose-handoff + judge read it).
    assert bridge._sessions["s1"]["last_response_text"] == "qa specialist prose"


@pytest.mark.asyncio
async def test_complex_finalize_full_turn_prose_catches_earlier_specialist_numbers(
    monkeypatch,
):
    """Issue 2: an ungrounded number emitted by an EARLIER specialist escaped the
    user-facing notice because only the LAST specialist's prose was checked."""
    monkeypatch.delenv("JONA_GROUNDING_ENFORCE", raising=False)
    bridge = _contract_bridge()
    bridge._sessions["s1"]["turn_response_texts"] = [
        "Data specialist: the burn extent covers 4812 hectares.",
        "QA specialist: analysis complete.",
    ]
    texts = bridge._sessions["s1"]["turn_response_texts"]

    ws_last = _RecorderWS()
    await bridge._emit_completion_contracts(
        ws_last, "s1", "how large is the burn", ["openeo_create_job"], texts[-1],
    )
    assert not [m for m in ws_last.messages if m["type"] == "text_delta"]

    ws_full = _RecorderWS()
    await bridge._emit_completion_contracts(
        ws_full, "s1", "how large is the burn", ["openeo_create_job"], "\n".join(texts),
    )
    deltas = [m for m in ws_full.messages if m["type"] == "text_delta"]
    assert len(deltas) == 1
    assert "could not be verified" in deltas[0]["content"]


@pytest.mark.asyncio
async def test_complex_tier_finalize_passes_full_turn_prose(monkeypatch):
    """BEHAVIOURAL: drive _multi_agent_query to its finalize and capture what the
    contracts actually receive. It must be the JOINED turn prose — reverting to
    last_response_text (only the LAST specialist) must fail this test."""
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    ws = _RecorderWS()
    bridge._sessions["s1"] = {
        "turn_viz_types": [],
        "turn_graph_processes": [],
        "turn_tool_names": [],
        "turn_tool_results": [],
        # Two specialists spoke this turn; the FIRST one owns the numbers.
        "turn_response_texts": ["data specialist says coverage 91.4%", "viz specialist rendered it"],
        "last_response_text": "viz specialist rendered it",
    }

    async def _plan(*a, **k):
        # A plan with steps but no routable specialist: the loop runs nothing and
        # execution falls straight through to the turn-final finalize.
        return {"steps": [{"specialist": "nobody", "tool": "noop"}], "routing": []}

    captured = {}

    async def _capture(websocket, session_id, prompt, tools_used, full_response_text):
        captured["text"] = full_response_text

    monkeypatch.setattr(bridge, "_run_planner_phase", _plan)
    monkeypatch.setattr(bridge, "_user_result_count", lambda *a, **k: 0)
    monkeypatch.setattr(bridge, "_save_plan_trajectory", lambda *a, **k: None)
    monkeypatch.setattr(bridge, "_emit_completion_contracts", _capture)

    await bridge._multi_agent_query("summarize the field conditions", ws, "s1")

    assert "text" in captured, "complex tier never reached the completion contracts"
    assert "coverage 91.4%" in captured["text"], (
        "the FIRST specialist's prose never reached the contracts — the complex tier "
        "is still checking only the last specialist's text"
    )
    assert "viz specialist rendered it" in captured["text"]


def test_class_table_payload_fires_on_impossible_percentage_sum():
    payload = _class_table_payload(_BAD_CLASS_TABLE)
    assert payload is not None
    assert payload["type"] == "class_table_gap"
    assert payload["violations"]
    assert "134" in payload["detail"]


def test_class_table_payload_silent_on_clean_table():
    assert _class_table_payload(_CLEAN_CLASS_TABLE) is None


@pytest.mark.asyncio
async def test_class_table_gap_is_observe_only_by_default(monkeypatch):
    """The finding fires as an EVENT (+ Langfuse score) but must NOT reach the
    user-visible notice by default: deciding "is this percentage column a partition
    of one whole?" from markdown produced verified false accusations on legitimate
    answers across three review rounds, so it ships observe-first like the grounding
    guard did and is promoted only once precision is measured."""
    monkeypatch.delenv("JONA_GROUNDING_ENFORCE", raising=False)
    monkeypatch.delenv("JONA_CLASS_TABLE_NOTICE", raising=False)
    ws = _RecorderWS()
    bridge = _contract_bridge()
    # Same numbers in a tool result — the grounding clause stays quiet, so the
    # notice would isolate the class-table clause if it were enabled.
    bridge._sessions["s1"]["turn_tool_results"] = [_BAD_CLASS_TABLE]
    await bridge._emit_completion_contracts(
        ws, "s1", "how severe was the burn", ["openeo_create_job"], _BAD_CLASS_TABLE,
    )
    assert "class_table_gap" in [m["type"] for m in ws.messages], "signal must still fire"
    assert not [m for m in ws.messages if m["type"] == "text_delta"], (
        "class-table finding must not speak to the user until promoted"
    )


@pytest.mark.asyncio
async def test_class_table_gap_annotates_when_promoted(monkeypatch):
    monkeypatch.delenv("JONA_GROUNDING_ENFORCE", raising=False)
    monkeypatch.setenv("JONA_CLASS_TABLE_NOTICE", "1")
    ws = _RecorderWS()
    bridge = _contract_bridge()
    bridge._sessions["s1"]["turn_tool_results"] = [_BAD_CLASS_TABLE]
    await bridge._emit_completion_contracts(
        ws, "s1", "how severe was the burn", ["openeo_create_job"], _BAD_CLASS_TABLE,
    )
    deltas = [m for m in ws.messages if m["type"] == "text_delta"]
    assert len(deltas) == 1
    assert "sum to 100%" in deltas[0]["content"]


def test_injector_threshold_step_demands_one_call_and_full_partition():
    routing, plan = ["data"], {"steps": []}
    assert _inject_change_detection(
        routing, plan,
        "map the burn severity of the palisades fire before the fire vs after the fire",
    ) is True
    step = next(s for s in plan["steps"] if s.get("tool") == "threshold_classify")
    assert "ONE threshold_classify call" in step["purpose"]
    assert "100%" in step["purpose"]


def test_system_prompt_pins_single_source_class_table():
    assert SYSTEM_PROMPT.count("=== ABSOLUTE RULE — DATA INTEGRITY ===") == 1
    _block = SYSTEM_PROMPT.split("=== ABSOLUTE RULE — DATA INTEGRITY ===", 1)[1]
    assert "ONE threshold_classify call" in _block
    assert "sum to 100%" in _block


def test_planner_prompt_pins_no_stitched_class_table():
    assert "histogram" in PLANNER_SYSTEM_PROMPT
    assert "NEVER stitched together" in PLANNER_SYSTEM_PROMPT
