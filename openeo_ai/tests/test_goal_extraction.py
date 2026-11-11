# ABOUTME: Tests for goal extraction in goal_tools.py.
# Covers extract_analysis_goal, validate_goal_completeness, domain/analysis
# type classification, location extraction, coordinate parsing, and output detection.

"""
Tests for structured goal extraction from Earth Observation queries.

Tests:
- extract_analysis_goal: full query decomposition with confidence scoring
- validate_goal_completeness: pass/fail checks and conflict detection
- _classify_analysis_type: keyword and inference-based classification
- _extract_location_name: regex-based place name extraction
- _extract_coordinates: bbox coordinate parsing
- _detect_output_type: output format detection
- _infer_collection: collection inference from domain/indices
"""

import json
import pytest

from openeo_ai.tools.goal_tools import (
    extract_analysis_goal,
    validate_goal_completeness,
    AnalysisGoal,
    _classify_analysis_type,
    _extract_location_name,
    _extract_coordinates,
    _detect_output_type,
    _infer_collection,
)


def _parse_goal_response(response: dict) -> dict:
    """Helper to parse the JSON text from tool response content."""
    text = response["content"][0]["text"]
    return json.loads(text)


# ============================================================================
# TestExtractAnalysisGoal
# ============================================================================

class TestExtractAnalysisGoal:
    """Tests for the extract_analysis_goal async tool function."""

    @pytest.mark.asyncio
    async def test_extract_complete_query(self):
        """Complete query with index, location, and temporal info yields high confidence."""
        result = await extract_analysis_goal(
            {"query": "Show NDVI for Berlin in January 2024"}
        )
        goal = _parse_goal_response(result)

        assert goal["analysis_type"] == "vegetation_index"
        assert goal["index"] == "ndvi"
        assert goal["location_name"] == "Berlin"
        # All confidence scores should be at least 0.5 for a well-specified query
        for component, score in goal["confidence"].items():
            assert score >= 0.5, (
                f"Confidence for '{component}' was {score}, expected >= 0.5"
            )

    @pytest.mark.asyncio
    async def test_extract_missing_location(self):
        """Query without location yields low spatial confidence and flags missing spatial."""
        result = await extract_analysis_goal(
            {"query": "Calculate NDVI"}
        )
        goal = _parse_goal_response(result)

        assert goal["confidence"]["spatial"] < 0.5
        assert "spatial" in goal["missing_components"]

    @pytest.mark.asyncio
    async def test_extract_missing_temporal(self):
        """Query without dates yields low temporal confidence."""
        result = await extract_analysis_goal(
            {"query": "NDVI for Berlin"}
        )
        goal = _parse_goal_response(result)

        # No date/time reference in query, temporal confidence should be low
        assert goal["confidence"]["temporal"] <= 0.5

    @pytest.mark.asyncio
    async def test_extract_ambiguous_analysis(self):
        """Vague query yields empty or low-confidence analysis type."""
        result = await extract_analysis_goal(
            {"query": "Analyze this area"}
        )
        goal = _parse_goal_response(result)

        # "Analyze this area" has no index or domain keywords
        # analysis_type should be empty or domain confidence very low
        if goal["analysis_type"]:
            assert goal["confidence"]["domain"] < 0.5
        else:
            assert goal["analysis_type"] == ""

    @pytest.mark.asyncio
    async def test_extract_water_mapping(self):
        """NDWI water query maps to water_mapping analysis type."""
        result = await extract_analysis_goal(
            {"query": "Show NDWI water extent for Mumbai"}
        )
        goal = _parse_goal_response(result)

        assert goal["analysis_type"] == "water_mapping"
        assert goal["index"] == "ndwi"

    @pytest.mark.asyncio
    async def test_extract_with_coordinates(self):
        """Query containing explicit bbox coordinates yields spatial confidence 1.0."""
        result = await extract_analysis_goal(
            {"query": "Calculate NDVI for area 10.0,20.0,11.0,21.0"}
        )
        goal = _parse_goal_response(result)

        assert goal["spatial_extent"] is not None
        assert goal["spatial_extent"]["west"] == 10.0
        assert goal["spatial_extent"]["south"] == 20.0
        assert goal["spatial_extent"]["east"] == 11.0
        assert goal["spatial_extent"]["north"] == 21.0
        assert goal["confidence"]["spatial"] == 1.0


# ============================================================================
# TestValidateGoalCompleteness
# ============================================================================

