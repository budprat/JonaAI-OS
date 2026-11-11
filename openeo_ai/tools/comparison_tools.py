# ABOUTME: Comparison tools for comparing EO data collections and raster results.
# Supports collection metadata comparison, pixel-difference maps, and correlation analysis.

"""
Comparison tools for Claude SDK.

Provides tools for comparing Earth Observation data collections,
computing raster differences, and calculating spatial correlations.
"""

import json
import os
import time
from typing import Any, Dict
from urllib.parse import quote

from .path_security import current_user_output_dir
from .path_security import validate_tool_input_path, validate_tool_output_path, PathSecurityError


COLLECTION_INFO = {
    "sentinel-2-l2a": {
        "name": "Sentinel-2 L2A", "provider": "ESA", "resolution": "10-60m",
        "revisit": "5 days", "bands": 13, "start_date": "2015-06-23",
        "spectral_range": "VNIR + SWIR", "processing_level": "L2A (surface reflectance)",
        "use_cases": ["vegetation mapping", "land cover classification", "water quality"]
    },
    "sentinel-2-l1c": {
        "name": "Sentinel-2 L1C", "provider": "ESA", "resolution": "10-60m",
        "revisit": "5 days", "bands": 13, "start_date": "2015-06-23",
        "spectral_range": "VNIR + SWIR", "processing_level": "L1C (TOA reflectance)",
        "use_cases": ["atmospheric studies", "custom atmospheric correction"]
    },
    "landsat-c2-l2": {
        "name": "Landsat C2 L2", "provider": "USGS", "resolution": "30m",
        "revisit": "16 days", "bands": 11, "start_date": "1982-08-22",
        "spectral_range": "VNIR + SWIR + TIR", "processing_level": "L2 (surface reflectance)",
        "use_cases": ["long-term change", "thermal analysis", "historical baselines"]
    },
    "cop-dem-glo-30": {
        "name": "Copernicus DEM 30m", "provider": "ESA", "resolution": "30m",
        "revisit": "static", "bands": 1, "start_date": "2021",
        "spectral_range": "elevation", "processing_level": "DEM",
        "use_cases": ["terrain analysis", "slope", "flood modeling"]
    },
    "cop-dem-glo-90": {
        "name": "Copernicus DEM 90m", "provider": "ESA", "resolution": "90m",
        "revisit": "static", "bands": 1, "start_date": "2021",
        "spectral_range": "elevation", "processing_level": "DEM",
        "use_cases": ["regional terrain", "large-scale modeling"]
    }
}


COMPARISON_TOOL_DEFINITIONS = [
    {
        "name": "compare_collections",
        "description": "Compare Earth Observation data collections side-by-side. Shows resolution, revisit time, bands, date range, spectral range, and recommended use cases.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of collection IDs to compare (e.g., ['sentinel-2-l2a', 'landsat-c2-l2'])"
                }
            },
            "required": ["collection_ids"]
        }
    },
    {
        "name": "result_difference",
        "description": "Compute pixel-by-pixel difference between two GeoTIFF rasters (A - B) for band 1. Saves difference raster and returns summary statistics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path_a": {
                    "type": "string",
                    "description": "Path to the first GeoTIFF file (A)"
                },
                "file_path_b": {
                    "type": "string",
                    "description": "Path to the second GeoTIFF file (B)"
                },
                "output_path": {
                    "type": "string",
                    "description": "Output path for the difference raster. Defaults to diff_{timestamp}.tif in the user's output folder"
                }
            },
            "required": ["file_path_a", "file_path_b"]
        }
    },
    {
        "name": "result_correlation",
        "description": "Compute Pearson correlation coefficient between two GeoTIFF rasters or two bands within a single multi-band file. Returns correlation, p-value estimate, and a scatter plot visualization. For multi-index jobs that output one multi-band GeoTIFF, omit file_path_b and use band_a/band_b to select bands.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path_a": {
                    "type": "string",
                    "description": "Path to the first GeoTIFF file (or the only file when correlating bands within it)"
                },
                "file_path_b": {
                    "type": "string",
                    "description": "Path to the second GeoTIFF file. Omit when correlating two bands within file_path_a."
                },
                "band_a": {
                    "type": "integer",
                    "description": "Band index (0-based) for the first variable. Default: 0. Used when correlating bands within a single file.",
                    "default": 0
                },
                "band_b": {
                    "type": "integer",
                    "description": "Band index (0-based) for the second variable. Default: 1. Used when correlating bands within a single file.",
                    "default": 1
                }
            },
            "required": ["file_path_a"]
        }
    }
]


def _ensure_output_dir(path: str) -> None:
    """Ensure the output directory exists."""
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _default_output_path(prefix: str, ext: str) -> str:
    """Generate a default output path with timestamp."""
    timestamp = int(time.time())
    return f"{current_user_output_dir()}/{prefix}_{timestamp}.{ext}"


