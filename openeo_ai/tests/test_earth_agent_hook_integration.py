# ABOUTME: Tests Earth-Agent GRPO and visualization behavior in Claude SDK hooks.

import pytest

from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
from openeo_ai.sdk.client import OpenEOAIConfig


class _FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_json(self, payload):
        self.messages.append(payload)


@pytest.mark.asyncio
async def test_pre_tool_hook_injects_earth_agent_strategy_context(monkeypatch):
    ws = _FakeWebSocket()
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    opts = bridge._build_options(
        ws_ref={"ws": ws},
        thinking_ids={},
        session_tools_used=[],
        prompt="analyze this raster",
    )
    pre_hook = opts.hooks["PreToolUse"][0].hooks[0]

    monkeypatch.setattr(
        "openeo_ai.earth_agent.grpo_strategies.detect_analysis_type",
        lambda tool_name, tool_input: "water",
    )
    monkeypatch.setattr(
        "openeo_ai.earth_agent.grpo_strategies.detect_data_type",
        lambda tool_name, tool_input: "spectral_index",
    )
    monkeypatch.setattr(
        "openeo_ai.earth_agent.grpo_strategies.get_relevant_strategies",
        lambda query_type, data_type: "## Earth-Agent Strategy: Water",
    )

    result = await pre_hook(
        {
            "tool_name": "mcp__openeo__earth_agent_analyze",
            "tool_input": {"query": "Find flood extent", "input_files": ["/tmp/openeo_results/job-1/result.tif"]},
            "tool_use_id": "tool-1",
        },
        None,
        None,
    )

    assert result["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert result["hookSpecificOutput"]["additionalContext"] == "## Earth-Agent Strategy: Water"
    assert any(message["type"] == "tool_start" for message in ws.messages)


@pytest.mark.asyncio
async def test_post_tool_hook_uses_earth_agent_result_mapper_when_default_extractor_has_no_viz(monkeypatch):
    ws = _FakeWebSocket()
    bridge = ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))
    opts = bridge._build_options(
        ws_ref={"ws": ws},
        thinking_ids={"tool-1": "thinking-1"},
        session_tools_used=[],
        prompt="analyze this raster",
    )
    post_hook = opts.hooks["PostToolUse"][0].hooks[0]

    monkeypatch.setattr(
        "openeo_ai.sdk.claude_sdk_bridge.extract_visualization",
        lambda *args, **kwargs: [],
    )

    await post_hook(
        {
            "tool_name": "mcp__openeo__ea_compute_linear_trend",
            "tool_response": {
                "content": [{"type": "text", "text": "{\"timestamps\":[\"2024-01\",\"2024-02\"],\"values\":[0.2,0.4]}"}]
            },
            "tool_use_id": "tool-1",
        },
        None,
        None,
    )

    viz_messages = [message for message in ws.messages if message.get("type") == "visualization"]
    assert len(viz_messages) == 1
    assert viz_messages[0]["visualization"]["type"] == "chart"
