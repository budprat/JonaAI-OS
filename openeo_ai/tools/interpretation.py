# ABOUTME: Interpretation engine for EO analysis results.
# Converts raw statistics into domain-aware grades, categories, and recommendations.
# Sources all thresholds from domain_knowledge.py (single source of truth).

"""
Interpretation engine for Earth Observation analysis results.

Pure-logic module. No HTTP, no file I/O (except rasterio in tool handlers).
Imports only domain_knowledge.py for threshold data.

Converts raw raster statistics into:
- Letter grades (A-F) with numeric scores (0-100)
- Category breakdowns with estimated area percentages
- Quality assessments
- Domain-specific recommendations
- Change severity classifications
"""

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from .path_security import validate_tool_input_path, PathSecurityError


def _get_domain_contexts():
    """Lazy-load DOMAIN_CONTEXTS to avoid circular import.

    The import chain interpretation.py -> sdk.domain_knowledge triggers
    sdk/__init__.py -> client.py -> _extend_tool_definitions() -> interpretation.py
    which creates a circular import at module load time.
    """
    # Reason: Use importlib to bypass the sdk/__init__.py re-entrant import
    import importlib
    mod = importlib.import_module("openeo_ai.sdk.domain_knowledge")
    return mod.DOMAIN_CONTEXTS


# --- Index-to-Domain Mapping ---

_INDEX_DOMAIN_MAP = {
    "NDVI": "vegetation",
    "EVI": "vegetation",
    "SAVI": "vegetation",
    "MSAVI": "vegetation",
    "MSAVI2": "vegetation",
    "GNDVI": "vegetation",
    "LAI": "vegetation",
    "ARVI": "vegetation",
    "NDRE": "vegetation",
    "CHL_RE": "vegetation",
    "NDWI": "water",
    "MNDWI": "water",
    "AWEI": "water",
    "WIW": "water",
    "dNBR": "fire",
    "NBR": "fire",
    "BAI": "fire",
    "NDBI": "urban",
    "UI": "urban",
    "BSI": "soil",
    "NDTI": "soil",
    "NDSI": "snow",
    "NDMI": "drought",
    "NDCI": "algal_bloom",
    "VCI": "drought",
    "TCI": "drought",
}

# Domains that have "healthy" value ranges (higher = better)
_POSITIVE_DOMAINS = {"vegetation", "agriculture", "deforestation"}
# Domains where the index interpretation is inverted or context-dependent
_NEUTRAL_DOMAINS = {"water", "urban", "snow", "soil", "coastal", "wetland"}


# --- Threshold Parsing ---

def parse_threshold_string(s: str) -> Tuple[Optional[float], Optional[float]]:
    """Parse threshold string from domain_knowledge into (min, max) bounds.

    Examples:
        "< 0.1"       -> (None, 0.1)
        "0.1 - 0.3"   -> (0.1, 0.3)
        "> 0.6"        -> (0.6, None)
        "~0.0"         -> (-0.05, 0.05)
        "-0.25 - -0.1" -> (-0.25, -0.1)
    """
    s = s.strip()

    # "~X" means approximately X (+-0.05)
    if s.startswith("~"):
        try:
            val = float(s[1:].strip())
            return (val - 0.05, val + 0.05)
        except ValueError:
            return (None, None)

    # "< X" or "<= X"
    m = re.match(r'^[<≤]\s*=?\s*([-+]?\d*\.?\d+)', s)
    if m:
        return (None, float(m.group(1)))

    # "> X" or ">= X"
    m = re.match(r'^[>≥]\s*=?\s*([-+]?\d*\.?\d+)', s)
    if m:
        return (float(m.group(1)), None)

    # "X - Y" range (handles negative numbers)
    m = re.match(r'^([-+]?\d*\.?\d+)\s*[-–]\s*([-+]?\d*\.?\d+)$', s)
    if m:
        return (float(m.group(1)), float(m.group(2)))

    return (None, None)


# --- Domain Threshold Loading ---

def _resolve_index_thresholds(
    domain: str, index_name: str
) -> Tuple[Optional[List[Dict]], Optional[str]]:
    """Resolve thresholds for a domain/index, reporting default-index borrows.

    Returns (thresholds, borrowed_from). thresholds is a list of
    {"min": float, "max": float, "label": str} or None. borrowed_from is the
    domain's default index name when the requested index has no table of its
    own and the default's table was used -- callers must surface that borrow
    to the user (never silent).
    """
    ctx = _get_domain_contexts().get(domain)
    if not ctx:
        return None, None

    thresholds = ctx.get("thresholds", {})
    index_upper = index_name.upper()
    # Case-insensitive direct match (keys like "dNBR" keep canonical case)
    index_thresholds = None
    for key, table in thresholds.items():
        if key.upper() == index_upper:
            index_thresholds = table
            break

    borrowed_from = None
    if not index_thresholds:
        # Try with the domain's default index
        default_idx = ctx.get("default_index")
        if default_idx and default_idx.upper() != index_upper:
            index_thresholds = thresholds.get(default_idx)
            if index_thresholds:
                borrowed_from = default_idx
        if not index_thresholds:
            return None, None

    result = []
    for label, threshold_str in index_thresholds.items():
        t_min, t_max = parse_threshold_string(threshold_str)
        entry = {
            "label": label.replace("_", " ").title(),
            "min": t_min if t_min is not None else float("-inf"),
            "max": t_max if t_max is not None else float("inf"),
        }
        result.append(entry)

    return (result if result else None), borrowed_from


def load_domain_thresholds(domain: str, index_name: str) -> Optional[List[Dict]]:
    """Convert domain_knowledge thresholds to threshold_classify format.

    Returns list of {"min": float, "max": float, "label": str} or None if
    no thresholds found for the given domain/index combination.
    """
    result, _ = _resolve_index_thresholds(domain, index_name)
    return result


