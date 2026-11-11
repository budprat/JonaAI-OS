# ABOUTME: Post-execution quality assessment for EO analysis results.
# Validates result values against domain expectations and grades output quality.

"""
Post-execution quality assessment for Earth Observation analysis results.

Reads GeoTIFF outputs, validates pixel values against domain-specific expected
ranges, checks spatial coverage, and assigns an overall quality grade (A-F)
with confidence rating. Uses DOMAIN_CONTEXTS from domain_knowledge.py as the
single source of truth for value ranges and quality thresholds.

rasterio is an optional dependency -- if unavailable, returns a simulated
report based on domain/index metadata only (with a warning).
"""

import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .path_security import validate_tool_input_path, PathSecurityError


def _get_domain_contexts():
    """Lazy-load DOMAIN_CONTEXTS to avoid circular import.

    The import chain quality_tools.py -> sdk.domain_knowledge triggers
    sdk/__init__.py -> client.py -> _extend_tool_definitions() -> quality_tools.py
    which creates a circular import at module load time.
    """
    import importlib
    mod = importlib.import_module("openeo_ai.sdk.domain_knowledge")
    return mod.DOMAIN_CONTEXTS


def _get_interpret_index_value():
    """Lazy-load interpret_index_value to avoid circular import.

    Same circular import issue as _get_domain_contexts -- interpretation.py
    is loaded by the SDK init chain.
    """
    import importlib
    mod = importlib.import_module("openeo_ai.tools.interpretation")
    return mod.interpret_index_value


@dataclass
class QualityReport:
    """Post-execution quality assessment for an EO analysis result."""
    overall_grade: str = "F"              # "A"|"B"|"C"|"D"|"F"
    confidence_rating: str = "INVALID"    # "HIGH"|"MODERATE"|"LOW"|"INVALID"
    value_range_check: Dict = field(default_factory=dict)   # {valid, expected, actual}
    spatial_coverage: Dict = field(default_factory=dict)     # {percent_valid, nodata_percent}
    domain_interpretation: Dict = field(default_factory=dict)  # {category, description, thresholds}
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)


def _calculate_grade(
    range_ok: bool,
    coverage_pct: float,
    mean_in_range: bool,
    nodata_pct: float,
) -> str:
    """Calculate overall quality grade from individual checks.

    Returns:
        "A" if range_ok AND coverage >= 80% AND mean_in_range
        "B" if range_ok AND coverage >= 50%
        "C" if range_ok OR coverage >= 30%
        "D" if coverage >= 10%
        "F" otherwise
    """
    if range_ok and coverage_pct >= 80.0 and mean_in_range:
        return "A"
    if range_ok and coverage_pct >= 50.0:
        return "B"
    if range_ok or coverage_pct >= 30.0:
        return "C"
    if coverage_pct >= 10.0:
        return "D"
    return "F"


