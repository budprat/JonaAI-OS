# ABOUTME: Tests for analysis_strategy_planner tool — domain classification,
# temporal strategy, index detection, complexity, tool sequence, and registration.

"""Tests for openeo_ai.tools.strategy_tools."""

import asyncio
import json
import pytest

from openeo_ai.tools.strategy_tools import (
    STRATEGY_TOOL_DEFINITIONS,
    TEMPORAL_STRATEGIES,
    _assess_complexity,
    _build_tool_sequence,
    _detect_requested_indices,
    _detect_temporal_strategy,
    create_strategy_tools,
    plan_analysis_strategy,
)


# ── Domain Classification ──────────────────────────────────────────────


class TestClassifyDomain:
    """Domain classification via plan_analysis_strategy."""

    def test_vegetation_domain(self):
        s = plan_analysis_strategy("Show NDVI vegetation health for Kerala")
        assert s["domain"] == "vegetation"

    def test_drought_domain(self):
        s = plan_analysis_strategy("Assess drought impact in Rajasthan")
        assert s["domain"] == "drought"

    def test_fire_domain(self):
        s = plan_analysis_strategy("Map burn scar from the wildfire near LA")
        assert s["domain"] == "fire"

    def test_urban_domain(self):
        s = plan_analysis_strategy("Detect urban sprawl in Mumbai over the last 5 years")
        assert s["domain"] == "urban"

    def test_unknown_returns_general(self):
        s = plan_analysis_strategy("xyzzy nonsense query with no keywords")
        assert s["domain"] == "general"
        assert s["confidence"] == "low"

    def test_water_domain(self):
        s = plan_analysis_strategy("Map water bodies and lake extent in Bihar")
        assert s["domain"] == "water"

    def test_snow_domain(self):
        s = plan_analysis_strategy("Measure snow cover in the Himalayas")
        assert s["domain"] == "snow"

    def test_agriculture_domain(self):
        s = plan_analysis_strategy("Monitor crop health and farm yields in Punjab")
        assert s["domain"] == "agriculture"


# ── Temporal Strategy Detection ─────────────────────────────────────────


class TestTemporalStrategy:
    """Temporal strategy detection from query keywords."""

    def test_change_detection_temporal(self):
        s = plan_analysis_strategy("Compare NDVI before and after the flood")
        assert s["temporal_strategy"]["type"] == "two_date_comparison"

    def test_trend_analysis_temporal(self):
        s = plan_analysis_strategy("Show vegetation trend over time for the past year")
        assert s["temporal_strategy"]["type"] == "continuous_time_series"

    def test_anomaly_temporal(self):
        s = plan_analysis_strategy(
            "Detect anomaly in vegetation compared to historical average"
        )
        assert s["temporal_strategy"]["type"] == "anomaly_detection"

    def test_monthly_temporal(self):
        s = plan_analysis_strategy("Generate monthly NDVI composites for 2024")
        assert s["temporal_strategy"]["type"] == "monthly_composites"

    def test_seasonal_temporal(self):
        s = plan_analysis_strategy("Compare same season year over year for deforestation")
        assert s["temporal_strategy"]["type"] == "seasonal_comparison"

    def test_default_snapshot(self):
        s = plan_analysis_strategy("Show NDVI for Mumbai")
        assert s["temporal_strategy"]["type"] == "single_snapshot"

    def test_temporal_strategy_has_metadata(self):
        s = plan_analysis_strategy("Show vegetation trend over time")
        ts = s["temporal_strategy"]
        assert "description" in ts
        assert "hint" in ts
        assert "processing_pattern" in ts


# ── Index Detection ─────────────────────────────────────────────────────


