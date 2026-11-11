# ABOUTME: The PostToolUse hook must run heavy viz extraction off the event loop —
# ABOUTME: a blocked loop starves WS keepalive pongs and kills the connection (1011).
import asyncio
import inspect
import threading
import time

import pytest

from openeo_ai.sdk import claude_sdk_bridge as bridge_mod
from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
from openeo_ai.sdk.client import OpenEOAIConfig


class _RecorderWS:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


def _post_hook(ws):
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    opts = bridge._build_options(
        ws_ref={"ws": ws},
        thinking_ids={"tool-1": "thinking-1"},
        session_tools_used=[],
        prompt="ndvi analysis",
    )
    return opts.hooks["PostToolUse"][0].hooks[0]


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


@pytest.mark.asyncio
async def test_viz_extraction_runs_off_loop_and_loop_stays_responsive(monkeypatch):
    """extract_visualization does full-band rasterio reads + a 32MB archive copy;
    on the event loop that blocks keepalive pongs for >=20s and the client
    closes 1011 mid-turn. The hook must offload it to a worker thread."""
    ws = _RecorderWS()
    hook = _post_hook(ws)
    seen = {}

    def _slow_extract(*args, **kwargs):
        seen["thread"] = threading.get_ident()
        time.sleep(0.8)  # stands in for the raster read + archive copy
        return [{"type": "map", "data": {"url": "/render"}}]

    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization", _slow_extract
    )

    beats = []

    async def _heartbeat():
        while True:
            beats.append(time.monotonic())
            await asyncio.sleep(0.02)

    beat_task = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0.05)  # let the heartbeat establish a baseline
    await hook(_job_result_input(), None, None)
    beat_task.cancel()

    loop_thread = threading.get_ident()
    assert seen["thread"] != loop_thread, "viz extraction ran on the event-loop thread"
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert max(gaps) < 0.5, (
        f"event loop starved for {max(gaps):.2f}s during viz extraction"
    )


@pytest.mark.asyncio
async def test_offloaded_viz_preserves_message_order_and_payload(monkeypatch):
    """Offloading must not change WS event ordering or payloads: tool_result,
    then visualization, then saved_job_created for an auto-saved result."""
    ws = _RecorderWS()
    hook = _post_hook(ws)

    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization",
        lambda *args, **kwargs: [{"type": "map", "data": {"x": 1}, "save_id": "save-1"}],
    )

    await hook(_job_result_input(), None, None)

    types = [m["type"] for m in ws.messages]
    assert "tool_result" in types and "visualization" in types and "saved_job_created" in types
    assert types.index("tool_result") < types.index("visualization") < types.index("saved_job_created")
    viz = next(m for m in ws.messages if m["type"] == "visualization")
    assert viz["visualization"]["data"] == {"x": 1}
    saved = next(m for m in ws.messages if m["type"] == "saved_job_created")
    assert saved["save_id"] == "save-1"


@pytest.mark.asyncio
async def test_earth_agent_viz_fallback_runs_off_loop(monkeypatch):
    """The earth-agent result_mapper fallback does the same class of raster
    work and must be offloaded too."""
    ws = _RecorderWS()
    hook = _post_hook(ws)
    seen = {}

    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization",
        lambda *args, **kwargs: [],
    )

    def _mapper(tool_result, tool_name=None, title=None):
        seen["thread"] = threading.get_ident()
        return [{"type": "chart", "data": {}}]

    monkeypatch.setattr(
        "openeo_ai.earth_agent.result_mapper.to_visualizations", _mapper
    )

    await hook(
        {
            "tool_name": "mcp__openeo__earth_agent_analyze",
            "tool_response": {"content": [{"type": "text", "text": "{}"}]},
            "tool_use_id": "tool-2",
        },
        None,
        None,
    )

    assert seen["thread"] != threading.get_ident(), (
        "earth-agent viz fallback ran on the event-loop thread"
    )


def test_sync_db_helpers_are_offloaded_in_async_tier_paths():
    """_user_result_count / _save_plan_trajectory do sync SQLAlchemy work whose
    first _get_session() runs DDL + reflection against the remote Supabase
    pooler — a bare call on the event loop wedges the whole server when the DB
    stalls (observed live: main thread hung in do_rollback, every WS dead)."""
    src = inspect.getsource(bridge_mod)
    bare_calls = [
        line.strip()
        for line in src.splitlines()
        for name in ("_user_result_count", "_save_plan_trajectory")
        if f"self.{name}(" in line and "asyncio.to_thread" not in line
    ]
    assert bare_calls == [], (
        f"bare sync DB calls on the event loop: {bare_calls}"
    )
