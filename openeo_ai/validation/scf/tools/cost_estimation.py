"""Cost and feasibility estimation tool."""
from __future__ import annotations

import math
from typing import Any, Dict, Optional


def _lon_span_degrees(west: float, east: float) -> float:
    """Longitude span in degrees, correct across the antimeridian.

    A plain abs(east - west) reads a 20-degree box spanning 170E..170W as 340, so the
    estimate came out 17x too large and classified a modest Pacific request as "large".
    Same correction as _lon_span_degrees in openeo_ai/sdk/permissions.py, which fixed the
    equivalent misread in the extent size guard. Kept local rather than shared to avoid
    importing the permission layer into a validation tool.
    """
    span = east - west
    if span < 0:
        span += 360.0
    return abs(span)


def _parse_period(value: Optional[str]) -> Optional[int]:
    """Parse ISO-8601 period strings for simple months/years into days."""
    if not value:
        return None
    value = value.upper()
    if value.startswith("P"):
        if "M" in value and "T" not in value:
            try:
                months = int(value.strip("P").strip("M"))
                return months * 30
            except Exception:
                return None
        if "Y" in value:
            try:
                years = int(value.strip("P").strip("Y"))
                return years * 365
            except Exception:
                return None
    return None


def estimate(scf: dict) -> Dict[str, Any]:
    data = scf.get("data") or {}
    policy = scf.get("policy_profile") or {}

    spatial = data.get("spatial_extent") or {}
    temporal = data.get("temporal_extent") or {}
    resolution = data.get("resolution") or {}

    west, south, east, north = (
        spatial.get("west"),
        spatial.get("south"),
        spatial.get("east"),
        spatial.get("north"),
    )

    if None in (west, south, east, north):
        return {"error": "Missing spatial extent"}

    lat_mid = (south + north) / 2.0
    m_per_deg_lat = 111320.0
    m_per_deg_lon = 111320.0 * math.cos(math.radians(lat_mid))

    width_m = _lon_span_degrees(east=east, west=west) * m_per_deg_lon
    height_m = abs(north - south) * m_per_deg_lat

    res_x = resolution.get("x") or 10
    res_y = resolution.get("y") or 10

    pixels = max(1, int((width_m / res_x) * (height_m / res_y)))

    bands = data.get("bands") or []
    band_count = len(bands) if bands else 1

    temporal_step = policy.get("temporal_step")
    step_days = _parse_period(temporal_step)

    # Estimate time slices from temporal extent
    start = temporal.get("start")
    end = temporal.get("end")
    time_slices = 1
    if start and end:
        try:
            from datetime import datetime
            s = datetime.fromisoformat(start)
            e = datetime.fromisoformat(end)
            days = max(1, (e - s).days)
            if step_days:
                time_slices = max(1, int(days / step_days))
            else:
                time_slices = max(1, int(days / 5))  # heuristic: 5-day cadence
        except Exception:
            time_slices = 1

    total_cells = pixels * band_count * time_slices
    bytes_est = total_cells * 4
    mb_est = bytes_est / (1024 * 1024)

    if mb_est < 500:
        runtime_class = "small"
    elif mb_est < 5000:
        runtime_class = "medium"
    else:
        runtime_class = "large"

    suggestions = []
    if runtime_class in {"medium", "large"}:
        suggestions.append("Consider temporal aggregation or reducing spatial extent.")
    if runtime_class == "large":
        suggestions.append("Consider downsampling resolution or tiling.")

    return {
        "estimated_pixels": pixels,
        "estimated_time_slices": time_slices,
        "estimated_cells": total_cells,
        "estimated_mb": round(mb_est, 2),
        "runtime_class": runtime_class,
        "suggestions": suggestions,
    }
