# ABOUTME: Prompt registry — Langfuse prompt management with code fallback (Phase C).

"""
Prompt registry for JonaAI.

Fetches system prompts from Langfuse prompt management so operators can
version prompts, A/B test, and iterate without redeploying. Falls back to
the constants embedded in `openeo_ai.sdk.client` if Langfuse is unavailable
or the prompt isn't registered yet — the app always boots.

Cache strategy:
    `@lru_cache` keeps the fetched prompts in-memory for the life of the
    process. Langfuse doesn't currently notify us of new versions, so the
    trade-off is: prompt changes require a server restart to take effect.
    This is fine for our workflow (deploys are cheap) and avoids per-query
    network round-trips to the Langfuse API.

Usage (from claude_sdk_bridge.py):
    from .prompts import get_prompt
    text, lf_prompt = get_prompt("jonaai/chat-assistant", fallback_attr="SYSTEM_PROMPT")
    opts.system_prompt = text
    # Pass lf_prompt to update_current_span(prompt=lf_prompt) to link generation
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Optional, Tuple

logger = logging.getLogger(__name__)


# Mapping of Langfuse prompt names → code-level fallback attribute on OpenEOAIClient or sdk.client
# Kept here (not inside the function) so the migration script can introspect it
# and upload exactly the same six prompts.
PROMPT_REGISTRY: dict[str, str] = {
    "jonaai/chat-assistant": "SYSTEM_PROMPT",
    "jonaai/multi-agent/planner": "PLANNER_SYSTEM_PROMPT",
    "jonaai/multi-agent/data-specialist": "DATA_AGENT_SYSTEM_PROMPT",
    "jonaai/multi-agent/viz-specialist": "VIZ_AGENT_SYSTEM_PROMPT",
    "jonaai/multi-agent/ml-specialist": "ML_AGENT_SYSTEM_PROMPT",
    "jonaai/multi-agent/qa-specialist": "QA_AGENT_SYSTEM_PROMPT",
}


def _fallback_text(attr: str) -> str:
    """Fetch the prompt text from the code-level constants.

    SYSTEM_PROMPT lives on OpenEOAIClient; the rest are module-level
    in sdk.client. Import lazily to avoid a circular at module load.
    """
    from openeo_ai.sdk import client as _client

    # Class-level constant lives on OpenEOAIClient
    if attr == "SYSTEM_PROMPT":
        return _client.OpenEOAIClient.SYSTEM_PROMPT
    return getattr(_client, attr)


@lru_cache(maxsize=32)
def get_prompt(
    name: str,
    fallback_attr: str,
    label: str = "production",
) -> Tuple[str, Optional[Any]]:
    """Fetch a prompt from Langfuse, with code fallback.

    Args:
        name: Langfuse prompt name (e.g. "jonaai/chat-assistant").
        fallback_attr: Name of the constant on `openeo_ai.sdk.client` (or
            `OpenEOAIClient`) to use if Langfuse is unavailable.
        label: Langfuse label to fetch (default "production").

    Returns:
        (prompt_text, langfuse_prompt_object_or_none)

        The second value is the raw Langfuse prompt object when available,
        suitable for passing to `update_current_span(prompt=...)` to link
        the generation to the prompt version in the Langfuse UI.
    """
    from openeo_ai.langfuse_setup import get_langfuse_client

    client = get_langfuse_client()
    if client is None:
        logger.debug("[prompts] %s: Langfuse unavailable, using code fallback", name)
        return (_fallback_text(fallback_attr), None)

    try:
        p = client.get_prompt(name, label=label)
        # For chat prompts, .prompt is a list of messages; for text prompts it's a string.
        # We use plain text for system_prompt, so extract from either shape.
        raw = p.prompt
        if isinstance(raw, list):
            # Chat format: [{"role": "system", "content": "..."}]
            for msg in raw:
                if isinstance(msg, dict) and msg.get("role") == "system":
                    return (msg.get("content", ""), p)
            # Fallback: concatenate all content
            parts = [m.get("content", "") for m in raw if isinstance(m, dict)]
            return ("\n\n".join(parts), p)
        return (str(raw), p)
    except Exception as exc:  # noqa: BLE001 — fall back gracefully
        logger.warning(
            "[prompts] %s not available in Langfuse (%s) — using code fallback",
            name,
            exc,
        )
        return (_fallback_text(fallback_attr), None)


def clear_cache() -> None:
    """Drop the prompt cache (for admin API use or tests)."""
    get_prompt.cache_clear()
