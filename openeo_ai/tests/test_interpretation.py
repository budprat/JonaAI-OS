# ABOUTME: Tests for the interpretation engine.
# Covers threshold parsing, index interpretation, change analysis, quality assessment,
# domain threshold loading, index detection from paths, and tool count verification.

"""Tests for openeo_ai.tools.interpretation module."""

import pytest
from openeo_ai.tools.interpretation import (
    parse_threshold_string,
    interpret_index_value,
    interpret_change,
    assess_quality,
    load_domain_thresholds,
    _detect_index_from_path,
    _infer_domain,
    _compute_grade,
    format_interpretation_text,
    format_quality_text,
    format_change_text,
    generate_recommendations,
    generate_report,
    INTERPRETATION_TOOL_DEFINITIONS,
    _INDEX_DOMAIN_MAP,
)
from openeo_ai.tools.interpretation import _get_domain_contexts


# --- TestParseThreshold ---

class TestParseThreshold:
    """Test threshold string parsing from domain_knowledge format."""

    def test_less_than(self):
        assert parse_threshold_string("< 0.1") == (None, 0.1)

    def test_less_than_negative(self):
        assert parse_threshold_string("< -0.3") == (None, -0.3)

    def test_greater_than(self):
        assert parse_threshold_string("> 0.6") == (0.6, None)

    def test_greater_than_negative(self):
        assert parse_threshold_string("> -0.25") == (-0.25, None)

    def test_range(self):
        assert parse_threshold_string("0.1 - 0.3") == (0.1, 0.3)

    def test_range_negative(self):
        assert parse_threshold_string("-0.25 - -0.1") == (-0.25, -0.1)

    def test_range_mixed_sign(self):
        assert parse_threshold_string("-0.1 - 0.1") == (-0.1, 0.1)

    def test_approximate(self):
        t_min, t_max = parse_threshold_string("~0.0")
        assert t_min == pytest.approx(-0.05)
        assert t_max == pytest.approx(0.05)

    def test_approximate_nonzero(self):
        t_min, t_max = parse_threshold_string("~0.5")
        assert t_min == pytest.approx(0.45)
        assert t_max == pytest.approx(0.55)

    def test_whitespace_handling(self):
        assert parse_threshold_string("  > 0.6  ") == (0.6, None)

    def test_invalid(self):
        assert parse_threshold_string("invalid") == (None, None)


# --- TestInterpretIndexValue ---

