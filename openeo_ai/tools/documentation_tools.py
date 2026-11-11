# ABOUTME: Workflow documentation and validation checklist tools for reproducible EO research.
# Generates structured markdown reports with annotated process graphs, quality assessments, and validation checklists.

"""
Documentation and Test Suite Generation tools for Claude SDK.

Provides tools for generating comprehensive workflow documentation
that ensures reproducibility of Earth Observation analyses:
- Structured markdown workflow reports
- Annotated process graph documentation
- Validation checklists (CRS, antimeridian, cloud cover, extent)
- Quality assessment summaries
- Reproducibility metadata
"""

import json
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from .path_security import current_user_output_dir

logger = logging.getLogger(__name__)

PROCESS_ANNOTATIONS = {
    "load_collection": "Loads a datacube from a satellite data collection (e.g., Sentinel-2, Landsat). Defines the spatial/temporal extent and bands to retrieve.",
    "filter_temporal": "Filters the datacube to a specific date range.",
    "filter_bands": "Selects specific spectral bands from the datacube.",
    "filter_spatial": "Clips the datacube to a specific spatial geometry.",
    "filter_bbox": "Clips the datacube to a bounding box.",
    "reduce_dimension": "Reduces a dimension by applying a function (e.g., mean over time, or NDVI across bands).",
    "aggregate_temporal": "Aggregates values over time intervals (e.g., monthly means).",
    "aggregate_temporal_period": "Aggregates values over calendar periods (month, season, year).",
    "aggregate_spatial": "Aggregates values over spatial regions (e.g., zonal statistics).",
    "apply": "Applies a unary function to every pixel (e.g., scale, clip).",
    "apply_dimension": "Applies a function along a specific dimension.",
    "apply_kernel": "Applies a spatial convolution kernel (e.g., smoothing, edge detection).",
    "merge_cubes": "Merges two datacubes into one combined datacube (used in multi-sensor fusion).",
    "resample_cube_spatial": "Resamples a datacube to match the spatial resolution of a target datacube.",
    "resample_spatial": "Resamples to a target resolution.",
    "mask": "Applies a binary mask to filter out invalid pixels (e.g., cloud masking using SCL band).",
    "mask_polygon": "Masks pixels outside a polygon geometry.",
    "normalized_difference": "Computes a normalized difference index: (A - B) / (A + B). Used for NDVI, NDWI, etc.",
    "save_result": "Saves the final result to a file format (GeoTIFF, NetCDF, etc.).",
    "array_element": "Selects a single element from an array dimension (e.g., a specific band).",
    "ndvi": "Computes NDVI (Normalized Difference Vegetation Index) from NIR and RED bands.",
}

VALIDATION_CHECKS = [
    {
        "id": "crs_validated",
        "category": "Coordinate System",
        "description": "CRS (Coordinate Reference System) was validated using openeo_validate_geospatial",
        "tool": "openeo_validate_geospatial",
    },
    {
        "id": "antimeridian_checked",
        "category": "Coordinate System",
        "description": "Antimeridian crossing was checked (important for Pacific regions)",
        "tool": "openeo_validate_geospatial",
    },
    {
        "id": "extent_estimated",
        "category": "Data Size",
        "description": "Data extent was estimated before processing to avoid oversized requests",
        "tool": "openeo_estimate_extent",
    },
    {
        "id": "quality_assessed",
        "category": "Data Quality",
        "description": "Data quality was assessed (cloud cover, temporal completeness)",
        "tool": "openeo_quality_metrics",
    },
    {
        "id": "graph_validated",
        "category": "Process Graph",
        "description": "Process graph was validated for structural correctness",
        "tool": "openeo_validate_graph",
    },
    {
        "id": "results_inspected",
        "category": "Results",
        "description": "Output results were inspected with band statistics (min, max, mean)",
        "tool": "result_inspect",
    },
    {
        "id": "map_displayed",
        "category": "Visualization",
        "description": "Results were displayed on an interactive map for visual verification",
        "tool": "viz_show_map",
    },
]


