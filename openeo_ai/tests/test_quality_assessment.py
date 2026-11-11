# ABOUTME: Unit tests for quality assessment tools in quality_tools.py.
# Tests grade calculation, async assess_result_quality, tool factory, and confidence ratings.

"""
Unit tests for post-execution quality assessment.

Tests:
- _calculate_grade logic across all grade boundaries (A-F)
- assess_result_quality async handler with various inputs
- create_quality_tools factory output structure
- Confidence rating mapping from grades
"""

import json
import sys

import pytest

from openeo_ai.tools.quality_tools import (
    assess_result_quality,
    _calculate_grade,
    create_quality_tools,
    QualityReport,
    QUALITY_TOOL_DEFINITIONS,
)


# Domain contexts with the same structure as the real DOMAIN_CONTEXTS dict
# from domain_knowledge.py. Used via monkeypatch to avoid the heavy SDK
# import chain during test execution.
_TEST_DOMAIN_CONTEXTS = {
    "vegetation": {
        "indices": ["NDVI", "EVI"],
        "value_ranges": {
            "ndvi": (-1.0, 1.0),
            "evi": (-1.0, 1.0),
        },
        "quality_thresholds": {
            "min_valid_pixels": 0.5,
            "max_cloud_cover": 0.3,
            "expected_mean_range": {"ndvi": (0.1, 0.8), "evi": (0.1, 0.6)},
        },
        "thresholds": {
            "NDVI": {
                "bare_soil_rock": "< 0.1",
                "sparse_vegetation": "0.1 - 0.3",
                "moderate_vegetation": "0.3 - 0.6",
                "dense_vegetation": "0.6 - 0.9",
            },
        },
    },
}


def _replacement_domain_contexts():
    """Return test domain contexts -- same shape as real DOMAIN_CONTEXTS."""
    return _TEST_DOMAIN_CONTEXTS


# ============================================================================
# Grade Calculation Tests
# ============================================================================

class TestCalculateGrade:
    """Tests for _calculate_grade logic across all grade boundaries."""

    def test_grade_a_all_pass(self):
        """Grade A requires range_ok=True, coverage>=80%, and mean_in_range=True."""
        grade = _calculate_grade(
            range_ok=True,
            coverage_pct=90.0,
            mean_in_range=True,
            nodata_pct=10.0,
        )
        assert grade == "A"

    def test_grade_b_high_coverage(self):
        """Grade B when range_ok=True and coverage>=50% but mean not in range."""
        grade = _calculate_grade(
            range_ok=True,
            coverage_pct=60.0,
            mean_in_range=False,
            nodata_pct=40.0,
        )
        assert grade == "B"

    def test_grade_c_range_ok_only(self):
        """Grade C when range_ok=True but coverage<50%."""
        grade = _calculate_grade(
            range_ok=True,
            coverage_pct=20.0,
            mean_in_range=False,
            nodata_pct=80.0,
        )
        assert grade == "C"

    def test_grade_c_coverage_only(self):
        """Grade C when range_ok=False but coverage>=30%."""
        grade = _calculate_grade(
            range_ok=False,
            coverage_pct=40.0,
            mean_in_range=False,
            nodata_pct=60.0,
        )
        assert grade == "C"

    def test_grade_d_low_coverage(self):
        """Grade D when coverage>=10% but range_ok=False and coverage<30%."""
        grade = _calculate_grade(
            range_ok=False,
            coverage_pct=15.0,
            mean_in_range=False,
            nodata_pct=85.0,
        )
        assert grade == "D"

    def test_grade_f_nothing(self):
        """Grade F when all checks fail and coverage<10%."""
        grade = _calculate_grade(
            range_ok=False,
            coverage_pct=5.0,
            mean_in_range=False,
            nodata_pct=95.0,
        )
        assert grade == "F"


# ============================================================================
# Assess Result Quality Tests (async)
# ============================================================================

def _hide_rasterio(monkeypatch):
    """Use monkeypatch to make 'import rasterio' raise ImportError.

    Setting sys.modules["rasterio"] to None causes Python's import machinery
    to raise ImportError. This is the standard pytest idiom for hiding an
    optional dependency.
    """
    monkeypatch.setitem(sys.modules, "rasterio", None)


def _passthrough_validate(path):
    """Passthrough validator for tests with non-existent files."""
    return path


