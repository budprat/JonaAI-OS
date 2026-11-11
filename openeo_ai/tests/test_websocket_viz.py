# ABOUTME: Tests for WebSocket visualization extraction pipeline.
# Validates colormap detection, chart conversion, table helpers, structured text
# extraction, and the main extract_visualization() function across all tool types.

"""
Tests for the visualization extraction module (viz_extractor.py).

Covers:
- Colormap auto-detection from job title / description
- Chart component conversion to frontend format
- Table and chart helper builders
- Structured text extraction for result_inspect, zonal_stats, etc.
- Main extract_visualization() function for map, chart, comparison, process_graph,
  multi_panel, dashboard, timelapse, and no-viz tool exclusions
"""

import json
import pytest

from openeo_ai.sdk.viz_extractor import (
    _detect_colormap_from_title,
    _convert_chart_component,
    _make_table,
    _make_chart,
    _extract_from_structured_text,
    extract_visualization,
    INDEX_COLORMAP_MAP,
)


# ============================================================================
# Colormap Detection Tests
# ============================================================================

class TestColormapDetection:
    """Test _detect_colormap_from_title for index-based colormap selection."""

    def test_ndvi_detects_ndvi_colormap(self):
        """NDVI title should detect ndvi colormap."""
        assert _detect_colormap_from_title("NDVI analysis") == "ndvi"

    def test_ndvi_case_insensitive(self):
        """Detection is case-insensitive."""
        assert _detect_colormap_from_title("ndvi analysis") == "ndvi"
        assert _detect_colormap_from_title("Ndvi Analysis") == "ndvi"

    def test_evi_detects_ndvi_colormap(self):
        """EVI uses same colormap family as NDVI (vegetation)."""
        assert _detect_colormap_from_title("EVI vegetation index") == "ndvi"

    def test_savi_detects_ndvi_colormap(self):
        """SAVI vegetation index uses ndvi colormap."""
        assert _detect_colormap_from_title("SAVI analysis") == "ndvi"

    def test_gndvi_detects_ndvi_colormap(self):
        """GNDVI is detected before NDVI (longer name first)."""
        assert _detect_colormap_from_title("GNDVI analysis") == "ndvi"

    def test_ndwi_detects_coolwarm(self):
        """NDWI water index should detect coolwarm colormap."""
        assert _detect_colormap_from_title("NDWI water index") == "coolwarm"

    def test_mndwi_detects_coolwarm(self):
        """MNDWI should detect coolwarm, not viridis."""
        result = _detect_colormap_from_title("MNDWI flood detection")
        assert result == "coolwarm"

    def test_ndbi_detects_coolwarm(self):
        """NDBI built-up index uses coolwarm."""
        assert _detect_colormap_from_title("NDBI urban analysis") == "coolwarm"

    def test_ndsi_detects_coolwarm(self):
        """NDSI snow index uses coolwarm."""
        assert _detect_colormap_from_title("NDSI snow cover") == "coolwarm"

    def test_nbr_detects_inferno(self):
        """NBR burn ratio uses inferno."""
        assert _detect_colormap_from_title("NBR fire analysis") == "inferno"

    def test_dnbr_detects_inferno(self):
        """dNBR differenced burn ratio uses inferno."""
        assert _detect_colormap_from_title("dNBR burn severity") == "inferno"

    def test_bsi_detects_plasma(self):
        """BSI bare soil index uses plasma."""
        assert _detect_colormap_from_title("BSI bare soil mapping") == "plasma"

    def test_dem_detects_terrain(self):
        """DEM elevation data uses terrain."""
        assert _detect_colormap_from_title("DEM elevation model") == "terrain"

    def test_elevation_detects_terrain(self):
        """Elevation keyword uses terrain."""
        assert _detect_colormap_from_title("Elevation analysis") == "terrain"

    def test_change_detects_coolwarm(self):
        """Change detection uses coolwarm."""
        assert _detect_colormap_from_title("Land cover change analysis") == "coolwarm"

    def test_unknown_returns_viridis(self):
        """Unknown title should return viridis default."""
        assert _detect_colormap_from_title("Random analysis") == "viridis"

    def test_empty_string_returns_viridis(self):
        """Empty string returns viridis."""
        assert _detect_colormap_from_title("") == "viridis"

    def test_none_returns_viridis(self):
        """None input returns viridis."""
        assert _detect_colormap_from_title(None) == "viridis"

    def test_longer_name_checked_before_shorter(self):
        """GNDVI (5 chars) must match before NDVI (4 chars) to avoid substring collision."""
        result = _detect_colormap_from_title("gndvi vegetation health")
        assert result == "ndvi"

    def test_ndci_detects_viridis(self):
        """NDCI chlorophyll index uses viridis."""
        assert _detect_colormap_from_title("NDCI algal bloom") == "viridis"

    def test_burn_severity_phrase(self):
        """Phrase 'burn severity' maps to inferno."""
        assert _detect_colormap_from_title("Post-fire burn severity assessment") == "inferno"

    def test_vegetation_index_phrase(self):
        """Phrase 'vegetation index' maps to ndvi colormap."""
        assert _detect_colormap_from_title("Custom vegetation index") == "ndvi"

    def test_water_index_phrase(self):
        """Phrase 'water index' maps to coolwarm."""
        assert _detect_colormap_from_title("Surface water index") == "coolwarm"

    def test_mining_detects_plasma(self):
        """Mining-related title uses plasma."""
        assert _detect_colormap_from_title("Open pit mining detection") == "plasma"

    def test_ndre_detects_ndvi_colormap(self):
        """NDRE red edge index uses ndvi colormap."""
        assert _detect_colormap_from_title("NDRE red edge") == "ndvi"

    def test_altitude_detects_terrain(self):
        """Altitude keyword uses terrain colormap."""
        assert _detect_colormap_from_title("High altitude mapping") == "terrain"

    def test_height_map_detects_terrain(self):
        """Height map phrase uses terrain."""
        assert _detect_colormap_from_title("Generate height map") == "terrain"

    def test_wetland_detects_coolwarm(self):
        """Wetland keyword uses coolwarm."""
        assert _detect_colormap_from_title("Wetland delineation") == "coolwarm"

    def test_bare_soil_detects_plasma(self):
        """Bare soil phrase uses plasma."""
        assert _detect_colormap_from_title("Bare soil exposure") == "plasma"