class TestValidateGoalCompleteness:
    """Tests for the validate_goal_completeness async tool function."""

    @pytest.mark.asyncio
    async def test_validate_complete_goal(self):
        """Goal with all fields present passes validation."""
        goal = {
            "analysis_type": "vegetation_index",
            "index": "ndvi",
            "spatial_extent": {"west": 13.0, "south": 52.0, "east": 13.5, "north": 52.5},
            "location_name": "Berlin",
            "temporal_extent": ["2024-01-01", "2024-01-31"],
            "temporal_strategy": "single_snapshot",
            "collection": "sentinel-2-l2a",
            "domain": "vegetation",
        }
        result = await validate_goal_completeness({"goal": goal})
        parsed = _parse_goal_response(result)

        assert parsed["complete"] is True
        assert parsed["checks"]["analysis_type"]["status"] == "pass"
        assert parsed["checks"]["spatial"]["status"] == "pass"

    @pytest.mark.asyncio
    async def test_validate_incomplete_goal(self):
        """Goal missing spatial info fails validation."""
        goal = {
            "analysis_type": "vegetation_index",
            "index": "ndvi",
            "spatial_extent": None,
            "location_name": None,
            "temporal_extent": ["2024-01-01", "2024-01-31"],
            "collection": "sentinel-2-l2a",
        }
        result = await validate_goal_completeness({"goal": goal})
        parsed = _parse_goal_response(result)

        assert parsed["complete"] is False
        assert parsed["checks"]["spatial"]["status"] == "fail"

    @pytest.mark.asyncio
    async def test_validate_conflicting_params(self):
        """NDWI index with fire domain triggers conflict warning."""
        goal = {
            "analysis_type": "water_mapping",
            "index": "ndwi",
            "spatial_extent": {"west": 13.0, "south": 52.0, "east": 13.5, "north": 52.5},
            "location_name": "Berlin",
            "temporal_extent": ["2024-01-01", "2024-01-31"],
            "collection": "sentinel-2-l2a",
            "domain": "fire",
        }
        result = await validate_goal_completeness({"goal": goal})
        parsed = _parse_goal_response(result)

        assert "conflicts" in parsed["checks"]
        assert parsed["checks"]["conflicts"]["status"] == "warning"
        issues = parsed["checks"]["conflicts"]["issues"]
        assert any("Water index" in issue or "NDWI" in issue for issue in issues)

    @pytest.mark.asyncio
    async def test_validate_urban_veg_conflict(self):
        """NDBI index with vegetation domain triggers conflict warning."""
        goal = {
            "analysis_type": "urban_mapping",
            "index": "ndbi",
            "spatial_extent": {"west": 77.0, "south": 28.0, "east": 77.5, "north": 28.5},
            "location_name": "Delhi",
            "temporal_extent": ["2024-06-01", "2024-06-30"],
            "collection": "sentinel-2-l2a",
            "domain": "vegetation",
        }
        result = await validate_goal_completeness({"goal": goal})
        parsed = _parse_goal_response(result)

        assert "conflicts" in parsed["checks"]
        assert parsed["checks"]["conflicts"]["status"] == "warning"
        issues = parsed["checks"]["conflicts"]["issues"]
        assert any("NDBI" in issue and "vegetation" in issue for issue in issues)

    @pytest.mark.asyncio
    async def test_validate_missing_analysis_type(self):
        """Empty analysis_type causes validation failure."""
        goal = {
            "analysis_type": "",
            "index": None,
            "spatial_extent": {"west": 13.0, "south": 52.0, "east": 13.5, "north": 52.5},
            "location_name": "Berlin",
            "temporal_extent": ["2024-01-01", "2024-01-31"],
            "collection": "sentinel-2-l2a",
        }
        result = await validate_goal_completeness({"goal": goal})
        parsed = _parse_goal_response(result)

        assert parsed["complete"] is False
        assert parsed["checks"]["analysis_type"]["status"] == "fail"


# ============================================================================
# TestDomainClassification
# ============================================================================

class TestDomainClassification:
    """Tests for domain classification via extract_analysis_goal."""

    @pytest.mark.asyncio
    async def test_vegetation_domain(self):
        """Vegetation health queries classify to vegetation domain."""
        result = await extract_analysis_goal(
            {"query": "Assess vegetation health and greenness in Kerala"}
        )
        goal = _parse_goal_response(result)

        assert goal["domain"] == "vegetation"

    @pytest.mark.asyncio
    async def test_water_domain(self):
        """Water body mapping queries classify to water domain."""
        result = await extract_analysis_goal(
            {"query": "Map water body extent in Lake Chad"}
        )
        goal = _parse_goal_response(result)

        assert goal["domain"] == "water"

    @pytest.mark.asyncio
    async def test_unknown_domain(self):
        """Unrelated query yields empty or generic domain."""
        result = await extract_analysis_goal(
            {"query": "Hello, what can you do?"}
        )
        goal = _parse_goal_response(result)

        # Domain should be empty string (no matching keywords)
        assert goal["domain"] == ""


# ============================================================================
# TestAnalysisTypeClassification
# ============================================================================