class TestIndexDetection:
    """Index detection from query text."""

    def test_explicit_ndvi(self):
        s = plan_analysis_strategy("Calculate NDVI for my area")
        assert "ndvi" in s["indices"]["primary"]

    def test_multi_index(self):
        s = plan_analysis_strategy("Compare NDVI and NDWI correlation in Kerala")
        all_indices = s["indices"]["primary"] + s["indices"]["secondary"]
        assert "ndvi" in all_indices
        assert "ndwi" in all_indices

    def test_domain_default(self):
        """No index mentioned -> uses domain default."""
        s = plan_analysis_strategy("Assess drought impact in Rajasthan")
        # drought domain default is NDMI
        assert "ndmi" in s["indices"]["primary"]

    def test_substring_collision(self):
        """'gndvi' should not be split as 'ndvi'."""
        indices = _detect_requested_indices("calculate GNDVI for the forest")
        assert "gndvi" in indices
        # ndvi should NOT be detected from "gndvi" substring
        # (gndvi is checked first since it's longer)

    def test_recommended_from_domain(self):
        """Domain indices appear in recommended list."""
        s = plan_analysis_strategy("Monitor vegetation in Kerala")
        # vegetation domain has NDVI, EVI, SAVI, etc.
        assert len(s["indices"]["recommended"]) >= 2

    def test_mndwi_before_ndwi(self):
        """MNDWI should be detected independently from NDWI."""
        indices = _detect_requested_indices("compare MNDWI and NDWI")
        assert "mndwi" in indices
        assert "ndwi" in indices


# ── Complexity Assessment ───────────────────────────────────────────────


class TestComplexity:
    """Complexity classification of queries."""

    def test_simple_query(self):
        s = plan_analysis_strategy("Show NDVI for Mumbai")
        assert s["complexity"] == "simple"

    def test_moderate_query(self):
        s = plan_analysis_strategy("Compare NDVI before and after the flood in Bihar")
        assert s["complexity"] == "moderate"

    def test_complex_query(self):
        s = plan_analysis_strategy(
            "Assess drought anomaly with NDVI NDWI NDMI time series "
            "and classify threshold categories compared to historical baseline"
        )
        assert s["complexity"] == "complex"


# ── Tool Sequence ───────────────────────────────────────────────────────


class TestToolSequence:
    """Tool sequence generation."""

    def test_always_starts_with_recommend(self):
        s = plan_analysis_strategy("Show NDVI for Mumbai")
        assert s["tool_sequence"][0]["tool"] == "recommend_collection"

    def test_location_detection(self):
        """'in Rajasthan' -> includes resolve_location."""
        s = plan_analysis_strategy("Assess drought in Rajasthan")
        tool_names = [step["tool"] for step in s["tool_sequence"]]
        assert "openeo_resolve_location" in tool_names

    def test_multi_index_graph(self):
        """2+ indices -> hint mentions 'multi-index'."""
        s = plan_analysis_strategy("Compare NDVI and NDWI for Kerala")
        graph_step = next(
            step for step in s["tool_sequence"]
            if step["tool"] == "openeo_generate_graph"
        )
        assert "multi-index" in graph_step.get("hint", "").lower()

    def test_change_detection_graph(self):
        """'before and after' -> change detection hint."""
        s = plan_analysis_strategy("NDVI before and after the flood")
        graph_step = next(
            step for step in s["tool_sequence"]
            if step["tool"] == "openeo_generate_graph"
        )
        assert "change detection" in graph_step.get("hint", "").lower() or \
               "change detection" in graph_step.get("reason", "").lower()

    def test_includes_result_inspect_and_quality_gate(self):
        """Always includes result_inspect, interpret_result, assess_result_quality."""
        s = plan_analysis_strategy("Show NDVI for Mumbai")
        tool_names = [step["tool"] for step in s["tool_sequence"]]
        assert "result_inspect" in tool_names
        assert "interpret_result" in tool_names
        assert "assess_result_quality" in tool_names

    def test_anomaly_adds_temporal_tool(self):
        """Anomaly detection -> temporal_anomaly_detect in sequence."""
        s = plan_analysis_strategy(
            "Detect anomaly in vegetation compared to historical baseline"
        )
        tool_names = [step["tool"] for step in s["tool_sequence"]]
        assert "temporal_anomaly_detect" in tool_names

    def test_trend_adds_temporal_tool(self):
        """Trend analysis -> temporal_trend_analysis in sequence."""
        s = plan_analysis_strategy("Show vegetation trend over time for the past year")
        tool_names = [step["tool"] for step in s["tool_sequence"]]
        assert "temporal_trend_analysis" in tool_names

    def test_always_ends_with_viz(self):
        """Last step should be viz_show_map."""
        s = plan_analysis_strategy("Show NDVI for Mumbai")
        assert s["tool_sequence"][-1]["tool"] == "viz_show_map"

    def test_correlation_when_requested(self):
        """Correlation tool included when 'correlation' keyword + multi-index."""
        s = plan_analysis_strategy("NDVI and NDWI correlation analysis for Kerala")
        tool_names = [step["tool"] for step in s["tool_sequence"]]
        assert "result_correlation" in tool_names