DOCUMENTATION_TOOL_DEFINITIONS = [
    {
        "name": "export_workflow_doc",
        "description": (
            "Generate a comprehensive, reproducible workflow document for an Earth Observation analysis. "
            "Includes: annotated process graph with explanations of each step, validation checklist "
            "(CRS, cloud cover, extent, antimeridian), quality assessment summary, reproducibility metadata "
            "(exact coordinates, dates, collection versions), and a structured markdown report. "
            "Use this after completing an analysis to provide the user with a shareable, citable document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Title for the workflow document (e.g., 'NDVI Analysis of Kerala Monsoon 2024')"
                },
                "objective": {
                    "type": "string",
                    "description": "The original research question or user query that initiated this analysis"
                },
                "process_graph": {
                    "type": "object",
                    "description": "The OpenEO process graph used in this analysis"
                },
                "collections_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of collection IDs used (e.g., ['sentinel-2-l2a'])"
                },
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box used in the analysis",
                    "properties": {
                        "west": {"type": "number"},
                        "south": {"type": "number"},
                        "east": {"type": "number"},
                        "north": {"type": "number"}
                    }
                },
                "temporal_extent": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Date range used [start, end]"
                },
                "results_summary": {
                    "type": "object",
                    "description": "Summary of analysis results (statistics, findings, key values)"
                },
                "quality_info": {
                    "type": "object",
                    "description": "Quality assessment results (grade, cloud cover, completeness)"
                },
                "tools_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool names that were called during this analysis"
                },
                "validation_checks_passed": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of validation check IDs that were performed (e.g., ['crs_validated', 'quality_assessed'])"
                },
                "output_format": {
                    "type": "string",
                    "description": "Output format: 'markdown' (default) or 'html'",
                    "default": "markdown"
                },
                "output_path": {
                    "type": "string",
                    "description": "Output file path. Defaults to workflow_doc_{timestamp}.md in the user's output folder"
                }
            },
            "required": ["title", "objective"]
        }
    },
    {
        "name": "generate_validation_checklist",
        "description": (
            "Generate a validation checklist for an Earth Observation workflow. "
            "Shows which quality and correctness checks were performed and which were skipped. "
            "Useful for ensuring analysis completeness and identifying potential issues."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "tools_used": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tool names that were called during the analysis"
                },
                "process_graph": {
                    "type": "object",
                    "description": "The process graph to check for validation completeness"
                }
            },
            "required": ["tools_used"]
        }
    },
]


def _ensure_output_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path) or current_user_output_dir(), exist_ok=True)


def _default_output_path(prefix: str, ext: str) -> str:
    timestamp = int(time.time())
    return f"{current_user_output_dir()}/{prefix}_{timestamp}.{ext}"


def _annotate_process_graph(process_graph: Dict[str, Any]) -> str:
    """Generate an annotated description of each node in a process graph."""
    if not process_graph:
        return "No process graph provided."

    lines = []
    for i, (node_id, node) in enumerate(process_graph.items(), 1):
        process_id = node.get("process_id", "unknown")
        args = node.get("arguments", {})
        annotation = PROCESS_ANNOTATIONS.get(process_id, f"Executes the '{process_id}' process.")
        is_result = node.get("result", False)

        lines.append(f"### Step {i}: `{node_id}` ({process_id})")
        lines.append(f"")
        lines.append(f"{annotation}")
        lines.append(f"")

        if "id" in args:
            lines.append(f"- **Collection**: `{args['id']}`")
        if "spatial_extent" in args:
            ext = args["spatial_extent"]
            lines.append(f"- **Spatial Extent**: W={ext.get('west')}, S={ext.get('south')}, E={ext.get('east')}, N={ext.get('north')}")
        if "temporal_extent" in args:
            te = args["temporal_extent"]
            lines.append(f"- **Temporal Range**: {te[0]} to {te[1]}" if isinstance(te, list) and len(te) >= 2 else f"- **Temporal**: {te}")
        if "bands" in args:
            lines.append(f"- **Bands**: {', '.join(args['bands']) if isinstance(args['bands'], list) else args['bands']}")
        if "method" in args:
            lines.append(f"- **Method**: {args['method']}")
        if "format" in args:
            lines.append(f"- **Output Format**: {args['format']}")

        from_refs = []
        for k, v in args.items():
            if isinstance(v, dict) and "from_node" in v:
                from_refs.append(f"`{v['from_node']}`")
        if from_refs:
            lines.append(f"- **Input from**: {', '.join(from_refs)}")

        if is_result:
            lines.append(f"- **Result node** (final output)")

        lines.append("")

    return "\n".join(lines)


