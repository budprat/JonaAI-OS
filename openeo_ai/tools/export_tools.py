# ABOUTME: Export tools for saving EO analysis results in different formats.
# Supports styled GeoTIFF rendering, HTML reports, and CSV data export.

"""
Export tools for Claude SDK.

Provides tools for exporting Earth Observation analysis results
as styled images, HTML reports, and CSV files.
"""

import csv
import html as html_lib
import io
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from .path_security import current_user_output_dir
from .path_security import validate_tool_input_path, validate_tool_output_path, PathSecurityError


EXPORT_TOOL_DEFINITIONS = [
    {
        "name": "export_styled_geotiff",
        "description": "Render a GeoTIFF as a styled PNG image with colormap, title, colorbar legend, and coordinate labels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the input GeoTIFF file"
                },
                "title": {
                    "type": "string",
                    "description": "Title to display on the image"
                },
                "colormap": {
                    "type": "string",
                    "description": "Matplotlib colormap name (e.g., viridis, plasma, RdYlGn, terrain)",
                    "default": "viridis"
                },
                "output_path": {
                    "type": "string",
                    "description": "Output PNG file path. Defaults to styled_{timestamp}.png in the user's output folder"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "export_report",
        "description": "Generate an HTML report summarizing an Earth Observation analysis with title, summary, statistics, and process graph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Report title"
                },
                "summary": {
                    "type": "string",
                    "description": "Analysis summary text"
                },
                "statistics": {
                    "type": "object",
                    "description": "Optional statistics dict to render as a table"
                },
                "process_graph": {
                    "type": "object",
                    "description": "Optional OpenEO process graph to include in the report"
                },
                "source_file": {
                    "type": "string",
                    "description": "Path to the source GeoTIFF file this report relates to. Used to associate the report with the correct saved result."
                },
                "output_path": {
                    "type": "string",
                    "description": "Output HTML file path. Defaults to report_{timestamp}.html in the user's output folder"
                }
            },
            "required": ["title", "summary"]
        }
    },
    {
        "name": "export_csv",
        "description": "Export time series or tabular data to a CSV file. Accepts either {dates, values} for time series or {headers, rows} for tabular data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "description": "Data to export. Either {\"dates\": [...], \"values\": [...]} or {\"headers\": [...], \"rows\": [[...], ...]}"
                },
                "source_file": {
                    "type": "string",
                    "description": "Path to the source GeoTIFF file this CSV relates to. Used to associate the CSV with the correct saved result."
                },
                "output_path": {
                    "type": "string",
                    "description": "Output CSV file path. Defaults to export_{timestamp}.csv in the user's output folder"
                }
            },
            "required": ["data"]
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


def _copy_to_saved_result(output_path: str, source_tif: str | None = None) -> None:
    """Copy an exported file into the saved result directory that matches the source GeoTIFF.
    If no source_tif provided, copy into the most recently modified saved result directory."""
    try:
        results_base = Path.home() / ".openeo_jobs" / "results"
        if not results_base.exists():
            return

        target_dir = None
        if source_tif:
            src = Path(source_tif).resolve()
            src_str = str(src)
            for d in results_base.iterdir():
                if d.is_dir():
                    d_resolved = str(d.resolve())
                    if src_str.startswith(d_resolved + "/"):
                        target_dir = d
                        break
                    result_file = d / "result.tif"
                    if result_file.exists():
                        try:
                            if result_file.resolve().samefile(src):
                                target_dir = d
                                break
                        except (OSError, ValueError):
                            pass
                    meta_file = d / "metadata.json"
                    if meta_file.exists():
                        try:
                            with open(meta_file) as mf:
                                meta = json.load(mf)
                                orig_path = meta.get("original_path", "")
                            if orig_path and Path(orig_path).resolve() == src:
                                target_dir = d
                                break
                        except Exception:
                            pass

        if not target_dir:
            # Don't guess — log warning instead of copying to wrong directory
            import logging
            logging.getLogger(__name__).warning(
                f"Cannot find saved result directory for source_tif={source_tif}. "
                f"Export file {output_path} will not be copied to archive."
            )
            return

        if target_dir:
            # Reading a shared result doesn't allow changing it.
            from ..sdk.user_context import get_current_user_id
            from ..storage.result_access import owns_saved_result

            user_id = get_current_user_id()
            if user_id and not owns_saved_result(target_dir.name, user_id):
                return
            dest = target_dir / Path(output_path).name
            out_resolved = Path(output_path).resolve()
            if dest.resolve() != out_resolved:
                shutil.copy2(output_path, dest)
    except Exception:
        pass


