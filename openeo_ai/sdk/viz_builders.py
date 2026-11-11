# ABOUTME: Structured-text visualization builders: convert tool results into
# ABOUTME: chart/table/comparison/process-graph payloads for the frontend.

import json
from typing import Any, Callable, Dict, List, Optional


def _sniff_bash_payload(data: Any) -> Optional[str]:
    """Map a parsed Bash payload to the result tool whose output it embeds."""
    if not isinstance(data, dict):
        return None
    if "band_statistics" in data:
        return "result_inspect"
    if "zonal_stats" in data:
        return "result_zonal_stats"
    if "values" in data and "point" in data:
        return "result_extract_point"
    return None


def _to_process_graph_viz(graph: Dict[str, Any], title: str,
                          include_result: bool = False) -> Dict[str, Any]:
    """Build a process_graph visualization from raw graph nodes.

    include_result controls whether each node carries a "result" key — the
    graph-tools payload emits it, the fusion/content payloads do not, and the
    frontend contract depends on that difference.
    """
    nodes = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        entry = {
            "id": node_id,
            "process_id": node.get("process_id", ""),
            "arguments": node.get("arguments", {}),
        }
        if include_result:
            entry["result"] = node.get("result", False)
        nodes.append(entry)
    return {"type": "process_graph", "data": {"nodes": nodes, "edges": []}, "title": title}