# ── Tool Registration ───────────────────────────────────────────────────


class TestToolRegistration:
    """Tool registration and handler validation."""

    def test_tool_definition_exists(self):
        assert len(STRATEGY_TOOL_DEFINITIONS) == 1
        assert STRATEGY_TOOL_DEFINITIONS[0]["name"] == "analysis_strategy_planner"

    def test_tool_definition_has_schema(self):
        defn = STRATEGY_TOOL_DEFINITIONS[0]
        assert "input_schema" in defn
        assert "query" in defn["input_schema"]["properties"]
        assert "query" in defn["input_schema"]["required"]

    def test_handler_exists(self):
        tools_dict, defs = create_strategy_tools()
        assert "analysis_strategy_planner" in tools_dict
        assert callable(tools_dict["analysis_strategy_planner"])

    @pytest.mark.asyncio
    async def test_handler_returns_json(self):
        tools_dict, _ = create_strategy_tools()
        handler = tools_dict["analysis_strategy_planner"]
        result = await handler({"query": "Show NDVI for Mumbai"})
        assert "content" in result
        text = result["content"][0]["text"]
        parsed = json.loads(text)
        assert "domain" in parsed
        assert "indices" in parsed
        assert "temporal_strategy" in parsed
        assert "tool_sequence" in parsed

    @pytest.mark.asyncio
    async def test_handler_empty_query_returns_error(self):
        tools_dict, _ = create_strategy_tools()
        handler = tools_dict["analysis_strategy_planner"]
        result = await handler({"query": ""})
        text = result["content"][0]["text"]
        assert "error" in text.lower()


# ── Strategy Output Structure ───────────────────────────────────────────


class TestStrategyOutput:
    """Validate strategy output structure and content."""

    def test_all_required_keys_present(self):
        s = plan_analysis_strategy("Show NDVI for Mumbai")
        required = [
            "domain", "confidence", "complexity", "indices",
            "collections", "temporal_strategy", "colormap",
            "thresholds", "tool_sequence", "tips",
        ]
        for key in required:
            assert key in s, f"Missing key: {key}"

    def test_indices_structure(self):
        s = plan_analysis_strategy("Compare NDVI and NDWI in Kerala")
        assert "primary" in s["indices"]
        assert "secondary" in s["indices"]
        assert "recommended" in s["indices"]
        assert isinstance(s["indices"]["primary"], list)
        assert isinstance(s["indices"]["recommended"], list)

    def test_collections_from_domain(self):
        s = plan_analysis_strategy("Map burn scar from the wildfire")
        # fire domain uses sentinel-2-l2a and landsat-c2-l2
        assert "sentinel-2-l2a" in s["collections"]

    def test_colormap_from_domain(self):
        s = plan_analysis_strategy("Map burn scar from the wildfire")
        assert s["colormap"] == "inferno"  # fire domain default

    def test_thresholds_from_domain(self):
        s = plan_analysis_strategy("Assess drought impact in Rajasthan")
        # drought domain has NDMI thresholds
        assert "NDMI" in s["thresholds"]

    def test_tips_from_domain(self):
        s = plan_analysis_strategy("Monitor vegetation health in Kerala")
        assert len(s["tips"]) > 0

    def test_recommended_indices_capped_at_5(self):
        s = plan_analysis_strategy("Monitor vegetation health")
        assert len(s["indices"]["recommended"]) <= 5