# --- Index Detection ---

def _detect_index_from_path(file_path: str) -> Optional[str]:
    """Detect spectral index name from file path.

    Checks longer names first to avoid substring collision
    (e.g., "gndvi" before "ndvi", "mndwi" before "ndwi").
    Returns the canonical key from _INDEX_DOMAIN_MAP (preserves case like "dNBR").
    """
    if not file_path:
        return None

    basename = os.path.basename(file_path).lower()
    # Remove extension
    name = os.path.splitext(basename)[0]

    # Sort by length descending to check longer names first
    for index_name in sorted(_INDEX_DOMAIN_MAP.keys(), key=len, reverse=True):
        if index_name.lower() in name:
            return index_name  # Preserve canonical case (e.g., "dNBR" not "DNBR")

    return None


def _infer_domain(index_name: Optional[str], domain: Optional[str] = None) -> Optional[str]:
    """Infer domain from index name, or use explicit domain if provided."""
    if domain:
        return domain
    if index_name:
        # Case-insensitive lookup: try exact match first, then upper-case key scan
        result = _INDEX_DOMAIN_MAP.get(index_name)
        if result:
            return result
        idx_upper = index_name.upper()
        for key, val in _INDEX_DOMAIN_MAP.items():
            if key.upper() == idx_upper:
                return val
    return None


# --- Grade Computation ---

def _compute_grade(score: float) -> str:
    """Convert 0-100 score to letter grade."""
    if score >= 80:
        return "A"
    elif score >= 60:
        return "B"
    elif score >= 40:
        return "C"
    elif score >= 20:
        return "D"
    else:
        return "F"


def _grade_description(grade: str) -> str:
    """Human-readable description for each grade."""
    return {
        "A": "Excellent",
        "B": "Good",
        "C": "Moderate",
        "D": "Poor",
        "F": "Critical",
    }.get(grade, "Unknown")


# --- Core Interpretation Functions ---

def interpret_index_value(
    index_name: str,
    stats: Dict[str, Any],
    domain: Optional[str] = None,
) -> Dict[str, Any]:
    """Interpret index statistics using domain thresholds.

    Args:
        index_name: Spectral index name (e.g., "NDVI", "NDWI")
        stats: Dict with keys like mean, median, min, max, std, p5, p25, p75, p95
        domain: Optional domain override (auto-inferred from index if omitted)

    Returns:
        Dict with: grade, grade_score, mean_category, category_breakdown,
                    warnings, summary, tips, domain
    """
    resolved_domain = _infer_domain(index_name, domain)
    index_upper = index_name.upper()

    result = {
        "index": index_upper,
        "domain": resolved_domain,
        "grade": None,
        "grade_score": None,
        "mean_category": None,
        "category_breakdown": [],
        "warnings": [],
        "summary": "",
        "tips": [],
    }

    # Get thresholds (borrow-aware: a default-index borrow must be surfaced to
    # the user, never silent -- see the NBR-scored-against-dNBR mislabel defect).
    thresholds = None
    borrowed_from = None
    if resolved_domain:
        thresholds, borrowed_from = _resolve_index_thresholds(resolved_domain, index_upper)

    if not thresholds:
        # Graceful fallback: return basic stats without interpretation
        mean_val = stats.get("mean")
        result["summary"] = (
            f"{index_upper} analysis complete. "
            f"Mean: {mean_val:.4f}. "
            f"No domain thresholds available for detailed interpretation."
            if mean_val is not None
            else f"{index_upper} analysis complete. No valid data."
        )
        return result

    # Classify mean value
    mean_val = stats.get("mean")
    if mean_val is not None:
        for t in thresholds:
            t_min = t["min"]
            t_max = t["max"]
            if t_min <= mean_val < t_max or (t_max == float("inf") and mean_val >= t_min):
                result["mean_category"] = t["label"]
                break

    # Estimate category breakdown from percentiles/distribution
    breakdown = _estimate_category_breakdown(stats, thresholds)
    result["category_breakdown"] = breakdown

    # Compute grade score
    grade_score = _compute_index_grade_score(stats, thresholds, resolved_domain, index_upper)
    result["grade_score"] = grade_score
    result["grade"] = _compute_grade(grade_score)

    # Generate warnings
    warnings = _generate_warnings(stats, index_upper, resolved_domain)
    result["warnings"] = warnings

    # Build summary
    grade_desc = _grade_description(result["grade"])
    domain_label = (resolved_domain or "unknown").replace("_", " ")
    result["summary"] = (
        f"{index_upper} ({domain_label}) -- Grade: {result['grade']} ({grade_score}/100, how well values fit this index) - {grade_desc}. "
        f"Mean value {mean_val:.4f} classified as '{result['mean_category'] or 'Unknown'}'."
        if mean_val is not None
        else f"{index_upper} ({domain_label}) -- No valid data for interpretation."
    )

    # Surface a default-index borrow so the reader never mistakes a borrowed
    # scale for the real one (the NBR-vs-dNBR mislabel that shipped once).
    if borrowed_from:
        borrow_note = (
            f"No {index_upper} thresholds for the {resolved_domain} domain; "
            f"interpreted against the {borrowed_from} scale."
        )
        result["warnings"].append(borrow_note)
        result["summary"] = f"{result['summary']} {borrow_note}"

    # Get tips from domain context
    if resolved_domain:
        ctx = _get_domain_contexts().get(resolved_domain, {})
        tips_str = ctx.get("tips", "")
        if tips_str:
            result["tips"] = [t.strip() for t in tips_str.split(". ") if t.strip()]

    return result