# ============================================================================
# Chart Conversion Tests
# ============================================================================

class TestConvertChartComponent:
    """Test _convert_chart_component for chart format conversion."""

    def test_spec_based_chart(self):
        """Chart with spec dict is converted to frontend format."""
        result_input = {
            "spec": {
                "chart_type": "line",
                "title": "NDVI Time Series",
                "data": {
                    "x": ["2024-01", "2024-02", "2024-03"],
                    "y": [0.3, 0.5, 0.7],
                    "series_name": "NDVI",
                },
                "xaxis": {"title": "Date"},
                "yaxis": {"title": "NDVI Value"},
                "style": {"color": "#4CAF50"},
            }
        }
        result = _convert_chart_component(result_input)

        assert result is not None
        assert result["type"] == "chart"
        assert result["data"]["chartType"] == "line"
        assert result["data"]["title"] == "NDVI Time Series"
        assert result["data"]["xAxis"]["label"] == "Date"
        assert result["data"]["xAxis"]["values"] == ["2024-01", "2024-02", "2024-03"]
        assert result["data"]["yAxis"]["label"] == "NDVI Value"
        assert len(result["data"]["series"]) == 1
        assert result["data"]["series"][0]["name"] == "NDVI"
        assert result["data"]["series"][0]["values"] == [0.3, 0.5, 0.7]
        assert result["data"]["series"][0]["color"] == "#4CAF50"
        assert result["title"] == "NDVI Time Series"

    def test_direct_chart_format(self):
        """Chart with chartType and series keys is passed through."""
        result_input = {
            "chartType": "bar",
            "title": "Band Values",
            "series": [{"name": "Data", "values": [1, 2, 3]}],
        }
        result = _convert_chart_component(result_input)

        assert result is not None
        assert result["type"] == "chart"
        assert result["data"] == result_input
        assert result["title"] == "Band Values"

    def test_non_dict_returns_none(self):
        """Non-dict input returns None."""
        assert _convert_chart_component("not a dict") is None
        assert _convert_chart_component(42) is None
        assert _convert_chart_component(None) is None
        assert _convert_chart_component([]) is None

    def test_empty_dict_returns_none(self):
        """Empty dict returns None (no spec, no chartType)."""
        assert _convert_chart_component({}) is None

    def test_spec_with_defaults(self):
        """Spec with minimal fields uses defaults."""
        result_input = {
            "spec": {
                "data": {"x": [1], "y": [2]},
            }
        }
        result = _convert_chart_component(result_input)

        assert result is not None
        assert result["data"]["chartType"] == "line"
        assert result["data"]["title"] == "Chart"
        assert result["data"]["series"][0]["name"] == "Data"

    def test_spec_without_style_color(self):
        """Spec without style.color produces None color in series."""
        result_input = {
            "spec": {
                "chart_type": "bar",
                "title": "No Color",
                "data": {"x": [1], "y": [2], "series_name": "S"},
                "xaxis": {"title": "X"},
                "yaxis": {"title": "Y"},
            }
        }
        result = _convert_chart_component(result_input)

        assert result is not None
        assert result["data"]["series"][0]["color"] is None


# ============================================================================
# Table and Chart Helper Tests
# ============================================================================

class TestMakeTable:
    """Test _make_table helper for building table visualizations."""

    def test_basic_table(self):
        """Creates table with correct structure."""
        headers = ["Band", "Min", "Max"]
        rows = [["red", "0.0", "1.0"], ["nir", "0.1", "0.9"]]
        result = _make_table(headers, rows, "Band Stats")

        assert result["type"] == "table"
        assert result["data"]["headers"] == headers
        assert result["data"]["rows"] == rows
        assert result["title"] == "Band Stats"

    def test_empty_table(self):
        """Empty rows/headers still creates valid structure."""
        result = _make_table([], [], "Empty")
        assert result["type"] == "table"
        assert result["data"]["headers"] == []
        assert result["data"]["rows"] == []

    def test_single_row_table(self):
        """Single row table works correctly."""
        result = _make_table(["A"], [["value"]], "Single")
        assert len(result["data"]["rows"]) == 1
        assert result["data"]["rows"][0] == ["value"]