class TestInterpretIndexValue:
    """Test index value interpretation with domain context."""

    @pytest.fixture
    def ndvi_healthy_stats(self):
        return {
            "min": 0.1, "max": 0.85, "mean": 0.55, "median": 0.58,
            "std": 0.18, "p5": 0.15, "p25": 0.4, "p75": 0.7, "p95": 0.82,
            "coverage_pct": 96.0,
        }

    @pytest.fixture
    def ndvi_bare_stats(self):
        return {
            "min": -0.1, "max": 0.3, "mean": 0.08, "median": 0.07,
            "std": 0.05, "p5": -0.05, "p25": 0.02, "p75": 0.12, "p95": 0.2,
            "coverage_pct": 85.0,
        }

    def test_ndvi_interpretation_has_grade(self, ndvi_healthy_stats):
        result = interpret_index_value("NDVI", ndvi_healthy_stats)
        assert result["grade"] in ("A", "B", "C", "D", "F")
        assert 0 <= result["grade_score"] <= 100
        assert result["index"] == "NDVI"
        assert result["domain"] == "vegetation"

    def test_ndvi_interpretation_has_breakdown(self, ndvi_healthy_stats):
        result = interpret_index_value("NDVI", ndvi_healthy_stats)
        assert len(result["category_breakdown"]) > 0
        for cat in result["category_breakdown"]:
            assert "category" in cat
            assert "range" in cat
            assert "assessment" in cat

    def test_ndvi_healthy_better_than_bare(self, ndvi_healthy_stats, ndvi_bare_stats):
        healthy = interpret_index_value("NDVI", ndvi_healthy_stats)
        bare = interpret_index_value("NDVI", ndvi_bare_stats)
        assert healthy["grade_score"] >= bare["grade_score"]

    def test_ndwi_interpretation(self):
        stats = {"mean": 0.3, "std": 0.2, "min": -0.5, "max": 0.8,
                 "p5": -0.3, "p25": 0.1, "p75": 0.5, "p95": 0.7, "coverage_pct": 90.0}
        result = interpret_index_value("NDWI", stats)
        assert result["domain"] == "water"
        assert result["grade"] is not None

    def test_dnbr_interpretation(self):
        stats = {"mean": 0.35, "std": 0.25, "min": -0.2, "max": 0.9,
                 "p5": -0.1, "p25": 0.15, "p75": 0.5, "p95": 0.75, "coverage_pct": 92.0}
        result = interpret_index_value("dNBR", stats)
        assert result["domain"] == "fire"
        assert result["grade"] is not None

    def test_domain_override(self, ndvi_healthy_stats):
        result = interpret_index_value("NDVI", ndvi_healthy_stats, domain="agriculture")
        assert result["domain"] == "agriculture"

    def test_unknown_index_graceful_fallback(self):
        stats = {"mean": 0.5, "std": 0.1, "min": 0.0, "max": 1.0, "coverage_pct": 100.0}
        result = interpret_index_value("UNKNOWN_INDEX_XYZ", stats)
        assert result["grade"] is None  # No thresholds available
        assert "No domain thresholds" in result["summary"]

    def test_summary_is_nonempty(self, ndvi_healthy_stats):
        result = interpret_index_value("NDVI", ndvi_healthy_stats)
        assert len(result["summary"]) > 10

    def test_all_default_indices(self):
        """Test that each domain's default index can be interpreted."""
        stats = {"mean": 0.3, "std": 0.1, "min": 0.0, "max": 0.8,
                 "p5": 0.05, "p25": 0.2, "p75": 0.4, "p95": 0.7, "coverage_pct": 90.0}
        for domain, ctx in _get_domain_contexts().items():
            default_idx = ctx.get("default_index")
            if default_idx and ctx.get("thresholds", {}).get(default_idx):
                result = interpret_index_value(default_idx, stats, domain=domain)
                assert result["grade"] is not None, f"Failed for {domain}/{default_idx}"
                assert len(result["category_breakdown"]) > 0, f"No breakdown for {domain}/{default_idx}"

    def test_nbr_negative_fire_domain_indicates_burned(self):
        """Absolute NBR -0.0558 in fire domain must NOT classify as unburned.

        Live defect: negative post-fire NBR was borrowed against dNBR's
        "unburned: -0.1 - 0.1" bin. Negative absolute NBR means likely
        burned/charred/bare, never unburned.
        """
        stats = {"mean": -0.0558, "std": 0.12, "min": -0.45, "max": 0.25,
                 "p5": -0.35, "p25": -0.15, "p75": 0.05, "p95": 0.18,
                 "coverage_pct": 92.0}
        result = interpret_index_value("NBR", stats, domain="fire")
        category = (result["mean_category"] or "").lower()
        assert "unburned" not in category
        assert "unburned" not in result["summary"].lower()
        assert "burned" in category or "charred" in category or "bare" in category

    def test_nbr_positive_fire_domain_healthy_vegetation(self):
        """Positive control: absolute NBR 0.5 in fire domain -> healthy vegetation."""
        stats = {"mean": 0.5, "std": 0.1, "min": 0.1, "max": 0.8,
                 "p5": 0.2, "p25": 0.4, "p75": 0.6, "p95": 0.75,
                 "coverage_pct": 95.0}
        result = interpret_index_value("NBR", stats, domain="fire")
        category = (result["mean_category"] or "").lower()
        assert "healthy" in category and "vegetation" in category

    def test_fallback_note_names_both_indices(self):
        """When thresholds are borrowed from the default index, the output
        text must name both the requested and the default index."""
        stats = {"mean": 0.2, "std": 0.1, "min": 0.0, "max": 0.6,
                 "p5": 0.05, "p25": 0.1, "p75": 0.3, "p95": 0.5,
                 "coverage_pct": 90.0}
        # BAI has no threshold table in the fire domain -> borrows dNBR's
        result = interpret_index_value("BAI", stats, domain="fire")
        assert "BAI" in result["summary"]
        assert "dNBR" in result["summary"]
        assert any("BAI" in w and "dNBR" in w for w in result["warnings"])

    def test_direct_threshold_match_has_no_fallback_note(self):
        """An index with its own threshold table gets no borrow note,
        including case-insensitive direct matches like dNBR."""
        stats = {"mean": 0.35, "std": 0.2, "min": -0.2, "max": 0.9,
                 "p5": -0.1, "p25": 0.15, "p75": 0.5, "p95": 0.75,
                 "coverage_pct": 92.0}
        result = interpret_index_value("dNBR", stats, domain="fire")
        assert "interpreted against" not in result["summary"]
        assert not any("interpreted against" in w for w in result["warnings"])