def _estimate_category_breakdown(
    stats: Dict[str, Any], thresholds: List[Dict]
) -> List[Dict[str, Any]]:
    """Estimate percentage of area in each category from percentile data.

    Uses p5, p25, median, p75, p95 to approximate the distribution across
    threshold categories.
    """
    breakdown = []
    percentiles = [
        (0, stats.get("p5", stats.get("min"))),
        (5, stats.get("p5", stats.get("min"))),
        (25, stats.get("p25")),
        (50, stats.get("median", stats.get("mean"))),
        (75, stats.get("p75")),
        (95, stats.get("p95", stats.get("max"))),
        (100, stats.get("p95", stats.get("max"))),
    ]

    # Filter out None values
    valid_percentiles = [(p, v) for p, v in percentiles if v is not None]

    if not valid_percentiles:
        for t in thresholds:
            breakdown.append({
                "category": t["label"],
                "range": f"{t['min']} to {t['max']}",
                "est_area_pct": None,
                "assessment": "No data",
            })
        return breakdown

    for t in thresholds:
        t_min = t["min"]
        t_max = t["max"]

        # Estimate what percentile range falls within this threshold
        pct_below_min = 0
        pct_below_max = 100

        for pct, val in valid_percentiles:
            if val is not None:
                if t_min != float("-inf") and val <= t_min:
                    pct_below_min = max(pct_below_min, pct)
                if t_max != float("inf") and val <= t_max:
                    pct_below_max = max(pct_below_max, pct)
                elif t_max == float("inf"):
                    pct_below_max = 100

        est_pct = max(0, pct_below_max - pct_below_min)

        # Format range string
        if t_min == float("-inf"):
            range_str = f"< {t_max}"
        elif t_max == float("inf"):
            range_str = f"> {t_min}"
        else:
            range_str = f"{t_min} - {t_max}"

        # Assessment
        if est_pct == 0:
            assessment = "None detected"
        elif est_pct < 10:
            assessment = "Minimal"
        elif est_pct < 25:
            assessment = "Some areas"
        elif est_pct < 50:
            assessment = "Significant"
        elif est_pct < 75:
            assessment = "Dominant"
        else:
            assessment = "Widespread"

        breakdown.append({
            "category": t["label"],
            "range": range_str,
            "est_area_pct": round(est_pct),
            "assessment": assessment,
        })

    return breakdown


def _compute_index_grade_score(
    stats: Dict[str, Any],
    thresholds: List[Dict],
    domain: Optional[str],
    index_name: str,
) -> int:
    """Compute 0-100 grade score based on domain context.

    For positive domains (vegetation): higher values = higher score.
    For neutral domains: score based on data quality and distribution spread.
    """
    mean_val = stats.get("mean")
    if mean_val is None:
        return 0

    coverage_pct = stats.get("coverage_pct")
    if coverage_pct is not None:
        coverage_bonus = min(20, int(coverage_pct / 5))  # 0-20 points for coverage
    else:
        coverage_bonus = 10  # assume decent coverage if not provided

    # Value-based scoring (0-60 points)
    value_score = 30  # default neutral

    if domain in _POSITIVE_DOMAINS:
        # Higher mean = better (e.g., NDVI for vegetation)
        # Find the position of mean in the threshold range
        if thresholds:
            n = len(thresholds)
            for i, t in enumerate(thresholds):
                t_min = t["min"] if t["min"] != float("-inf") else -1.0
                t_max = t["max"] if t["max"] != float("inf") else 1.0
                if t_min <= mean_val < t_max or (t["max"] == float("inf") and mean_val >= t_min):
                    # Position in categories: first = worst, last = best
                    value_score = int(60 * (i + 1) / n)
                    break
    elif domain in _NEUTRAL_DOMAINS:
        # For water/urban/snow: presence detection matters, not "higher is better"
        # Score based on distribution spread and data completeness
        std = stats.get("std", 0) or 0
        if std > 0:
            value_score = min(50, int(30 + std * 40))
        else:
            value_score = 30
    else:
        # Unknown domain: moderate default
        value_score = 30

    # Distribution quality score (0-20 points)
    std = stats.get("std", 0) or 0
    dist_score = 10  # default
    if std < 0.01:
        dist_score = 5  # suspiciously low variance
    elif std < 0.3:
        dist_score = 15  # healthy variance
    else:
        dist_score = 10  # high variance

    total = min(100, coverage_bonus + value_score + dist_score)
    return max(0, total)


def _generate_warnings(stats: Dict, index_name: str, domain: Optional[str]) -> List[str]:
    """Generate warnings about potential data issues."""
    warnings = []
    mean_val = stats.get("mean")
    std = stats.get("std")
    min_val = stats.get("min")
    max_val = stats.get("max")
    coverage = stats.get("coverage_pct")

    if coverage is not None and coverage < 50:
        warnings.append(f"Low coverage: only {coverage}% valid pixels. Results may be unreliable.")

    if mean_val is not None and std is not None and std < 0.001:
        warnings.append("Very low variance -- data may be uniform or corrupted.")

    if index_name in ("NDVI", "EVI", "SAVI", "GNDVI", "NDRE", "LAI"):
        if mean_val is not None and mean_val > 0.8:
            warnings.append(f"{index_name} saturates above ~0.8. Consider using EVI for dense canopy.")
        if min_val is not None and min_val < -0.5:
            warnings.append(f"Strongly negative {index_name} values detected -- possible water/shadow contamination.")

    if index_name in ("NDWI", "MNDWI"):
        if mean_val is not None and mean_val > 0.8:
            warnings.append("Very high water index -- check for cloud contamination.")

    if index_name == "dNBR":
        if max_val is not None and max_val > 1.3:
            warnings.append("Extreme dNBR values (>1.3) may indicate data artifacts.")

    return warnings


# --- Change Interpretation ---