# Map JonaAI custom colormap names to valid matplotlib colormaps
_COLORMAP_ALIASES = {
    "ndvi": "RdYlGn",
    "vegetation": "RdYlGn",
    "water": "Blues",
    "ndwi": "Blues",
    "fire": "hot",
    "burn": "RdYlGn_r",
    "temperature": "inferno",
    "thermal": "inferno",
    "urban": "YlOrRd",
    "soil": "YlOrBr",
    "snow": "coolwarm",
    "moisture": "YlGnBu",
    "elevation": "terrain",
    "dem": "terrain",
    "hotspot": "hot",
}


def _resolve_colormap(name: str) -> str:
    """Resolve colormap name, mapping JonaAI aliases to matplotlib names.

    Also fixes case-sensitivity issues — matplotlib colormaps are case-sensitive
    (e.g., 'RdYlGn' not 'rdylgn').
    """
    if not name:
        return "viridis"
    # Check if it's a known alias
    resolved = _COLORMAP_ALIASES.get(name.lower())
    if resolved:
        return resolved
    # Fix case-sensitivity: try to find the correct-case matplotlib name
    try:
        import matplotlib.pyplot as plt
        valid_cmaps = plt.colormaps()
        lower_map = {cm.lower(): cm for cm in valid_cmaps}
        if name.lower() in lower_map:
            return lower_map[name.lower()]
    except Exception:
        pass
    return name