class TestAnalysisTypeClassification:
    """Tests for _classify_analysis_type helper function."""

    def test_vegetation_index_type(self):
        """NDVI keyword maps to vegetation_index analysis type."""
        result = _classify_analysis_type("Calculate NDVI for the region", None, [])
        assert result == "vegetation_index"

    def test_change_detection_type(self):
        """Change detection keywords map to change_detection type."""
        result = _classify_analysis_type(
            "Perform change detection before and after the event", None, []
        )
        assert result == "change_detection"

    def test_infer_from_index(self):
        """MNDWI index infers water_mapping analysis type."""
        result = _classify_analysis_type("Calculate MNDWI", None, ["mndwi"])
        assert result == "water_mapping"


# ============================================================================
# TestExtractLocationName
# ============================================================================

class TestExtractLocationName:
    """Tests for _extract_location_name helper function."""

    def test_extract_simple_location(self):
        """Extracts location name after spatial preposition."""
        assert _extract_location_name("Show NDVI for Berlin") == "Berlin"

    def test_extract_multi_word_location(self):
        """Extracts multi-word capitalized location name."""
        assert _extract_location_name("Analyze area in New Delhi") == "New Delhi"

    def test_no_location(self):
        """Returns None when no location is found."""
        assert _extract_location_name("calculate ndvi") is None

    def test_filters_index_names(self):
        """Filters out spectral index names that look like locations."""
        # "NDVI" starts with a capital letter but is in false_positives
        assert _extract_location_name("Show NDVI") is None

    def test_filters_month_names(self):
        """Filters out month names from location extraction."""
        result = _extract_location_name("data for January")
        assert result is None


# ============================================================================
# TestExtractCoordinates
# ============================================================================

class TestExtractCoordinates:
    """Tests for _extract_coordinates helper function."""

    def test_extract_valid_bbox(self):
        """Extracts valid bbox coordinates from query string."""
        result = _extract_coordinates("Area is 10.0,20.0,11.0,21.0")
        assert result is not None
        assert result["west"] == 10.0
        assert result["south"] == 20.0
        assert result["east"] == 11.0
        assert result["north"] == 21.0

    def test_extract_negative_coordinates(self):
        """Handles negative coordinate values."""
        result = _extract_coordinates("bbox: -73.5,40.0,-72.0,41.5")
        assert result is not None
        assert result["west"] == -73.5
        assert result["south"] == 40.0

    def test_no_coordinates(self):
        """Returns None when no coordinates are present."""
        assert _extract_coordinates("Show NDVI for Berlin") is None

    def test_invalid_coordinate_range(self):
        """Returns None for out-of-range coordinates."""
        # Longitude > 180 or latitude > 90 should fail validation
        result = _extract_coordinates("area 200.0,100.0,210.0,110.0")
        assert result is None


# ============================================================================
# TestDetectOutputType
# ============================================================================

class TestDetectOutputType:
    """Tests for _detect_output_type helper function."""

    def test_default_map(self):
        """Defaults to map output when no specific keywords present."""
        assert _detect_output_type("Show NDVI for Berlin") == "map"

    def test_statistics_output(self):
        """Statistics keywords yield statistics output type."""
        assert _detect_output_type("Calculate statistics for the region") == "statistics"

    def test_time_series_output(self):
        """Time series keywords yield time_series output type."""
        assert _detect_output_type("Plot the time series chart") == "time_series"

    def test_download_output(self):
        """Download/export keywords yield download output type."""
        assert _detect_output_type("Export result as geotiff") == "download"

    def test_comparison_output(self):
        """Compare keywords yield comparison output type."""
        assert _detect_output_type("Compare these two results side by side") == "comparison"


# ============================================================================
# TestInferCollection
# ============================================================================

class TestInferCollection:
    """Tests for _infer_collection helper function."""

    def test_infer_from_vegetation_domain(self):
        """Vegetation domain infers sentinel-2-l2a collection."""
        result = _infer_collection("vegetation", ["ndvi"])
        assert result == "sentinel-2-l2a"

    def test_infer_from_indices_only(self):
        """With indices but no domain, defaults to sentinel-2-l2a."""
        result = _infer_collection(None, ["ndvi"])
        assert result == "sentinel-2-l2a"

    def test_no_domain_no_indices(self):
        """No domain and no indices returns None."""
        result = _infer_collection(None, [])
        assert result is None

    def test_infer_from_fire_domain(self):
        """Fire domain infers sentinel-2-l2a as first collection."""
        result = _infer_collection("fire", ["dnbr"])
        assert result == "sentinel-2-l2a"

    def test_infer_from_terrain_domain(self):
        """Terrain domain infers cop-dem-glo-30 collection."""
        result = _infer_collection("terrain", [])
        assert result == "cop-dem-glo-30"