def interpret_change(
    diff_stats: Dict[str, Any],
    index_name: str,
    domain: Optional[str] = None,
) -> Dict[str, Any]:
    """Interpret difference statistics between two time periods.

    Args:
        diff_stats: Dict with mean, std, min, max from difference raster
        index_name: The spectral index being compared
        domain: Optional domain override

    Returns:
        Dict with: direction, magnitude, severity, likely_causes,
                    recommendations, summary
    """
    resolved_domain = _infer_domain(index_name, domain)
    mean_diff = diff_stats.get("mean", 0) or 0
    std_diff = diff_stats.get("std", 0) or 0
    abs_mean = abs(mean_diff)

    # Direction
    if abs_mean < 0.01:
        direction = "stable"
    elif mean_diff > 0:
        direction = "increase"
    else:
        direction = "decrease"

    # Magnitude classification
    if abs_mean < 0.05:
        magnitude = "negligible"
    elif abs_mean < 0.1:
        magnitude = "minor"
    elif abs_mean < 0.2:
        magnitude = "moderate"
    elif abs_mean < 0.4:
        magnitude = "significant"
    else:
        magnitude = "extreme"

    # Severity (1-5 scale)
    severity_map = {"negligible": 1, "minor": 2, "moderate": 3, "significant": 4, "extreme": 5}
    severity = severity_map.get(magnitude, 3)

    # Context-dependent interpretation
    likely_causes = _infer_change_causes(direction, magnitude, index_name, resolved_domain)
    recommendations = _infer_change_recommendations(direction, magnitude, index_name, resolved_domain)

    # Summary
    index_upper = index_name.upper()
    domain_label = (resolved_domain or "unknown").replace("_", " ")
    summary = (
        f"{index_upper} change ({domain_label}): {direction} of {abs_mean:.4f} "
        f"(magnitude: {magnitude}, severity: {severity}/5). "
    )
    if direction == "stable":
        summary += "No significant change detected."
    elif likely_causes:
        summary += f"Likely cause(s): {', '.join(likely_causes[:2])}."

    return {
        "index": index_upper,
        "domain": resolved_domain,
        "direction": direction,
        "magnitude": magnitude,
        "severity": severity,
        "mean_difference": round(mean_diff, 6),
        "std_difference": round(std_diff, 6),
        "likely_causes": likely_causes,
        "recommendations": recommendations,
        "summary": summary,
    }


def _infer_change_causes(
    direction: str, magnitude: str, index_name: str, domain: Optional[str]
) -> List[str]:
    """Infer likely causes for observed changes."""
    causes = []
    # Normalize to uppercase for all comparisons
    idx = index_name.upper()

    if direction == "stable":
        return ["No significant change"]

    if idx in ("NDVI", "EVI", "SAVI", "GNDVI", "NDRE", "LAI"):
        if direction == "decrease":
            causes = ["Vegetation stress or die-off", "Seasonal senescence", "Land clearing or deforestation",
                       "Drought conditions", "Disease or pest damage"]
        else:
            causes = ["Vegetation recovery or growth", "Seasonal green-up", "Irrigation improvement",
                       "Reforestation", "Favorable weather conditions"]

    elif idx in ("NDWI", "MNDWI"):
        if direction == "increase":
            causes = ["Water body expansion", "Flooding event", "Reservoir filling",
                       "Increased precipitation"]
        else:
            causes = ["Water body shrinkage", "Drought", "Water extraction or diversion"]

    elif idx in ("DNBR", "NBR"):
        if direction == "increase" and idx == "DNBR":
            causes = ["Fire/burn event", "Vegetation removal"]
        elif direction == "decrease" and idx == "DNBR":
            causes = ["Post-fire vegetation recovery", "Regrowth"]

    elif idx in ("NDBI", "UI"):
        if direction == "increase":
            causes = ["Urban expansion", "New construction", "Land development"]
        else:
            causes = ["Urban greening", "Building demolition", "Vegetation recovery"]

    elif idx == "BSI":
        if direction == "increase":
            causes = ["Soil exposure", "Land degradation", "Mining activity", "Deforestation"]
        else:
            causes = ["Vegetation cover increase", "Land rehabilitation"]

    elif idx == "NDSI":
        if direction == "increase":
            causes = ["Increased snowfall", "Temperature decrease"]
        else:
            causes = ["Snowmelt", "Temperature increase", "Reduced precipitation"]

    elif idx == "NDMI":
        if direction == "decrease":
            causes = ["Water stress increase", "Drought onset", "Reduced soil moisture"]
        else:
            causes = ["Improved moisture conditions", "Rainfall increase"]

    if not causes:
        causes = [f"{direction.capitalize()} in {index_name} values"]

    # Filter to top 3
    return causes[:3]


def _infer_change_recommendations(
    direction: str, magnitude: str, index_name: str, domain: Optional[str]
) -> List[str]:
    """Generate recommendations based on change analysis."""
    recs = []
    idx = index_name.upper()

    if magnitude in ("significant", "extreme"):
        recs.append("Investigate with higher temporal resolution to identify exact timing of change.")
        recs.append("Cross-validate with additional spectral indices for confirmation.")

    if idx in ("NDVI", "EVI") and direction == "decrease" and magnitude not in ("negligible",):
        recs.append("Check for drought conditions using NDMI.")
        recs.append("Compare with precipitation data for the period.")

    if idx == "DNBR" and direction == "increase":
        recs.append("Use threshold_classify with fire domain thresholds for burn severity mapping.")

    if idx in ("NDWI", "MNDWI") and magnitude in ("moderate", "significant", "extreme"):
        recs.append("Use multi-temporal analysis to distinguish permanent vs. temporary change.")

    if not recs:
        recs.append("Continue monitoring with regular temporal intervals.")

    return recs[:4]


# --- Quality Assessment ---

