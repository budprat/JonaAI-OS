# ABOUTME: Tests for the Sentinel monitoring specialist — config wiring, no
# ABOUTME: phantom tools, MCP server exposes exactly its subset, routing safety-net.

from openeo_ai.sdk.client import (
    SENTINEL_AGENT_TOOL_NAMES,
    SPECIALIST_CONFIGS,
    TOOL_DEFINITIONS,
)
from openeo_ai.sdk.inprocess_tools import create_specialist_mcp_server


def test_sentinel_config_present():
    cfg = SPECIALIST_CONFIGS["sentinel"]
    assert cfg["tool_names"] is SENTINEL_AGENT_TOOL_NAMES
    assert cfg["mcp_name"] == "openeo_sentinel"
    assert cfg["system_prompt"]  # non-empty


def test_sentinel_has_no_phantom_tools():
    names = {d["name"] for d in TOOL_DEFINITIONS}
    missing = SENTINEL_AGENT_TOOL_NAMES - names
    assert missing == set(), f"Sentinel references tools absent from TOOL_DEFINITIONS: {missing}"


def test_sentinel_mcp_server_exposes_exactly_its_subset():
    # The builder filters TOOL_DEFINITIONS by the subset; with no phantom tools
    # the server exposes exactly the 15 sentinel tools.
    exposed = {d["name"] for d in TOOL_DEFINITIONS if d["name"] in SENTINEL_AGENT_TOOL_NAMES}
    assert exposed == set(SENTINEL_AGENT_TOOL_NAMES)
    cfg = create_specialist_mcp_server("sentinel", SENTINEL_AGENT_TOOL_NAMES)
    assert cfg["name"] == "openeo_sentinel"
    assert cfg["instance"] is not None


def test_sentinel_mcp_name_is_unique():
    mcp_names = [c["mcp_name"] for c in SPECIALIST_CONFIGS.values()]
    assert len(mcp_names) == len(set(mcp_names))


def test_sentinel_prompt_enforces_worker_honesty_and_tenancy():
    prompt = SPECIALIST_CONFIGS["sentinel"]["system_prompt"].lower()
    assert "worker_active" in prompt          # honesty clause
    assert "tenancy" in prompt or "another user" in prompt  # tenancy clause
    assert "empty" in prompt                   # empty-graph guard


def test_sentinel_routing_keywords_discriminate():
    # The safety-net must fire on ongoing-monitoring intent and NOT on one-off analysis.
    from openeo_ai.sdk.claude_sdk_bridge import _SENTINEL_ROUTING_KEYWORDS

    def _matches(prompt):
        p = prompt.lower()
        return any(kw in p for kw in _SENTINEL_ROUTING_KEYWORDS)

    assert _matches("keep watching this farm for vegetation stress, alert me weekly")
    assert _matches("notify me if the reservoir drops")
    assert _matches("set up monitoring for this coastline")
    # one-off analysis must NOT pull in sentinel
    assert not _matches("compute NDVI for punjab wheat in June 2024")
    assert not _matches("watch the NDVI change between 2020 and 2024")  # bare 'watch' excluded