class TestMakeChart:
    """Test _make_chart helper for building chart visualizations."""

    def test_basic_chart(self):
        """Creates chart with correct structure."""
        series = [{"name": "NDVI", "values": [0.3, 0.5, 0.7], "color": "#4CAF50"}]
        result = _make_chart("bar", "NDVI Stats", "Band", ["red", "nir", "swir"],
                             "Value", series)

        assert result["type"] == "chart"
        assert result["data"]["chartType"] == "bar"
        assert result["data"]["title"] == "NDVI Stats"
        assert result["data"]["xAxis"]["label"] == "Band"
        assert result["data"]["xAxis"]["values"] == ["red", "nir", "swir"]
        assert result["data"]["yAxis"]["label"] == "Value"
        assert result["data"]["series"] == series
        assert result["title"] == "NDVI Stats"

    def test_line_chart(self):
        """Creates line chart."""
        series = [{"name": "Trend", "values": [1, 2, 3]}]
        result = _make_chart("line", "Trend", "Time", ["Jan", "Feb", "Mar"],
                             "Value", series)
        assert result["data"]["chartType"] == "line"

    def test_multiple_series(self):
        """Chart supports multiple series."""
        series = [
            {"name": "A", "values": [1, 2]},
            {"name": "B", "values": [3, 4]},
        ]
        result = _make_chart("bar", "Multi", "X", [1, 2], "Y", series)
        assert len(result["data"]["series"]) == 2


# ============================================================================
# Structured Text Extraction Tests
# ============================================================================

