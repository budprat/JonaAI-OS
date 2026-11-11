"""Load and format learned Earth-Agent GRPO analysis strategies."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True, slots=True)
class StrategyExperience:
    """One learned Earth-Agent strategy experience."""

    experience_id: str
    text: str
    query_types: tuple[str, ...]
    data_types: tuple[str, ...]


QUERY_TYPE_KEYWORDS = {
    "vegetation": (
        "ndvi", "vegetation", "forest", "crop", "canopy", "greenness", "lai",
    ),
    "water": (
        "water", "ndwi", "lake", "river", "flood", "shoreline", "ship",
    ),
    "urban": (
        "urban", "city", "built-up", "road", "impervious", "nighttime lights",
    ),
    "change_detection": (
        "change", "before", "after", "comparison", "difference", "growth",
    ),
    "climate": (
        "temperature", "precipitation", "trend", "anomaly", "drought", "tvdi",
    ),
    "disaster": (
        "burn", "fire", "wildfire", "damage", "disaster", "hotspot",
    ),
    "agriculture": (
        "agriculture", "crop", "soil moisture", "harvest", "field",
    ),
    "terrain": (
        "elevation", "dem", "terrain", "slope", "topography",
    ),
    "object_detection": (
        "object", "detect", "count", "segmentation", "classification", "ship", "aircraft", "tank",
    ),
}

DATA_TYPE_KEYWORDS = {
    "spectral_index": ("ndvi", "ndwi", "nbr", "index", "dnbr", "tvdi"),
    "rgb": ("rgb", "optical", "color", "true color"),
    "multi_temporal": ("trend", "time series", "monthly", "seasonal", "per-date", "per month"),
    "thermal": ("lst", "temperature", "thermal", "brightness temperature"),
    "sar": ("sar", "radar", "sentinel-1", "backscatter"),
}


def _default_experiences_path() -> Path:
    """Get path to GRPO experiences file.

    Prefers the enhanced config (which has the complete system prompt + experiences),
    falls back to raw experiences file.
    """
    configured = os.getenv("EARTH_AGENT_GRPO_EXPERIENCES")
    if configured:
        return Path(configured).expanduser().resolve(strict=False)

    base = Path(__file__).resolve().parents[2] / "Earth-Agent" / "training_free_results_deepseek"

    # Prefer enhanced config — it has the battle-tested prompt + experiences
    enhanced = base / "earth_agent_practice_deepseek_enhanced_config.json"
    if enhanced.exists():
        return enhanced

    # Fallback to raw experiences
    return base / "earth_agent_practice_deepseek_experiences.json"


def _match_labels(text: str, keywords: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    lower = text.lower()
    matched = [
        label
        for label, patterns in keywords.items()
        if any(pattern in lower for pattern in patterns)
    ]
    return tuple(matched)


@lru_cache(maxsize=1)
def load_strategy_experiences() -> tuple[StrategyExperience, ...]:
    """Load learned experiences from the Earth-Agent GRPO output."""
    path = _default_experiences_path()
    if not path.exists():
        return ()
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)

    # Handle both formats:
    # 1. Enhanced config: {"system_prompt": ..., "experiences": {"0": "text", ...}, ...}
    # 2. Raw experiences: {"0": "text", "1": "text", ...}
    if "experiences" in raw and isinstance(raw["experiences"], dict):
        exp_dict = raw["experiences"]
    else:
        exp_dict = raw

    experiences: list[StrategyExperience] = []
    for experience_id, text in exp_dict.items():
        if not isinstance(text, str):
            continue
        query_types = _match_labels(text, QUERY_TYPE_KEYWORDS) or ("general",)
        data_types = _match_labels(text, DATA_TYPE_KEYWORDS)
        experiences.append(
            StrategyExperience(
                experience_id=str(experience_id),
                text=text,
                query_types=query_types,
                data_types=data_types,
            )
        )
    return tuple(experiences)


def detect_query_type(text: str) -> str:
    """Infer a strategy category from free text."""
    matched = _match_labels(text, QUERY_TYPE_KEYWORDS)
    return matched[0] if matched else "general"


def detect_data_type_from_context(value: Any) -> str:
    """Infer data type from a prompt, tool input, or context value."""
    if isinstance(value, dict):
        for key in ("data_type", "query", "original_query", "process_applied"):
            if key in value and isinstance(value[key], str):
                detected = detect_data_type_from_context(value[key])
                if detected != "general":
                    return detected
        stats = value.get("statistics")
        if isinstance(stats, dict):
            detected = detect_data_type_from_context(" ".join(str(k) for k in stats.keys()))
            if detected != "general":
                return detected
        if isinstance(value.get("input_files"), list) and len(value["input_files"]) > 1:
            return "multi_temporal"
        return "general"
    if isinstance(value, list):
        if len(value) > 1:
            return "multi_temporal"
        return detect_data_type_from_context(" ".join(str(item) for item in value))
    text = str(value).lower()
    matched = _match_labels(text, DATA_TYPE_KEYWORDS)
    return matched[0] if matched else "general"


def detect_analysis_type(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Infer the Earth-Agent analysis category for hook enrichment."""
    for candidate in (
        tool_input.get("query"),
        tool_input.get("analysis_query"),
        tool_input.get("original_query"),
        tool_input.get("data_context", {}).get("original_query")
        if isinstance(tool_input.get("data_context"), dict)
        else None,
        tool_name,
    ):
        if candidate:
            detected = detect_query_type(str(candidate))
            if detected != "general":
                return detected
    return "general"


def detect_data_type(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Infer the data type for a strategy request or hook context."""
    for candidate in (
        tool_input.get("data_type"),
        tool_input.get("data_context"),
        tool_input.get("input_files"),
        tool_input.get("query"),
        tool_name,
    ):
        if candidate:
            detected = detect_data_type_from_context(candidate)
            if detected != "general":
                return detected
    return "general"


def get_relevant_strategies(
    query_type: str,
    data_type: str | None = None,
    *,
    limit: int = 3,
) -> str:
    """Return formatted strategy guidance for the requested task."""
    experiences = load_strategy_experiences()
    if not experiences:
        return ""

    normalized_query = query_type or "general"
    normalized_data = data_type or "general"

    ranked = sorted(
        experiences,
        key=lambda exp: (
            normalized_query in exp.query_types,
            normalized_data in exp.data_types,
            "general" not in exp.query_types,
        ),
        reverse=True,
    )
    selected = [exp for exp in ranked if normalized_query in exp.query_types or normalized_query == "general"][:limit]
    if not selected:
        selected = list(ranked[:limit])

    lines = [
        f"## Earth-Agent Strategy: {normalized_query.replace('_', ' ').title()}",
        f"Data type: {normalized_data.replace('_', ' ').title()}",
        "",
        "Apply these learned patterns before chaining Earth-Agent tools:",
    ]
    for exp in selected:
        lines.append(f"{len(lines) - 3}. [Experience {exp.experience_id}] {exp.text}")
    lines.append("")
    lines.append("Use exact tool-returned file paths in subsequent Earth-Agent calls.")
    return "\n".join(lines)
