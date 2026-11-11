# ABOUTME: Visualization tools wrapping map and chart components for Claude SDK.
# Creates MCP-UI compatible interactive maps, time series, and comparison views.

"""
Visualization tools for Claude SDK.

Provides tools for creating interactive maps, charts, and comparison views
following the MCP-UI component format.
"""

import json
from typing import Any, Dict

from .path_security import validate_tool_input_path, PathSecurityError


def create_viz_tools(config) -> Dict[str, Any]:
    """
    Create visualization tools dict for Claude SDK.

    Tools return MCP-UI compatible component specifications
    wrapped in Claude SDK format.
    """
    from ..visualization.maps import MapComponent
    from ..visualization.charts import ChartComponent

    # Lazy initialization
    _map_component = None
    _chart_component = None

    def get_map_component():
        nonlocal _map_component
        if _map_component is None:
            _map_component = MapComponent()
        return _map_component

    def get_chart_component():
        nonlocal _chart_component
        if _chart_component is None:
            _chart_component = ChartComponent()
        return _chart_component

    async def _show_map(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Display a GeoTIFF on an interactive map.

        Returns a lightweight server-rendered URL (not base64) so the response
        fits through the SDK subprocess pipe.  The frontend fetches the actual
        PNG tile from /render-raster.
        """
        try:
            geotiff_path = validate_tool_input_path(args["geotiff_path"])
        except PathSecurityError as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        import rasterio
        colormap = args.get("colormap", "viridis")
        title = args.get("title", "Result")

        # Extract bounds from GeoTIFF for map positioning
        bounds = None
        try:
            with rasterio.open(geotiff_path) as ds:
                b = ds.bounds
                bounds = [b.left, b.bottom, b.right, b.top]
        except Exception:
            pass

        # Build server-side render URL (lightweight — no base64)
        from urllib.parse import quote
        render_url = f"/render-raster?path={quote(geotiff_path, safe='/')}&colormap={quote(colormap, safe='')}"

        # Return as JSON text — the SDK CLI only preserves "type": "text" items,
        # not custom types like "visualization". The viz_extractor will parse it.
        import json as _json
        viz_data = {
            "type": "map",
            "spec": {
                "title": title,
                "layers": [{
                    "type": "raster",
                    "url": render_url,
                    "bounds": bounds,
                    "source": geotiff_path,
                    "colormap": colormap,
                    "opacity": 0.5,
                    "vmin": args.get("vmin"),
                    "vmax": args.get("vmax"),
                }],
            },
        }
        return {
            "content": [{"type": "text", "text": _json.dumps(viz_data)}]
        }

    async def _show_ndvi_map(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Display NDVI results with vegetation color scale.

        Args:
            geotiff_path: Path to NDVI GeoTIFF
            title: Map title (default: "NDVI")

        Returns:
            Claude SDK format response with NDVI visualization as JSON text
        """
        try:
            geotiff_path = validate_tool_input_path(args["geotiff_path"])
        except PathSecurityError as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        import rasterio
        from urllib.parse import quote

        title = args.get("title", "NDVI")
        colormap = "ndvi"

        # Extract bounds from GeoTIFF for map positioning
        bounds = None
        try:
            with rasterio.open(geotiff_path) as ds:
                b = ds.bounds
                bounds = [b.left, b.bottom, b.right, b.top]
        except Exception:
            pass

        render_url = f"/render-raster?path={quote(geotiff_path, safe='/')}&colormap={quote(colormap, safe='')}"

        viz_data = {
            "type": "map",
            "spec": {
                "title": title,
                "layers": [{
                    "type": "raster",
                    "url": render_url,
                    "bounds": bounds,
                    "source": geotiff_path,
                    "colormap": colormap,
                    "opacity": 0.5,
                    "vmin": -1.0,
                    "vmax": 1.0,
                }],
                "colorbar": {
                    "min": -1.0,
                    "max": 1.0,
                    "colormap": "RdYlGn",
                },
            },
        }
        return {
            "content": [{"type": "text", "text": json.dumps(viz_data)}]
        }

    async def _show_time_series(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Display a time series chart.

        Args:
            values: List of values
            dates: List of date strings
            title: Chart title
            ylabel: Y-axis label

        Returns:
            Claude SDK format response with chart data as JSON text
        """
        title = args.get("title", "Time Series")
        ylabel = args.get("ylabel", "Value")
        series_name = args.get("series_name", "Data")

        viz_data = {
            "type": "chart",
            "spec": {
                "chart_type": "line",
                "title": title,
                "data": {
                    "x": args["dates"],
                    "y": args["values"],
                    "series_name": series_name,
                },
                "xaxis": {"title": "Date"},
                "yaxis": {"title": ylabel},
            },
        }
        return {
            "content": [{"type": "text", "text": json.dumps(viz_data)}]
        }

    async def _compare_images(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a before/after comparison slider.

        Args:
            before_path: Path to before image
            after_path: Path to after image
            title: Comparison title

        Returns:
            Claude SDK format response with comparison data as JSON text
        """
        try:
            before_path = validate_tool_input_path(args["before_path"])
            after_path = validate_tool_input_path(args["after_path"])
        except PathSecurityError as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        import rasterio
        from urllib.parse import quote

        title = args.get("title", "Before / After")
        colormap = args.get("colormap", "viridis")

        # Extract bounds from the before image for map positioning
        bounds = None
        try:
            with rasterio.open(before_path) as ds:
                b = ds.bounds
                bounds = [b.left, b.bottom, b.right, b.top]
        except Exception:
            pass

        before_url = f"/render-raster?path={quote(before_path, safe='/')}&colormap={quote(colormap, safe='')}"
        after_url = f"/render-raster?path={quote(after_path, safe='/')}&colormap={quote(colormap, safe='')}"

        viz_data = {
            "type": "comparison_slider",
            "spec": {
                "title": title,
                "before": {
                    "label": "Before",
                    "bounds": bounds,
                    "source": before_path,
                    "url": before_url,
                },
                "after": {
                    "label": "After",
                    "bounds": bounds,
                    "source": after_path,
                    "url": after_url,
                },
                "colorbar": {
                    "colormap": colormap,
                },
                "initial_position": 50,
            },
        }
        return {
            "content": [{"type": "text", "text": json.dumps(viz_data)}]
        }

    async def _show_dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a composite dashboard from a single result file.

        Reads raster metadata + stats and assembles map, stat, and histogram
        panels into a dashboard visualization component.

        Args:
            file_path: Path to a GeoTIFF result file
            title: Dashboard title
            include: Which panel types to include (default: all)
            colormap: Colormap name for map panel (default: viridis)

        Returns:
            Claude SDK format response with dashboard visualization component
        """
        import os
        import numpy as np

        try:
            file_path = validate_tool_input_path(args["file_path"])
        except PathSecurityError as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        title = args.get("title", "Analysis Dashboard")
        include = args.get("include", ["map", "stats", "histogram"])
        colormap = args.get("colormap", "viridis")

        try:
            import rasterio
        except ImportError:
            return {"content": [{"type": "text", "text": "Error: rasterio not available"}]}

        panels = []

        with rasterio.open(file_path) as ds:
            band = 1
            data = ds.read(band).astype(float)
            nodata = ds.nodata
            if nodata is not None:
                data[data == nodata] = np.nan

            valid = data[~np.isnan(data)].ravel()
            bounds = [
                [ds.bounds.bottom, ds.bounds.left],
                [ds.bounds.top, ds.bounds.right],
            ]

            # Compute stats
            if valid.size > 0:
                stats_min = float(np.nanmin(valid))
                stats_max = float(np.nanmax(valid))
                stats_mean = float(np.nanmean(valid))
                stats_median = float(np.nanmedian(valid))
                stats_std = float(np.nanstd(valid))
                total_pixels = data.size
                valid_pct = round(valid.size / total_pixels * 100, 1)
            else:
                stats_min = stats_max = stats_mean = stats_median = stats_std = 0.0
                valid_pct = 0.0

            # Compute pixel area
            res_x, res_y = abs(ds.res[0]), abs(ds.res[1])
            pixel_area_sq_m = res_x * res_y
            if ds.crs and not ds.crs.is_projected:
                center_lat = (ds.bounds.top + ds.bounds.bottom) / 2
                m_per_deg_lat = 111320.0
                m_per_deg_lon = 111320.0 * np.cos(np.radians(center_lat))
                pixel_area_sq_m = (res_x * m_per_deg_lon) * (res_y * m_per_deg_lat)
            total_area_ha = round(data.size * pixel_area_sq_m / 10000.0, 2)

            # Map panel
            if "map" in include:
                from urllib.parse import quote as _quote
                render_url = f"/render-raster?path={_quote(file_path, safe='/')}&colormap={_quote(colormap, safe='')}"
                panels.append({
                    "type": "map",
                    "title": title,
                    "data": {
                        "type": "raster",
                        "url": render_url,
                        "bounds": bounds,
                        "colormap": colormap,
                        "vmin": stats_min,
                        "vmax": stats_max,
                    }
                })

            # Stats panel
            if "stats" in include:
                panels.append({
                    "type": "stat",
                    "title": "Key Metrics",
                    "data": {
                        "metrics": [
                            {"label": "Min", "value": round(stats_min, 4)},
                            {"label": "Max", "value": round(stats_max, 4)},
                            {"label": "Mean", "value": round(stats_mean, 4)},
                            {"label": "Median", "value": round(stats_median, 4)},
                            {"label": "Std Dev", "value": round(stats_std, 4)},
                            {"label": "Valid Pixels", "value": f"{valid_pct}%"},
                            {"label": "Area", "value": total_area_ha, "unit": "ha"},
                            {"label": "Bands", "value": ds.count},
                        ]
                    }
                })

            # Histogram panel
            if "histogram" in include and valid.size > 0:
                hist_counts, hist_edges = np.histogram(valid, bins=20)
                bin_labels = [f"{hist_edges[i]:.3f}" for i in range(len(hist_counts))]
                panels.append({
                    "type": "chart",
                    "title": "Value Distribution",
                    "data": {
                        "chartType": "bar",
                        "title": "Histogram",
                        "xAxis": {"label": "Value", "values": bin_labels},
                        "yAxis": {"label": "Pixel Count"},
                        "series": [{"name": "Frequency", "values": [int(c) for c in hist_counts]}],
                    }
                })

        # Build summary text
        summary_lines = [
            f"Dashboard: {title}",
            f"File: {os.path.basename(file_path)}",
            f"Panels: {', '.join(include)}",
            f"Stats: min={stats_min:.4f}, max={stats_max:.4f}, mean={stats_mean:.4f}",
            f"Area: {total_area_ha} ha | Valid: {valid_pct}%",
        ]

        return {
            "content": [
                {"type": "text", "text": "\n".join(summary_lines)},
                {
                    "type": "visualization",
                    "component": {
                        "type": "dashboard",
                        "title": title,
                        "data": {"panels": panels},
                    }
                }
            ]
        }

    return {
        "viz_show_map": _show_map,
        "viz_show_ndvi_map": _show_ndvi_map,
        "viz_show_time_series": _show_time_series,
        "viz_compare_images": _compare_images,
        "viz_show_dashboard": _show_dashboard,
    }


# Standalone tool functions for testing
async def show_map_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone show map tool."""
    try:
        geotiff_path = validate_tool_input_path(args["geotiff_path"])
    except PathSecurityError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    from ..visualization.maps import MapComponent

    component = MapComponent()
    result = await component.create_raster_map(
        geotiff_path=geotiff_path,
        title=args.get("title", "Result"),
        colormap=args.get("colormap", "viridis"),
        vmin=args.get("vmin"),
        vmax=args.get("vmax")
    )

    return {
        "content": [{
            "type": "visualization",
            "component": result
        }]
    }


async def show_ndvi_map_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone NDVI map tool."""
    try:
        geotiff_path = validate_tool_input_path(args["geotiff_path"])
    except PathSecurityError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    from ..visualization.maps import MapComponent

    component = MapComponent()
    result = await component.create_ndvi_map(
        geotiff_path=geotiff_path,
        title=args.get("title", "NDVI")
    )

    return {
        "content": [{
            "type": "visualization",
            "component": result
        }]
    }


async def show_time_series_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone time series tool."""
    from ..visualization.charts import ChartComponent

    component = ChartComponent()
    result = await component.create_time_series(
        values=args["values"],
        dates=args["dates"],
        title=args.get("title", "Time Series"),
        ylabel=args.get("ylabel", "Value")
    )

    return {
        "content": [{
            "type": "visualization",
            "component": result
        }]
    }


async def compare_images_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone comparison tool."""
    try:
        before_path = validate_tool_input_path(args["before_path"])
        after_path = validate_tool_input_path(args["after_path"])
    except PathSecurityError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    from ..visualization.maps import MapComponent

    component = MapComponent()
    result = await component.create_comparison_slider(
        before_path=before_path,
        after_path=after_path,
        title=args.get("title", "Before / After")
    )

    return {
        "content": [{
            "type": "visualization",
            "component": result
        }]
    }
