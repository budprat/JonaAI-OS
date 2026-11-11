# ABOUTME: Tests Earth-Agent tool registration in the SDK tool surface.

import pytest

from openeo_ai.tools import earth_agent_tools


@pytest.fixture()
def installed(monkeypatch):
    """Earth-Agent's checkout and dependencies, which this host and CI don't have."""
    monkeypatch.delenv("DISABLE_EARTH_AGENT_INTEGRATION", raising=False)
    monkeypatch.setattr(earth_agent_tools, "_earth_agent_installed", lambda: True)


def test_earth_agent_meta_tool_definition_present(installed):
    definitions = earth_agent_tools.get_earth_agent_tool_definitions()

    assert any(defn["name"] == "earth_agent_analyze" for defn in definitions)
    assert any(defn["name"] == "get_analysis_strategies" for defn in definitions)


def test_earth_agent_direct_tools_can_be_stubbed(installed, monkeypatch):
    monkeypatch.setattr(earth_agent_tools, "_earth_agent_direct_tools_enabled", lambda: True)
    monkeypatch.setattr(
        earth_agent_tools,
        "_load_direct_tool_registry",
        lambda: (
            [
                {
                    "name": "ea_calc_batch_image_mean",
                    "description": "Compute means.",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            {},
        ),
    )

    definitions = earth_agent_tools.get_earth_agent_tool_definitions()

    names = {defn["name"] for defn in definitions}
    assert "earth_agent_analyze" in names
    assert "ea_calc_batch_image_mean" in names


def test_client_registers_earth_agent_meta_handler(installed):
    from openeo_ai.sdk.client import OpenEOAIClient, OpenEOAIConfig

    client = OpenEOAIClient(OpenEOAIConfig(api_key="test-key"))

    assert "earth_agent_analyze" in client.tools
    assert "get_analysis_strategies" in client.tools


def test_bridge_strips_earth_agent_mcp_prefix():
    from openeo_ai.sdk.claude_sdk_bridge import _strip_mcp_prefix

    assert _strip_mcp_prefix("mcp__earth_agent__ea_calc_batch_image_mean") == "ea_calc_batch_image_mean"


def test_system_prompt_mentions_earth_agent_strategy_flow():
    from openeo_ai.sdk.client import OpenEOAIClient

    prompt = OpenEOAIClient.SYSTEM_PROMPT
    assert "get_analysis_strategies" in prompt
    assert "earth_agent_analyze" in prompt
    assert "EARTH-AGENT POST-PROCESSING" in prompt


@pytest.mark.parametrize("disabled,installed_here", [("", False), ("1", True)])
def test_no_earth_agent_tools_where_it_cant_run_or_is_disabled(monkeypatch, disabled, installed_here):
    # On the production host the checkout and langgraph are missing, so every call failed.
    monkeypatch.setenv("DISABLE_EARTH_AGENT_INTEGRATION", disabled)
    monkeypatch.setattr(earth_agent_tools, "_earth_agent_installed", lambda: installed_here)

    assert earth_agent_tools.get_earth_agent_tool_definitions() == []
    assert earth_agent_tools.create_earth_agent_tools() == {}


def test_installed_needs_the_checkout(monkeypatch, tmp_path):
    monkeypatch.setattr(earth_agent_tools, "__file__", str(tmp_path / "openeo_ai/tools/earth_agent_tools.py"))
    assert earth_agent_tools._earth_agent_installed() is False
    (tmp_path / "Earth-Agent/.venv/bin").mkdir(parents=True)
    (tmp_path / "Earth-Agent/.venv/bin/python3").write_text("")
    assert earth_agent_tools._earth_agent_installed() is True