# --- TestInterpretChange ---

class TestInterpretChange:
    """Test change interpretation between two time periods."""

    def test_increase(self):
        diff_stats = {"mean": 0.15, "std": 0.1, "min": -0.1, "max": 0.4}
        result = interpret_change(diff_stats, "NDVI")
        assert result["direction"] == "increase"
        assert result["magnitude"] in ("moderate", "minor", "significant")
        assert 1 <= result["severity"] <= 5

    def test_decrease(self):
        diff_stats = {"mean": -0.25, "std": 0.15, "min": -0.6, "max": 0.1}
        result = interpret_change(diff_stats, "NDVI")
        assert result["direction"] == "decrease"
        assert result["magnitude"] in ("moderate", "significant")

    def test_stable(self):
        diff_stats = {"mean": 0.005, "std": 0.02, "min": -0.05, "max": 0.05}
        result = interpret_change(diff_stats, "NDVI")
        assert result["direction"] == "stable"
        assert result["magnitude"] == "negligible"
        assert result["severity"] == 1

    def test_extreme_change(self):
        diff_stats = {"mean": 0.6, "std": 0.3, "min": 0.0, "max": 1.0}
        result = interpret_change(diff_stats, "dNBR")
        assert result["magnitude"] == "extreme"
        assert result["severity"] == 5

    def test_has_likely_causes(self):
        diff_stats = {"mean": -0.2, "std": 0.15, "min": -0.5, "max": 0.1}
        result = interpret_change(diff_stats, "NDVI")
        assert len(result["likely_causes"]) > 0

    def test_has_recommendations(self):
        diff_stats = {"mean": 0.3, "std": 0.2, "min": -0.1, "max": 0.7}
        result = interpret_change(diff_stats, "NDWI")
        assert len(result["recommendations"]) > 0

    def test_summary_nonempty(self):
        diff_stats = {"mean": 0.1, "std": 0.05, "min": 0.0, "max": 0.3}
        result = interpret_change(diff_stats, "NDBI")
        assert len(result["summary"]) > 10


# --- TestAssessQuality ---