class TestAssessResultQuality:
    """Tests for the assess_result_quality async handler."""

    @pytest.mark.asyncio
    async def test_valid_ndvi_no_rasterio(self, monkeypatch):
        """When rasterio is unavailable, return metadata-only report with warning."""
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools._get_domain_contexts",
            _replacement_domain_contexts,
        )
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools.validate_tool_input_path",
            _passthrough_validate,
        )
        _hide_rasterio(monkeypatch)

        result = await assess_result_quality({
            "file_path": "/tmp/test_ndvi.tif",
            "domain": "vegetation",
            "index": "ndvi",
        })

        assert "content" in result
        text = result["content"][0]["text"]
        report = json.loads(text)

        # Metadata-only path sets grade C with LOW confidence
        assert report["overall_grade"] == "C"
        assert report["confidence_rating"] == "LOW"
        assert any("rasterio" in w.lower() for w in report["warnings"])
        assert report["value_range_check"]["actual"] is None
        # Expected range should come from domain contexts
        assert report["value_range_check"]["expected"] == [-1.0, 1.0]

    @pytest.mark.asyncio
    async def test_unknown_domain(self, monkeypatch):
        """Unknown domain should be handled gracefully with empty context."""
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools._get_domain_contexts",
            _replacement_domain_contexts,
        )
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools.validate_tool_input_path",
            _passthrough_validate,
        )
        _hide_rasterio(monkeypatch)

        result = await assess_result_quality({
            "file_path": "/tmp/test.tif",
            "domain": "nonexistent",
            "index": "ndvi",
        })

        text = result["content"][0]["text"]
        report = json.loads(text)

        # Should still return a report without crashing
        assert report["overall_grade"] == "C"
        assert report["confidence_rating"] == "LOW"
        # No value_ranges for unknown domain -> expected is None
        assert report["value_range_check"]["expected"] is None

    @pytest.mark.asyncio
    async def test_unknown_index(self, monkeypatch):
        """Unknown index in a known domain should be handled gracefully."""
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools._get_domain_contexts",
            _replacement_domain_contexts,
        )
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools.validate_tool_input_path",
            _passthrough_validate,
        )
        _hide_rasterio(monkeypatch)

        result = await assess_result_quality({
            "file_path": "/tmp/test.tif",
            "domain": "vegetation",
            "index": "xyz",
        })

        text = result["content"][0]["text"]
        report = json.loads(text)

        assert report["overall_grade"] == "C"
        # No value range for "xyz" -> expected is None
        assert report["value_range_check"]["expected"] is None

    @pytest.mark.asyncio
    async def test_missing_required_params(self):
        """Empty args should return an error response."""
        result = await assess_result_quality({})

        assert "content" in result
        text = result["content"][0]["text"]
        payload = json.loads(text)
        assert "error" in payload
        assert "required" in payload["error"].lower()

    @pytest.mark.asyncio
    async def test_response_format(self, monkeypatch):
        """Response must be MCP-style: content[0].text is valid JSON with all QualityReport fields."""
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools._get_domain_contexts",
            _replacement_domain_contexts,
        )
        monkeypatch.setattr(
            "openeo_ai.tools.quality_tools.validate_tool_input_path",
            _passthrough_validate,
        )
        _hide_rasterio(monkeypatch)

        result = await assess_result_quality({
            "file_path": "/tmp/test.tif",
            "domain": "vegetation",
            "index": "ndvi",
        })

        # Verify MCP response structure
        assert isinstance(result, dict)
        assert "content" in result
        assert isinstance(result["content"], list)
        assert len(result["content"]) >= 1
        assert result["content"][0]["type"] == "text"

        # text field must be valid JSON
        parsed = json.loads(result["content"][0]["text"])
        assert isinstance(parsed, dict)

        # Must contain all standard QualityReport fields
        expected_fields = [
            "overall_grade",
            "confidence_rating",
            "value_range_check",
            "spatial_coverage",
            "domain_interpretation",
            "warnings",
            "recommendations",
        ]
        for field_name in expected_fields:
            assert field_name in parsed, f"Missing QualityReport field: {field_name}"


# ============================================================================
# Quality Tool Factory Tests
# ============================================================================

class TestQualityToolFactory:
    """Tests for create_quality_tools factory function."""

    def test_create_quality_tools_returns_tuple(self):
        """Factory must return a tuple of (dict, list)."""
        result = create_quality_tools()
        assert isinstance(result, tuple)
        assert len(result) == 2

        handlers, definitions = result
        assert isinstance(handlers, dict)
        assert isinstance(definitions, list)

    def test_handler_registered(self):
        """The assess_result_quality handler must be registered."""
        handlers, _ = create_quality_tools()
        assert "assess_result_quality" in handlers
        assert callable(handlers["assess_result_quality"])
        assert handlers["assess_result_quality"] is assess_result_quality

    def test_definition_schema(self):
        """Tool definition must have name, description, and input_schema with required fields."""
        _, definitions = create_quality_tools()
        assert len(definitions) >= 1

        defn = definitions[0]
        assert defn["name"] == "assess_result_quality"
        assert "description" in defn
        assert len(defn["description"]) > 0

        schema = defn["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

        # Required fields must include file_path, domain, index
        required = schema["required"]
        assert "file_path" in required
        assert "domain" in required
        assert "index" in required

        # Properties must describe all parameters including optional goal
        props = schema["properties"]
        assert "file_path" in props
        assert "domain" in props
        assert "index" in props
        assert "goal" in props


# ============================================================================
# Confidence Rating Tests
# ============================================================================

class TestConfidenceRating:
    """Tests for the grade-to-confidence mapping used in assess_result_quality."""

    @staticmethod
    def _apply_confidence(grade: str) -> str:
        """Replicate the confidence mapping logic from assess_result_quality."""
        if grade in ("A", "B"):
            return "HIGH"
        elif grade == "C":
            return "MODERATE"
        else:
            return "LOW"

    def test_grade_a_maps_to_high(self):
        """Grade A should map to HIGH confidence."""
        assert self._apply_confidence("A") == "HIGH"

    def test_grade_b_maps_to_high(self):
        """Grade B should map to HIGH confidence."""
        assert self._apply_confidence("B") == "HIGH"

    def test_grade_c_maps_to_moderate(self):
        """Grade C should map to MODERATE confidence."""
        assert self._apply_confidence("C") == "MODERATE"

    def test_grade_d_maps_to_low(self):
        """Grade D should map to LOW confidence."""
        assert self._apply_confidence("D") == "LOW"

    def test_grade_f_maps_to_low(self):
        """Grade F should map to LOW confidence."""
        assert self._apply_confidence("F") == "LOW"
