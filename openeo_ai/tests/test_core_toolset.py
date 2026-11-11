# ABOUTME: Locks the core toolset under the CLI auto-eager threshold and keeps
# ABOUTME: every core name a real TOOL_DEFINITIONS member.
import json

from openeo_ai.sdk.client import CORE_TOOL_NAMES, TOOL_DEFINITIONS

_DEFS = {td["name"]: td for td in TOOL_DEFINITIONS}

# CLI 2.1.37: tools defer when definitions exceed ~10% of context ≈ 40,000
# chars. 36,000 leaves headroom for AskUserQuestion + growth.
_CHAR_BUDGET = 36_000


def test_core_names_are_real_tools():
    missing = sorted(n for n in CORE_TOOL_NAMES if n not in _DEFS)
    assert missing == []


def test_core_defs_fit_under_eager_threshold():
    total = sum(len(json.dumps(_DEFS[n])) for n in CORE_TOOL_NAMES)
    assert total < _CHAR_BUDGET, f"core defs {total} chars >= {_CHAR_BUDGET}"


def test_core_covers_the_flagship_paths():
    # Chat analysis, spine control, decision-grade — each must stay reachable.
    for required in (
        "openeo_create_job", "viz_show_map",             # chat → job → map
        "monitor_create_watch", "monitor_list_signals",  # spine from chat
        "agentic_decision_query",                        # decision-grade router
    ):
        assert required in CORE_TOOL_NAMES


def test_core_server_builds_only_core_tools():
    from openeo_ai.sdk.inprocess_tools import _build_core_tools

    tools = _build_core_tools()
    names = {t.name for t in tools}
    assert "AskUserQuestion" in names
    assert names - {"AskUserQuestion"} <= set(CORE_TOOL_NAMES)
    assert "openeo_create_job" in names
    # Every core tool with a registered handler must be present.
    assert len(names) >= 30


def test_core_server_config_keeps_the_openeo_name():
    # The server name is the mcp__openeo__ prefix contract with the frontend.
    from openeo_ai.sdk.inprocess_tools import create_core_mcp_server

    server = create_core_mcp_server()
    assert server["name"] == "openeo"
    assert server["type"] == "sdk"


def test_core_allowed_tools_default(monkeypatch):
    monkeypatch.delenv("JONA_FULL_TOOLSET", raising=False)
    from openeo_ai.sdk import claude_sdk_bridge as bm

    allowed = bm._core_allowed_tools()
    core_prefixed = {f"mcp__openeo__{n}" for n in CORE_TOOL_NAMES}
    assert set(allowed) - {"mcp__openeo__AskUserQuestion"} == core_prefixed
    assert "mcp__openeo__AskUserQuestion" not in allowed


def test_full_toolset_escape_hatch(monkeypatch):
    monkeypatch.setenv("JONA_FULL_TOOLSET", "1")
    from openeo_ai.sdk import claude_sdk_bridge as bm

    allowed = bm._core_allowed_tools()
    assert len(allowed) == len(TOOL_DEFINITIONS)  # full surface + AskUserQuestion


def test_build_options_uses_core_allowed_tools(monkeypatch):
    monkeypatch.delenv("JONA_FULL_TOOLSET", raising=False)
    from openeo_ai.sdk import claude_sdk_bridge as bm
    from openeo_ai.sdk.client import OpenEOAIConfig

    bridge = bm.ClaudeSDKBridge(OpenEOAIConfig())
    opts = bridge._build_options(
        ws_ref={"ws": None}, thinking_ids={}, session_tools_used=[],
        session_id="core-wiring-test", prompt="show ndvi",
    )
    core_prefixed = {f"mcp__openeo__{n}" for n in CORE_TOOL_NAMES}
    assert set(opts.allowed_tools) - {"mcp__openeo__AskUserQuestion"} == core_prefixed


def test_build_options_isolates_filesystem_mcp_config():
    # Without --strict-mcp-config the CLI merges the operator's personal MCP
    # servers from ~/.claude.json into backend sessions (measured: 458 tools
    # vs 57). Backend sessions must only see the SDK-provided servers.
    from openeo_ai.sdk import claude_sdk_bridge as bm
    from openeo_ai.sdk.client import OpenEOAIConfig

    bridge = bm.ClaudeSDKBridge(OpenEOAIConfig())
    opts = bridge._build_options(
        ws_ref={"ws": None}, thinking_ids={}, session_tools_used=[],
        session_id="strict-mcp-test", prompt="show ndvi",
    )
    assert "strict-mcp-config" in opts.extra_args