class TestAssessQuality:
    """Test data quality assessment."""

    def test_full_coverage_grade_a(self):
        stats = {"coverage_pct": 100.0, "valid_pixels": 1000, "total_pixels": 1000,
                 "statistics": {"min": 0.1, "max": 0.9, "mean": 0.5, "std": 0.15}}
        result = assess_quality(stats)
        assert result["grade"] == "A"
        assert result["score"] >= 80

    def test_low_coverage_penalty(self):
        stats = {"coverage_pct": 25.0, "valid_pixels": 250, "total_pixels": 1000,
                 "statistics": {"min": 0.1, "max": 0.9, "mean": 0.5, "std": 0.15}}
        result = assess_quality(stats)
        assert result["score"] < 80
        assert any("coverage" in f.lower() for f in result["flags"])

    def test_moderate_coverage(self):
        stats = {"coverage_pct": 50.0, "valid_pixels": 500, "total_pixels": 1000,
                 "statistics": {"min": 0.1, "max": 0.9, "mean": 0.5, "std": 0.15}}
        result = assess_quality(stats)
        assert result["grade"] in ("A", "B", "C")

    def test_zero_range_warning(self):
        stats = {"coverage_pct": 95.0, "valid_pixels": 950, "total_pixels": 1000,
                 "statistics": {"min": 0.5, "max": 0.5, "mean": 0.5, "std": 0.0}}
        result = assess_quality(stats)
        assert any("zero" in f.lower() or "constant" in f.lower() for f in result["flags"])

    def test_suspicious_values_warning(self):
        stats = {"coverage_pct": 90.0, "valid_pixels": 900, "total_pixels": 1000,
                 "statistics": {"min": -100.0, "max": 100.0, "mean": 0.5, "std": 0.001}}
        result = assess_quality(stats)
        assert any("range" in f.lower() or "variance" in f.lower() for f in result["flags"])


# --- TestLoadDomainThresholds ---

class TestLoadDomainThresholds:
    """Test loading thresholds from domain_knowledge."""

    def test_vegetation_ndvi(self):
        result = load_domain_thresholds("vegetation", "NDVI")
        assert result is not None
        assert len(result) > 0
        for t in result:
            assert "min" in t
            assert "max" in t
            assert "label" in t

    def test_fire_dnbr(self):
        result = load_domain_thresholds("fire", "dNBR")
        assert result is not None
        assert len(result) >= 5  # dNBR has 7 categories

    def test_water_ndwi(self):
        result = load_domain_thresholds("water", "NDWI")
        assert result is not None

    def test_urban_ndbi(self):
        result = load_domain_thresholds("urban", "NDBI")
        assert result is not None

    def test_snow_ndsi(self):
        result = load_domain_thresholds("snow", "NDSI")
        assert result is not None

    def test_drought_ndmi(self):
        result = load_domain_thresholds("drought", "NDMI")
        assert result is not None

    def test_soil_bsi(self):
        result = load_domain_thresholds("soil", "BSI")
        assert result is not None

    def test_algal_bloom_ndci(self):
        result = load_domain_thresholds("algal_bloom", "NDCI")
        assert result is not None

    def test_mining_bsi(self):
        result = load_domain_thresholds("mining", "BSI")
        assert result is not None

    def test_deforestation_ndvi(self):
        result = load_domain_thresholds("deforestation", "NDVI")
        assert result is not None

    def test_unknown_domain_returns_none(self):
        result = load_domain_thresholds("nonexistent_domain", "NDVI")
        assert result is None

    def test_unknown_index_falls_back_to_default(self):
        """Unknown index falls back to domain's default index thresholds."""
        result = load_domain_thresholds("vegetation", "NONEXISTENT_INDEX")
        # Falls back to NDVI (vegetation default_index) thresholds
        assert result is not None
        assert len(result) > 0

    def test_no_thresholds_domain_returns_none(self):
        """Domain with no thresholds returns None."""
        result = load_domain_thresholds("climate", "NONEXISTENT_INDEX")
        assert result is None

    def test_all_domains_with_thresholds(self):
        """Test that every domain with defined thresholds loads successfully."""
        for domain, ctx in _get_domain_contexts().items():
            thresholds = ctx.get("thresholds", {})
            for index_name in thresholds:
                result = load_domain_thresholds(domain, index_name)
                assert result is not None, f"Failed for {domain}/{index_name}"
                assert len(result) > 0, f"Empty thresholds for {domain}/{index_name}"