def assess_quality(
    stats: Dict[str, Any],
    metadata: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Assess data quality from statistics.

    Args:
        stats: Dict with coverage_pct, valid_pixels, total_pixels, statistics sub-dict
        metadata: Optional additional metadata

    Returns:
        Dict with: grade, score, flags, summary
    """
    flags = []
    score = 100  # Start perfect, deduct for issues

    # Coverage check
    coverage = stats.get("coverage_pct")
    if coverage is not None:
        if coverage < 30:
            flags.append(f"Very low coverage ({coverage}%) -- most pixels are nodata/NaN")
            score -= 40
        elif coverage < 60:
            flags.append(f"Low coverage ({coverage}%) -- significant nodata areas")
            score -= 20
        elif coverage < 80:
            flags.append(f"Moderate coverage ({coverage}%)")
            score -= 10

    # Value range plausibility
    inner_stats = stats.get("statistics") or stats
    min_val = inner_stats.get("min")
    max_val = inner_stats.get("max")
    mean_val = inner_stats.get("mean")
    std_val = inner_stats.get("std")

    if min_val is not None and max_val is not None:
        val_range = max_val - min_val
        if val_range == 0:
            flags.append("Zero value range -- all pixels have the same value (possible error)")
            score -= 30
        elif val_range > 10:
            flags.append(f"Very wide value range ({min_val:.4f} to {max_val:.4f}) -- check for outliers")
            score -= 10

    # Std deviation anomalies
    if std_val is not None:
        if std_val == 0:
            flags.append("Zero standard deviation -- data is constant")
            score -= 20
        elif std_val < 0.001:
            flags.append("Extremely low variance -- data may be uniform or corrupted")
            score -= 10

    # NaN ratio
    valid_px = stats.get("valid_pixels")
    total_px = stats.get("total_pixels")
    if valid_px is not None and total_px is not None and total_px > 0:
        nan_ratio = 1 - (valid_px / total_px)
        if nan_ratio > 0.5:
            score -= 15

    score = max(0, min(100, score))
    grade = _compute_grade(score)

    # Summary
    coverage_str = f"{coverage}% valid pixels" if coverage else "coverage unknown"
    summary = f"Pixel data check: {grade} ({score}/100) -- {coverage_str}."
    if flags:
        summary += f" Issues: {'; '.join(flags[:2])}."

    return {
        "grade": grade,
        "score": score,
        "flags": flags,
        "summary": summary,
    }


# --- Recommendations ---

def generate_recommendations(
    domain: Optional[str],
    index_name: str,
    stats: Dict[str, Any],
    interpretation: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    """Generate actionable recommendations based on analysis results.

    Returns list of {"action": str, "reason": str, "tool": str}.
    """
    recs = []
    idx = index_name.upper()
    mean_val = stats.get("mean") or stats.get("statistics", {}).get("mean")

    # Index-specific recommendations
    if idx == "NDVI" and mean_val is not None and mean_val > 0.8:
        recs.append({
            "action": "Use EVI for dense canopy areas",
            "reason": "NDVI saturates above 0.8",
            "tool": "spectral_index_calculate",
        })

    if idx in ("NDVI", "EVI", "SAVI"):
        recs.append({
            "action": "Compare temporal trend with previous season",
            "reason": "Seasonal context improves interpretation accuracy",
            "tool": "temporal_trend_analysis",
        })

    if idx in ("NDWI", "MNDWI"):
        recs.append({
            "action": "Use threshold_classify to map water vs. land",
            "reason": "Binary classification at threshold 0.0 for NDWI",
            "tool": "threshold_classify",
        })

    if idx == "DNBR":
        recs.append({
            "action": "Classify burn severity using USGS thresholds",
            "reason": "dNBR > 0.27 indicates moderate+ burn severity",
            "tool": "threshold_classify",
        })

    if domain == "agriculture":
        recs.append({
            "action": "Analyze phenological cycle for crop stage",
            "reason": "Growth stage context aids yield estimation",
            "tool": "temporal_phenology",
        })

    # Quality-based recommendations
    coverage = stats.get("coverage_pct")
    if coverage is not None and coverage < 70:
        recs.append({
            "action": "Check cloud masking or expand temporal range",
            "reason": f"Only {coverage}% valid pixel coverage",
            "tool": "openeo_quality_metrics",
        })

    return recs[:5]


# --- Formatting ---

def format_interpretation_text(
    interpretation: Dict[str, Any],
    stats: Optional[Dict[str, Any]] = None,
    quality: Optional[Dict[str, Any]] = None,
) -> str:
    """Format interpretation dict as rich markdown text for ReportPanel display.

    Produces a comprehensive, well-structured report with:
    - Executive summary paragraph
    - Key findings bullets
    - Category breakdown table
    - Statistics table (if stats provided)
    - Quality assessment (if quality provided)
    - Warnings and recommendations

    Compatible with ReactMarkdown + remarkGfm (renders tables natively).
    """
    lines: List[str] = []
    index_name = interpretation.get("index", "Unknown")
    domain = (interpretation.get("domain") or "unknown").replace("_", " ")
    grade = interpretation.get("grade", "?")
    score = interpretation.get("grade_score", "?")
    grade_desc = _grade_description(grade) if isinstance(grade, str) else "Unknown"
    mean_category = interpretation.get("mean_category", "")
    summary = interpretation.get("summary", "")

    # --- Header ---
    lines.append(f"## {index_name} Interpretation — Grade: {grade} ({score}/100)")
    lines.append("")

    # --- Executive Summary ---
    lines.append("### Executive Summary")
    lines.append("")
    mean_val = None
    if stats:
        inner = stats.get("statistics") or stats
        mean_val = inner.get("mean")
    exec_parts = [f"The {domain} analysis using **{index_name}** received a grade of "
                  f"**{grade} ({grade_desc})** with a score of {score}/100."]
    if mean_val is not None and mean_category:
        exec_parts.append(f"The mean value of **{mean_val:.4f}** falls within the "
                         f"**{mean_category}** category.")
    elif summary:
        exec_parts.append(summary)
    coverage = stats.get("coverage_pct") if stats else None
    if coverage is not None:
        exec_parts.append(f"Data coverage is **{coverage}%** of the analyzed area.")
    lines.append(" ".join(exec_parts))
    lines.append("")

    # --- Key Findings ---
    lines.append("### Key Findings")
    lines.append("")
    lines.append(f"- **Overall Grade**: {grade} — {grade_desc} ({score}/100)")
    if mean_val is not None:
        lines.append(f"- **Mean {index_name}**: {mean_val:.4f} ({mean_category or 'Unclassified'})")
    if stats:
        inner = stats.get("statistics") or stats
        p5 = inner.get("p5")
        p95 = inner.get("p95")
        std_val = inner.get("std")
        if p5 is not None and p95 is not None:
            lines.append(f"- **Value Range (P5–P95)**: {p5:.4f} to {p95:.4f}")
        if std_val is not None:
            variability = "low" if std_val < 0.05 else "moderate" if std_val < 0.15 else "high"
            lines.append(f"- **Spatial Variability**: {variability} (std: {std_val:.4f})")
    if coverage is not None:
        lines.append(f"- **Data Coverage**: {coverage}% valid pixels")
    area = stats.get("valid_area_ha") if stats else None
    if area is not None:
        lines.append(f"- **Analyzed Area**: {area:,.1f} hectares")
    lines.append("")

    # --- Category Breakdown ---
    breakdown = interpretation.get("category_breakdown", [])
    if breakdown:
        lines.append("### Category Breakdown")
        lines.append("")
        lines.append("| Category | Range | Est. Area | Assessment |")
        lines.append("|----------|-------|-----------|------------|")
        for cat in breakdown:
            pct = f"~{cat['est_area_pct']}%" if cat.get("est_area_pct") is not None else "N/A"
            lines.append(f"| {cat['category']} | {cat['range']} | {pct} | {cat['assessment']} |")
        lines.append("")

    # --- Statistics Table ---
    if stats:
        inner = stats.get("statistics") or stats
        stat_keys = [("min", "Minimum"), ("max", "Maximum"), ("mean", "Mean"),
                     ("median", "Median"), ("std", "Std Deviation"),
                     ("p5", "5th Percentile"), ("p25", "25th Percentile"),
                     ("p75", "75th Percentile"), ("p95", "95th Percentile")]
        has_stats = any(inner.get(k) is not None for k, _ in stat_keys)
        if has_stats:
            lines.append("### Statistics")
            lines.append("")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            for key, label in stat_keys:
                val = inner.get(key)
                if val is not None:
                    lines.append(f"| {label} | {val:.6f} |")
            lines.append("")

    # --- Quality Assessment ---
    if quality:
        q_grade = quality.get("grade", "?")
        q_score = quality.get("score", "?")
        q_summary = quality.get("summary", "")
        flags = quality.get("flags", [])
        lines.append("### Data Quality")
        lines.append("")
        lines.append(f"**Grade: {q_grade} ({q_score}/100)** — {q_summary}")
        if flags:
            lines.append("")
            for f in flags:
                lines.append(f"- {f}")
        lines.append("")

    # --- Warnings ---
    warnings = interpretation.get("warnings", [])
    if warnings:
        lines.append("### Warnings")
        lines.append("")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    # --- Recommendations ---
    tips = interpretation.get("tips", [])
    if tips:
        lines.append("### Recommendations")
        lines.append("")
        for tip in tips[:5]:
            lines.append(f"- {tip}")
        lines.append("")

    return "\n".join(lines)


def format_quality_text(quality: Dict[str, Any]) -> str:
    """Format quality assessment as a structured markdown section."""
    grade = quality.get("grade", "?")
    score = quality.get("score", "?")
    grade_desc = _grade_description(grade) if isinstance(grade, str) else "Unknown"
    flags = quality.get("flags", [])

    lines = [f"**Data Quality: {grade} ({score}/100) — {grade_desc}**"]

    if flags:
        lines.append("")
        for f in flags:
            lines.append(f"- {f}")
    else:
        lines.append("")
        lines.append("- No quality issues detected")

    return "\n".join(lines)


def format_change_text(change: Dict[str, Any]) -> str:
    """Format change interpretation as markdown."""
    lines = []
    lines.append(f"### Change Analysis: {change.get('index', 'Unknown')}")
    lines.append("")
    lines.append(change.get("summary", ""))
    lines.append("")
    lines.append(f"- **Direction**: {change.get('direction', 'unknown')}")
    lines.append(f"- **Magnitude**: {change.get('magnitude', 'unknown')}")
    lines.append(f"- **Severity**: {change.get('severity', '?')}/5")
    lines.append(f"- **Mean difference**: {change.get('mean_difference', 0):.4f}")
    lines.append("")

    causes = change.get("likely_causes", [])
    if causes:
        lines.append("**Likely causes:**")
        for c in causes:
            lines.append(f"- {c}")
        lines.append("")

    recs = change.get("recommendations", [])
    if recs:
        lines.append("**Recommendations:**")
        for r in recs:
            lines.append(f"- {r}")

    return "\n".join(lines)


# --- Report Generation ---

def generate_report(
    file_path: str,
    stats: Dict[str, Any],
    interpretation: Optional[Dict] = None,
    quality: Optional[Dict] = None,
    title: Optional[str] = None,
    context: Optional[str] = None,
) -> str:
    """Generate a comprehensive markdown analysis report.

    Combines executive summary, statistics, interpretation, quality assessment,
    and recommendations into a single well-formatted document.
    """
    index_name = interpretation.get("index", "Unknown") if interpretation else "Unknown"
    domain = interpretation.get("domain", "unknown") if interpretation else "unknown"
    domain_label = domain.replace("_", " ")
    report_title = title or f"{index_name} Analysis Report"

    lines: List[str] = [f"# {report_title}", ""]

    # --- Executive Summary ---
    inner_stats = stats.get("statistics") or stats
    mean_val = inner_stats.get("mean")
    coverage = stats.get("coverage_pct")
    area = stats.get("valid_area_ha")

    lines.append("## Executive Summary")
    lines.append("")
    summary_parts = []
    if interpretation:
        grade = interpretation.get("grade", "?")
        score = interpretation.get("grade_score", "?")
        grade_desc = _grade_description(grade) if isinstance(grade, str) else "Unknown"
        mean_cat = interpretation.get("mean_category", "")
        summary_parts.append(
            f"This {domain_label} analysis using **{index_name}** received an overall "
            f"grade of **{grade} ({grade_desc}, {score}/100)**."
        )
        if mean_val is not None and mean_cat:
            summary_parts.append(
                f"The mean value of **{mean_val:.4f}** is classified as **{mean_cat}**."
            )
    if coverage is not None:
        summary_parts.append(f"Data coverage is **{coverage}%** of the analyzed area.")
    if area is not None:
        summary_parts.append(f"The valid analyzed area spans **{area:,.1f} hectares**.")
    if context:
        summary_parts.append(f"*Context: {context}*")
    lines.append(" ".join(summary_parts) if summary_parts else "Analysis complete.")
    lines.append("")

    # --- Key Findings ---
    if interpretation:
        lines.append("## Key Findings")
        lines.append("")
        grade = interpretation.get("grade", "?")
        grade_desc = _grade_description(grade) if isinstance(grade, str) else "Unknown"
        score = interpretation.get("grade_score", "?")
        lines.append(f"- **Overall Grade**: {grade} — {grade_desc} ({score}/100)")
        if mean_val is not None:
            lines.append(f"- **Mean {index_name}**: {mean_val:.4f} ({interpretation.get('mean_category', 'Unclassified')})")
        std_val = inner_stats.get("std")
        if std_val is not None:
            variability = "low" if std_val < 0.05 else "moderate" if std_val < 0.15 else "high"
            lines.append(f"- **Spatial Variability**: {variability} (std: {std_val:.4f})")
        if coverage is not None:
            lines.append(f"- **Data Coverage**: {coverage}%")
        if area is not None:
            lines.append(f"- **Analyzed Area**: {area:,.1f} hectares")
        lines.append("")

    # --- Statistics ---
    lines.append("## Statistics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| **File** | `{os.path.basename(file_path)}` |")
    stat_keys = [("min", "Minimum"), ("max", "Maximum"), ("mean", "Mean"),
                 ("median", "Median"), ("std", "Std Deviation"),
                 ("p5", "5th Percentile"), ("p25", "25th Percentile"),
                 ("p75", "75th Percentile"), ("p95", "95th Percentile")]
    for key, label in stat_keys:
        val = inner_stats.get(key)
        if val is not None:
            lines.append(f"| {label} | {val:.6f} |")
    if coverage is not None:
        lines.append(f"| Coverage | {coverage}% |")
    if area is not None:
        lines.append(f"| Valid Area | {area:,.1f} ha |")
    total_area = stats.get("total_area_ha")
    if total_area is not None:
        lines.append(f"| Total Area | {total_area:,.1f} ha |")
    lines.append("")

    # --- Category Breakdown ---
    if interpretation:
        breakdown = interpretation.get("category_breakdown", [])
        if breakdown:
            lines.append("## Category Breakdown")
            lines.append("")
            lines.append("| Category | Range | Est. Area | Assessment |")
            lines.append("|----------|-------|-----------|------------|")
            for cat in breakdown:
                pct = f"~{cat['est_area_pct']}%" if cat.get("est_area_pct") is not None else "N/A"
                lines.append(f"| {cat['category']} | {cat['range']} | {pct} | {cat['assessment']} |")
            lines.append("")

    # --- Data Quality ---
    if quality:
        lines.append("## Data Quality")
        lines.append("")
        lines.append(format_quality_text(quality))
        lines.append("")

    # --- Warnings ---
    if interpretation:
        warnings = interpretation.get("warnings", [])
        if warnings:
            lines.append("## Warnings")
            lines.append("")
            for w in warnings:
                lines.append(f"- {w}")
            lines.append("")

    # --- Recommendations ---
    if interpretation:
        recs = generate_recommendations(
            interpretation.get("domain"),
            interpretation.get("index", "Unknown"),
            stats,
            interpretation,
        )
        tips = interpretation.get("tips", [])
        if recs or tips:
            lines.append("## Recommended Next Steps")
            lines.append("")
            for r in recs:
                lines.append(f"- **{r['action']}** — {r['reason']} (use `{r['tool']}`)")
            for tip in tips[:3]:
                lines.append(f"- {tip}")
            lines.append("")

    lines.extend(["---", "*Generated by JonaAI Interpretation Engine*"])

    return "\n".join(lines)


# --- Tool Definitions ---

INTERPRETATION_TOOL_DEFINITIONS = [
    {
        "name": "interpret_result",
        "description": (
            "Interpret EO analysis results with domain-aware context. "
            "Reads a GeoTIFF, computes statistics, and returns interpretation with "
            "letter grade (A-F), category breakdown, quality assessment, and recommendations. "
            "Auto-detects index and domain from file path. Use after raster_statistics or "
            "spectral_index_calculate for meaningful interpretation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF result file",
                },
                "index_name": {
                    "type": "string",
                    "description": "Spectral index name (e.g., NDVI, NDWI). Auto-detected from file path if omitted.",
                },
                "domain": {
                    "type": "string",
                    "description": "Domain override (vegetation, water, fire, etc.). Auto-inferred from index if omitted.",
                },
                "band": {
                    "type": "integer",
                    "description": "Band number to analyze (1-indexed, default: 1)",
                    "default": 1,
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "generate_report",
        "description": (
            "Generate a comprehensive markdown analysis report for an EO result file. "
            "Combines statistics, interpretation, quality assessment, and recommendations "
            "into a formatted document. Use after interpret_result or raster_statistics."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF result file",
                },
                "title": {
                    "type": "string",
                    "description": "Report title (auto-generated if omitted)",
                },
                "index_name": {
                    "type": "string",
                    "description": "Spectral index name (auto-detected from file path if omitted)",
                },
                "domain": {
                    "type": "string",
                    "description": "Domain override (auto-inferred from index if omitted)",
                },
                "context": {
                    "type": "string",
                    "description": "Optional context description for the report header",
                },
            },
            "required": ["file_path"],
        },
    },
]


# --- Tool Factory ---

def create_interpretation_tools(config=None) -> Dict[str, Any]:
    """Create interpretation tool handlers for Claude SDK.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _interpret_result(args: Dict[str, Any]) -> Dict[str, Any]:
        """Interpret an EO result file with domain context."""
        try:
            import rasterio
            import numpy as np

            file_path = args["file_path"]
            index_name = args.get("index_name")
            domain = args.get("domain")
            band = args.get("band", 1)

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            # Auto-detect index from path if not provided
            if not index_name:
                index_name = _detect_index_from_path(file_path)

            if not index_name:
                return {"content": [{"type": "text", "text": (
                    f"Could not detect spectral index from file path: {file_path}. "
                    "Please specify index_name parameter (e.g., 'NDVI', 'NDWI')."
                )}]}

            # Compute statistics
            with rasterio.open(file_path) as ds:
                if band < 1 or band > ds.count:
                    return {"content": [{"type": "text", "text": f"Error: Band {band} out of range (1-{ds.count})"}]}

                data = ds.read(band).astype(float)
                nodata = ds.nodata
                if nodata is not None:
                    data[data == nodata] = np.nan

                total_pixels = data.size
                valid_mask = ~np.isnan(data)
                valid = data[valid_mask].ravel()

                if valid.size == 0:
                    return {"content": [{"type": "text", "text": "No valid pixel data for interpretation."}]}

                stats = {
                    "min": float(np.min(valid)),
                    "max": float(np.max(valid)),
                    "mean": float(np.mean(valid)),
                    "median": float(np.median(valid)),
                    "std": float(np.std(valid)),
                    "p5": float(np.percentile(valid, 5)),
                    "p25": float(np.percentile(valid, 25)),
                    "p75": float(np.percentile(valid, 75)),
                    "p95": float(np.percentile(valid, 95)),
                    "coverage_pct": round(100.0 * valid.size / total_pixels, 1),
                    "valid_pixels": int(valid.size),
                    "total_pixels": total_pixels,
                }

            # Run interpretation
            interp = interpret_index_value(index_name, stats, domain)
            quality = assess_quality(stats)

            # Format output — pass stats and quality for comprehensive display
            text = format_interpretation_text(interp, stats=stats, quality=quality)

            # Structured JSON for viz extraction
            result_json = {
                "interpretation": interp,
                "quality": quality,
                "statistics": stats,
                "file_path": file_path,
            }

            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(result_json)},
                ]
            }

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error interpreting result: {str(e)}"}]}

    async def _generate_report(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a comprehensive analysis report."""
        try:
            import rasterio
            import numpy as np

            file_path = args["file_path"]
            title = args.get("title")
            index_name = args.get("index_name")
            domain = args.get("domain")
            context = args.get("context")

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            # Auto-detect
            if not index_name:
                index_name = _detect_index_from_path(file_path)

            # Compute statistics
            with rasterio.open(file_path) as ds:
                data = ds.read(1).astype(float)
                nodata = ds.nodata
                if nodata is not None:
                    data[data == nodata] = np.nan

                total_pixels = data.size
                valid_mask = ~np.isnan(data)
                valid = data[valid_mask].ravel()

                # Pixel area
                res_x, res_y = abs(ds.res[0]), abs(ds.res[1])
                crs = ds.crs
                pixel_area_sq_m = res_x * res_y
                if crs and not crs.is_projected:
                    center_lat = (ds.bounds.top + ds.bounds.bottom) / 2
                    m_per_deg_lat = 111320.0
                    m_per_deg_lon = 111320.0 * np.cos(np.radians(center_lat))
                    pixel_area_sq_m = (res_x * m_per_deg_lon) * (res_y * m_per_deg_lat)
                pixel_area_ha = pixel_area_sq_m / 10000.0

                stats = {
                    "total_pixels": total_pixels,
                    "valid_pixels": int(valid.size),
                    "coverage_pct": round(100.0 * valid.size / total_pixels, 1) if total_pixels > 0 else 0,
                    "valid_area_ha": round(valid.size * pixel_area_ha, 2),
                    "total_area_ha": round(total_pixels * pixel_area_ha, 2),
                    "statistics": {
                        "min": round(float(np.min(valid)), 6),
                        "max": round(float(np.max(valid)), 6),
                        "mean": round(float(np.mean(valid)), 6),
                        "median": round(float(np.median(valid)), 6),
                        "std": round(float(np.std(valid)), 6),
                        "p5": round(float(np.percentile(valid, 5)), 6),
                        "p25": round(float(np.percentile(valid, 25)), 6),
                        "p75": round(float(np.percentile(valid, 75)), 6),
                        "p95": round(float(np.percentile(valid, 95)), 6),
                    } if valid.size > 0 else None,
                }

            # Interpretation (if index detected)
            interp = None
            quality_assessment = None
            if index_name and valid.size > 0:
                flat_stats = {**stats, **(stats.get("statistics") or {})}
                interp = interpret_index_value(index_name, flat_stats, domain)
                quality_assessment = assess_quality(stats)

            report_text = generate_report(
                file_path, stats, interp, quality_assessment, title, context
            )

            return {
                "content": [
                    {"type": "text", "text": report_text},
                ]
            }

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error generating report: {str(e)}"}]}

    return {
        "interpret_result": _interpret_result,
        "generate_report": _generate_report,
    }