def _build_validation_checklist(tools_used: List[str], checks_passed: Optional[List[str]] = None) -> str:
    """Build a validation checklist showing passed/skipped checks."""
    lines = ["## Validation Checklist", ""]

    tool_set = set(tools_used or [])
    passed_set = set(checks_passed or [])

    categories = {}
    for check in VALIDATION_CHECKS:
        cat = check["category"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(check)

    total = len(VALIDATION_CHECKS)
    passed = 0

    for category, checks in categories.items():
        lines.append(f"### {category}")
        for check in checks:
            is_done = check["id"] in passed_set or check["tool"] in tool_set
            if is_done:
                passed += 1
            icon = "[x]" if is_done else "[ ]"
            status = "Done" if is_done else "Skipped"
            lines.append(f"- {icon} {check['description']} ({status})")
        lines.append("")

    score_pct = round(passed / total * 100) if total > 0 else 0
    lines.append(f"**Validation Score: {passed}/{total} checks passed ({score_pct}%)**")
    lines.append("")

    if score_pct < 50:
        lines.append("*Recommendation: Consider running additional validation tools before using results in production.*")
    elif score_pct < 80:
        lines.append("*Recommendation: Good coverage. Consider adding the skipped checks for maximum confidence.*")
    else:
        lines.append("*Excellent validation coverage. Results have been thoroughly checked.*")

    return "\n".join(lines)


def _build_workflow_markdown(
    title: str,
    objective: str,
    process_graph: Optional[Dict[str, Any]],
    collections_used: Optional[List[str]],
    spatial_extent: Optional[Dict[str, float]],
    temporal_extent: Optional[List[str]],
    results_summary: Optional[Dict[str, Any]],
    quality_info: Optional[Dict[str, Any]],
    tools_used: Optional[List[str]],
    checks_passed: Optional[List[str]],
) -> str:
    """Build the full workflow documentation as markdown."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    sections = []

    sections.append(f"# {title}")
    sections.append(f"")
    sections.append(f"*Generated by Jona AI - Earth Observation Assistant*")
    sections.append(f"*Date: {timestamp}*")
    sections.append(f"")
    sections.append(f"---")
    sections.append(f"")

    sections.append(f"## Research Objective")
    sections.append(f"")
    sections.append(f"{objective}")
    sections.append(f"")

    sections.append(f"## Reproducibility Parameters")
    sections.append(f"")
    if collections_used:
        sections.append(f"**Collections Used**: {', '.join(collections_used)}")
    if spatial_extent:
        sections.append(f"**Spatial Extent**:")
        sections.append(f"- West: {spatial_extent.get('west')}")
        sections.append(f"- South: {spatial_extent.get('south')}")
        sections.append(f"- East: {spatial_extent.get('east')}")
        sections.append(f"- North: {spatial_extent.get('north')}")
    if temporal_extent:
        sections.append(f"**Temporal Range**: {temporal_extent[0]} to {temporal_extent[1]}" if len(temporal_extent) >= 2 else f"**Temporal**: {temporal_extent}")
    sections.append(f"")

    if process_graph:
        sections.append(f"## Process Graph (Annotated)")
        sections.append(f"")
        sections.append(_annotate_process_graph(process_graph))
        sections.append(f"### Raw Process Graph (JSON)")
        sections.append(f"")
        sections.append(f"```json")
        sections.append(json.dumps(process_graph, indent=2))
        sections.append(f"```")
        sections.append(f"")

    if quality_info:
        sections.append(f"## Data Quality Assessment")
        sections.append(f"")
        for k, v in quality_info.items():
            sections.append(f"- **{k.replace('_', ' ').title()}**: {v}")
        sections.append(f"")

    if results_summary:
        sections.append(f"## Results Summary")
        sections.append(f"")
        if isinstance(results_summary, dict):
            for k, v in results_summary.items():
                if isinstance(v, dict):
                    sections.append(f"### {k.replace('_', ' ').title()}")
                    for sub_k, sub_v in v.items():
                        sections.append(f"- {sub_k}: {sub_v}")
                else:
                    sections.append(f"- **{k.replace('_', ' ').title()}**: {v}")
        sections.append(f"")

    if tools_used:
        sections.append(_build_validation_checklist(tools_used, checks_passed))
        sections.append(f"")

    if tools_used:
        sections.append(f"## Tools Used in This Analysis")
        sections.append(f"")
        for i, tool in enumerate(tools_used, 1):
            sections.append(f"{i}. `{tool}`")
        sections.append(f"")

    sections.append(f"---")
    sections.append(f"")
    sections.append(f"*This document was auto-generated by Jona AI for reproducibility and peer review.*")
    sections.append(f"*OpenEO process graphs can be re-executed on any compatible OpenEO backend.*")

    return "\n".join(sections)


def _markdown_to_html(markdown_text: str, title: str) -> str:
    """Convert markdown workflow doc to styled HTML."""
    import re

    html_body = markdown_text

    html_body = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html_body)
    html_body = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html_body)
    html_body = re.sub(r'`([^`]+)`', r'<code>\1</code>', html_body)
    html_body = re.sub(r'^- \[x\] (.+)$', r'<li class="check done">\1</li>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^- \[ \] (.+)$', r'<li class="check skip">\1</li>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^- (.+)$', r'<li>\1</li>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', html_body, flags=re.MULTILINE)
    html_body = re.sub(r'```json\n(.*?)```', r'<pre class="json">\1</pre>', html_body, flags=re.DOTALL)
    html_body = re.sub(r'^---$', r'<hr>', html_body, flags=re.MULTILINE)
    html_body = html_body.replace('\n\n', '</p><p>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f8f9fa; color: #2c3e50; line-height: 1.6; }}
    .container {{ max-width: 900px; margin: 0 auto; background: #fff; border-radius: 8px; padding: 40px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); }}
    h1 {{ color: #1a5276; border-bottom: 3px solid #2980b9; padding-bottom: 12px; }}
    h2 {{ color: #2c3e50; margin-top: 32px; border-bottom: 1px solid #eee; padding-bottom: 8px; }}
    h3 {{ color: #34495e; }}
    code {{ background: #f1f3f5; padding: 2px 6px; border-radius: 3px; font-size: 0.9em; color: #c0392b; }}
    pre.json {{ background: #2c3e50; color: #ecf0f1; padding: 16px; border-radius: 6px; overflow-x: auto; font-size: 13px; }}
    li {{ margin-bottom: 4px; }}
    li.check {{ list-style: none; }}
    li.check.done::before {{ content: "\\2713 "; color: #27ae60; font-weight: bold; }}
    li.check.skip::before {{ content: "\\2717 "; color: #e74c3c; font-weight: bold; }}
    hr {{ border: none; border-top: 2px solid #eee; margin: 30px 0; }}
    em {{ color: #7f8c8d; }}
    strong {{ color: #2c3e50; }}
</style>
</head>
<body>
<div class="container">
<p>{html_body}</p>
</div>
</body>
</html>"""


def create_documentation_tools(config=None) -> Dict[str, Any]:
    """Create documentation tools dict for Claude SDK.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _export_workflow_doc(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a comprehensive workflow document."""
        try:
            title = args["title"]
            objective = args["objective"]
            process_graph = args.get("process_graph")
            collections_used = args.get("collections_used")
            spatial_extent = args.get("spatial_extent")
            temporal_extent = args.get("temporal_extent")
            results_summary = args.get("results_summary")
            quality_info = args.get("quality_info")
            tools_used = args.get("tools_used")
            checks_passed = args.get("validation_checks_passed")
            output_format = args.get("output_format", "markdown")

            ext = "html" if output_format == "html" else "md"
            output_path = args.get("output_path") or _default_output_path("workflow_doc", ext)
            _ensure_output_dir(output_path)

            markdown_content = _build_workflow_markdown(
                title=title,
                objective=objective,
                process_graph=process_graph,
                collections_used=collections_used,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
                results_summary=results_summary,
                quality_info=quality_info,
                tools_used=tools_used,
                checks_passed=checks_passed,
            )

            if output_format == "html":
                content = _markdown_to_html(markdown_content, title)
            else:
                content = markdown_content

            with open(output_path, "w") as f:
                f.write(content)

            node_count = len(process_graph) if process_graph else 0
            tool_count = len(tools_used) if tools_used else 0

            checks_total = len(VALIDATION_CHECKS)
            if tools_used:
                tool_set = set(tools_used)
                passed_set = set(checks_passed or [])
                checks_done = sum(
                    1 for c in VALIDATION_CHECKS
                    if c["id"] in passed_set or c["tool"] in tool_set
                )
            else:
                checks_done = 0

            summary = (
                f"Workflow documentation saved to: {output_path}\n\n"
                f"Document Summary:\n"
                f"  Title:             {title}\n"
                f"  Format:            {output_format}\n"
                f"  Process graph:     {node_count} nodes\n"
                f"  Tools documented:  {tool_count}\n"
                f"  Validation score:  {checks_done}/{checks_total} checks passed\n"
                f"\nThis document is ready for sharing, archiving, or peer review."
            )

            return {
                "content": [{"type": "text", "text": summary}]
            }
        except Exception as e:
            logger.error(f"Documentation tool error: {e}", exc_info=True)
            return {
                "content": [{"type": "text", "text": f"Error generating workflow document: {str(e)}"}]
            }

    async def _generate_validation_checklist(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a validation checklist for a workflow."""
        try:
            tools_used = args.get("tools_used", [])
            process_graph = args.get("process_graph")

            checklist = _build_validation_checklist(tools_used)

            if process_graph:
                processes = [n.get("process_id") for n in process_graph.values() if "process_id" in n]
                checklist += "\n\n## Process Graph Contents\n\n"
                checklist += f"Processes in graph: {', '.join(processes)}\n"
                checklist += f"Total nodes: {len(process_graph)}\n"

                has_mask = "mask" in processes
                has_merge = "merge_cubes" in processes
                has_resample = "resample_cube_spatial" in processes

                if has_merge:
                    checklist += "\nMulti-sensor fusion detected (merge_cubes)."
                if has_resample:
                    checklist += "\nResolution alignment applied (resample_cube_spatial)."
                if has_mask:
                    checklist += "\nCloud/quality masking applied."
                if not has_mask:
                    checklist += "\n*Warning: No cloud masking detected. Consider adding mask process for optical data.*"

            return {
                "content": [{"type": "text", "text": checklist}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error generating checklist: {str(e)}"}]
            }

    return {
        "export_workflow_doc": _export_workflow_doc,
        "generate_validation_checklist": _generate_validation_checklist,
    }
