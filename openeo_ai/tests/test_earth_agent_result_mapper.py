# ABOUTME: Tests Earth-Agent result-to-visualization conversion heuristics.

from openeo_ai.earth_agent.result_mapper import to_visualizations


def test_result_mapper_creates_map_visualization_for_raster_output():
    visualizations = to_visualizations(
        {"output_path": "/tmp/openeo_results/job-1/result.tif"},
        tool_name="ea_threshold_segmentation",
    )

    assert visualizations[0]["type"] == "map"
    assert visualizations[0]["data"]["source"].endswith("result.tif")


def test_result_mapper_creates_chart_for_time_series_payload():
    visualizations = to_visualizations(
        {"timestamps": ["2024-01", "2024-02"], "values": [0.2, 0.4]},
        tool_name="ea_compute_linear_trend",
    )

    assert visualizations[0]["type"] == "chart"
    assert visualizations[0]["data"]["series"][0]["values"] == [0.2, 0.4]


def test_result_mapper_creates_table_for_scalar_statistics():
    visualizations = to_visualizations(
        {"mean": 0.42, "std": 0.08, "count": 12},
        tool_name="ea_calc_batch_image_mean",
    )

    assert visualizations[0]["type"] == "table"
    assert visualizations[0]["data"]["headers"] == ["Metric", "Value"]