async def assess_result_quality(args: Dict[str, Any]) -> Dict[str, Any]:
    """Assess quality of an EO analysis result file against domain expectations.

    Input args:
        file_path (str): Path to the GeoTIFF result file.
        domain (str): EO domain (vegetation, water, fire, etc.).
        index (str): Spectral index name (ndvi, ndwi, dnbr, etc.).
        goal (dict, optional): AnalysisGoal dict for additional context.

    Returns MCP-style result with QualityReport as JSON.
    """
    file_path = args.get("file_path", "")
    domain = args.get("domain", "")
    index = args.get("index", "")
    goal = args.get("goal")

    if not file_path or not domain or not index:
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "error": "'file_path', 'domain', and 'index' are all required parameters."
                }),
            }]
        }

    try:
        file_path = validate_tool_input_path(file_path)
    except PathSecurityError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    index_lower = index.lower()
    domain_contexts = _get_domain_contexts()
    domain_ctx = domain_contexts.get(domain, {})

    # Look up expected ranges and quality thresholds
    value_ranges = domain_ctx.get("value_ranges", {})
    quality_thresholds = domain_ctx.get("quality_thresholds", {})
    expected_range = value_ranges.get(index_lower)
    expected_mean_ranges = quality_thresholds.get("expected_mean_range", {})
    expected_mean_range = expected_mean_ranges.get(index_lower)
    min_valid_pixels = quality_thresholds.get("min_valid_pixels", 0.5)

    report = QualityReport()
    rasterio_available = False

    # Try to read the GeoTIFF via rasterio
    try:
        import rasterio
        import numpy as np
        rasterio_available = True
    except ImportError:
        rasterio_available = False

    if rasterio_available:
        import numpy as np
        import os

        if not os.path.exists(file_path):
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"error": f"File not found: {file_path}"}),
                }]
            }

        try:
            with rasterio.open(file_path) as ds:
                data = ds.read(1).astype(float)
                nodata = ds.nodata
                if nodata is not None:
                    data[data == nodata] = np.nan

                total_pixels = data.size
                valid_mask = ~np.isnan(data)
                valid = data[valid_mask].ravel()

                if valid.size == 0:
                    report.overall_grade = "F"
                    report.confidence_rating = "INVALID"
                    report.spatial_coverage = {
                        "percent_valid": 0.0,
                        "nodata_percent": 100.0,
                    }
                    report.warnings.append("No valid pixels in the result file.")
                    report.recommendations.append(
                        "Check cloud masking parameters or expand temporal/spatial extent."
                    )
                    return _format_report_response(report)

                # Basic stats
                actual_min = float(np.min(valid))
                actual_max = float(np.max(valid))
                actual_mean = float(np.mean(valid))
                actual_std = float(np.std(valid))
                coverage_pct = round(100.0 * valid.size / total_pixels, 2)
                nodata_pct = round(100.0 - coverage_pct, 2)

                # Value range check
                range_ok = True
                if expected_range:
                    exp_min, exp_max = expected_range
                    # Allow 10% tolerance beyond expected range for edge cases
                    tolerance = (exp_max - exp_min) * 0.1
                    range_ok = (
                        actual_min >= exp_min - tolerance
                        and actual_max <= exp_max + tolerance
                    )
                    report.value_range_check = {
                        "valid": range_ok,
                        "expected": list(expected_range),
                        "actual": [round(actual_min, 6), round(actual_max, 6)],
                    }
                else:
                    report.value_range_check = {
                        "valid": True,
                        "expected": None,
                        "actual": [round(actual_min, 6), round(actual_max, 6)],
                    }
                    report.warnings.append(
                        f"No expected value range defined for index '{index}' in domain '{domain}'."
                    )

                # Spatial coverage check
                report.spatial_coverage = {
                    "percent_valid": coverage_pct,
                    "nodata_percent": nodata_pct,
                }
                if coverage_pct < min_valid_pixels * 100:
                    report.warnings.append(
                        f"Low spatial coverage: {coverage_pct}% valid pixels "
                        f"(domain minimum: {min_valid_pixels * 100}%)."
                    )

                # Mean range check
                mean_in_range = True
                if expected_mean_range:
                    emr_min, emr_max = expected_mean_range
                    mean_in_range = emr_min <= actual_mean <= emr_max
                    if not mean_in_range:
                        report.warnings.append(
                            f"Mean value {actual_mean:.4f} outside expected range "
                            f"({emr_min} - {emr_max}) for {index.upper()} in {domain} domain."
                        )

                # Statistical sanity checks
                if actual_std < 0.001:
                    report.warnings.append(
                        "Extremely low variance (std < 0.001) -- data may be uniform or corrupted."
                    )
                if nodata_pct > 80:
                    report.warnings.append(
                        f"Very high nodata percentage ({nodata_pct}%) -- result may be unreliable."
                    )

                # Value range violation details
                if not range_ok and expected_range:
                    exp_min, exp_max = expected_range
                    if actual_min < exp_min:
                        report.warnings.append(
                            f"Minimum value {actual_min:.4f} is below expected minimum {exp_min}."
                        )
                    if actual_max > exp_max:
                        report.warnings.append(
                            f"Maximum value {actual_max:.4f} is above expected maximum {exp_max}."
                        )

                # Grade calculation
                report.overall_grade = _calculate_grade(
                    range_ok, coverage_pct, mean_in_range, nodata_pct
                )

                # Confidence rating
                if report.overall_grade in ("A", "B"):
                    report.confidence_rating = "HIGH"
                elif report.overall_grade == "C":
                    report.confidence_rating = "MODERATE"
                else:
                    report.confidence_rating = "LOW"

                # Domain interpretation via interpret_index_value
                try:
                    interpret_fn = _get_interpret_index_value()
                    stats_for_interp = {
                        "mean": actual_mean,
                        "std": actual_std,
                        "min": actual_min,
                        "max": actual_max,
                        "coverage_pct": coverage_pct,
                    }
                    interp = interpret_fn(index.upper(), stats_for_interp, domain)
                    report.domain_interpretation = {
                        "category": interp.get("mean_category", "Unknown"),
                        "description": interp.get("summary", ""),
                        "thresholds": domain_ctx.get("thresholds", {}).get(index.upper(), {}),
                    }
                except Exception:
                    report.domain_interpretation = {
                        "category": "Unknown",
                        "description": "Could not run domain interpretation.",
                        "thresholds": {},
                    }

        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": json.dumps({"error": f"Error reading GeoTIFF: {str(e)}"}),
                }]
            }
    else:
        # rasterio NOT available -- simulated report based on domain/index metadata
        report.warnings.append(
            "rasterio is not installed. Actual file was NOT read. "
            "Report is based on domain/index metadata only."
        )
        report.value_range_check = {
            "valid": None,
            "expected": list(expected_range) if expected_range else None,
            "actual": None,
        }
        report.spatial_coverage = {
            "percent_valid": None,
            "nodata_percent": None,
        }

        # Provide domain interpretation from metadata alone
        thresholds_for_index = domain_ctx.get("thresholds", {}).get(index.upper(), {})
        report.domain_interpretation = {
            "category": "Unknown (file not read)",
            "description": (
                f"Domain '{domain}' with index '{index.upper()}'. "
                f"Expected value range: {expected_range}. "
                f"Expected mean range: {expected_mean_range}."
            ),
            "thresholds": thresholds_for_index,
        }
        report.overall_grade = "C"
        report.confidence_rating = "LOW"
        report.recommendations.append(
            "Install rasterio for actual file-based quality assessment."
        )

    # Generate recommendations based on findings
    if report.overall_grade in ("D", "F"):
        report.recommendations.append(
            "Consider re-running the analysis with adjusted parameters "
            "(wider temporal range, different cloud masking threshold, or larger spatial extent)."
        )
    if any("coverage" in w.lower() for w in report.warnings):
        report.recommendations.append(
            "Increase temporal range or relax cloud masking to improve spatial coverage."
        )
    if any("range" in w.lower() and "outside" in w.lower() for w in report.warnings):
        report.recommendations.append(
            "Verify the correct index and collection are being used for this domain."
        )
    if any("variance" in w.lower() for w in report.warnings):
        report.recommendations.append(
            "Check that the spatial extent covers a heterogeneous area, not a single land cover type."
        )

    # Add goal-specific recommendations if goal provided
    if goal and isinstance(goal, dict):
        if goal.get("temporal_strategy") == "single_snapshot" and report.overall_grade in ("C", "D"):
            report.recommendations.append(
                "Consider using a multi-temporal composite (e.g., monthly median) to reduce noise."
            )

    return _format_report_response(report)