# --- TestDetectIndexFromPath ---

class TestDetectIndexFromPath:
    """Test index detection from file paths."""

    def test_ndvi(self):
        assert _detect_index_from_path("/results/ndvi_2024.tif") == "NDVI"

    def test_gndvi_before_ndvi(self):
        """GNDVI must be detected before NDVI (longer name first)."""
        assert _detect_index_from_path("/results/gndvi_analysis.tif") == "GNDVI"

    def test_mndwi_before_ndwi(self):
        """MNDWI must be detected before NDWI."""
        assert _detect_index_from_path("/results/mndwi_map.tif") == "MNDWI"

    def test_msavi2(self):
        assert _detect_index_from_path("/tmp/openeo_results/msavi2_output.tif") == "MSAVI2"

    def test_dnbr(self):
        """dNBR preserves canonical case from _INDEX_DOMAIN_MAP."""
        assert _detect_index_from_path("/tmp/openeo_results/dnbr_burn_severity.tif") == "dNBR"

    def test_ndwi(self):
        assert _detect_index_from_path("/data/ndwi_water.tif") == "NDWI"

    def test_ndbi(self):
        assert _detect_index_from_path("/results/ndbi_urban.tif") == "NDBI"

    def test_ndsi(self):
        assert _detect_index_from_path("/results/ndsi_snow.tif") == "NDSI"

    def test_bsi(self):
        assert _detect_index_from_path("/results/bsi_soil.tif") == "BSI"

    def test_ndci(self):
        assert _detect_index_from_path("/results/ndci_algal.tif") == "NDCI"

    def test_random_no_match(self):
        assert _detect_index_from_path("/results/random.tif") is None

    def test_empty_path(self):
        assert _detect_index_from_path("") is None

    def test_none_path(self):
        assert _detect_index_from_path(None) is None

    def test_case_insensitive(self):
        assert _detect_index_from_path("/results/NDVI_output.tif") == "NDVI"


# --- TestInferDomain ---

class TestInferDomain:
    """Test domain inference from index names."""

    def test_ndvi_to_vegetation(self):
        assert _infer_domain("NDVI") == "vegetation"

    def test_ndwi_to_water(self):
        assert _infer_domain("NDWI") == "water"

    def test_dnbr_to_fire(self):
        assert _infer_domain("dNBR") == "fire"

    def test_ndbi_to_urban(self):
        assert _infer_domain("NDBI") == "urban"

    def test_domain_override(self):
        assert _infer_domain("NDVI", domain="agriculture") == "agriculture"

    def test_unknown_index(self):
        assert _infer_domain("UNKNOWN") is None


# --- TestGrading ---

class TestGrading:
    """Test grade computation."""

    def test_grade_a(self):
        assert _compute_grade(80) == "A"
        assert _compute_grade(100) == "A"

    def test_grade_b(self):
        assert _compute_grade(60) == "B"
        assert _compute_grade(79) == "B"

    def test_grade_c(self):
        assert _compute_grade(40) == "C"
        assert _compute_grade(59) == "C"

    def test_grade_d(self):
        assert _compute_grade(20) == "D"
        assert _compute_grade(39) == "D"

    def test_grade_f(self):
        assert _compute_grade(0) == "F"
        assert _compute_grade(19) == "F"


# --- TestFormatting ---

