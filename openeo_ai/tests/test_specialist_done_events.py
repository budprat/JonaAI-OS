# ABOUTME: A specialist's stream must not emit the turn-final "done" event —
# ABOUTME: clients treat the first done as end-of-turn and leave before ml/qa run.
import asyncio
import inspect

import pytest
from claude_agent_sdk.types import ResultMessage

from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
from openeo_ai.sdk.client import OpenEOAIConfig


class _RecorderWS:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


class _OneShotClient:
    """Yields a single ResultMessage, like a specialist turn that just ended."""

    def __init__(self, msg):
        self._msg = msg

    async def receive_response(self):
        yield self._msg


def _result_message():
    return ResultMessage(
        subtype="success", duration_ms=10, duration_api_ms=8, is_error=False,
        num_turns=1, session_id="sess-1", total_cost_usd=0.001, usage={}, result="ok",
    )


def _stream(ws, **kwargs):
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    return asyncio.run(bridge._stream_response(
        _OneShotClient(_result_message()), ws, "sess-1", {"ws": ws},
        {}, [], None, "ndvi analysis", **kwargs,
    ))


def _done_count(ws):
    return sum(1 for m in ws.messages if m.get("type") == "done")


def test_stream_response_sends_done_by_default():
    ws = _RecorderWS()
    _stream(ws)
    assert _done_count(ws) == 1


def test_stream_response_suppresses_done_for_subagent_streams():
    """Regression for the live t2_ea_handoff failure: the data specialist's
    stream sent {"type": "done"}, the client ended the turn, and the ml
    specialist (sole holder of earth_agent_analyze) never ran."""
    ws = _RecorderWS()
    _stream(ws, send_done=False)
    assert _done_count(ws) == 0


def test_specialist_streams_suppress_done_and_multi_agent_owns_the_final_one():
    run_specialist_src = inspect.getsource(ClaudeSDKBridge._run_specialist)
    assert "send_done=False" in run_specialist_src, (
        "_run_specialist must suppress its stream's turn-final done"
    )
    multi_src = inspect.getsource(ClaudeSDKBridge._multi_agent_query)
    assert '{"type": "done"}' in multi_src, (
        "_multi_agent_query must send the single turn-final done itself"
    )
