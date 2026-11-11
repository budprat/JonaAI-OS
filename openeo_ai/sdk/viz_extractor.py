"""Extract visualization data from tool results for WebSocket delivery.

Extracted from web_interface.py to avoid circular imports when the
Claude Agent SDK bridge hooks need to call extract_visualization().
"""

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import numpy as np


# Split into viz_builders/viz_raster (TECH-DEBT-HOTSPOTS.md #3). These imports
# double as re-export shims: web_interface.py and the test suite import these
# names from this module.
from .viz_builders import (  # noqa: F401
    _convert_chart_component,
    _extract_from_structured_text,
    _make_chart,
    _make_table,
    _sniff_bash_payload,
    _to_comparison_viz,
    _to_process_graph_viz,
)
from .viz_raster import (  # noqa: F401
    INDEX_COLORMAP_MAP,
    _archive_result,
    _is_unarchived_result,
    _detect_colormap_from_title,
)

try:
    from langfuse import observe as _lf_observe_viz  # type: ignore
except ImportError:  # pragma: no cover
    def _lf_observe_viz(*args, **kwargs):  # type: ignore
        def _decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _decorator



@_lf_observe_viz(name="viz_extractor.extract")
def extract_visualization(tool_name: str, result: Any, title_hint: str = "",
                          project_id: Optional[str] = None,
                          user_id: Optional[str] = None,
                          source_query: str = "") -> List[Dict[str, Any]]:
    """Extract visualization data from tool result.

    Returns a list of visualizations (can be empty, one, or multiple).
    If project_id is provided, auto-saved results are assigned to that project.
    If user_id is provided, auto-saved results are owned by that authenticated user.
    If source_query is provided, auto-saved results record the user's original prompt.

    Phase 16: wrapped in @observe so viz extraction shows up as a child
    span under the tool_call → query trace tree.
    """
    print(f"[extract_visualization] tool_name={tool_name}, result type={type(result)}, project_id={project_id}")
    vizs: List[Dict[str, Any]] = []

    # Tools that should never produce visualizations
    _NO_VIZ_TOOLS = {
        "saved_jobs_delete", "saved_jobs_list",
        "openeo_resolve_location", "openeo_parse_temporal",
    }
    if tool_name in _NO_VIZ_TOOLS:
        return vizs

    if tool_name == "viz_show_map":
        map_component = None
        if isinstance(result, dict) and "spec" in result:
            map_component = result
        elif isinstance(result, dict) and "content" in result:
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("type") == "visualization":
                    comp = item.get("component", {})
                    if isinstance(comp, dict) and "spec" in comp:
                        map_component = comp
                        break
        elif isinstance(result, list):
            # SDK may pass raw content array instead of wrapped dict
            for item in result:
                if isinstance(item, dict):
                    if item.get("type") == "visualization":
                        comp = item.get("component", {})
                        if isinstance(comp, dict) and "spec" in comp:
                            map_component = comp
                            break
                    elif "spec" in item:
                        map_component = item
                        break
                    elif item.get("type") == "text":
                        # Text item may contain JSON with viz spec
                        try:
                            parsed = json.loads(item.get("text", ""))
                            if isinstance(parsed, dict) and "spec" in parsed:
                                map_component = parsed
                                break
                        except (json.JSONDecodeError, TypeError):
                            pass
        if map_component and "spec" in map_component:
            spec = map_component["spec"]
            layers = spec.get("layers", [])
            if layers:
                layer = layers[0]
                viz = {
                    "type": "map",
                    "data": {
                        "type": "raster",
                        "url": layer.get("url", ""),
                        "bounds": layer.get("bounds"),
                        "colormap": layer.get("colormap", "viridis"),
                        "opacity": layer.get("opacity", 0.5),
                        "vmin": layer.get("vmin"),
                        "vmax": layer.get("vmax"),
                        "source": layer.get("source"),
                        "colorbar": spec.get("colorbar"),
                    },
                    "title": spec.get("title") or title_hint or "Map"
                }
                print(f"[extract_visualization] viz_show_map visualization: {json.dumps(viz, indent=2)[:500]}")
                _source = layer.get("source", "")
                if _source and _is_unarchived_result(_source) and _source.endswith((".tif", ".tiff")):
                    _viz_title = spec.get("title") or title_hint or "Map"
                    _archive_result(_source, _viz_title,
                                    bounds=layer.get("bounds"),
                                    colormap=layer.get("colormap", "viridis"),
                                    vmin=layer.get("vmin"), vmax=layer.get("vmax"),
                                    source_query=source_query, project_id=project_id,
                                    user_id=user_id, context="viz_show_map")
                vizs.append(viz)
                return vizs
        print(f"[extract_visualization] viz_show_map: invalid result structure. result type={type(result).__name__}, keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}, preview={str(result)[:300]}")
        return vizs
    elif tool_name == "viz_show_time_series":
        chart_data = result
        if isinstance(result, dict) and "content" in result:
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("type") == "visualization":
                    comp = item.get("component", {})
                    if isinstance(comp, dict) and "spec" in comp:
                        chart_data = comp
                        break
        converted = _convert_chart_component(chart_data)
        if converted:
            vizs.append(converted)
        return vizs
    elif tool_name == "openeo_quality_metrics":
        # Quality data is already sent via the dedicated "quality_metrics" WS
        # message in the PostToolUse hook. Emitting a viz here would create a
        # duplicate panel with no matching renderer on the frontend.
        return vizs

    if isinstance(result, dict):
        output_path = result.get("output_path") or result.get("path")

        # Reason: PNG files must NOT go through /render-raster, which expects
        # GeoTIFFs and uses rasterio to parse bands. PNGs sent through that
        # path produce broken tiles. Instead, read the PNG directly, base64-
        # encode it, and serve as a data URL that Leaflet ImageOverlay can
        # display without any server round-trip or auth issues.
        if output_path and output_path.endswith(".png"):
            bounds = result.get("bounds") or result.get("bbox")
            _png_title = result.get("title") or title_hint or "Result"
            print(f"[extract_visualization] Creating PNG image viz: path={output_path}, bounds={bounds}")

            # Read PNG and base64-encode for direct display
            png_data_url = None
            try:
                png_path = Path(output_path)
                if png_path.exists() and png_path.stat().st_size > 0:
                    png_bytes = png_path.read_bytes()
                    png_b64 = base64.b64encode(png_bytes).decode("ascii")
                    png_data_url = f"data:image/png;base64,{png_b64}"
            except Exception as _e:
                print(f"[extract_visualization] Failed to read PNG {output_path}: {_e}")

            if png_data_url:
                viz = {
                    "type": "map",
                    "data": {
                        "type": "raster",
                        "url": png_data_url,
                        "bounds": bounds,
                        "opacity": 0.8,
                        "source": output_path,
                    },
                    "title": _png_title,
                }
                # Auto-save to job archive
                if _is_unarchived_result(output_path):
                    _save_id = _archive_result(output_path, _png_title, bounds=bounds,
                                               source_query=source_query, project_id=project_id,
                                               user_id=user_id, context="PNG result")
                    if _save_id:
                        viz["save_id"] = _save_id
                        print(f"[auto-save] Archived PNG result {_save_id}: {_png_title}")
                print(f"[extract_visualization] PNG visualization created: {_png_title} ({len(png_data_url)} chars)")
                vizs.append(viz)

        elif output_path and (output_path.endswith(".tif") or output_path.endswith(".tiff")):
            bounds = result.get("bounds") or result.get("bbox")
            print(f"[extract_visualization] Creating map viz: path={output_path}, bounds={bounds}")

            # Reason: Auto-detect colormap from title/description when not explicitly set.
            # The AI agent may not always pass the right colormap, so we fall back to
            # index-based detection from the job title.
            colormap = result.get("colormap")
            if not colormap or colormap == "viridis":
                # Try auto-detecting from title
                detect_title = result.get("title") or title_hint or ""
                detected = _detect_colormap_from_title(detect_title)
                colormap = detected if detected != "viridis" else (colormap or "viridis")
            viz = {
                "type": "map",
                "data": {
                    "type": "raster",
                    "url": f"/render-raster?path={quote(output_path, safe='/')}&colormap={quote(colormap, safe='')}",
                    "bounds": bounds,
                    "colormap": colormap,
                    "opacity": 0.5,
                    "vmin": result.get("vmin"),
                    "vmax": result.get("vmax"),
                    "source": output_path,
                },
                "title": result.get("title") or title_hint or "Result"
            }
            # Compute raster stats and attach to viz data
            try:
                import rasterio as _rio
                with _rio.open(output_path) as _src:
                    _band = _src.read(1)
                    _nodata = _src.nodata
                    if np.issubdtype(_band.dtype, np.floating):
                        _valid = _band[~np.isnan(_band)]
                    else:
                        _valid = _band.ravel()
                    if _nodata is not None:
                        _valid = _valid[~np.isclose(_valid.astype(float), float(_nodata))]
                    _total = _band.size
                    if len(_valid) > 0:
                        viz["data"]["stats"] = {
                            "min": float(_valid.min()),
                            "max": float(_valid.max()),
                            "mean": float(_valid.mean()),
                            "median": float(np.median(_valid)),
                            "std": float(_valid.std()),
                            "nodata_percent": round((1 - len(_valid) / _total) * 100, 1),
                            "valid_pixels": int(len(_valid)),
                            "total_pixels": int(_total),
                            "crs": str(_src.crs) if _src.crs else None,
                            "resolution": [abs(float(_src.res[0])), abs(float(_src.res[1]))],
                            "width": _src.width,
                            "height": _src.height,
                            "bands": _src.count,
                            "dtype": str(_band.dtype),
                            "file_size_bytes": os.path.getsize(output_path),
                        }
                        # Fill vmin/vmax from stats if not already set
                        if viz["data"].get("vmin") is None:
                            viz["data"]["vmin"] = viz["data"]["stats"]["min"]
                        if viz["data"].get("vmax") is None:
                            viz["data"]["vmax"] = viz["data"]["stats"]["max"]
            except Exception:
                pass  # Stats are best-effort, never block visualization
            print(f"[extract_visualization] Visualization: {json.dumps(viz, indent=2)}")
            if _is_unarchived_result(output_path):
                _save_title = result.get("title") or title_hint or tool_name.replace("_", " ").title() or "Result"
                _save_id = _archive_result(output_path, _save_title, bounds=bounds,
                                           colormap=colormap, vmin=result.get("vmin"),
                                           vmax=result.get("vmax"), source_query=source_query,
                                           project_id=project_id, user_id=user_id)
                if _save_id:
                    # Attach save_id to viz for frontend auto-refresh
                    viz["save_id"] = _save_id
                    print(f"[auto-save] Archived result {_save_id}: {_save_title}")
            vizs.append(viz)

        # Handle comparison_slider results from maps.py
        if isinstance(result, dict) and result.get("type") == "comparison_slider" and "spec" in result:
            vizs.append(_to_comparison_viz(result["spec"]))
        _GRAPH_TOOLS = {"openeo_generate_graph", "openeo_compose_graph", "openeo_raw_graph", "openeo_algorithm"}
        if tool_name in _GRAPH_TOOLS and isinstance(result, dict):
            _graph_nodes = {k: v for k, v in result.items()
                            if isinstance(v, dict) and "process_id" in v}
            if _graph_nodes:
                vizs.append(_to_process_graph_viz(_graph_nodes, "Process Graph", include_result=True))

        content_items = result.get("content", [])
        if isinstance(content_items, list):
            for item in content_items:
                if isinstance(item, dict) and item.get("type") == "visualization":
                    component = item.get("component", {})
                    comp_type = component.get("type", "")
                    if comp_type == "chart":
                        if "spec" in component:
                            converted = _convert_chart_component(component)
                            if converted:
                                vizs.append(converted)
                                continue
                        vizs.append({
                            "type": "chart",
                            "data": {
                                "chartType": component.get("chartType", "bar"),
                                "title": component.get("title", "Chart"),
                                "xAxis": component.get("xAxis", {}),
                                "yAxis": component.get("yAxis", {}),
                                "series": component.get("series", []),
                            },
                            "title": component.get("title", "Chart"),
                        })
                    elif comp_type == "map":
                        vizs.append({
                            "type": "map",
                            "data": {
                                "type": component.get("mapType", "raster"),
                                "url": component.get("url", ""),
                                "bounds": component.get("bounds"),
                                "colormap": component.get("colormap", "viridis"),
                                "opacity": component.get("opacity", 0.8),
                                "vmin": component.get("vmin"),
                                "vmax": component.get("vmax"),
                                "source": component.get("source"),
                            },
                            "title": component.get("title", "Map"),
                        })
                    elif comp_type == "table":
                        vizs.append({
                            "type": "table",
                            "data": {
                                "headers": component.get("headers", []),
                                "rows": component.get("rows", []),
                            },
                            "title": component.get("title", "Data"),
                        })
                    elif comp_type == "comparison_slider":
                        spec = component.get("spec", component)
                        vizs.append(_to_comparison_viz(spec))
                    elif comp_type == "process_graph":
                        graph_data = component.get("data", {})
                        graph = graph_data.get("graph", {})
                        if graph:
                            vizs.append(_to_process_graph_viz(
                                graph,
                                graph_data.get("title", component.get("title", "Process Graph")),
                            ))

    # Detect multi_panel visualization from tool results that contain multiple map panels
    if isinstance(result, dict):
        content_items = result.get("content", [])
        if isinstance(content_items, list):
            for item in content_items:
                if isinstance(item, dict) and item.get("type") == "visualization":
                    component = item.get("component", {})
                    if isinstance(component, dict) and component.get("type") == "multi_panel":
                        panels = component.get("panels", [])
                        if panels:
                            vizs.append({
                                "type": "multi_panel",
                                "data": {"panels": panels},
                                "title": component.get("title", "Multi-Panel Comparison"),
                            })
                    elif isinstance(component, dict) and component.get("type") == "dashboard":
                        dashboard_panels = component.get("panels", [])
                        if dashboard_panels:
                            vizs.append({
                                "type": "dashboard",
                                "data": {"panels": dashboard_panels},
                                "title": component.get("title", "Dashboard"),
                            })
                    elif isinstance(component, dict) and component.get("type") == "timelapse":
                        layers = component.get("layers", [])
                        if layers:
                            vizs.append({
                                "type": "timelapse",
                                "data": {"layers": layers, "colormap": component.get("colormap", "viridis")},
                                "title": component.get("title", "Timelapse"),
                            })

    auto_vizs = _extract_from_structured_text(tool_name, result)
    for av in auto_vizs:
        already_has = any(
            v.get("type") == av.get("type") and v.get("title") == av.get("title")
            for v in vizs
        )
        if not already_has:
            vizs.append(av)

    if vizs:
        print(f"[extract_visualization] Returning {len(vizs)} visualizations: {[v.get('type') for v in vizs]}")
    return vizs