def create_comparison_tools(config=None) -> Dict[str, Any]:
    """
    Create comparison tools dict for Claude SDK.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _compare_collections(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compare EO data collections side-by-side."""
        try:
            collection_ids = args["collection_ids"]

            if not collection_ids or len(collection_ids) < 2:
                return {
                    "content": [{"type": "text", "text": "Error: Please provide at least 2 collection IDs to compare."}]
                }

            found = {}
            not_found = []
            for cid in collection_ids:
                if cid in COLLECTION_INFO:
                    found[cid] = COLLECTION_INFO[cid]
                else:
                    not_found.append(cid)

            if not found:
                available = ", ".join(COLLECTION_INFO.keys())
                return {
                    "content": [{"type": "text", "text": f"Error: None of the requested collections were found. Available: {available}"}]
                }

            fields = ["name", "provider", "resolution", "revisit", "bands",
                       "start_date", "spectral_range", "processing_level", "use_cases"]

            header = f"{'Property':<22}" + "".join(f"{info['name']:<28}" for info in found.values())
            separator = "-" * len(header)
            lines = [header, separator]

            for field in fields:
                label = field.replace("_", " ").title()
                row = f"{label:<22}"
                for info in found.values():
                    val = info.get(field, "N/A")
                    if isinstance(val, list):
                        val = ", ".join(val)
                    row += f"{str(val):<28}"
                lines.append(row)

            result = "\n".join(lines)

            if not_found:
                result += f"\n\nNot found: {', '.join(not_found)}. Available: {', '.join(COLLECTION_INFO.keys())}"

            return {
                "content": [{"type": "text", "text": result}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error comparing collections: {str(e)}"}]
            }

    async def _result_difference(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compute pixel-by-pixel difference between two GeoTIFFs."""
        try:
            import numpy as np
            import rasterio

            file_path_a = args["file_path_a"]
            file_path_b = args["file_path_b"]
            output_path = args.get("output_path") or _default_output_path("diff", "tif")

            try:
                file_path_a = validate_tool_input_path(file_path_a)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            try:
                file_path_b = validate_tool_input_path(file_path_b)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            if args.get("output_path"):
                try:
                    output_path = validate_tool_output_path(output_path)
                except PathSecurityError as e:
                    return {"content": [{"type": "text", "text": f"Error: {e}"}]}

            _ensure_output_dir(output_path)

            with rasterio.open(file_path_a) as src_a:
                data_a = src_a.read(1).astype(float)
                profile = src_a.profile.copy()
                nodata_a = src_a.nodata
                raster_bounds = [src_a.bounds.left, src_a.bounds.bottom, src_a.bounds.right, src_a.bounds.top]

            with rasterio.open(file_path_b) as src_b:
                data_b = src_b.read(1).astype(float)
                nodata_b = src_b.nodata

            if data_a.shape != data_b.shape:
                return {
                    "content": [{"type": "text", "text": f"Error: Raster dimensions do not match. A: {data_a.shape}, B: {data_b.shape}"}]
                }

            mask = np.ones(data_a.shape, dtype=bool)
            if nodata_a is not None:
                mask &= data_a != nodata_a
            if nodata_b is not None:
                mask &= data_b != nodata_b

            diff = np.full(data_a.shape, np.nan)
            diff[mask] = data_a[mask] - data_b[mask]

            profile.update(dtype='float64', count=1, nodata=np.nan)
            with rasterio.open(output_path, 'w', **profile) as dst:
                dst.write(diff, 1)

            valid = diff[mask]
            if valid.size == 0:
                return {
                    "content": [{"type": "text", "text": "Error: No valid (non-nodata) pixels to compare."}]
                }

            diff_mean = float(np.nanmean(valid))
            diff_median = float(np.nanmedian(valid))
            diff_std = float(np.nanstd(valid))
            diff_min = float(np.nanmin(valid))
            diff_max = float(np.nanmax(valid))

            summary = (
                f"Difference raster saved to: {output_path}\n\n"
                f"Statistics (A - B):\n"
                f"  Mean difference:   {diff_mean:.6f}\n"
                f"  Median difference: {diff_median:.6f}\n"
                f"  Std deviation:     {diff_std:.6f}\n"
                f"  Min difference:    {diff_min:.6f}\n"
                f"  Max difference:    {diff_max:.6f}\n"
                f"  Valid pixels:      {valid.size}"
            )

            # --- Auto-interpretation: detect index and append change context ---
            try:
                from .interpretation import (
                    _detect_index_from_path, interpret_change, format_change_text,
                )
                detected_index = (
                    _detect_index_from_path(file_path_a)
                    or _detect_index_from_path(file_path_b)
                )
                if detected_index:
                    diff_stats = {
                        "mean": diff_mean, "median": diff_median,
                        "std": diff_std, "min": diff_min, "max": diff_max,
                    }
                    change_interp = interpret_change(diff_stats, detected_index)
                    summary += "\n\n--- Change Interpretation ---\n"
                    summary += format_change_text(change_interp)
            except Exception:
                pass  # Graceful fallback

            colormap = "RdBu_r"
            render_url = f"/render-raster?path={quote(output_path, safe='/')}&colormap={quote(colormap, safe='')}"
            viz_component = {
                "type": "map",
                "url": render_url,
                "bounds": raster_bounds,
                "title": "Pixel Difference (A - B)",
                "colormap": colormap,
                "source": output_path,
            }

            return {
                "content": [
                    {"type": "text", "text": summary},
                    {"type": "visualization", "component": viz_component}
                ]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error computing difference: {str(e)}"}]
            }

    async def _result_correlation(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compute Pearson correlation between two GeoTIFF rasters or two bands within one file."""
        try:
            import numpy as np
            import rasterio

            file_path_a = args["file_path_a"]
            file_path_b = args.get("file_path_b")
            band_a = args.get("band_a", 0)
            band_b = args.get("band_b", 1)

            if file_path_b:
                # Two-file mode: read band 1 from each (backward compatible)
                with rasterio.open(file_path_a) as src_a:
                    data_a = src_a.read(1).astype(float)
                    nodata_a = src_a.nodata
                with rasterio.open(file_path_b) as src_b:
                    data_b = src_b.read(1).astype(float)
                    nodata_b = src_b.nodata
                label_a = "Raster A values"
                label_b = "Raster B values"
            else:
                # Single-file mode: extract two bands from the same file
                with rasterio.open(file_path_a) as src:
                    num_bands = src.count
                    nodata_val = src.nodata
                    band_names = list(src.descriptions) if any(src.descriptions) else [f"Band {i+1}" for i in range(num_bands)]

                    if num_bands < 2:
                        return {
                            "content": [{"type": "text", "text": f"Error: File has only {num_bands} band(s). Need at least 2 for intra-file correlation. Provide file_path_b for two-file mode."}]
                        }
                    if band_a < 0 or band_a >= num_bands:
                        return {
                            "content": [{"type": "text", "text": f"Error: band_a={band_a} out of range (0-{num_bands-1})."}]
                        }
                    if band_b < 0 or band_b >= num_bands:
                        return {
                            "content": [{"type": "text", "text": f"Error: band_b={band_b} out of range (0-{num_bands-1})."}]
                        }
                    if band_a == band_b:
                        return {
                            "content": [{"type": "text", "text": f"Error: band_a and band_b are the same ({band_a}). Choose different bands."}]
                        }

                    # rasterio bands are 1-indexed
                    data_a = src.read(band_a + 1).astype(float)
                    data_b = src.read(band_b + 1).astype(float)
                    nodata_a = nodata_val
                    nodata_b = nodata_val
                    label_a = band_names[band_a]
                    label_b = band_names[band_b]

            if data_a.shape != data_b.shape:
                return {
                    "content": [{"type": "text", "text": f"Error: Raster dimensions do not match. A: {data_a.shape}, B: {data_b.shape}"}]
                }

            mask = np.ones(data_a.shape, dtype=bool)
            if nodata_a is not None:
                mask &= data_a != nodata_a
            if nodata_b is not None:
                mask &= data_b != nodata_b
            mask &= np.isfinite(data_a) & np.isfinite(data_b)

            a_flat = data_a[mask]
            b_flat = data_b[mask]

            if a_flat.size < 2:
                return {
                    "content": [{"type": "text", "text": "Error: Not enough valid pixels to compute correlation."}]
                }

            corr_matrix = np.corrcoef(a_flat, b_flat)
            r = corr_matrix[0, 1]

            n = a_flat.size
            t_stat = r * np.sqrt((n - 2) / (1 - r**2 + 1e-10))
            p_value_approx = 2.0 * np.exp(-0.717 * abs(t_stat) - 0.416 * t_stat**2) if abs(t_stat) < 6 else 0.0
            p_value_approx = min(max(p_value_approx, 0.0), 1.0)

            mode_str = f"(bands {band_a} vs {band_b} in single file)" if not file_path_b else "(two separate files)"
            summary = (
                f"Pearson Correlation Analysis {mode_str}:\n"
                f"  Correlation coefficient (r): {r:.6f}\n"
                f"  R-squared:                   {r**2:.6f}\n"
                f"  Approximate p-value:         {p_value_approx:.2e}\n"
                f"  Sample size (valid pixels):  {n}\n\n"
                f"Interpretation: {'Strong' if abs(r) > 0.7 else 'Moderate' if abs(r) > 0.4 else 'Weak'} "
                f"{'positive' if r > 0 else 'negative'} correlation."
            )

            max_points = 5000
            if a_flat.size > max_points:
                indices = np.random.choice(a_flat.size, max_points, replace=False)
                scatter_a = a_flat[indices].tolist()
                scatter_b = b_flat[indices].tolist()
            else:
                scatter_a = a_flat.tolist()
                scatter_b = b_flat.tolist()

            viz_component = {
                "type": "chart",
                "chartType": "scatter",
                "title": f"Correlation: r = {r:.4f}",
                "xAxis": {"label": label_a, "values": scatter_a},
                "yAxis": {"label": label_b},
                "series": [{"name": f"r = {r:.4f}", "values": scatter_b}],
            }

            return {
                "content": [
                    {"type": "text", "text": summary},
                    {"type": "visualization", "component": viz_component}
                ]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error computing correlation: {str(e)}"}]
            }

    return {
        "compare_collections": _compare_collections,
        "result_difference": _result_difference,
        "result_correlation": _result_correlation,
    }
