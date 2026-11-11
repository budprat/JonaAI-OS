# ABOUTME: Result analysis tools for inspecting, analyzing, and extracting data from EO result files.

"""
Result analysis tools for EO result files.

Provides tools for inspecting, computing histograms, zonal statistics,
and extracting point values from GeoTIFF, NetCDF, and JSON result files.
"""

import base64
import io
import json
import os
from typing import Any, Dict, List, Optional

from .path_security import validate_tool_input_path, PathSecurityError


def create_result_analysis_tools(config=None) -> Dict[str, Any]:
    """
    Create result analysis tools dict for Claude SDK.

    Args:
        config: Optional configuration object.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _result_inspect(args: Dict[str, Any]) -> Dict[str, Any]:
        """Inspect a result file and return metadata and statistics."""
        try:
            file_path = args.get("file_path", "")

            if not file_path or not file_path.strip():
                return {
                    "content": [{"type": "text", "text": "Error: file_path parameter is required and cannot be empty."}]
                }

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            ext = os.path.splitext(file_path)[1].lower()

            if ext in (".tif", ".tiff"):
                import rasterio
                import numpy as np

                with rasterio.open(file_path) as ds:
                    crs = str(ds.crs) if ds.crs else "Unknown"
                    width = ds.width
                    height = ds.height
                    res_x, res_y = ds.res
                    num_bands = ds.count
                    band_names = list(ds.descriptions) if any(ds.descriptions) else [f"Band {i+1}" for i in range(num_bands)]
                    nodata = ds.nodata
                    bounds = ds.bounds
                    transform = ds.transform

                    bbox = {
                        "west": bounds.left,
                        "south": bounds.bottom,
                        "east": bounds.right,
                        "north": bounds.top,
                    }

                    band_stats = []
                    for i in range(1, num_bands + 1):
                        data = ds.read(i).astype(float)
                        if nodata is not None:
                            data = data[data != nodata]
                        valid = data[~np.isnan(data)]
                        if valid.size == 0:
                            band_stats.append({
                                "band": i,
                                "name": band_names[i - 1],
                                "min": None,
                                "max": None,
                                "mean": None,
                                "std": None,
                                "median": None,
                            })
                        else:
                            band_stats.append({
                                "band": i,
                                "name": band_names[i - 1],
                                "min": float(np.min(valid)),
                                "max": float(np.max(valid)),
                                "mean": float(np.mean(valid)),
                                "std": float(np.std(valid)),
                                "median": float(np.median(valid)),
                            })

                    info = {
                        "file": file_path,
                        "format": "GeoTIFF",
                        "crs": crs,
                        "width": width,
                        "height": height,
                        "resolution": {"x": res_x, "y": res_y},
                        "num_bands": num_bands,
                        "band_names": band_names,
                        "nodata": nodata,
                        "bbox": bbox,
                        "band_statistics": band_stats,
                    }

                    lines = [
                        f"File: {file_path}",
                        f"Format: GeoTIFF",
                        f"CRS: {crs}",
                        f"Dimensions: {width} x {height}",
                        f"Resolution: {res_x} x {res_y}",
                        f"Bands: {num_bands}",
                        f"Band names: {', '.join(band_names)}",
                        f"NoData: {nodata}",
                        f"Bounding Box: west={bbox['west']:.6f}, south={bbox['south']:.6f}, east={bbox['east']:.6f}, north={bbox['north']:.6f}",
                        "",
                        "Band Statistics:",
                    ]
                    for bs in band_stats:
                        if bs["min"] is not None:
                            lines.append(
                                f"  {bs['name']}: min={bs['min']:.4f}, max={bs['max']:.4f}, "
                                f"mean={bs['mean']:.4f}, std={bs['std']:.4f}, median={bs['median']:.4f}"
                            )
                        else:
                            lines.append(f"  {bs['name']}: No valid data")

                    text = "\n".join(lines)
                    return {
                        "content": [
                            {"type": "text", "text": text},
                            {"type": "text", "text": json.dumps(info)},
                        ]
                    }

            elif ext in (".nc", ".nc4"):
                import xarray as xr
                import numpy as np

                with xr.open_dataset(file_path) as ds:
                    dims = dict(ds.dims)
                    coords = list(ds.coords.keys())
                    data_vars = list(ds.data_vars.keys())
                    attrs = dict(ds.attrs)

                    crs = attrs.get("crs", attrs.get("spatial_ref", "Unknown"))

                    band_stats = []
                    for var_name in data_vars:
                        var = ds[var_name]
                        values = var.values.astype(float).ravel()
                        valid = values[~np.isnan(values)]
                        if valid.size == 0:
                            band_stats.append({
                                "variable": var_name,
                                "shape": list(var.shape),
                                "min": None, "max": None,
                                "mean": None, "std": None, "median": None,
                            })
                        else:
                            band_stats.append({
                                "variable": var_name,
                                "shape": list(var.shape),
                                "min": float(np.min(valid)),
                                "max": float(np.max(valid)),
                                "mean": float(np.mean(valid)),
                                "std": float(np.std(valid)),
                                "median": float(np.median(valid)),
                            })

                info = {
                    "file": file_path,
                    "format": "NetCDF",
                    "crs": str(crs),
                    "dimensions": dims,
                    "coordinates": coords,
                    "data_variables": data_vars,
                    "attributes": {k: str(v) for k, v in attrs.items()},
                    "variable_statistics": band_stats,
                }

                lines = [
                    f"File: {file_path}",
                    f"Format: NetCDF",
                    f"CRS: {crs}",
                    f"Dimensions: {dims}",
                    f"Coordinates: {', '.join(coords)}",
                    f"Data Variables: {', '.join(data_vars)}",
                    "",
                    "Variable Statistics:",
                ]
                for bs in band_stats:
                    if bs["min"] is not None:
                        lines.append(
                            f"  {bs['variable']} (shape={bs['shape']}): min={bs['min']:.4f}, max={bs['max']:.4f}, "
                            f"mean={bs['mean']:.4f}, std={bs['std']:.4f}, median={bs['median']:.4f}"
                        )
                    else:
                        lines.append(f"  {bs['variable']} (shape={bs['shape']}): No valid data")

                text = "\n".join(lines)
                return {
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "text", "text": json.dumps(info)},
                    ]
                }

            elif ext == ".json":
                with open(file_path, "r") as f:
                    data = json.load(f)

                def summarize_structure(obj, depth=0, max_depth=3):
                    if depth >= max_depth:
                        return f"<{type(obj).__name__}>"
                    if isinstance(obj, dict):
                        return {k: summarize_structure(v, depth + 1, max_depth) for k, v in list(obj.items())[:20]}
                    elif isinstance(obj, list):
                        if len(obj) == 0:
                            return "[]"
                        return [summarize_structure(obj[0], depth + 1, max_depth), f"... ({len(obj)} items)"]
                    else:
                        return f"<{type(obj).__name__}: {str(obj)[:50]}>"

                structure = summarize_structure(data)

                info = {
                    "file": file_path,
                    "format": "JSON",
                    "top_level_type": type(data).__name__,
                    "structure": structure,
                }

                if isinstance(data, dict):
                    keys_text = ", ".join(list(data.keys())[:30])
                    text = f"File: {file_path}\nFormat: JSON\nTop-level type: dict\nKeys ({len(data)}): {keys_text}"
                elif isinstance(data, list):
                    text = f"File: {file_path}\nFormat: JSON\nTop-level type: list\nLength: {len(data)}"
                else:
                    text = f"File: {file_path}\nFormat: JSON\nTop-level type: {type(data).__name__}"

                return {
                    "content": [
                        {"type": "text", "text": text},
                        {"type": "text", "text": json.dumps(info, default=str)},
                    ]
                }

            else:
                return {
                    "content": [{"type": "text", "text": f"Error: Unsupported file format: {ext}. Supported: .tif, .tiff, .nc, .nc4, .json"}]
                }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error inspecting file: {str(e)}"}]
            }

    async def _result_histogram(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compute and return a histogram of pixel values for a raster band."""
        try:
            import rasterio
            import numpy as np

            file_path = args["file_path"]
            band = args.get("band", 1)
            bins = args.get("bins", 50)

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            with rasterio.open(file_path) as ds:
                if band < 1 or band > ds.count:
                    return {
                        "content": [{"type": "text", "text": f"Error: Band {band} out of range. File has {ds.count} bands."}]
                    }

                data = ds.read(band).astype(float)
                nodata = ds.nodata

                if nodata is not None:
                    data = data[data != nodata]

                valid = data[~np.isnan(data)].ravel()

                if valid.size == 0:
                    return {
                        "content": [{"type": "text", "text": f"No valid pixel data in band {band}."}]
                    }

                counts, bin_edges = np.histogram(valid, bins=bins)
                bin_labels = [f"{bin_edges[i]:.2f}-{bin_edges[i+1]:.2f}" for i in range(len(counts))]
                counts_list = [int(c) for c in counts]

            return {
                "content": [{
                    "type": "visualization",
                    "component": {
                        "type": "chart",
                        "chartType": "bar",
                        "title": f"Pixel Value Distribution - Band {band}",
                        "xAxis": {"label": "Value Range", "values": bin_labels},
                        "yAxis": {"label": "Pixel Count"},
                        "series": [{"name": f"Band {band}", "values": counts_list}]
                    }
                }]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error computing histogram: {str(e)}"}]
            }

    async def _result_zonal_stats(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compute zonal statistics for a raster clipped to a geometry."""
        try:
            import rasterio
            import rasterio.mask
            import numpy as np
            from shapely.geometry import shape, box

            file_path = args["file_path"]
            geometry = args["geometry"]
            requested_stats = args.get("stats", ["min", "max", "mean", "std", "median", "count"])

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            if all(k in geometry for k in ("west", "south", "east", "north")):
                geom = box(geometry["west"], geometry["south"], geometry["east"], geometry["north"])
                geojson_geom = json.loads(json.dumps(geom.__geo_interface__))
            elif "type" in geometry:
                geojson_geom = geometry
                geom = shape(geometry)
            else:
                return {
                    "content": [{"type": "text", "text": "Error: geometry must be a GeoJSON geometry or a bbox dict with west, south, east, north."}]
                }

            with rasterio.open(file_path) as ds:
                out_image, out_transform = rasterio.mask.mask(ds, [geojson_geom], crop=True, nodata=ds.nodata)
                nodata = ds.nodata
                num_bands = ds.count
                band_names = list(ds.descriptions) if any(ds.descriptions) else [f"Band {i+1}" for i in range(num_bands)]

            results = []
            for i in range(num_bands):
                band_data = out_image[i].astype(float)
                if nodata is not None:
                    band_data = band_data[band_data != nodata]
                valid = band_data[~np.isnan(band_data)].ravel()

                stats = {"band": i + 1, "name": band_names[i]}
                if valid.size == 0:
                    for s in requested_stats:
                        stats[s] = None
                else:
                    stat_funcs = {
                        "min": lambda v: float(np.min(v)),
                        "max": lambda v: float(np.max(v)),
                        "mean": lambda v: float(np.mean(v)),
                        "std": lambda v: float(np.std(v)),
                        "median": lambda v: float(np.median(v)),
                        "count": lambda v: int(v.size),
                        "sum": lambda v: float(np.sum(v)),
                    }
                    for s in requested_stats:
                        if s in stat_funcs:
                            stats[s] = stat_funcs[s](valid)
                        else:
                            stats[s] = None
                results.append(stats)

            lines = [f"Zonal Statistics for: {file_path}", ""]
            for r in results:
                parts = [f"  {r['name']}:"]
                for s in requested_stats:
                    val = r.get(s)
                    if val is not None:
                        if isinstance(val, float):
                            parts.append(f"{s}={val:.4f}")
                        else:
                            parts.append(f"{s}={val}")
                    else:
                        parts.append(f"{s}=N/A")
                lines.append(" ".join(parts))

            text = "\n".join(lines)
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps({"zonal_stats": results})},
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error computing zonal statistics: {str(e)}"}]
            }

    async def _result_extract_point(args: Dict[str, Any]) -> Dict[str, Any]:
        """Extract pixel values at a given lat/lon from all bands."""
        try:
            import rasterio
            import numpy as np

            file_path = args["file_path"]
            latitude = args["latitude"]
            longitude = args["longitude"]

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            with rasterio.open(file_path) as ds:
                num_bands = ds.count
                band_names = list(ds.descriptions) if any(ds.descriptions) else [f"Band {i+1}" for i in range(num_bands)]

                coords = [(longitude, latitude)]
                values = list(ds.sample(coords))

                if len(values) == 0:
                    return {
                        "content": [{"type": "text", "text": f"Error: Could not sample point at ({latitude}, {longitude})."}]
                    }

                pixel_values = values[0]

                results = []
                for i in range(num_bands):
                    val = float(pixel_values[i]) if not np.isnan(pixel_values[i]) else None
                    results.append({
                        "band": i + 1,
                        "name": band_names[i],
                        "value": val,
                    })

            lines = [
                f"Point extraction at lat={latitude}, lon={longitude}",
                f"File: {file_path}",
                "",
            ]
            for r in results:
                val_str = f"{r['value']:.4f}" if r['value'] is not None else "NoData"
                lines.append(f"  {r['name']}: {val_str}")

            text = "\n".join(lines)
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps({"point": {"latitude": latitude, "longitude": longitude}, "values": results})},
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error extracting point values: {str(e)}"}]
            }

    async def _raster_statistics(args: Dict[str, Any]) -> Dict[str, Any]:
        """Comprehensive raster statistics with percentiles, area, and coverage."""
        try:
            import rasterio
            import numpy as np
            from pyproj import CRS as ProjCRS, Transformer

            file_path = args.get("file_path", "")
            band = args.get("band", 1)

            if not file_path or not file_path.strip():
                return {"content": [{"type": "text", "text": "Error: file_path parameter is required and cannot be empty."}]}

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

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
                nan_count = total_pixels - valid.size

                # Calculate pixel area in sq meters
                res_x, res_y = abs(ds.res[0]), abs(ds.res[1])
                crs = ds.crs
                pixel_area_sq_m = res_x * res_y  # approximate
                if crs and not crs.is_projected:
                    # For geographic CRS, approximate pixel area at center latitude
                    center_lat = (ds.bounds.top + ds.bounds.bottom) / 2
                    m_per_deg_lat = 111320.0
                    m_per_deg_lon = 111320.0 * np.cos(np.radians(center_lat))
                    pixel_area_sq_m = (res_x * m_per_deg_lon) * (res_y * m_per_deg_lat)

                pixel_area_ha = pixel_area_sq_m / 10000.0
                total_area_ha = total_pixels * pixel_area_ha
                valid_area_ha = valid.size * pixel_area_ha

                if valid.size == 0:
                    stats = {
                        "band": band,
                        "total_pixels": total_pixels,
                        "valid_pixels": 0,
                        "nan_pixels": nan_count,
                        "coverage_pct": 0.0,
                        "total_area_ha": round(total_area_ha, 2),
                        "valid_area_ha": 0.0,
                        "statistics": None,
                    }
                else:
                    stats = {
                        "band": band,
                        "total_pixels": total_pixels,
                        "valid_pixels": int(valid.size),
                        "nan_pixels": int(nan_count),
                        "coverage_pct": round(100.0 * valid.size / total_pixels, 1),
                        "total_area_ha": round(total_area_ha, 2),
                        "valid_area_ha": round(valid_area_ha, 2),
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
                        },
                    }

            text_lines = [
                f"Raster Statistics: {file_path} (Band {band})",
                f"Coverage: {stats['valid_pixels']:,} valid / {stats['total_pixels']:,} total pixels ({stats['coverage_pct']}%)",
                f"Area: {stats['valid_area_ha']:,.1f} ha valid / {stats['total_area_ha']:,.1f} ha total",
            ]
            if stats["statistics"]:
                s = stats["statistics"]
                text_lines.extend([
                    f"Min: {s['min']:.4f}  |  Max: {s['max']:.4f}",
                    f"Mean: {s['mean']:.4f}  |  Median: {s['median']:.4f}  |  Std: {s['std']:.4f}",
                    f"Percentiles: P5={s['p5']:.4f}  P25={s['p25']:.4f}  P75={s['p75']:.4f}  P95={s['p95']:.4f}",
                ])
            else:
                text_lines.append("No valid pixel data.")

            # --- Auto-interpretation: detect index from file path and append domain context ---
            try:
                from .interpretation import (
                    _detect_index_from_path, interpret_index_value,
                    assess_quality, format_interpretation_text, format_quality_text,
                )
                detected_index = _detect_index_from_path(file_path)
                if detected_index and stats["statistics"]:
                    flat_stats = {**stats, **stats["statistics"]}
                    interp = interpret_index_value(detected_index, flat_stats)
                    quality = assess_quality(stats)
                    text_lines.append("")
                    text_lines.append("--- Interpretation ---")
                    text_lines.append(format_interpretation_text(interp, stats=stats, quality=quality))
                    stats["interpretation"] = interp
                    stats["quality"] = quality
            except Exception:
                pass  # Graceful fallback: interpretation is optional

            return {
                "content": [
                    {"type": "text", "text": "\n".join(text_lines)},
                    {"type": "text", "text": json.dumps(stats, default=str)},
                ]
            }

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error computing raster statistics: {str(e)}"}]}

    async def _threshold_classify(args: Dict[str, Any]) -> Dict[str, Any]:
        """Classify raster values into categories based on thresholds and report area per class."""
        try:
            import rasterio
            import numpy as np

            file_path = args["file_path"]
            thresholds = args.get("thresholds")  # list of {"min": x, "max": y, "label": "name"}
            band = args.get("band", 1)
            domain_arg = args.get("domain")
            index_name_arg = args.get("index_name")

            # Auto-load thresholds from domain_knowledge if domain/index provided and no manual thresholds
            if not thresholds and (domain_arg or index_name_arg):
                try:
                    from .interpretation import load_domain_thresholds, _detect_index_from_path
                    idx = index_name_arg or _detect_index_from_path(file_path)
                    dom = domain_arg
                    if idx and not dom:
                        from .interpretation import _infer_domain
                        dom = _infer_domain(idx)
                    if dom and idx:
                        thresholds = load_domain_thresholds(dom, idx)
                except Exception:
                    pass

            if not thresholds:
                return {"content": [{"type": "text", "text": (
                    "Error: No thresholds provided and could not auto-load from domain. "
                    "Provide 'thresholds' array or both 'domain' and 'index_name' parameters."
                )}]}

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            with rasterio.open(file_path) as ds:
                data = ds.read(band).astype(float)
                nodata = ds.nodata
                if nodata is not None:
                    data[data == nodata] = np.nan

                valid_mask = ~np.isnan(data)
                valid = data[valid_mask]

                # Calculate pixel area
                res_x, res_y = abs(ds.res[0]), abs(ds.res[1])
                crs = ds.crs
                pixel_area_sq_m = res_x * res_y
                if crs and not crs.is_projected:
                    center_lat = (ds.bounds.top + ds.bounds.bottom) / 2
                    m_per_deg_lat = 111320.0
                    m_per_deg_lon = 111320.0 * np.cos(np.radians(center_lat))
                    pixel_area_sq_m = (res_x * m_per_deg_lon) * (res_y * m_per_deg_lat)

                pixel_area_ha = pixel_area_sq_m / 10000.0

                classes = []
                for t in thresholds:
                    t_min = t.get("min", float("-inf"))
                    t_max = t.get("max", float("inf"))
                    label = t.get("label", f"{t_min} to {t_max}")

                    mask = (valid >= t_min) & (valid < t_max)
                    pixel_count = int(np.sum(mask))
                    area_ha = round(pixel_count * pixel_area_ha, 2)
                    pct = round(100.0 * pixel_count / valid.size, 1) if valid.size > 0 else 0.0

                    classes.append({
                        "label": label,
                        "range": [t_min if t_min != float("-inf") else None,
                                  t_max if t_max != float("inf") else None],
                        "pixel_count": pixel_count,
                        "area_ha": area_ha,
                        "percentage": pct,
                    })

            text_lines = [f"Threshold Classification: {file_path} (Band {band})", ""]
            for c in classes:
                text_lines.append(
                    f"  {c['label']}: {c['pixel_count']:,} pixels | {c['area_ha']:,.1f} ha | {c['percentage']}%"
                )

            return {
                "content": [
                    {"type": "text", "text": "\n".join(text_lines)},
                    {"type": "text", "text": json.dumps({"classification": classes})},
                ]
            }

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error classifying raster: {str(e)}"}]}

    async def _analyze_result_image(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate PNG thumbnail from GeoTIFF and return as image for Claude to analyze."""
        try:
            import rasterio
            import numpy as np
            from PIL import Image

            file_path = args["file_path"]
            band = args.get("band", 1)
            question = args.get("question", "Describe what you see in this Earth observation image.")
            colormap_name = args.get("colormap", "viridis")

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in (".tif", ".tiff"):
                return {
                    "content": [{"type": "text", "text": f"Error: Only GeoTIFF supported, got: {ext}"}]
                }

            with rasterio.open(file_path) as ds:
                if band < 1 or band > ds.count:
                    return {
                        "content": [{"type": "text", "text": f"Error: Band {band} out of range (1-{ds.count})"}]
                    }

                data = ds.read(band).astype(float)
                nodata = ds.nodata
                width = ds.width
                height = ds.height
                crs = str(ds.crs) if ds.crs else "Unknown"
                bounds = ds.bounds

            # Build nodata/NaN mask
            mask = np.isnan(data)
            if nodata is not None:
                mask |= np.isclose(data, float(nodata))

            # Compute value range from valid pixels
            valid = data[~mask]
            if valid.size == 0:
                return {
                    "content": [{"type": "text", "text": "No valid pixel data in this band."}]
                }

            vmin = float(np.nanmin(valid))
            vmax = float(np.nanmax(valid))

            # Apply colormap (reuse pattern from visualization/maps.py)
            from openeo_ai.visualization.maps import COLORMAPS
            colors = COLORMAPS.get(colormap_name, COLORMAPS["viridis"])
            n_colors = len(colors)

            normalized = np.clip((data - vmin) / (vmax - vmin + 1e-10), 0, 1)
            normalized[mask] = 0

            indices = normalized * (n_colors - 1)
            lower_idx = np.clip(np.floor(indices).astype(int), 0, n_colors - 1)
            upper_idx = np.clip(np.ceil(indices).astype(int), 0, n_colors - 1)
            frac = indices - lower_idx

            rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
            for c in range(3):
                lower_colors = np.array([colors[i][c] for i in range(n_colors)])
                upper_colors = np.array([colors[i][c] for i in range(n_colors)])
                lower_vals = lower_colors[lower_idx]
                upper_vals = upper_colors[upper_idx]
                rgba[:, :, c] = (lower_vals * (1 - frac) + upper_vals * frac).astype(np.uint8)
            rgba[:, :, 3] = 255
            rgba[mask, 3] = 0  # Transparent for nodata

            # Reason: Claude Agent SDK has a 1MB JSON buffer limit.
            # Noisy EO data compresses poorly as PNG, so use JPEG (smaller)
            # and cap at 384px to stay safely under the limit.
            # A 384px JPEG at quality=85 is typically 50-200KB base64.
            img = Image.fromarray(rgba, mode="RGBA")
            max_dim = 384
            if width > max_dim or height > max_dim:
                scale = max_dim / max(width, height)
                new_w = int(width * scale)
                new_h = int(height * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)
            else:
                new_w, new_h = width, height

            # Convert RGBA → RGB (JPEG doesn't support alpha)
            # Composite nodata transparency onto white background
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])

            # Encode as base64 JPEG — much smaller than PNG for noisy EO data
            buffer = io.BytesIO()
            bg.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            img_bytes = buffer.read()

            # Safety check: if still too large, reduce quality further
            if len(img_bytes) > 500_000:
                buffer = io.BytesIO()
                smaller = bg.resize((new_w // 2, new_h // 2), Image.LANCZOS)
                smaller.save(buffer, format="JPEG", quality=70)
                buffer.seek(0)
                img_bytes = buffer.read()
                new_w, new_h = new_w // 2, new_h // 2

            base64_img = base64.b64encode(img_bytes).decode("utf-8")

            # Return as image content block for Claude vision analysis
            return {
                "content": [
                    {
                        "type": "image",
                        "data": base64_img,
                        "mimeType": "image/jpeg",
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Image generated from {file_path} (band {band}, "
                            f"{new_w}x{new_h}px, colormap={colormap_name}, "
                            f"CRS={crs}, "
                            f"bounds=[{bounds.left:.4f}, {bounds.bottom:.4f}, "
                            f"{bounds.right:.4f}, {bounds.top:.4f}], "
                            f"value range: {vmin:.4f} to {vmax:.4f}). "
                            f"Question: {question}"
                        ),
                    },
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error generating image: {str(e)}"}]
            }

    async def _result_extract_temporal_layers(args: Dict[str, Any]) -> Dict[str, Any]:
        """Extract per-timestep layers from a multi-band temporal GeoTIFF for timelapse visualization."""
        try:
            import rasterio
            import numpy as np

            file_path = args["file_path"]
            labels = args.get("labels", [])
            colormap_name = args.get("colormap", "viridis")

            try:
                file_path = validate_tool_input_path(file_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            with rasterio.open(file_path) as ds:
                num_bands = ds.count
                nodata = ds.nodata
                bounds = list(ds.bounds)  # [west, south, east, north]
                band_names = list(ds.descriptions) if any(ds.descriptions) else [f"Band {i+1}" for i in range(num_bands)]

            if num_bands < 2:
                return {"content": [{"type": "text", "text": f"Error: File has only {num_bands} band. Timelapse requires multi-band temporal data."}]}

            # Generate timestamps from labels or band names
            timestamps = []
            if labels and len(labels) >= num_bands:
                timestamps = labels[:num_bands]
            else:
                # Try to extract dates from band names, fallback to indices
                for i, name in enumerate(band_names):
                    timestamps.append(name if name and name != f"Band {i+1}" else f"T{i+1}")

            # Render each band to a PNG tile via the render-raster endpoint
            layers = []
            with rasterio.open(file_path) as ds:
                for band_idx in range(num_bands):
                    data = ds.read(band_idx + 1).astype(float)
                    if nodata is not None:
                        data[data == nodata] = np.nan
                    valid = data[~np.isnan(data)]

                    stats = {}
                    if valid.size > 0:
                        stats = {
                            "min": round(float(np.min(valid)), 4),
                            "max": round(float(np.max(valid)), 4),
                            "mean": round(float(np.mean(valid)), 4),
                        }

                    # Use render-raster endpoint with band parameter
                    tile_url = f"/render-raster?path={file_path}&colormap={colormap_name}&band={band_idx + 1}"

                    layers.append({
                        "timestamp": timestamps[band_idx],
                        "url": tile_url,
                        "bounds": [[bounds[1], bounds[0]], [bounds[3], bounds[2]]],  # [[south, west], [north, east]]
                        "colormap": colormap_name,
                        "bandIndex": band_idx,
                        "stats": stats,
                    })

            summary_lines = [
                f"Temporal layers extracted from {file_path}:",
                f"  Bands: {num_bands}",
                f"  Timestamps: {', '.join(timestamps)}",
                f"  Colormap: {colormap_name}",
            ]

            viz_component = {
                "type": "timelapse",
                "layers": layers,
                "colormap": colormap_name,
                "title": f"Timelapse: {os.path.basename(file_path)}",
            }

            return {
                "content": [
                    {"type": "text", "text": "\n".join(summary_lines)},
                    {"type": "visualization", "component": viz_component},
                ]
            }

        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error extracting temporal layers: {str(e)}"}]}

    return {
        "result_inspect": _result_inspect,
        "result_histogram": _result_histogram,
        "result_zonal_stats": _result_zonal_stats,
        "result_extract_point": _result_extract_point,
        "raster_statistics": _raster_statistics,
        "threshold_classify": _threshold_classify,
        "analyze_result_image": _analyze_result_image,
        "result_extract_temporal_layers": _result_extract_temporal_layers,
    }


RESULT_ANALYSIS_TOOL_DEFINITIONS = [
    {
        "name": "analyze_result_image",
        "description": "Generate a visual thumbnail from a GeoTIFF result and analyze it. Use this to visually inspect job outputs — detect spatial patterns, anomalies, cloud contamination, verify spatial coverage, or assess data quality. The image is shown to you (Claude) for visual analysis. Returns an image content block plus metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF file"
                },
                "band": {
                    "type": "integer",
                    "description": "Band to visualize (1-indexed, default: 1)",
                    "default": 1
                },
                "question": {
                    "type": "string",
                    "description": "What to analyze in the image (e.g., 'Is this NDVI map correct?', 'Do you see cloud contamination?')",
                    "default": "Describe what you see in this Earth observation image."
                },
                "colormap": {
                    "type": "string",
                    "description": "Colormap to apply (viridis, ndvi, plasma, inferno, rdylgn, etc.)",
                    "default": "viridis"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "result_inspect",
        "description": "Inspect a result file (GeoTIFF, NetCDF, JSON) and return metadata including CRS, dimensions, resolution, bands, nodata value, bounding box, and per-band statistics (min, max, mean, std, median).",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the result file (.tif, .tiff, .nc, .nc4, or .json)"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "result_histogram",
        "description": "Compute a histogram of pixel values for a specific band in a GeoTIFF file. Returns a bar chart visualization of the pixel value distribution.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF file"
                },
                "band": {
                    "type": "integer",
                    "description": "Band number to compute histogram for (1-indexed, default: 1)",
                    "default": 1
                },
                "bins": {
                    "type": "integer",
                    "description": "Number of histogram bins (default: 50)",
                    "default": 50
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "result_zonal_stats",
        "description": "Compute zonal statistics for a raster file clipped to a given geometry. Supports GeoJSON geometries and bounding box dicts (west, south, east, north). Returns statistics like min, max, mean, std, median, count per band.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF file"
                },
                "geometry": {
                    "type": "object",
                    "description": "GeoJSON geometry object or bounding box dict with west, south, east, north keys"
                },
                "stats": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of statistics to compute (default: ['min', 'max', 'mean', 'std', 'median', 'count'])",
                    "default": ["min", "max", "mean", "std", "median", "count"]
                }
            },
            "required": ["file_path", "geometry"]
        }
    },
    {
        "name": "result_extract_point",
        "description": "Extract pixel values at a specific latitude/longitude from all bands of a GeoTIFF file. Returns the value for each band at that location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF file"
                },
                "latitude": {
                    "type": "number",
                    "description": "Latitude of the point (WGS84)"
                },
                "longitude": {
                    "type": "number",
                    "description": "Longitude of the point (WGS84)"
                }
            },
            "required": ["file_path", "latitude", "longitude"]
        }
    },
    {
        "name": "raster_statistics",
        "description": "Compute comprehensive statistics for a GeoTIFF raster band including min, max, mean, median, std, percentiles (P5, P25, P75, P95), pixel counts, coverage percentage, and area in hectares. Use this for detailed quantitative analysis of EO results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF file"
                },
                "band": {
                    "type": "integer",
                    "description": "Band number to analyze (1-indexed, default: 1)",
                    "default": 1
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "result_extract_temporal_layers",
        "description": "Extract per-timestep layers from a multi-band temporal GeoTIFF (e.g., monthly composites) for timelapse visualization. Each band is rendered as a separate map layer with timestamp. Returns a timelapse visualization that the frontend renders with a temporal slider and play/pause controls.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the multi-band GeoTIFF file (e.g., from aggregate_temporal_period)"
                },
                "labels": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional timestamp labels for each band (e.g., ['2024-01', '2024-02', ...]). If omitted, uses band names or T1, T2, etc."
                },
                "colormap": {
                    "type": "string",
                    "description": "Colormap to use for rendering (default: viridis)",
                    "default": "viridis"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "threshold_classify",
        "description": "Classify raster pixel values into categories based on threshold ranges. Returns pixel count, area in hectares, and percentage for each class. Useful for land cover classification, NDVI vegetation health categories, burn severity classes, etc. Can auto-load thresholds from domain knowledge by passing domain and index_name instead of manual thresholds.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the GeoTIFF file"
                },
                "thresholds": {
                    "type": "array",
                    "description": "Array of threshold objects defining classes. Each object has 'min' (number), 'max' (number), and 'label' (string). Omit to auto-load from domain/index_name.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "min": {
                                "type": "number",
                                "description": "Minimum value (inclusive)"
                            },
                            "max": {
                                "type": "number",
                                "description": "Maximum value (exclusive)"
                            },
                            "label": {
                                "type": "string",
                                "description": "Human-readable class label"
                            }
                        },
                        "required": ["min", "max", "label"]
                    }
                },
                "domain": {
                    "type": "string",
                    "description": "Auto-load thresholds for this domain (vegetation, water, fire, urban, etc.)"
                },
                "index_name": {
                    "type": "string",
                    "description": "Index name for domain threshold lookup (NDVI, NDWI, dNBR, etc.)"
                },
                "band": {
                    "type": "integer",
                    "description": "Band number to classify (1-indexed, default: 1)",
                    "default": 1
                }
            },
            "required": ["file_path"]
        }
    },
]
