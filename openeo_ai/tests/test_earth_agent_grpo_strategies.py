# ABOUTME: Tests Earth-Agent GRPO strategy loading, categorization, and formatting.

from pathlib import Path

import pytest

from openeo_ai.earth_agent.grpo_strategies import (
    detect_analysis_type,
    detect_data_type,
    detect_query_type,
    get_relevant_strategies,
    load_strategy_experiences,
)

# The real GRPO output lives under the gitignored Earth-Agent clone, so it is
# absent in CI. Pin loading to a committed copy of that output via the module's
# own EARTH_AGENT_GRPO_EXPERIENCES override, keeping these tests hermetic and
# deterministic regardless of whether Earth-Agent is checked out locally.
_GRPO_FIXTURE = Path(__file__).parent / "fixtures" / "grpo_experiences.json"


@pytest.fixture(autouse=True)
def _pin_grpo_experiences(monkeypatch):
    monkeypatch.setenv("EARTH_AGENT_GRPO_EXPERIENCES", str(_GRPO_FIXTURE))


def test_strategy_experiences_load():
    experiences = load_strategy_experiences()

    assert len(experiences) >= 10


def test_detect_query_type_for_vegetation_prompt():
    assert detect_query_type("Analyze NDVI deforestation hotspots") == "vegetation"


def test_detect_data_type_from_context_prefers_temporal_when_multiple_files():
    detected = detect_data_type("ea_compute_linear_trend", {"input_files": ["a.tif", "b.tif"]})

    assert detected == "multi_temporal"


def test_detect_analysis_type_from_tool_input_query():
    detected = detect_analysis_type(
        "ea_threshold_segmentation",
        {"query": "Find flood extent in this NDWI raster"},
    )

    assert detected == "water"


def test_get_relevant_strategies_returns_formatted_markdown():
    text = get_relevant_strategies("water", "spectral_index")

    assert "Earth-Agent Strategy" in text
    assert "Experience" in text