def _format_report_response(report: QualityReport) -> Dict[str, Any]:
    """Format QualityReport as MCP-style response."""
    report_dict = asdict(report)
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(report_dict, indent=2),
        }]
    }


# --- Tool Definitions ---

QUALITY_TOOL_DEFINITIONS = [
    {
        "name": "assess_result_quality",
        "description": (
            "Assess the quality of an EO analysis result file against domain expectations. "
            "Reads the GeoTIFF, validates pixel values against expected ranges, checks spatial "
            "coverage, and returns an overall quality grade (A-F) with confidence rating. "
            "Use AFTER job execution and result download to validate output quality before "
            "presenting results to the user."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF result file to assess",
                },
                "domain": {
                    "type": "string",
                    "description": (
                        "EO analysis domain (vegetation, water, fire, urban, agriculture, "
                        "snow, climate, soil, coastal, drought, deforestation, terrain, "
                        "flood, wetland, algal_bloom, mining)"
                    ),
                },
                "index": {
                    "type": "string",
                    "description": "Spectral index name (e.g., ndvi, ndwi, dnbr, bsi, ndsi)",
                },
                "goal": {
                    "type": "object",
                    "description": "Optional AnalysisGoal dict from extract_analysis_goal for additional context",
                },
            },
            "required": ["file_path", "domain", "index"],
        },
    },
]


def create_quality_tools() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Create quality assessment tools.

    Returns:
        Tuple of (tool_handlers_dict, tool_definitions_list)
    """
    tools_dict = {
        "assess_result_quality": assess_result_quality,
    }
    return tools_dict, QUALITY_TOOL_DEFINITIONS