class TestExtractFromStructuredText:
    """Test _extract_from_structured_text for auto-generating visualizations."""

    def test_result_inspect_generates_table_and_chart(self):
        """result_inspect with band_statistics produces table + bar chart."""
        result = {
            "band_statistics": [
                {"band": 1, "name": "red", "min": 0.0, "max": 1.0, "mean": 0.45, "std": 0.12, "median": 0.44},
                {"band": 2, "name": "nir", "min": 0.05, "max": 0.95, "mean": 0.55, "std": 0.15, "median": 0.53},
            ]
        }
        vizs = _extract_from_structured_text("result_inspect", result)

        assert len(vizs) == 2
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Band Statistics"
        assert "Band" in vizs[0]["data"]["headers"]
        assert len(vizs[0]["data"]["rows"]) == 2
        assert vizs[1]["type"] == "chart"
        assert vizs[1]["data"]["chartType"] == "bar"

    def test_result_inspect_zero_means_no_chart(self):
        """result_inspect with all-zero means skips chart."""
        result = {
            "band_statistics": [
                {"band": 1, "name": "empty", "min": 0, "max": 0, "mean": 0, "std": 0, "median": 0},
            ]
        }
        vizs = _extract_from_structured_text("result_inspect", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"

    def test_zonal_stats_generates_table(self):
        """result_zonal_stats with zonal_stats produces table."""
        result = {
            "zonal_stats": [
                {"band": 1, "name": "ndvi", "min": 0.1, "max": 0.8, "mean": 0.45, "std": 0.1},
            ]
        }
        vizs = _extract_from_structured_text("result_zonal_stats", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Zonal Statistics"

    def test_extract_point_generates_table_and_chart(self):
        """result_extract_point with values produces table + bar chart."""
        result = {
            "values": [
                {"band": 1, "name": "red", "value": 0.35},
                {"band": 2, "name": "nir", "value": 0.72},
            ],
            "point": {"latitude": 46.0, "longitude": 11.0},
        }
        vizs = _extract_from_structured_text("result_extract_point", result)

        assert len(vizs) == 2
        assert vizs[0]["type"] == "table"
        assert "46.0" in vizs[0]["title"]
        assert "11.0" in vizs[0]["title"]
        assert vizs[1]["type"] == "chart"

    def test_bash_tool_detects_band_statistics(self):
        """Bash tool output containing band_statistics is detected as result_inspect."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "band_statistics": [
                        {"band": 1, "name": "ndvi", "min": -0.1, "max": 0.9, "mean": 0.4, "std": 0.2, "median": 0.35},
                    ]
                })}
            ]
        }
        vizs = _extract_from_structured_text("Bash", result)

        assert len(vizs) >= 1
        assert any(v["type"] == "table" for v in vizs)

    def test_bash_tool_detects_zonal_stats(self):
        """Bash tool output with zonal_stats is detected."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "zonal_stats": [
                        {"band": 1, "name": "ndvi", "min": 0.1, "max": 0.8, "mean": 0.5},
                    ]
                })}
            ]
        }
        vizs = _extract_from_structured_text("Bash", result)

        assert len(vizs) >= 1
        assert any(v["title"] == "Zonal Statistics" for v in vizs)

    def test_bash_tool_detects_extract_point(self):
        """Bash tool output with values+point is detected as extract_point."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "values": [{"band": 1, "name": "red", "value": 0.5}],
                    "point": {"latitude": 46.0, "longitude": 11.0},
                })}
            ]
        }
        vizs = _extract_from_structured_text("Bash", result)

        assert len(vizs) >= 1
        assert any(v["type"] == "table" for v in vizs)

    def test_unknown_tool_empty_result(self):
        """Unknown tool with no structured data returns empty list."""
        vizs = _extract_from_structured_text("some_unknown_tool", {"key": "value"})
        assert vizs == []

    def test_non_dict_returns_empty(self):
        """Non-dict input returns empty list."""
        assert _extract_from_structured_text("result_inspect", "not a dict") == []
        assert _extract_from_structured_text("result_inspect", None) == []

    def test_spectral_index_search_generates_table(self):
        """spectral_index_search with results produces table."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "results": [
                        {"key": "NDVI", "name": "Normalized Difference Vegetation Index",
                         "category": "vegetation", "required_bands": ["red", "nir"]},
                        {"key": "NDWI", "name": "Normalized Difference Water Index",
                         "category": "water", "required_bands": ["green", "nir"]},
                    ]
                })}
            ]
        }
        vizs = _extract_from_structured_text("spectral_index_search", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Spectral Index Search Results"

    def test_openeo_list_collections_generates_table(self):
        """openeo_list_collections with collections list produces table."""
        result = {
            "collections": [
                {"id": "sentinel-2-l2a", "title": "Sentinel-2 L2A", "description": "Surface reflectance"},
                {"id": "landsat-c2-l2", "title": "Landsat C2 L2", "description": "Surface reflectance"},
            ]
        }
        vizs = _extract_from_structured_text("openeo_list_collections", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Available Collections"

    def test_openeo_list_collections_with_string_items(self):
        """openeo_list_collections with string collection items produces table."""
        result = {"collections": ["sentinel-2-l2a", "landsat-c2-l2"]}
        vizs = _extract_from_structured_text("openeo_list_collections", result)

        assert len(vizs) == 1
        assert vizs[0]["data"]["rows"][0][0] == "sentinel-2-l2a"

    def test_openeo_list_jobs_generates_table(self):
        """openeo_list_jobs with jobs list produces table."""
        result = {
            "jobs": [
                {"id": "job-1", "status": "finished", "title": "NDVI analysis", "created": "2024-06-01T10:00:00Z"},
            ]
        }
        vizs = _extract_from_structured_text("openeo_list_jobs", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Jobs"

    def test_interpret_result_category_breakdown(self):
        """interpret_result with category_breakdown produces table."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "interpretation": {
                        "index": "NDVI",
                        "grade": "B+",
                        "grade_score": 85,
                        "category_breakdown": [
                            {"category": "Healthy", "range": "0.6-0.9", "est_area_pct": 45, "assessment": "Good"},
                            {"category": "Sparse", "range": "0.2-0.4", "est_area_pct": 30, "assessment": "Fair"},
                        ]
                    },
                    "quality": {"grade": "A", "score": 90, "flags": ["Low cloud cover"]},
                })}
            ]
        }
        vizs = _extract_from_structured_text("interpret_result", result)

        assert len(vizs) >= 1
        interp_tables = [v for v in vizs if "Interpretation" in v["title"]]
        assert len(interp_tables) == 1
        assert "NDVI" in interp_tables[0]["title"]
        assert "B+" in interp_tables[0]["title"]

    def test_compare_collections_generates_table(self):
        """compare_collections with comparison data produces table."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "comparison": [
                        {"feature": "Resolution", "sentinel-2": "10m", "landsat-8": "30m"},
                        {"feature": "Revisit", "sentinel-2": "5 days", "landsat-8": "16 days"},
                    ]
                })}
            ]
        }
        vizs = _extract_from_structured_text("compare_collections", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Collection Comparison"

    def test_explain_collection_bands_generates_table(self):
        """explain_collection_bands with bands data produces table."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "collection_id": "sentinel-2-l2a",
                    "bands": [
                        {"name": "B04", "common_name": "red", "center_wavelength": 0.665,
                         "gsd": 10, "description": "Red band for vegetation"},
                        {"name": "B08", "common_name": "nir", "center_wavelength": 0.842,
                         "gsd": 10, "description": "Near infrared band"},
                    ]
                })}
            ]
        }
        vizs = _extract_from_structured_text("explain_collection_bands", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert "sentinel-2-l2a" in vizs[0]["title"]

    def test_recommend_collection_generates_table(self):
        """recommend_collection with recommendations produces table."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "recommendations": [
                        {"collection_id": "sentinel-2-l2a", "relevance": "High",
                         "resolution": "10m", "revisit_time": "5 days"},
                    ]
                })}
            ]
        }
        vizs = _extract_from_structured_text("recommend_collection", result)

        assert len(vizs) == 1
        assert vizs[0]["type"] == "table"
        assert vizs[0]["title"] == "Recommended Collections"

    def test_band_statistics_formatting(self):
        """Band statistics values are formatted to 4 decimal places."""
        result = {
            "band_statistics": [
                {"band": 1, "name": "red", "min": 0.123456789, "max": 0.987654321,
                 "mean": 0.555555555, "std": 0.111111111, "median": 0.444444444},
            ]
        }
        vizs = _extract_from_structured_text("result_inspect", result)

        row = vizs[0]["data"]["rows"][0]
        # Band and Name columns, then stat values
        assert row[2] == "0.1235"  # min rounded to 4 decimals
        assert row[3] == "0.9877"  # max rounded to 4 decimals

    def test_extract_point_nodata_value(self):
        """Point values with None value show 'NoData'."""
        result = {
            "values": [
                {"band": 1, "name": "red", "value": None},
            ],
            "point": {"latitude": 46.0, "longitude": 11.0},
        }
        vizs = _extract_from_structured_text("result_extract_point", result)

        assert len(vizs) >= 1
        row = vizs[0]["data"]["rows"][0]
        assert row[2] == "NoData"


