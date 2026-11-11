"""Convert Earth-Agent outputs into JonaAI frontend visualization objects."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote


def to_visualizations(result: Any, tool_name: str = "", title: str | None = None) -> list[dict[str, Any]]:
    parsed = _coerce_payload(result)
    visualizations: list[dict[str, Any]] = []

    raster_path = _extract_raster_path(parsed)
    if raster_path:
        label = title or _title_from_tool_name(tool_name, default="Earth-Agent Result")
        colormap = _colormap_for_tool(tool_name)
        # Extract bounds from the GeoTIFF for frontend map positioning
        bounds = _extract_bounds(raster_path)
        map_data: dict[str, Any] = {
            "type": "raster",
            "url": f"/render-raster?path={quote(raster_path, safe='/')}&colormap={quote(colormap, safe='')}",
            "source": raster_path,
            "colormap": colormap,
            "opacity": 0.6,
        }
        if bounds:
            map_data["bounds"] = bounds
        visualizations.append(
            {
                "type": "map",
                "data": map_data,
                "title": label,
            }
        )

    if isinstance(parsed, dict):
        if "geojson" in parsed and isinstance(parsed["geojson"], dict):
            visualizations.append(
                {
                    "type": "map",
                    "data": {
                        "type": "vector",
                        "geojson": parsed["geojson"],
                    },
                    "title": title or _title_from_tool_name(tool_name, default="Detection Results"),
                }
            )
        # Classification results → badge visualization
        if _looks_like_classification(parsed):
            cls_label = parsed.get("classification") or parsed.get("label") or parsed.get("class", "Unknown")
            cls_confidence = float(parsed.get("confidence", 0))
            categories = []
            for cat in parsed.get("categories", parsed.get("classes", [])):
                if isinstance(cat, dict):
                    categories.append({
                        "name": cat.get("name", cat.get("class", "")),
                        "value": cat.get("value", cat.get("count", cat.get("percentage", 0))),
                        "color": cat.get("color"),
                    })
            visualizations.append(
                {
                    "type": "classification",
                    "data": {
                        "label": str(cls_label),
                        "confidence": cls_confidence,
                        "categories": categories,
                    },
                    "title": title or _title_from_tool_name(tool_name, default="Classification"),
                }
            )
        if _looks_like_time_series(parsed):
            x_values = parsed.get("timestamps") or parsed.get("x") or list(range(len(parsed.get("values", []))))
            y_values = parsed.get("values") or parsed.get("series")
            visualizations.append(
                {
                    "type": "chart",
                    "data": {
                        "chartType": "line",
                        "title": title or _title_from_tool_name(tool_name, default="Trend Analysis"),
                        "xAxis": {"label": parsed.get("x_label", "Step"), "values": x_values},
                        "yAxis": {"label": parsed.get("y_label", "Value")},
                        "series": [
                            {
                                "name": parsed.get("series_name", "Value"),
                                "values": [float(value) for value in y_values],
                            }
                        ],
                    },
                    "title": title or _title_from_tool_name(tool_name, default="Trend Analysis"),
                }
            )
        elif _has_scalar_stats(parsed):
            rows = [[key, _format_scalar(value)] for key, value in parsed.items() if _is_scalar(value)]
            visualizations.append(
                {
                    "type": "table",
                    "data": {
                        "headers": ["Metric", "Value"],
                        "rows": rows,
                    },
                    "title": title or _title_from_tool_name(tool_name, default="Statistics"),
                }
            )

    return visualizations


def to_visualization(result: Any, tool_name: str = "", title: str | None = None) -> dict[str, Any] | None:
    visualizations = to_visualizations(result=result, tool_name=tool_name, title=title)
    return visualizations[0] if visualizations else None


def _coerce_payload(result: Any) -> Any:
    if isinstance(result, dict):
        if "content" in result and isinstance(result["content"], list):
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    parsed = _parse_json(text)
                    if parsed is not None:
                        return parsed
            return result
        return result
    if isinstance(result, str):
        parsed = _parse_json(result)
        return parsed if parsed is not None else result
    return result


def _parse_json(text: str) -> Any | None:
    text = text.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _extract_raster_path(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("output_path", "path", "source"):
            value = payload.get(key)
            if isinstance(value, str) and value.endswith((".tif", ".tiff")):
                return value
        for value in payload.values():
            path = _extract_raster_path(value)
            if path:
                return path
    elif isinstance(payload, list):
        for item in payload:
            path = _extract_raster_path(item)
            if path:
                return path
    elif isinstance(payload, str):
        # Extract path from "Result save(d) at /path/file.tif" patterns first
        for prefix in ("Result saved at ", "Result save at "):
            if prefix in payload:
                path = payload.split(prefix, 1)[1].strip()
                if path.endswith((".tif", ".tiff")):
                    return path
        # Only return raw string if it starts with "/" (actual path, not text with path)
        if payload.startswith("/") and payload.endswith((".tif", ".tiff")):
            return payload
    return None


def _looks_like_time_series(payload: dict[str, Any]) -> bool:
    values = payload.get("values") or payload.get("series")
    if not isinstance(values, list) or not values:
        return False
    return all(isinstance(value, (int, float)) for value in values)


def _has_scalar_stats(payload: dict[str, Any]) -> bool:
    scalar_keys = [key for key, value in payload.items() if _is_scalar(value)]
    return bool(scalar_keys)


def _is_scalar(value: Any) -> bool:
    return isinstance(value, (str, int, float, bool))


def _format_scalar(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    return value


def _title_from_tool_name(tool_name: str, default: str) -> str:
    if not tool_name:
        return default
    clean = tool_name.replace("ea_", "").replace("_", " ").strip()
    return clean.title() if clean else default


def _looks_like_classification(payload: dict[str, Any]) -> bool:
    """Check if the payload looks like a classification result.

    Requires either a strong signal key (classification/categories/classes)
    or a weak key (label/class) with a co-occurring confidence or categories key.
    """
    # Strong signals — these keys are specific to classification
    if "classification" in payload:
        return True
    if "categories" in payload or "classes" in payload:
        items = payload.get("categories") or payload.get("classes", [])
        if isinstance(items, list) and items:
            return True
    # Weak signals — require co-occurring evidence to avoid false positives
    if any(key in payload for key in ("label", "class")):
        if "confidence" in payload or "categories" in payload or "classes" in payload:
            return True
    return False


# Tool name → colormap mapping for Earth-Agent results
# Aligned with viz_extractor.py INDEX_COLORMAP_MAP for consistency
_TOOL_COLORMAP_MAP = {
    "hotspot": "hot",
    "getis_ord": "hot",
    "thermal": "inferno",
    "lst": "inferno",
    "temperature": "inferno",
    "ndvi": "RdYlGn",
    "vegetation": "RdYlGn",
    "water": "coolwarm",
    "ndwi": "coolwarm",
    "mndwi": "coolwarm",
    "moisture": "YlGnBu",
    "ndmi": "YlGnBu",
    "burn": "inferno",
    "nbr": "RdYlGn_r",
    "fire": "hot",
    "snow": "coolwarm",
    "ndsi": "coolwarm",
    "urban": "YlOrRd",
    "ndbi": "YlOrRd",
    "soil": "YlOrBr",
    "bsi": "YlOrBr",
    "segmentation": "Set2",
    "threshold": "RdYlGn",
    "evi": "RdYlGn",
    "savi": "RdYlGn",
    "elevation": "terrain",
    "dem": "terrain",
}


def _extract_bounds(raster_path: str) -> list[list[float]] | None:
    """Extract [[south, west], [north, east]] bounds from a GeoTIFF for Leaflet."""
    try:
        import rasterio
        with rasterio.open(raster_path) as src:
            b = src.bounds
            return [[b.bottom, b.left], [b.top, b.right]]
    except Exception:
        return None


def _colormap_for_tool(tool_name: str) -> str:
    """Pick an appropriate colormap based on the EA tool name."""
    lower = tool_name.lower()
    for keyword, cmap in _TOOL_COLORMAP_MAP.items():
        if keyword in lower:
            return cmap
    return "viridis"
