# ABOUTME: Tests Earth-Agent MCP tool discovery and schema conversion.

import pytest

from openeo_ai.earth_agent.tool_registry import EarthAgentToolRegistry


class _FakeMCPClient:
    def __init__(self):
        self.calls = 0

    async def list_tools(self, server_name=None, force_refresh=False):
        self.calls += 1
        return {
            "Statistics": [
                {
                    "name": "calc_batch_image_mean",
                    "description": "Compute per-image means.",
                    "inputSchema": {"type": "object", "properties": {"file_list": {"type": "array"}}},
                }
            ],
            "Analysis": [
                {
                    "name": "compute_linear_trend",
                    "description": "Compute a trend line.",
                    "inputSchema": {"type": "object", "properties": {"y": {"type": "array"}}},
                }
            ],
        }


@pytest.mark.asyncio
async def test_tool_registry_prefixes_and_caches_tools():
    client = _FakeMCPClient()
    registry = EarthAgentToolRegistry(client)

    definitions = await registry.build_tool_definitions()
    cached = await registry.build_tool_definitions()

    assert {tool["name"] for tool in definitions} == {
        "ea_calc_batch_image_mean",
        "ea_compute_linear_trend",
    }
    assert cached == definitions
    assert client.calls == 1

    resolved = registry.resolve("ea_compute_linear_trend")
    assert resolved.server_name == "Analysis"
    assert resolved.source_name == "compute_linear_trend"