# ============================================================================
# Main extract_visualization Tests
# ============================================================================

class TestExtractVisualization:
    """Test the main extract_visualization() entry point."""

    def test_returns_list(self):
        """extract_visualization always returns a list."""
        result = extract_visualization("some_tool", {})
        assert isinstance(result, list)

    def test_no_output_path_returns_empty(self):
        """Tool result without output_path returns empty list."""
        result = extract_visualization("openeo_create_job", {"status": "completed"})
        assert result == []

    def test_no_viz_tools_excluded(self):
        """Tools in _NO_VIZ_TOOLS set always return empty list."""
        no_viz_tools = [
            "saved_jobs_delete", "saved_jobs_list",
            "openeo_resolve_location", "openeo_parse_temporal",
        ]
        for tool in no_viz_tools:
            result = extract_visualization(tool, {"output_path": "/var/test.tif"})
            assert result == [], f"Tool {tool} should return empty viz list"

    def test_no_viz_tools_ignore_content(self):
        """No-viz tools return empty even with visualization content."""
        result = extract_visualization("saved_jobs_delete", {
            "content": [{
                "type": "visualization",
                "component": {"type": "table", "headers": ["A"], "rows": [["B"]], "title": "T"}
            }]
        })
        assert result == []

    def test_viz_show_map_with_spec(self):
        """viz_show_map tool with spec extracts map visualization."""
        result = extract_visualization("viz_show_map", {
            "spec": {
                "title": "Test Map",
                "layers": [{
                    "url": "/render-raster?path=/var/test.tif",
                    "bounds": [[46.0, 11.0], [46.5, 11.5]],
                    "colormap": "ndvi",
                    "opacity": 0.7,
                    "vmin": -0.1,
                    "vmax": 0.9,
                    "source": "/var/test.tif",
                }],
                "colorbar": {"min": -0.1, "max": 0.9},
            }
        })

        assert len(result) == 1
        viz = result[0]
        assert viz["type"] == "map"
        assert viz["data"]["colormap"] == "ndvi"
        assert viz["data"]["opacity"] == 0.7
        assert viz["data"]["vmin"] == -0.1
        assert viz["data"]["vmax"] == 0.9
        assert viz["title"] == "Test Map"

    def test_viz_show_map_with_content_wrapper(self):
        """viz_show_map with content wrapper extracts map visualization."""
        result = extract_visualization("viz_show_map", {
            "content": [{
                "type": "visualization",
                "component": {
                    "spec": {
                        "title": "Wrapped Map",
                        "layers": [{
                            "url": "/render-raster?path=/var/test.tif",
                            "bounds": [[46.0, 11.0], [46.5, 11.5]],
                            "colormap": "viridis",
                        }],
                    }
                }
            }]
        })

        assert len(result) == 1
        assert result[0]["type"] == "map"
        assert result[0]["title"] == "Wrapped Map"

    def test_viz_show_map_invalid_result(self):
        """viz_show_map with invalid structure returns empty list."""
        result = extract_visualization("viz_show_map", {"invalid": "data"})
        assert result == []

    def test_viz_show_map_no_layers(self):
        """viz_show_map with empty layers returns empty list."""
        result = extract_visualization("viz_show_map", {
            "spec": {"title": "No Layers", "layers": []}
        })
        assert result == []

    def test_viz_show_time_series(self):
        """viz_show_time_series extracts chart visualization."""
        result = extract_visualization("viz_show_time_series", {
            "spec": {
                "chart_type": "line",
                "title": "NDVI Time Series",
                "data": {
                    "x": ["2024-01", "2024-02"],
                    "y": [0.3, 0.7],
                    "series_name": "NDVI",
                },
                "xaxis": {"title": "Date"},
                "yaxis": {"title": "Value"},
            }
        })

        assert len(result) == 1
        assert result[0]["type"] == "chart"
        assert result[0]["data"]["chartType"] == "line"

    def test_viz_show_time_series_with_content_wrapper(self):
        """viz_show_time_series with content wrapper extracts chart."""
        result = extract_visualization("viz_show_time_series", {
            "content": [{
                "type": "visualization",
                "component": {
                    "spec": {
                        "chart_type": "line",
                        "title": "Wrapped Chart",
                        "data": {"x": [1], "y": [2], "series_name": "S"},
                        "xaxis": {"title": "X"},
                        "yaxis": {"title": "Y"},
                    }
                }
            }]
        })

        assert len(result) == 1
        assert result[0]["type"] == "chart"

    def test_quality_metrics_produces_no_viz(self):
        """openeo_quality_metrics returns no visualization — data is sent via dedicated WS message."""
        result = extract_visualization("openeo_quality_metrics", {
            "score": 85,
            "grade": "B+",
        })

        assert len(result) == 0

    def test_process_graph_tool_produces_graph_viz(self):
        """Graph builder tools produce process_graph visualization."""
        graph_tools = [
            "openeo_generate_graph",
            "openeo_compose_graph",
            "openeo_raw_graph",
            "openeo_algorithm",
        ]
        for tool_name in graph_tools:
            graph_result = {
                "load": {
                    "process_id": "load_collection",
                    "arguments": {"id": "sentinel-2-l2a"},
                },
                "save": {
                    "process_id": "save_result",
                    "arguments": {"data": {"from_node": "load"}, "format": "GTiff"},
                    "result": True,
                },
            }
            result = extract_visualization(tool_name, graph_result)

            graph_vizs = [v for v in result if v["type"] == "process_graph"]
            assert len(graph_vizs) == 1, f"Tool {tool_name} should produce process_graph viz"
            assert graph_vizs[0]["title"] == "Process Graph"
            nodes = graph_vizs[0]["data"]["nodes"]
            node_ids = [n["id"] for n in nodes]
            assert "load" in node_ids
            assert "save" in node_ids

    def test_process_graph_nodes_have_process_id(self):
        """Process graph nodes include process_id and arguments."""
        result = extract_visualization("openeo_generate_graph", {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"},
            },
            "ndvi": {
                "process_id": "ndvi",
                "arguments": {"data": {"from_node": "load"}},
            },
            "save": {
                "process_id": "save_result",
                "arguments": {"data": {"from_node": "ndvi"}, "format": "GTiff"},
                "result": True,
            },
        })

        pg_vizs = [v for v in result if v["type"] == "process_graph"]
        assert len(pg_vizs) == 1
        nodes = pg_vizs[0]["data"]["nodes"]
        load_node = next(n for n in nodes if n["id"] == "load")
        assert load_node["process_id"] == "load_collection"
        save_node = next(n for n in nodes if n["id"] == "save")
        assert save_node["result"] is True

    def test_non_graph_tool_no_process_graph(self):
        """Non-graph tools with dict result don't produce process_graph viz."""
        result = extract_visualization("openeo_create_job", {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "sentinel-2-l2a"},
            },
        })
        graph_vizs = [v for v in result if v["type"] == "process_graph"]
        assert len(graph_vizs) == 0

    def test_comparison_slider_from_result(self):
        """Comparison slider result produces comparison visualization."""
        result = extract_visualization("some_tool", {
            "type": "comparison_slider",
            "spec": {
                "title": "Before vs After",
                "before": {
                    "url": "/render-raster?path=/var/before.tif",
                    "bounds": [[46.0, 11.0], [46.5, 11.5]],
                    "label": "Pre-fire",
                    "source": "/var/before.tif",
                },
                "after": {
                    "url": "/render-raster?path=/var/after.tif",
                    "bounds": [[46.0, 11.0], [46.5, 11.5]],
                    "label": "Post-fire",
                    "source": "/var/after.tif",
                },
                "colorbar": {
                    "colormap": "inferno",
                    "min": -0.5,
                    "max": 0.5,
                },
            }
        })

        comp_vizs = [v for v in result if v["type"] == "comparison"]
        assert len(comp_vizs) == 1
        comp = comp_vizs[0]
        assert comp["title"] == "Before vs After"
        assert comp["data"]["labels"]["before"] == "Pre-fire"
        assert comp["data"]["labels"]["after"] == "Post-fire"
        assert comp["data"]["before"]["colormap"] == "inferno"

    def test_content_chart_visualization(self):
        """Chart visualization in content array is extracted."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "chart",
                    "chartType": "bar",
                    "title": "Band Values",
                    "xAxis": {"label": "Band", "values": ["red", "nir"]},
                    "yAxis": {"label": "Value"},
                    "series": [{"name": "Values", "values": [0.3, 0.7]}],
                }
            }]
        })

        chart_vizs = [v for v in result if v["type"] == "chart"]
        assert len(chart_vizs) >= 1
        assert chart_vizs[0]["data"]["chartType"] == "bar"

    def test_content_table_visualization(self):
        """Table visualization in content array is extracted."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "table",
                    "title": "Results",
                    "headers": ["Name", "Value"],
                    "rows": [["NDVI", "0.5"]],
                }
            }]
        })

        table_vizs = [v for v in result if v["type"] == "table"]
        assert len(table_vizs) >= 1
        assert table_vizs[0]["data"]["headers"] == ["Name", "Value"]

    def test_content_map_visualization(self):
        """Map visualization in content array is extracted."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "map",
                    "mapType": "raster",
                    "url": "/render-raster?path=/var/test.tif",
                    "bounds": [[46.0, 11.0], [46.5, 11.5]],
                    "colormap": "ndvi",
                    "title": "Content Map",
                }
            }]
        })

        map_vizs = [v for v in result if v["type"] == "map"]
        assert len(map_vizs) >= 1
        assert map_vizs[0]["data"]["colormap"] == "ndvi"

    def test_multi_panel_visualization(self):
        """Multi-panel visualization in content is extracted."""
        panels = [
            {"type": "map", "title": "Panel 1", "url": "/render-raster?path=/var/p1.tif"},
            {"type": "map", "title": "Panel 2", "url": "/render-raster?path=/var/p2.tif"},
        ]
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "multi_panel",
                    "title": "Side-by-Side",
                    "panels": panels,
                }
            }]
        })

        mp_vizs = [v for v in result if v["type"] == "multi_panel"]
        assert len(mp_vizs) == 1
        assert mp_vizs[0]["title"] == "Side-by-Side"
        assert len(mp_vizs[0]["data"]["panels"]) == 2

    def test_dashboard_visualization(self):
        """Dashboard visualization in content is extracted."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "dashboard",
                    "title": "Analysis Dashboard",
                    "panels": [{"type": "chart", "title": "Chart 1"}],
                }
            }]
        })

        dash_vizs = [v for v in result if v["type"] == "dashboard"]
        assert len(dash_vizs) == 1
        assert dash_vizs[0]["title"] == "Analysis Dashboard"

    def test_timelapse_visualization(self):
        """Timelapse visualization in content is extracted."""
        layers = [
            {"url": "/render-raster?path=/var/t1.tif", "label": "Jan 2024"},
            {"url": "/render-raster?path=/var/t2.tif", "label": "Feb 2024"},
        ]
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "timelapse",
                    "title": "NDVI Over Time",
                    "layers": layers,
                    "colormap": "ndvi",
                }
            }]
        })

        tl_vizs = [v for v in result if v["type"] == "timelapse"]
        assert len(tl_vizs) == 1
        assert tl_vizs[0]["title"] == "NDVI Over Time"
        assert len(tl_vizs[0]["data"]["layers"]) == 2
        assert tl_vizs[0]["data"]["colormap"] == "ndvi"

    def test_comparison_slider_in_content(self):
        """Comparison slider in content array is extracted."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "comparison_slider",
                    "spec": {
                        "title": "Content Comparison",
                        "before": {"url": "/before", "bounds": None, "label": "Before"},
                        "after": {"url": "/after", "bounds": None, "label": "After"},
                        "colorbar": {"colormap": "viridis", "min": 0, "max": 1},
                    }
                }
            }]
        })

        comp_vizs = [v for v in result if v["type"] == "comparison"]
        assert len(comp_vizs) == 1
        assert comp_vizs[0]["title"] == "Content Comparison"

    def test_process_graph_in_content(self):
        """Process graph in content visualization component is extracted."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "process_graph",
                    "title": "My Graph",
                    "data": {
                        "title": "Graph Title",
                        "graph": {
                            "load": {"process_id": "load_collection", "arguments": {"id": "sentinel-2-l2a"}},
                            "save": {"process_id": "save_result", "arguments": {"data": {"from_node": "load"}}},
                        }
                    }
                }
            }]
        })

        pg_vizs = [v for v in result if v["type"] == "process_graph"]
        assert len(pg_vizs) >= 1
        nodes = pg_vizs[0]["data"]["nodes"]
        node_ids = [n["id"] for n in nodes]
        assert "load" in node_ids
        assert "save" in node_ids

    def test_deduplication_of_auto_vizs(self):
        """Auto-generated vizs from structured text are deduplicated against explicit vizs."""
        result = extract_visualization("result_inspect", {
            "band_statistics": [
                {"band": 1, "name": "red", "min": 0.0, "max": 1.0, "mean": 0.5, "std": 0.1, "median": 0.45},
            ],
            "content": [{
                "type": "visualization",
                "component": {
                    "type": "table",
                    "title": "Band Statistics",
                    "headers": ["Band", "Name", "Min", "Max", "Mean"],
                    "rows": [["1", "red", "0.0", "1.0", "0.5"]],
                }
            }]
        })

        table_vizs = [v for v in result if v["type"] == "table" and v["title"] == "Band Statistics"]
        assert len(table_vizs) == 1

    def test_empty_result_returns_empty(self):
        """Empty dict result returns empty list for generic tool."""
        result = extract_visualization("generic_tool", {})
        assert result == []

    def test_none_result_returns_empty(self):
        """None-like result returns empty list."""
        result = extract_visualization("generic_tool", None)
        assert isinstance(result, list)

    def test_string_result_returns_empty(self):
        """String result returns empty list."""
        result = extract_visualization("generic_tool", "just a string")
        assert isinstance(result, list)

    def test_viz_show_map_list_result(self):
        """viz_show_map with list result (raw content array) is handled."""
        result = extract_visualization("viz_show_map", [
            {
                "type": "visualization",
                "component": {
                    "spec": {
                        "title": "List Map",
                        "layers": [{
                            "url": "/render-raster?path=/var/test.tif",
                            "bounds": [[46.0, 11.0], [46.5, 11.5]],
                            "colormap": "viridis",
                        }],
                    }
                }
            }
        ])

        assert len(result) == 1
        assert result[0]["type"] == "map"
        assert result[0]["title"] == "List Map"

    def test_viz_show_map_list_with_spec_item(self):
        """viz_show_map with list containing spec item directly."""
        result = extract_visualization("viz_show_map", [
            {
                "spec": {
                    "title": "Direct Spec",
                    "layers": [{
                        "url": "/render-raster?path=/var/test.tif",
                        "colormap": "viridis",
                    }],
                }
            }
        ])

        assert len(result) == 1
        assert result[0]["type"] == "map"
        assert result[0]["title"] == "Direct Spec"

    def test_content_with_non_visualization_items_ignored(self):
        """Non-visualization items in content array are skipped."""
        result = extract_visualization("some_tool", {
            "content": [
                {"type": "text", "text": "Just some text"},
                {"type": "other", "data": "irrelevant"},
            ]
        })

        assert result == []

    def test_content_with_empty_component(self):
        """Visualization with empty component type is skipped."""
        result = extract_visualization("some_tool", {
            "content": [{
                "type": "visualization",
                "component": {"type": "", "title": "Empty Type"}
            }]
        })
        # Empty type won't match any handler, but shouldn't crash
        assert isinstance(result, list)

    def test_multiple_content_visualizations(self):
        """Multiple visualizations in content array are all extracted."""
        result = extract_visualization("some_tool", {
            "content": [
                {
                    "type": "visualization",
                    "component": {
                        "type": "table",
                        "title": "Table 1",
                        "headers": ["A"],
                        "rows": [["1"]],
                    }
                },
                {
                    "type": "visualization",
                    "component": {
                        "type": "chart",
                        "chartType": "bar",
                        "title": "Chart 1",
                        "xAxis": {},
                        "yAxis": {},
                        "series": [],
                    }
                },
            ]
        })

        assert len(result) >= 2
        types = [v["type"] for v in result]
        assert "table" in types
        assert "chart" in types

    def test_title_hint_used_in_viz_title(self):
        """title_hint is used as viz title when result has no title."""
        result = extract_visualization("viz_show_map", {
            "spec": {
                "layers": [{
                    "url": "/render-raster?path=/var/test.tif",
                    "colormap": "viridis",
                }],
            }
        }, title_hint="My Custom Title")

        assert len(result) == 1
        assert result[0]["title"] == "My Custom Title"

    def test_fusion_multi_sensor_generates_graph_and_table(self):
        """fusion_multi_sensor with process_graph and metadata produces visualizations."""
        result = {
            "content": [
                {"type": "text", "text": json.dumps({
                    "process_graph": {
                        "load1": {"process_id": "load_collection", "arguments": {"id": "sentinel-2-l2a"}},
                        "load2": {"process_id": "load_collection", "arguments": {"id": "landsat-c2-l2"}},
                    },
                    "metadata": {
                        "primary_collection": "sentinel-2-l2a",
                        "secondary_collection": "landsat-c2-l2",
                        "resampled": True,
                        "output_format": "GTiff",
                        "node_count": 5,
                        "processes_used": ["load_collection", "merge_cubes"],
                    }
                })}
            ]
        }
        vizs = _extract_from_structured_text("fusion_multi_sensor", result)

        assert len(vizs) >= 1
        pg_vizs = [v for v in vizs if v["type"] == "process_graph"]
        assert len(pg_vizs) == 1
        assert pg_vizs[0]["title"] == "Fusion Process Graph"

        table_vizs = [v for v in vizs if v["type"] == "table"]
        assert len(table_vizs) == 1
        assert table_vizs[0]["title"] == "Fusion Parameters"


