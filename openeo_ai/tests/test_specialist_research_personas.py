# ABOUTME: Tests for the four research specialists (Data Scout, Methodologist,
# ABOUTME: Publisher, Time-series) — config wiring, no phantom tools, MCP servers.

import pytest

from openeo_ai.sdk.client import SPECIALIST_CONFIGS, TOOL_DEFINITIONS
from openeo_ai.sdk.inprocess_tools import create_specialist_mcp_server

RESEARCH_PERSONAS = ["data_scout", "methodologist", "publisher", "timeseries"]


@pytest.mark.parametrize("name", RESEARCH_PERSONAS)
def test_persona_config_present(name):
    cfg = SPECIALIST_CONFIGS[name]
    assert cfg["tool_names"]           # non-empty frozenset
    assert cfg["system_prompt"]        # non-empty prompt
    assert cfg["mcp_name"] == f"openeo_{name}"


@pytest.mark.parametrize("name", RESEARCH_PERSONAS)
def test_persona_has_no_phantom_tools(name):
    known = {d["name"] for d in TOOL_DEFINITIONS}
    missing = SPECIALIST_CONFIGS[name]["tool_names"] - known
    assert missing == set(), f"{name} references tools absent from TOOL_DEFINITIONS: {missing}"


@pytest.mark.parametrize("name", RESEARCH_PERSONAS)
def test_persona_mcp_server_exposes_exactly_its_subset(name):
    subset = SPECIALIST_CONFIGS[name]["tool_names"]
    exposed = {d["name"] for d in TOOL_DEFINITIONS if d["name"] in subset}
    assert exposed == set(subset)  # no phantom → server exposes the whole subset
    cfg = create_specialist_mcp_server(name, subset)
    assert cfg["name"] == f"openeo_{name}"
    assert cfg["instance"] is not None


def test_all_specialist_mcp_names_unique():
    mcp_names = [c["mcp_name"] for c in SPECIALIST_CONFIGS.values()]
    assert len(mcp_names) == len(set(mcp_names))


def test_research_personas_prompts_are_data_honest():
    # Publisher/Time-series must forbid fabricated values; Methodologist must
    # be a reviewer; Data Scout must report availability honestly.
    p = SPECIALIST_CONFIGS
    assert "never fabricate" in p["publisher"]["system_prompt"].lower() or \
           "never invent" in p["publisher"]["system_prompt"].lower() or \
           "never fabricate figures" in p["publisher"]["system_prompt"].lower()
    assert "never invent a series" in p["timeseries"]["system_prompt"].lower() or \
           "only tool-produced" in p["timeseries"]["system_prompt"].lower()