def create_export_tools(config=None) -> Dict[str, Any]:
    """
    Create export tools dict for Claude SDK.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _export_styled_geotiff(args: Dict[str, Any]) -> Dict[str, Any]:
        """Render a GeoTIFF as a styled PNG with colormap and labels."""
        try:
            file_path = validate_tool_input_path(args["file_path"])
        except PathSecurityError as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

        output_path = args.get("output_path")
        if output_path:
            try:
                output_path = validate_tool_output_path(output_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}
        else:
            output_path = _default_output_path("styled", "png")

        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            import numpy as np
            import rasterio

            title = args.get("title", "")
            colormap = _resolve_colormap(args.get("colormap", "viridis"))

            _ensure_output_dir(output_path)

            with rasterio.open(file_path) as src:
                data = src.read(1)
                bounds = src.bounds
                nodata = src.nodata

            if nodata is not None:
                data = np.where(data == nodata, np.nan, data.astype(float))

            fig, ax = plt.subplots(1, 1, figsize=(10, 8))
            extent = [bounds.left, bounds.right, bounds.bottom, bounds.top]
            im = ax.imshow(data, cmap=colormap, extent=extent, origin='upper')

            if title:
                ax.set_title(title, fontsize=14, fontweight='bold')
            ax.set_xlabel("Longitude")
            ax.set_ylabel("Latitude")
            plt.colorbar(im, ax=ax, shrink=0.8, label="Value")
            plt.tight_layout()
            plt.savefig(output_path, dpi=150, bbox_inches='tight')
            plt.close(fig)

            _copy_to_saved_result(output_path, source_tif=file_path)

            return {
                "content": [{"type": "text", "text": f"Styled image saved to: {output_path}"}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error exporting styled GeoTIFF: {str(e)}"}]
            }

    async def _export_report(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate an HTML report with analysis summary."""
        output_path = args.get("output_path")
        if output_path:
            try:
                output_path = validate_tool_output_path(output_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}
        else:
            output_path = _default_output_path("report", "html")

        try:
            # HTML-escape all user-controlled values to prevent stored XSS
            title = html_lib.escape(args["title"])
            summary = html_lib.escape(args["summary"])
            statistics = args.get("statistics")
            process_graph = args.get("process_graph")

            _ensure_output_dir(output_path)

            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            stats_html = ""
            if statistics:
                rows = "".join(
                    f"<tr><td>{html_lib.escape(str(k))}</td><td>{html_lib.escape(str(v))}</td></tr>"
                    for k, v in statistics.items()
                )
                stats_html = f"""
                <h2>Statistics</h2>
                <table>
                    <tr><th>Metric</th><th>Value</th></tr>
                    {rows}
                </table>
                """

            graph_html = ""
            if process_graph:
                graph_json = html_lib.escape(json.dumps(process_graph, indent=2))
                graph_html = f"""
                <h2>Process Graph</h2>
                <pre>{graph_json}</pre>
                """

            html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f5f5f5; color: #333; }}
    .container {{ max-width: 900px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
    h1 {{ color: #1a5276; border-bottom: 2px solid #2980b9; padding-bottom: 10px; }}
    h2 {{ color: #2c3e50; margin-top: 30px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
    th, td {{ border: 1px solid #ddd; padding: 10px 14px; text-align: left; }}
    th {{ background: #2980b9; color: #fff; }}
    tr:nth-child(even) {{ background: #f9f9f9; }}
    pre {{ background: #2c3e50; color: #ecf0f1; padding: 15px; border-radius: 4px; overflow-x: auto; font-size: 13px; }}
    .summary {{ line-height: 1.7; font-size: 15px; }}
    .timestamp {{ color: #888; font-size: 13px; margin-top: 30px; border-top: 1px solid #eee; padding-top: 10px; }}
</style>
</head>
<body>
<div class="container">
    <h1>{title}</h1>
    <h2>Summary</h2>
    <div class="summary">{summary}</div>
    {stats_html}
    {graph_html}
    <div class="timestamp">Generated: {timestamp}</div>
</div>
</body>
</html>"""

            with open(output_path, "w") as f:
                f.write(html)

            source_file = args.get("source_file")
            _copy_to_saved_result(output_path, source_tif=source_file)

            return {
                "content": [{"type": "text", "text": f"Report saved to: {output_path}"}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error generating report: {str(e)}"}]
            }

    async def _export_csv(args: Dict[str, Any]) -> Dict[str, Any]:
        """Export time series or tabular data to CSV."""
        output_path = args.get("output_path")
        if output_path:
            try:
                output_path = validate_tool_output_path(output_path)
            except PathSecurityError as e:
                return {"content": [{"type": "text", "text": f"Error: {e}"}]}
        else:
            output_path = _default_output_path("export", "csv")

        try:
            data = args["data"]

            _ensure_output_dir(output_path)

            with open(output_path, "w", newline="") as f:
                writer = csv.writer(f)

                if "dates" in data and "values" in data:
                    writer.writerow(["date", "value"])
                    for date, value in zip(data["dates"], data["values"]):
                        writer.writerow([date, value])
                elif "headers" in data and "rows" in data:
                    writer.writerow(data["headers"])
                    for row in data["rows"]:
                        writer.writerow(row)
                else:
                    return {
                        "content": [{"type": "text", "text": "Error: data must contain either {dates, values} or {headers, rows}"}]
                    }

            source_file = args.get("source_file")
            _copy_to_saved_result(output_path, source_tif=source_file)

            return {
                "content": [{"type": "text", "text": f"CSV exported to: {output_path}"}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error exporting CSV: {str(e)}"}]
            }

    return {
        "export_styled_geotiff": _export_styled_geotiff,
        "export_report": _export_report,
        "export_csv": _export_csv,
    }