# ============================================================================
# INDEX_COLORMAP_MAP Consistency Tests
# ============================================================================

class TestIndexColormapMap:
    """Validate INDEX_COLORMAP_MAP data structure consistency."""

    def test_all_values_are_valid_colormaps(self):
        """All colormap values should be one of the known colormaps."""
        known_colormaps = {"ndvi", "viridis", "coolwarm", "inferno", "plasma", "terrain"}
        for index_name, colormap in INDEX_COLORMAP_MAP.items():
            assert colormap in known_colormaps, \
                f"Index '{index_name}' maps to unknown colormap '{colormap}'"

    def test_all_keys_are_lowercase(self):
        """All index keys should be lowercase for consistent matching."""
        for key in INDEX_COLORMAP_MAP:
            assert key == key.lower(), f"Key '{key}' should be lowercase"

    def test_map_is_not_empty(self):
        """INDEX_COLORMAP_MAP should have entries."""
        assert len(INDEX_COLORMAP_MAP) > 10

    def test_vegetation_indices_use_ndvi_colormap(self):
        """Common vegetation indices should map to ndvi colormap."""
        veg_indices = ["ndvi", "evi", "savi", "gndvi", "lai"]
        for idx in veg_indices:
            assert INDEX_COLORMAP_MAP.get(idx) == "ndvi", \
                f"Vegetation index '{idx}' should use ndvi colormap"

    def test_water_indices_use_coolwarm(self):
        """Water indices should map to coolwarm."""
        water_indices = ["ndwi", "mndwi"]
        for idx in water_indices:
            assert INDEX_COLORMAP_MAP.get(idx) == "coolwarm", \
                f"Water index '{idx}' should use coolwarm colormap"

    def test_fire_indices_use_inferno(self):
        """Fire/burn indices should map to inferno."""
        fire_indices = ["nbr", "dnbr", "bai"]
        for idx in fire_indices:
            assert INDEX_COLORMAP_MAP.get(idx) == "inferno", \
                f"Fire index '{idx}' should use inferno colormap"

    def test_soil_indices_use_plasma(self):
        """Soil/mining indices should map to plasma."""
        soil_indices = ["bsi", "ndti", "bare soil", "mining"]
        for idx in soil_indices:
            assert INDEX_COLORMAP_MAP.get(idx) == "plasma", \
                f"Soil index '{idx}' should use plasma colormap"

    def test_elevation_indices_use_terrain(self):
        """Elevation-related entries should map to terrain."""
        terrain_entries = ["dem", "elevation", "altitude", "height map"]
        for entry in terrain_entries:
            assert INDEX_COLORMAP_MAP.get(entry) == "terrain", \
                f"Entry '{entry}' should use terrain colormap"
