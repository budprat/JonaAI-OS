"""Tool discovery and schema conversion for Earth-Agent MCP servers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from .mcp_client import EarthAgentMCPClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegisteredEarthAgentTool:
    """Metadata about one Earth-Agent tool exposed into Claude SDK."""

    prefixed_name: str
    server_name: str
    source_name: str
    description: str
    input_schema: dict[str, Any]


class EarthAgentToolRegistry:
    """Discover and cache Earth-Agent tool definitions."""

    def __init__(self, mcp_client: EarthAgentMCPClient) -> None:
        self.mcp_client = mcp_client
        self._cache: list[dict[str, Any]] | None = None
        self._index: dict[str, RegisteredEarthAgentTool] = {}

    async def build_tool_definitions(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        if self._cache is not None and not force_refresh:
            return list(self._cache)

        discovered = await self.mcp_client.list_tools(force_refresh=force_refresh)
        definitions: list[dict[str, Any]] = []
        index: dict[str, RegisteredEarthAgentTool] = {}

        for server_name, tools in discovered.items():
            for tool in tools:
                source_name = tool["name"]
                prefixed_name = f"ea_{source_name}"
                if prefixed_name in index:
                    raise ValueError(f"Earth-Agent tool name collision: {prefixed_name}")
                schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
                description = tool.get("description", "").strip()
                index[prefixed_name] = RegisteredEarthAgentTool(
                    prefixed_name=prefixed_name,
                    server_name=server_name,
                    source_name=source_name,
                    description=description,
                    input_schema=schema,
                )
                definitions.append(
                    {
                        "name": prefixed_name,
                        "description": description,
                        "input_schema": schema,
                    }
                )

        self._cache = definitions
        self._index = index
        return list(definitions)

    def resolve(self, prefixed_name: str) -> RegisteredEarthAgentTool:
        if prefixed_name not in self._index:
            raise KeyError(f"Unknown Earth-Agent tool: {prefixed_name}")
        return self._index[prefixed_name]

    def entries(self) -> dict[str, RegisteredEarthAgentTool]:
        return dict(self._index)


_SYNC_CACHE: tuple[list[dict[str, Any]], dict[str, RegisteredEarthAgentTool]] | None = None


def load_earth_agent_registry_sync(
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, RegisteredEarthAgentTool]]:
    """Synchronously discover Earth-Agent tools with in-memory caching."""
    global _SYNC_CACHE

    if _SYNC_CACHE is not None and not force_refresh:
        definitions, index = _SYNC_CACHE
        return list(definitions), dict(index)

    async def _load() -> tuple[list[dict[str, Any]], dict[str, RegisteredEarthAgentTool]]:
        client = EarthAgentMCPClient()
        registry = EarthAgentToolRegistry(client)
        try:
            definitions = await registry.build_tool_definitions(force_refresh=force_refresh)
            return definitions, registry.entries()
        finally:
            await client.close()

    try:
        # Try asyncio.run() for CLI/script contexts (no running loop)
        loaded = asyncio.run(_load())
    except RuntimeError:
        # Inside an already-running event loop (uvicorn) — can't use asyncio.run().
        # Return empty and rely on async startup via load_earth_agent_registry_async().
        logger.info(
            "Earth-Agent direct tool discovery deferred to async startup "
            "(running inside event loop)"
        )
        if _SYNC_CACHE is not None:
            definitions, index = _SYNC_CACHE
            return list(definitions), dict(index)
        return [], {}
    except Exception as exc:
        logger.warning("Earth-Agent direct tool discovery unavailable: %s", exc)
        if _SYNC_CACHE is not None:
            definitions, index = _SYNC_CACHE
            return list(definitions), dict(index)
        return [], {}

    _SYNC_CACHE = loaded
    definitions, index = loaded
    return list(definitions), dict(index)


async def load_earth_agent_registry_async(
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, RegisteredEarthAgentTool]]:
    """Async version of tool discovery — call from FastAPI startup event.

    This populates _SYNC_CACHE so that subsequent sync calls return cached results
    without needing asyncio.run().
    """
    global _SYNC_CACHE

    if _SYNC_CACHE is not None and not force_refresh:
        definitions, index = _SYNC_CACHE
        return list(definitions), dict(index)

    try:
        client = EarthAgentMCPClient()
        registry = EarthAgentToolRegistry(client)
        try:
            definitions = await registry.build_tool_definitions(force_refresh=force_refresh)
            loaded = (definitions, registry.entries())
        finally:
            await client.close()

        _SYNC_CACHE = loaded
        definitions, index = loaded
        logger.info(f"Earth-Agent async discovery: {len(definitions)} direct tools registered")
        return list(definitions), dict(index)
    except Exception as exc:
        logger.warning("Earth-Agent async tool discovery failed: %s", exc)
        return [], {}