def _to_comparison_viz(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Build a before/after comparison visualization from a comparison_slider spec."""
    before = spec.get("before", {})
    after = spec.get("after", {})
    colorbar = spec.get("colorbar", {})

    def _layer(side: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "raster",
            "url": side.get("url", ""),
            "bounds": side.get("bounds"),
            "colormap": colorbar.get("colormap", "viridis"),
            "opacity": 0.8,
            "vmin": colorbar.get("min"),
            "vmax": colorbar.get("max"),
            "source": side.get("source"),
        }

    return {
        "type": "comparison",
        "data": {
            "before": _layer(before),
            "after": _layer(after),
            "labels": {
                "before": before.get("label", "Before"),
                "after": after.get("label", "After"),
            },
        },
        "title": spec.get("title", "Comparison"),
    }



def _convert_chart_component(result: Any) -> Optional[Dict[str, Any]]:
    """Convert various chart result formats to the frontend ChartVisualization format.

    The frontend expects: {type, data: {chartType, title, xAxis: {label, values}, yAxis: {label}, series: [{name, values}]}, title}
    """
    if not isinstance(result, dict):
        return None

    spec = result.get("spec", {})
    if spec:
        chart_type = spec.get("chart_type", "line")
        title = spec.get("title", "Chart")
        data_block = spec.get("data", {})
        x_values = data_block.get("x", [])
        y_values = data_block.get("y", [])
        series_name = data_block.get("series_name", "Data")
        x_label = spec.get("xaxis", {}).get("title", "")
        y_label = spec.get("yaxis", {}).get("title", "")
        color = spec.get("style", {}).get("color")

        return {
            "type": "chart",
            "data": {
                "chartType": chart_type,
                "title": title,
                "xAxis": {"label": x_label, "values": x_values},
                "yAxis": {"label": y_label},
                "series": [{"name": series_name, "values": y_values, "color": color}],
            },
            "title": title,
        }

    if "chartType" in result and "series" in result:
        return {
            "type": "chart",
            "data": result,
            "title": result.get("title", "Chart"),
        }

    return None


def _make_table(headers: List[str], rows: List[list], title: str) -> Dict[str, Any]:
    return {
        "type": "table",
        "data": {"headers": headers, "rows": rows},
        "title": title,
    }


def _make_chart(chart_type: str, title: str, x_label: str, x_values: list,
                y_label: str, series: list) -> Dict[str, Any]:
    return {
        "type": "chart",
        "data": {
            "chartType": chart_type,
            "title": title,
            "xAxis": {"label": x_label, "values": x_values},
            "yAxis": {"label": y_label},
            "series": series,
        },
        "title": title,
    }


def _build_band_statistics(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for result_inspect tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(result, dict) and "band_statistics" in result:
        band_stats = result["band_statistics"]
    elif isinstance(parsed_json, dict) and "band_statistics" in parsed_json:
        band_stats = parsed_json["band_statistics"]
    else:
        band_stats = None

    if band_stats and isinstance(band_stats, list) and len(band_stats) > 0:
        stat_keys = ["min", "max", "mean", "std", "median"]
        headers = ["Band", "Name"] + [s.capitalize() for s in stat_keys]
        rows = []
        for bs in band_stats:
            row = [bs.get("band", ""), bs.get("name", "")]
            for s in stat_keys:
                val = bs.get(s)
                row.append(f"{val:.4f}" if isinstance(val, (int, float)) and val is not None else "N/A")
            rows.append(row)
        vizs.append(_make_table(headers, rows, "Band Statistics"))

        band_names = [bs.get("name", f"Band {bs.get('band', '?')}") for bs in band_stats]
        means = [bs.get("mean", 0) or 0 for bs in band_stats]
        if any(m != 0 for m in means):
            vizs.append(_make_chart(
                "bar", "Band Statistics Overview",
                "Band", band_names,
                "Mean Value",
                [{"name": "Mean", "values": means, "color": "#4CAF50"}],
            ))
    return vizs


def _build_zonal_stats(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for result_zonal_stats tool results."""
    vizs: List[Dict[str, Any]] = []
    zs_data = None
    if isinstance(result, dict) and "zonal_stats" in result:
        zs_data = result["zonal_stats"]
    elif isinstance(parsed_json, dict) and "zonal_stats" in parsed_json:
        zs_data = parsed_json["zonal_stats"]
    if zs_data is not None:
        stats = zs_data
        if isinstance(stats, list) and len(stats) > 0:
            stat_keys = [k for k in stats[0].keys() if k not in ("band", "name")]
            headers = ["Band", "Name"] + [s.capitalize() for s in stat_keys]
            rows = []
            for s in stats:
                row = [s.get("band", ""), s.get("name", "")]
                for k in stat_keys:
                    val = s.get(k)
                    row.append(f"{val:.4f}" if isinstance(val, (int, float)) and val is not None else "N/A")
                rows.append(row)
            vizs.append(_make_table(headers, rows, "Zonal Statistics"))
    return vizs


def _build_point_values(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for result_extract_point tool results."""
    vizs: List[Dict[str, Any]] = []
    ep_data = None
    if isinstance(result, dict) and "values" in result:
        ep_data = result
    elif isinstance(parsed_json, dict) and "values" in parsed_json:
        ep_data = parsed_json
    if ep_data is not None:
        values = ep_data["values"]
        point = ep_data.get("point", {})
        if isinstance(values, list) and len(values) > 0:
            headers = ["Band", "Name", "Value"]
            rows = []
            for v in values:
                val = v.get("value")
                rows.append([
                    v.get("band", ""),
                    v.get("name", ""),
                    f"{val:.4f}" if isinstance(val, (int, float)) and val is not None else "NoData"
                ])
            lat = point.get("latitude", "?")
            lon = point.get("longitude", "?")
            vizs.append(_make_table(headers, rows, f"Point Values ({lat}, {lon})"))

            band_names = [v.get("name", f"Band {v.get('band', '?')}") for v in values]
            band_values = [v.get("value", 0) or 0 for v in values]
            if any(bv != 0 for bv in band_values):
                vizs.append(_make_chart(
                    "bar", f"Band Values at ({lat}, {lon})",
                    "Band", band_names,
                    "Value",
                    [{"name": "Value", "values": band_values, "color": "#2196F3"}],
                ))
    return vizs


def _build_index_search(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for spectral_index_search tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(parsed_json, dict) and "results" in parsed_json:
        indices = parsed_json["results"]
        if isinstance(indices, list) and len(indices) > 0:
            headers = ["Key", "Name", "Category", "Bands Required"]
            rows = []
            for idx in indices:
                rows.append([
                    idx.get("key", ""),
                    idx.get("name", ""),
                    idx.get("category", ""),
                    ", ".join(idx.get("required_bands", [])) if isinstance(idx.get("required_bands"), list) else str(idx.get("required_bands", "")),
                ])
            vizs.append(_make_table(headers, rows, "Spectral Index Search Results"))
    return vizs


def _build_interpretation(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for interpret_result / generate_report tool results."""
    vizs: List[Dict[str, Any]] = []
    # Extract interpretation category breakdown as a table
    if isinstance(parsed_json, dict):
        interp = parsed_json.get("interpretation", {})
        breakdown = interp.get("category_breakdown", [])
        if isinstance(breakdown, list) and len(breakdown) > 0:
            headers = ["Category", "Range", "Est. Area", "Assessment"]
            rows = []
            for cat in breakdown:
                pct = f"~{cat.get('est_area_pct', 'N/A')}%" if cat.get("est_area_pct") is not None else "N/A"
                rows.append([
                    cat.get("category", ""),
                    cat.get("range", ""),
                    pct,
                    cat.get("assessment", ""),
                ])
            idx_name = interp.get("index", "")
            grade = interp.get("grade", "")
            score = interp.get("grade_score", "")
            vizs.append(_make_table(
                headers, rows,
                f"{idx_name} Interpretation (Grade: {grade}, {score}/100)"
            ))

        # Also extract quality as a compact info table
        quality = parsed_json.get("quality", {})
        if quality:
            q_headers = ["Metric", "Value"]
            q_rows = [
                ["Grade", quality.get("grade", "?")],
                ["Score", str(quality.get("score", "?"))],
            ]
            for flag in quality.get("flags", [])[:3]:
                q_rows.append(["Issue", flag])
            vizs.append(_make_table(q_headers, q_rows, "Data Quality Assessment"))
    return vizs


def _build_collection_comparison(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for compare_collections tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(parsed_json, dict) and "comparison" in parsed_json:
        comp = parsed_json["comparison"]
        if isinstance(comp, list) and len(comp) > 0:
            headers = list(comp[0].keys())
            rows = [[str(c.get(h, "")) for h in headers] for c in comp]
            vizs.append(_make_table(headers, rows, "Collection Comparison"))
    return vizs


def _build_collection_bands(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for explain_collection_bands tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(parsed_json, dict):
        bands = parsed_json.get("bands", [])
        if isinstance(bands, list) and len(bands) > 0:
            headers = ["Name", "Common Name", "Wavelength", "Resolution", "Description"]
            rows = []
            for b in bands:
                rows.append([
                    b.get("name", ""),
                    b.get("common_name", ""),
                    str(b.get("center_wavelength", b.get("wavelength", ""))),
                    str(b.get("gsd", b.get("resolution", ""))),
                    b.get("description", "")[:60],
                ])
            coll_name = parsed_json.get("collection_id", "Collection")
            vizs.append(_make_table(headers, rows, f"{coll_name} - Bands"))
    return vizs


def _build_recommendations(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for recommend_collection tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(parsed_json, dict):
        recommendations = parsed_json.get("recommendations", [])
        if isinstance(recommendations, list) and len(recommendations) > 0:
            headers = ["Collection", "Relevance", "Resolution", "Revisit"]
            rows = []
            for r in recommendations:
                rows.append([
                    r.get("collection_id", r.get("name", "")),
                    r.get("relevance", r.get("score", "")),
                    r.get("resolution", ""),
                    r.get("revisit_time", r.get("revisit", "")),
                ])
            vizs.append(_make_table(headers, rows, "Recommended Collections"))
    return vizs


def _build_fusion(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for fusion_multi_sensor tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(parsed_json, dict) and "process_graph" in parsed_json:
        pg = parsed_json["process_graph"]
        if isinstance(pg, dict) and len(pg) > 0:
            vizs.append(_to_process_graph_viz(pg, "Fusion Process Graph"))
        metadata = parsed_json.get("metadata", {})
        if metadata:
            headers = ["Parameter", "Value"]
            rows = [
                ["Primary Collection", metadata.get("primary_collection", "")],
                ["Secondary Collection", metadata.get("secondary_collection", "")],
                ["Resampled", "Yes" if metadata.get("resampled") else "No"],
                ["Output Format", metadata.get("output_format", "")],
                ["Graph Nodes", str(metadata.get("node_count", ""))],
                ["Processes Used", ", ".join(metadata.get("processes_used", []))],
            ]
            vizs.append(_make_table(headers, rows, "Fusion Parameters"))
    return vizs


def _build_raster_statistics(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for raster_statistics tool results."""
    vizs: List[Dict[str, Any]] = []
    data_src = parsed_json if isinstance(parsed_json, dict) else (result if isinstance(result, dict) else {})
    stats = data_src.get("statistics", {})
    headers = ["Metric", "Value"]
    rows = []
    for key in ["min", "max", "mean", "median", "std", "p5", "p25", "p75", "p95"]:
        if key in stats:
            rows.append([key.upper(), f"{stats[key]:.4f}" if isinstance(stats[key], (int, float)) else str(stats[key])])
    # Add pixel/area info
    for key in ["valid_pixels", "total_pixels", "coverage_pct", "valid_area_ha", "total_area_ha"]:
        if key in data_src:
            rows.append([key.replace("_", " ").title(), str(data_src[key])])
    if rows:
        vizs.append({"type": "table", "data": {"headers": headers, "rows": rows}, "title": "Raster Statistics"})
    return vizs


def _build_classification(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for threshold_classify tool results."""
    vizs: List[Dict[str, Any]] = []
    data_src = parsed_json if isinstance(parsed_json, dict) else (result if isinstance(result, dict) else {})
    classes = data_src.get("classification", [])
    if classes:
        headers = ["Class", "Pixels", "Area (ha)", "Percentage"]
        rows = [[c.get("label", ""), str(c.get("pixel_count", "")), f"{c.get('area_ha', 0):.1f}", f"{c.get('percentage', 0):.1f}%"] for c in classes]
        vizs.append({"type": "table", "data": {"headers": headers, "rows": rows}, "title": "Classification Results"})
    return vizs


def _build_collections_list(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for openeo_list_collections tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(result, dict) and "collections" in result:
        collections = result["collections"]
    elif isinstance(parsed_json, dict) and "collections" in parsed_json:
        collections = parsed_json["collections"]
    elif isinstance(result, list):
        collections = result
    elif isinstance(parsed_json, list):
        collections = parsed_json
    else:
        collections = None

    if collections and isinstance(collections, list) and len(collections) > 0:
        headers = ["ID", "Title", "Description"]
        rows = []
        for c in collections:
            if isinstance(c, dict):
                rows.append([
                    c.get("id", ""),
                    c.get("title", "")[:40],
                    (c.get("description", "") or "")[:60],
                ])
            elif isinstance(c, str):
                rows.append([c, "", ""])
        if rows:
            vizs.append(_make_table(headers, rows, "Available Collections"))
    return vizs


def _build_jobs_list(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Builder for openeo_list_jobs tool results."""
    vizs: List[Dict[str, Any]] = []
    if isinstance(result, dict) and "jobs" in result:
        jobs = result["jobs"]
    elif isinstance(parsed_json, dict) and "jobs" in parsed_json:
        jobs = parsed_json["jobs"]
    elif isinstance(result, list):
        jobs = result
    elif isinstance(parsed_json, list):
        jobs = parsed_json
    else:
        jobs = None

    if jobs and isinstance(jobs, list) and len(jobs) > 0:
        headers = ["Job ID", "Status", "Title", "Created"]
        rows = []
        for j in jobs:
            if isinstance(j, dict):
                rows.append([
                    j.get("id", ""),
                    j.get("status", ""),
                    j.get("title", "")[:30],
                    j.get("created", "")[:19],
                ])
        if rows:
            vizs.append(_make_table(headers, rows, "Jobs"))
    return vizs


def _build_no_viz(result: Any, parsed_json: Any) -> List[Dict[str, Any]]:
    """Tools that must not fall through to the payload-shape fallbacks."""
    return []


_STRUCTURED_TEXT_BUILDERS: Dict[str, Callable[[Any, Any], List[Dict[str, Any]]]] = {
    "result_inspect": _build_band_statistics,
    "result_zonal_stats": _build_zonal_stats,
    "result_extract_point": _build_point_values,
    "spectral_index_search": _build_index_search,
    "interpret_result": _build_interpretation,
    "generate_report": _build_interpretation,
    "compare_collections": _build_collection_comparison,
    "explain_collection_bands": _build_collection_bands,
    "recommend_collection": _build_recommendations,
    "fusion_multi_sensor": _build_fusion,
    "fusion_list_presets": _build_no_viz,
    "raster_statistics": _build_raster_statistics,
    "threshold_classify": _build_classification,
    "openeo_list_collections": _build_collections_list,
    "openeo_list_jobs": _build_jobs_list,
}


def _extract_from_structured_text(tool_name: str, result: Any) -> List[Dict[str, Any]]:
    """Auto-generate table/chart visualizations from tools that return structured JSON in text content."""
    vizs: List[Dict[str, Any]] = []
    if not isinstance(result, dict):
        return vizs

    content_items = result.get("content", [])
    if not isinstance(content_items, list):
        content_items = []

    parsed_json = None
    for item in content_items:
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text", "")
            if text.startswith("{") or text.startswith("["):
                try:
                    parsed_json = json.loads(text)
                    break
                except (json.JSONDecodeError, ValueError):
                    pass

    effective_tool = tool_name
    if tool_name == "Bash":
        data = parsed_json if parsed_json else result
        sniffed = _sniff_bash_payload(data)
        if sniffed:
            effective_tool = sniffed
        elif isinstance(data, dict):
            text_output = data.get("output", data.get("stdout", data.get("content", "")))
            if isinstance(text_output, str) and text_output.strip():
                for line in text_output.strip().split("\n"):
                    line = line.strip()
                    if line.startswith("{") or line.startswith("["):
                        try:
                            embedded = json.loads(line)
                        except (json.JSONDecodeError, TypeError, ValueError):
                            continue
                        sniffed = _sniff_bash_payload(embedded)
                        if sniffed:
                            effective_tool = sniffed
                            parsed_json = embedded
                            break
        if effective_tool == tool_name and isinstance(data, str):
            try:
                embedded = json.loads(data)
            except (json.JSONDecodeError, TypeError, ValueError):
                embedded = None
            sniffed = _sniff_bash_payload(embedded)
            if sniffed:
                effective_tool = sniffed
                parsed_json = embedded
        if effective_tool != tool_name:
            print(f"[extract_visualization] Bash output detected as {effective_tool}")

    builder = _STRUCTURED_TEXT_BUILDERS.get(effective_tool)
    if builder is not None:
        vizs.extend(builder(result, parsed_json))
        return vizs

    # Payload-shape fallbacks — ordered checks after a registry miss, kept
    # out of the registry on purpose: they key on result shape, not tool name.
    if (isinstance(parsed_json, dict) and "statistics" in parsed_json
            and effective_tool not in ("result_inspect", "result_zonal_stats", "result_extract_point")):
        vizs.extend(_build_raster_statistics(result, parsed_json))
    elif (isinstance(parsed_json, dict) and "classification" in parsed_json
            and effective_tool not in ("result_inspect", "result_zonal_stats", "result_extract_point")):
        vizs.extend(_build_classification(result, parsed_json))

    return vizs
