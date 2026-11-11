"""Prompt construction for Earth-Agent meta-tool execution.

CRITICAL: The GRPO-learned strategies MUST be injected into the ReAct agent's
system prompt. The original Earth-Agent embeds all 15 experiences directly in
the system prompt so the LLM sees them during every Thought phase. If strategies
are only available as a separate SDK tool (get_analysis_strategies), the inner
ReAct agent never sees them — breaking the core GRPO innovation.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


def _format_statistics(statistics: Any) -> str:
    if not statistics:
        return "Not provided"
    if isinstance(statistics, str):
        return statistics
    try:
        return json.dumps(statistics, ensure_ascii=True, sort_keys=True)
    except TypeError:
        return str(statistics)


def _load_enhanced_system_prompt() -> str:
    """Load the battle-tested system prompt from EA enhanced config.

    This is the original Earth-Agent system prompt with all 15 GRPO experiences
    embedded. It was composed during GRPO training and is calibrated for optimal
    tool selection.

    Falls back to a minimal geoscientist prompt if the config is unavailable.
    """
    # Check environment override first
    config_path_env = os.getenv("EARTH_AGENT_ENHANCED_CONFIG")
    if config_path_env:
        config_path = Path(config_path_env).expanduser().resolve()
    else:
        config_path = (
            Path(__file__).resolve().parents[2]
            / "Earth-Agent"
            / "training_free_results_deepseek"
            / "earth_agent_practice_deepseek_enhanced_config.json"
        )

    try:
        if config_path.exists():
            with open(config_path) as f:
                config = json.load(f)
            system_prompt = config.get("system_prompt", "")
            if system_prompt:
                logger.info(
                    f"Loaded EA enhanced system prompt: {len(system_prompt)} chars, "
                    f"{system_prompt.count('[Experience')} experiences embedded"
                )
                return system_prompt
    except Exception as e:
        logger.warning(f"Failed to load EA enhanced config: {e}")

    # Fallback: minimal geoscientist prompt without GRPO
    logger.warning("EA enhanced config not found — using minimal system prompt (no GRPO strategies)")
    return (
        "You are a geoscientist analyzing Earth observation data. "
        "Use tools to examine the input files and provide quantitative answers. "
        "When a tool returns 'Result saved at /path/to/file', you MUST use the "
        "full returned path in all subsequent tool calls. "
        "If a tool returns an error, you can only try again once."
    )


# Cache the system prompt at module level (loaded once)
_CACHED_SYSTEM_PROMPT: str | None = None


def _get_system_prompt() -> str:
    """Get cached system prompt."""
    global _CACHED_SYSTEM_PROMPT
    if _CACHED_SYSTEM_PROMPT is None:
        _CACHED_SYSTEM_PROMPT = _load_enhanced_system_prompt()
    return _CACHED_SYSTEM_PROMPT


def build_analysis_prompt(
    query: str,
    input_files: Sequence[str],
    data_context: Mapping[str, Any] | None = None,
    analysis_mode: str = "auto",
    max_steps: int = 20,
) -> str:
    """Build the Earth-Agent analysis prompt with GRPO strategies injected.

    This is the prompt that the inner ReAct agent receives. It combines:
    1. The original EA system prompt (with all 15 GRPO experiences)
    2. JonaAI-specific metadata (file paths, CRS, bands, statistics)
    3. The user's analysis query

    The GRPO experiences guide the ReAct agent's Thought phase and tool
    selection — this is the core innovation of Earth-Agent's POMDP approach.
    """
    context = dict(data_context or {})

    # Get the full EA system prompt with embedded GRPO strategies
    system_prompt = _get_system_prompt()

    # Build JonaAI-specific context section
    jona_context = f"""
## JonaAI Data Context
- File(s): {", ".join(input_files)}
- Data type: {context.get("data_type", "Unknown")}
- CRS: {context.get("crs", "Unknown")}
- Bounds: {context.get("bounds", "Unknown")}
- Resolution: {context.get("resolution", "Unknown")}
- Bands: {context.get("bands", "Unknown")}
- Time range: {context.get("time_range", "Unknown")}
- Process applied: {context.get("process_applied", "Unknown")}
- Statistics: {_format_statistics(context.get("statistics"))}
"""

    # Compose the full prompt: EA system prompt + JonaAI context + query
    prompt = f"""{system_prompt}

{jona_context}

## Analysis Request
{query}

## Execution Constraints
- Planning mode: {analysis_mode}
- Maximum steps: {max_steps}
- IMPORTANT: Use the full file paths provided above. When tools return new file paths, use those exact paths in subsequent calls.
- If you generate output files, provide their full paths for visualization.
"""
    return prompt