class TestFormatting:
    """Test markdown formatting functions."""

    def test_format_interpretation_text_has_grade(self):
        interp = {
            "index": "NDVI", "domain": "vegetation",
            "grade": "B", "grade_score": 68,
            "mean_category": "Moderate Vegetation",
            "category_breakdown": [
                {"category": "Bare Soil", "range": "< 0.1", "est_area_pct": 5, "assessment": "Minimal"},
                {"category": "Dense", "range": "> 0.6", "est_area_pct": 35, "assessment": "Healthy"},
            ],
            "warnings": ["Low coverage"],
            "tips": ["Use EVI for dense canopy"],
            "summary": "NDVI (vegetation) -- Grade: B (68/100)",
        }
        text = format_interpretation_text(interp)
        assert "NDVI" in text
        assert "Grade: B" in text
        assert "| Category |" in text  # markdown table
        assert "Bare Soil" in text

    def test_format_quality_text(self):
        quality = {"grade": "A", "score": 92, "flags": [], "summary": "Quality: A (92/100) -- 96% valid pixels."}
        text = format_quality_text(quality)
        assert "A" in text
        assert "92" in text

    def test_format_change_text(self):
        change = {
            "index": "NDVI", "direction": "decrease", "magnitude": "moderate",
            "severity": 3, "mean_difference": -0.15,
            "likely_causes": ["Drought conditions"],
            "recommendations": ["Check NDMI"],
            "summary": "Test summary",
        }
        text = format_change_text(change)
        assert "decrease" in text
        assert "moderate" in text
        assert "Drought" in text


# --- TestGenerateRecommendations ---

class TestGenerateRecommendations:
    """Test recommendation generation."""

    def test_ndvi_saturation_recommendation(self):
        stats = {"mean": 0.85, "std": 0.1}
        recs = generate_recommendations("vegetation", "NDVI", stats)
        assert any("EVI" in r["action"] for r in recs)

    def test_low_coverage_recommendation(self):
        stats = {"mean": 0.5, "coverage_pct": 40.0}
        recs = generate_recommendations("vegetation", "NDVI", stats)
        assert any("cloud" in r["action"].lower() or "coverage" in r["reason"].lower() for r in recs)

    def test_dnbr_burn_severity_recommendation(self):
        stats = {"mean": 0.4, "std": 0.2}
        recs = generate_recommendations("fire", "dNBR", stats)
        # dNBR should recommend burn severity classification or threshold use
        assert any(
            "severity" in r["action"].lower()
            or "threshold" in r["action"].lower()
            or "burn" in r["action"].lower()
            or "classify" in r["action"].lower()
            for r in recs
        ), f"Expected burn severity recommendation, got: {[r['action'] for r in recs]}"


# --- TestGenerateReport ---

class TestGenerateReport:
    """Test report generation."""

    def test_report_has_title(self):
        stats = {"statistics": {"min": 0.1, "max": 0.9, "mean": 0.5, "median": 0.5, "std": 0.15},
                 "coverage_pct": 95.0, "valid_area_ha": 1500.0}
        interp = {
            "index": "NDVI", "domain": "vegetation", "grade": "B", "grade_score": 70,
            "mean_category": "Moderate", "category_breakdown": [],
            "warnings": [], "tips": [], "summary": "Test summary",
        }
        quality = {"grade": "A", "score": 95, "flags": [], "summary": "Good quality"}
        report = generate_report("/tmp/test.tif", stats, interp, quality, title="Test Report")
        assert "# Test Report" in report
        assert "NDVI" in report
        assert "1,500.0 ha" in report

    def test_report_without_interpretation(self):
        stats = {"statistics": {"min": 0.0, "max": 1.0, "mean": 0.5, "median": 0.5, "std": 0.1}}
        report = generate_report("/tmp/test.tif", stats)
        assert "# Unknown Analysis Report" in report
        assert "0.500000" in report


# --- TestToolDefinitions ---

class TestToolDefinitions:
    """Test tool definitions structure."""

    def test_interpretation_tool_count(self):
        assert len(INTERPRETATION_TOOL_DEFINITIONS) == 2

    def test_tool_names(self):
        names = {t["name"] for t in INTERPRETATION_TOOL_DEFINITIONS}
        assert "interpret_result" in names
        assert "generate_report" in names

    def test_tools_have_required_fields(self):
        for tool in INTERPRETATION_TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert "file_path" in tool["input_schema"]["properties"]

    def test_interpret_result_requires_file_path(self):
        tool = next(t for t in INTERPRETATION_TOOL_DEFINITIONS if t["name"] == "interpret_result")
        assert "file_path" in tool["input_schema"]["required"]


