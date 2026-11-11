# ABOUTME: Tests for validate_pre_execution semantic pre-flight validation.
# Covers band availability, temporal sanity, extent size, domain-index match,
# cloud masking hints, and the validation factory function.

"""
Tests for openeo_ai.tools.validation_tools.validate_pre_execution.

Each test builds a goal dict simulating the output of extract_analysis_goal
and verifies the checks, errors, and warnings returned by the validator.
"""

import json
import pytest
from typing import Any, Dict, List

from openeo_ai.tools.validation_tools import (
    validate_pre_execution,
    create_validation_tools,
    VALIDATION_TOOL_DEFINITIONS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_goal(**overrides) -> Dict[str, Any]:
    """Return a minimal valid goal dict, merging any overrides."""
    goal = {
        "analysis_type": "vegetation_index",
        "index": "ndvi",
        "domain": "vegetation",
        "collection": "sentinel-2-l2a",
        "spatial_extent": {"west": 13.0, "south": 52.0, "east": 14.0, "north": 53.0},
        "temporal_extent": ["2024-01-01", "2024-03-01"],
        "temporal_strategy": "single_snapshot",
        "bands": [],
    }
    goal.update(overrides)
    return goal


def _parse_result(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the JSON payload from the MCP-style tool return."""
    text = raw["content"][0]["text"]
    return json.loads(text)


def _checks_by_name(result: Dict[str, Any], check_name: str) -> List[Dict[str, str]]:
    """Filter result checks to those matching *check_name*."""
    return [c for c in result.get("checks", []) if c["check"] == check_name]


# ===========================================================================
# 1. Band availability
# ===========================================================================


class TestBandAvailability:
    """Verify band-availability checks against COLLECTION_BANDS and INDEX_SPECS."""

    @pytest.mark.asyncio
    async def test_valid_ndvi_sentinel2(self):
        """NDVI on sentinel-2-l2a should pass -- nir and red are both available."""
        goal = _base_goal(index="ndvi", collection="sentinel-2-l2a")
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        band_checks = _checks_by_name(result, "band_availability")
        assert len(band_checks) == 1
        assert band_checks[0]["level"] == "info"
        assert result["valid"] is True

    @pytest.mark.asyncio
    async def test_missing_bands_for_index(self):
        """NBR requires nir+swir22; cop-dem-glo-30 only has 'data' -> error."""
        goal = _base_goal(index="nbr", collection="cop-dem-glo-30")
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        band_checks = _checks_by_name(result, "band_availability")
        assert len(band_checks) == 1
        assert band_checks[0]["level"] == "error"
        assert "not available" in band_checks[0]["message"]
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_unknown_index_skips_check(self):
        """An index not in INDEX_SPECS should not produce a band_availability check."""
        goal = _base_goal(index="totally_made_up_index_xyz")
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        band_checks = _checks_by_name(result, "band_availability")
        assert len(band_checks) == 0


# ===========================================================================
# 2. Temporal sanity
# ===========================================================================


class TestTemporalSanity:
    """Verify temporal-strategy-related warnings."""

    @pytest.mark.asyncio
    async def test_change_detection_single_date(self):
        """change_detection with only one date should produce a warning."""
        goal = _base_goal(
            temporal_strategy="change_detection",
            temporal_extent=["2024-01-01"],
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        temporal_checks = _checks_by_name(result, "temporal_sanity")
        assert any(c["level"] == "warning" for c in temporal_checks)
        assert any("two temporal windows" in c["message"] for c in temporal_checks)

    @pytest.mark.asyncio
    async def test_change_detection_two_dates(self):
        """change_detection with two dates should NOT warn about missing windows."""
        goal = _base_goal(
            temporal_strategy="change_detection",
            temporal_extent=["2024-01-01", "2024-06-01"],
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        temporal_checks = _checks_by_name(result, "temporal_sanity")
        # Should not contain the 'two temporal windows' warning
        assert not any("two temporal windows" in c["message"] for c in temporal_checks)

    @pytest.mark.asyncio
    async def test_time_series_short_range(self):
        """time_series with only 30 days should warn about insufficient data."""
        goal = _base_goal(
            temporal_strategy="time_series",
            temporal_extent=["2024-01-01", "2024-01-31"],
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        temporal_checks = _checks_by_name(result, "temporal_sanity")
        assert any(c["level"] == "warning" for c in temporal_checks)
        assert any("6 months" in c["message"] for c in temporal_checks)


# ===========================================================================
# 3. Extent size
# ===========================================================================


class TestExtentSize:
    """Verify spatial-extent size checks."""

    @pytest.mark.asyncio
    async def test_too_large_extent(self):
        """A 10x10 degree bbox should produce an extent_size error."""
        goal = _base_goal(
            spatial_extent={"west": 0.0, "south": 0.0, "east": 10.0, "north": 10.0}
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        extent_checks = _checks_by_name(result, "extent_size")
        assert len(extent_checks) == 1
        assert extent_checks[0]["level"] == "error"
        assert result["valid"] is False

    @pytest.mark.asyncio
    async def test_valid_extent(self):
        """A 1x1 degree bbox should NOT produce an extent_size error."""
        goal = _base_goal(
            spatial_extent={"west": 13.0, "south": 52.0, "east": 14.0, "north": 53.0}
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        extent_checks = _checks_by_name(result, "extent_size")
        assert len(extent_checks) == 0


# ===========================================================================
# 4. Domain-index match
# ===========================================================================


class TestDomainIndexMatch:
    """Verify that mismatched domain/index combos trigger warnings."""

    @pytest.mark.asyncio
    async def test_matching_domain_index(self):
        """NDVI in 'vegetation' domain -- no mismatch warning expected."""
        goal = _base_goal(index="ndvi", domain="vegetation")
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        domain_checks = _checks_by_name(result, "domain_index_match")
        assert len(domain_checks) == 0

    @pytest.mark.asyncio
    async def test_mismatched_domain_index(self):
        """NDBI is an urban index; using it in 'vegetation' domain -> warning."""
        goal = _base_goal(index="ndbi", domain="vegetation")
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        domain_checks = _checks_by_name(result, "domain_index_match")
        assert len(domain_checks) == 1
        assert domain_checks[0]["level"] == "warning"
        assert "not among the recommended" in domain_checks[0]["message"]


# ===========================================================================
# 5. Cloud masking
# ===========================================================================


class TestCloudMasking:
    """Verify cloud-masking advisory checks."""

    @pytest.mark.asyncio
    async def test_no_cloud_masking_warning(self):
        """Optical index on S2 without SCL in bands -> cloud_masking warning."""
        goal = _base_goal(
            index="ndvi",
            collection="sentinel-2-l2a",
            bands=[],  # no SCL
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        cloud_checks = _checks_by_name(result, "cloud_masking")
        assert len(cloud_checks) == 1
        assert cloud_checks[0]["level"] == "warning"
        assert "SCL" in cloud_checks[0]["message"]

    @pytest.mark.asyncio
    async def test_non_optical_no_warning(self):
        """DEM/terrain analysis (non-optical) should NOT trigger cloud warning."""
        goal = _base_goal(
            analysis_type="terrain",
            index="",
            domain="terrain",
            collection="cop-dem-glo-30",
            bands=[],
        )
        raw = await validate_pre_execution({"goal": goal})
        result = _parse_result(raw)

        cloud_checks = _checks_by_name(result, "cloud_masking")
        assert len(cloud_checks) == 0


# ===========================================================================
# 6. Validation factory
# ===========================================================================


class TestValidationFactory:
    """Verify create_validation_tools returns expected structure."""

    def test_factory_returns_handlers(self):
        """create_validation_tools should return handlers for both tools."""
        handlers, definitions = create_validation_tools()
        assert "openeo_validate_graph" in handlers
        assert "validate_pre_execution" in handlers
        assert callable(handlers["openeo_validate_graph"])
        assert callable(handlers["validate_pre_execution"])

    def test_definitions_include_pre_execution(self):
        """VALIDATION_TOOL_DEFINITIONS should contain validate_pre_execution."""
        names = [d["name"] for d in VALIDATION_TOOL_DEFINITIONS]
        assert "validate_pre_execution" in names
        # Also check that the definition has the expected schema fields
        pre_exec_def = next(
            d for d in VALIDATION_TOOL_DEFINITIONS if d["name"] == "validate_pre_execution"
        )
        assert "goal" in pre_exec_def["input_schema"]["properties"]
        assert "goal" in pre_exec_def["input_schema"]["required"]