# --- TestToolCount ---

class TestToolCount:
    """Verify total tool count after adding interpretation tools."""

    def test_total_tool_count(self):
        """Ensure TOOL_DEFINITIONS has the expected number of tools."""
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        # The exact count depends on what's loaded; verify the 2 new tools are present
        tool_names = {t["name"] for t in TOOL_DEFINITIONS}
        assert "interpret_result" in tool_names, "interpret_result not in TOOL_DEFINITIONS"
        assert "generate_report" in tool_names, "generate_report not in TOOL_DEFINITIONS"

    def test_index_domain_map_coverage(self):
        """Every index in _INDEX_DOMAIN_MAP should map to a valid domain."""
        for index_name, domain in _INDEX_DOMAIN_MAP.items():
            assert domain in _get_domain_contexts(), f"Domain '{domain}' for index '{index_name}' not in _get_domain_contexts()"


class TestDomainThresholdRatchet:
    """Ratchet against the silent-borrow defect (a domain advertises an index in
    its 'indices' but has no threshold table for it, so interpret_result borrows
    the default index's scale — the NBR-scored-against-dNBR mislabel). The known
    gaps are allow-listed; the set may only SHRINK. Add a threshold table (then
    delete the entry here) to close one — never add a new gap without a table."""

    # Each "domain:INDEX" has NO threshold table today and falls back to the
    # domain default. Interpretation now names the borrow (never silent), but the
    # real fix is a per-index table. Remove an entry only when you add its table.
    KNOWN_THRESHOLD_GAPS = frozenset({
        "vegetation:SAVI", "vegetation:MSAVI", "vegetation:LAI", "vegetation:GNDVI",
        "vegetation:ARVI", "vegetation:NDRE", "vegetation:CHL_RE",
        "water:AWEI",
        "fire:BAI",  # NBR closed 2026-07-24; BAI still borrows dNBR.
        "urban:BSI", "urban:UI",
        "agriculture:SAVI", "agriculture:LAI", "agriculture:NDRE", "agriculture:CHL_RE",
        "climate:NDVI", "climate:NDMI", "climate:EVI",
        "soil:NDVI", "soil:SAVI", "soil:NDTI",
        "coastal:MNDWI",
        "drought:NDVI", "drought:NDWI", "drought:VCI", "drought:TCI",
        "deforestation:NBR", "deforestation:EVI", "deforestation:SAVI",
        "flood:MNDWI",
        "wetland:MNDWI", "wetland:NDVI", "wetland:WIW",
        "algal_bloom:NDWI", "algal_bloom:MNDWI",
        "mining:NDVI",
    })

    def _current_gaps(self):
        gaps = set()
        for domain, ctx in _get_domain_contexts().items():
            have = {t.upper() for t in ctx.get("thresholds", {})}
            for idx in ctx.get("indices", []):
                if idx.upper() not in have:
                    gaps.add(f"{domain}:{idx}")
        return gaps

    def test_no_new_silent_borrow_gaps(self):
        new_gaps = self._current_gaps() - self.KNOWN_THRESHOLD_GAPS
        assert not new_gaps, (
            f"New index/threshold gap(s) with no table: {sorted(new_gaps)}. "
            "Add a threshold table for the index, or (only if unavoidable) add it "
            "to KNOWN_THRESHOLD_GAPS."
        )

    def test_fire_nbr_gap_is_closed(self):
        # The 2026-07-24 fix: fire/NBR now has its own absolute table.
        assert "fire:NBR" not in self._current_gaps()

    def test_allowlist_only_shrinks(self):
        # No stale allow-list entries: everything listed is still a real gap.
        stale = self.KNOWN_THRESHOLD_GAPS - self._current_gaps()
        assert not stale, f"Stale allow-list entries (table now exists): {sorted(stale)}"
