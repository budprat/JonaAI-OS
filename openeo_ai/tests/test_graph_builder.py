# ABOUTME: Tests for atomic graph builder tools (graph_builder_tools.py).
# Tests auto-wiring helpers, 7 atomic tools, and end-to-end workflows.

"""Tests for atomic graph builder tools."""

import asyncio
import json
import pytest

from openeo_ai.tools.graph_builder_tools import (
    _collect_from_nodes,
    _find_last_node,
    _generate_node_id,
    _graph_add_band_filter,
    _graph_add_logic,
    _graph_add_mask,
    _graph_add_math,
    _graph_add_merge,
    _graph_add_threshold,
    _graph_validate_full,
    _build_graph_topology,
    create_graph_builder_tools,
    GRAPH_BUILDER_TOOL_DEFINITIONS,
)


# ── Helpers to build base graphs ──

def _base_load_graph():
    """Minimal graph with just a load_collection node."""
    return {
        "load": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {"west": 72, "south": 19, "east": 73, "north": 20},
                "temporal_extent": ["2024-06-01", "2024-09-01"],
                "bands": ["green", "nir", "swir16"],
            },
        }
    }


def _base_ndvi_graph():
    """Graph with load + ndvi + reduce_time (no save)."""
    return {
        "load": {
            "process_id": "load_collection",
            "arguments": {
                "id": "sentinel-2-l2a",
                "spatial_extent": {"west": 72, "south": 19, "east": 73, "north": 20},
                "temporal_extent": ["2024-06-01", "2024-09-01"],
                "bands": ["red", "nir"],
            },
        },
        "ndvi": {
            "process_id": "ndvi",
            "arguments": {
                "data": {"from_node": "load"},
                "nir": "nir",
                "red": "red",
            },
        },
        "reduce_time": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": "ndvi"},
                "dimension": "time",
                "reducer": {
                    "process_graph": {
                        "median": {
                            "process_id": "median",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                },
            },
        },
    }


def _base_complete_graph():
    """Complete graph with save_result (valid for submission)."""
    graph = _base_ndvi_graph()
    graph["save"] = {
        "process_id": "save_result",
        "arguments": {
            "data": {"from_node": "reduce_time"},
            "format": "GTiff",
        },
        "result": True,
    }
    return graph


# ── TestAutoWiring ──

class TestAutoWiring:
    """Test auto-wiring helper functions."""

    def test_find_last_node_linear_chain(self):
        """Linear chain: load -> ndvi -> reduce_time. Last = reduce_time."""
        graph = _base_ndvi_graph()
        assert _find_last_node(graph) == "reduce_time"

    def test_find_last_node_with_save(self):
        """With save node as the only terminal, returns save."""
        graph = _base_complete_graph()
        # load referenced by ndvi, ndvi by reduce_time, reduce_time by save
        # Only terminal is save
        assert _find_last_node(graph) == "save"

    def test_find_last_node_branch(self):
        """Two branches: load -> A, load -> B. Should return one of the terminals."""
        graph = {
            "load": {
                "process_id": "load_collection",
                "arguments": {"id": "s2"},
            },
            "branch_a": {
                "process_id": "ndvi",
                "arguments": {"data": {"from_node": "load"}},
            },
            "branch_b": {
                "process_id": "ndwi",
                "arguments": {"data": {"from_node": "load"}},
            },
        }
        result = _find_last_node(graph)
        assert result in ("branch_a", "branch_b")

    def test_find_last_node_empty(self):
        assert _find_last_node({}) is None

    def test_collect_from_nodes(self):
        """Should collect from_node refs from nested args."""
        refs = set()
        _collect_from_nodes({
            "data": {"from_node": "load"},
            "mask": {"from_node": "cloud_mask"},
        }, refs)
        assert refs == {"load", "cloud_mask"}

    def test_collect_from_nodes_skips_process_graph(self):
        """Should NOT descend into process_graph sub-graphs."""
        refs = set()
        _collect_from_nodes({
            "data": {"from_node": "ndvi"},
            "reducer": {
                "process_graph": {
                    "median": {
                        "process_id": "median",
                        "arguments": {"data": {"from_parameter": "data"}},
                    }
                }
            },
        }, refs)
        assert refs == {"ndvi"}

    def test_generate_node_id(self):
        graph = {"gt_1": {}, "gt_2": {}}
        assert _generate_node_id(graph, "gt") == "gt_3"

    def test_generate_node_id_empty(self):
        assert _generate_node_id({}, "threshold") == "threshold_1"


# ── TestGraphAddThreshold ──

class TestGraphAddThreshold:
    """Test graph_add_threshold tool."""

    def test_gt_auto_wire(self):
        graph = _base_ndvi_graph()
        graph, nid = _graph_add_threshold(graph, "gt", value=0.3)
        assert nid == "gt_1"
        node = graph["gt_1"]
        assert node["process_id"] == "gt"
        assert node["arguments"]["x"] == {"from_node": "reduce_time"}
        assert node["arguments"]["y"] == 0.3

    def test_lt_explicit_input(self):
        graph = _base_ndvi_graph()
        graph, nid = _graph_add_threshold(graph, "lt", value=0.2, input_node="ndvi")
        assert graph[nid]["arguments"]["x"] == {"from_node": "ndvi"}

    def test_between(self):
        graph = _base_ndvi_graph()
        graph, nid = _graph_add_threshold(graph, "between", min_value=0.2, max_value=0.8)
        node = graph[nid]
        assert node["process_id"] == "between"
        assert node["arguments"]["min"] == 0.2
        assert node["arguments"]["max"] == 0.8

    def test_custom_node_id(self):
        graph = _base_ndvi_graph()
        graph, nid = _graph_add_threshold(graph, "gt", value=0.5, node_id="my_threshold")
        assert nid == "my_threshold"
        assert "my_threshold" in graph

    def test_missing_value_raises(self):
        graph = _base_ndvi_graph()
        with pytest.raises(ValueError, match="requires 'value'"):
            _graph_add_threshold(graph, "gt")

    def test_between_missing_bounds_raises(self):
        graph = _base_ndvi_graph()
        with pytest.raises(ValueError, match="requires both min_value and max_value"):
            _graph_add_threshold(graph, "between", min_value=0.2)

    def test_invalid_operator_raises(self):
        graph = _base_ndvi_graph()
        with pytest.raises(ValueError, match="Invalid operator"):
            _graph_add_threshold(graph, "eq", value=0.5)


# ── TestGraphAddLogic ──

class TestGraphAddLogic:
    """Test graph_add_logic tool."""

    def test_and_two_thresholds(self):
        graph = _base_ndvi_graph()
        graph, t1 = _graph_add_threshold(graph, "gt", value=0.4, node_id="ndsi_check")
        graph, t2 = _graph_add_threshold(graph, "lt", value=0.2, input_node="ndvi", node_id="ndvi_check")
        graph, nid = _graph_add_logic(graph, "and", input_a="ndsi_check", input_b="ndvi_check")
        node = graph[nid]
        assert node["process_id"] == "and"
        assert node["arguments"]["x"] == {"from_node": "ndsi_check"}
        assert node["arguments"]["y"] == {"from_node": "ndvi_check"}

    def test_or_two_masks(self):
        graph = _base_ndvi_graph()
        graph["mask_a"] = {"process_id": "gt", "arguments": {"x": {"from_node": "ndvi"}, "y": 0.3}}
        graph["mask_b"] = {"process_id": "lt", "arguments": {"x": {"from_node": "ndvi"}, "y": 0.1}}
        graph, nid = _graph_add_logic(graph, "or", input_a="mask_a", input_b="mask_b")
        assert graph[nid]["process_id"] == "or"

    def test_not_single_input(self):
        graph = _base_ndvi_graph()
        graph["mask"] = {"process_id": "gt", "arguments": {"x": {"from_node": "ndvi"}, "y": 0.3}}
        graph, nid = _graph_add_logic(graph, "not", input_a="mask")
        assert graph[nid]["process_id"] == "not"
        assert graph[nid]["arguments"]["x"] == {"from_node": "mask"}

    def test_and_missing_input_b_raises(self):
        graph = _base_ndvi_graph()
        with pytest.raises(ValueError, match="requires both input_a and input_b"):
            _graph_add_logic(graph, "and", input_a="ndvi")

    def test_invalid_node_ref_raises(self):
        graph = _base_ndvi_graph()
        with pytest.raises(ValueError, match="not found in graph"):
            _graph_add_logic(graph, "and", input_a="ndvi", input_b="nonexistent")


# ── TestGraphAddMath ──

class TestGraphAddMath:
    """Test graph_add_math tool."""

    def test_subtract_two_nodes(self):
        graph = _base_load_graph()
        graph["pre_nbr"] = {"process_id": "normalized_difference", "arguments": {"x": {"from_node": "load"}, "y": {"from_node": "load"}}}
        graph["post_nbr"] = {"process_id": "normalized_difference", "arguments": {"x": {"from_node": "load"}, "y": {"from_node": "load"}}}
        graph, nid = _graph_add_math(graph, "subtract", "pre_nbr", "post_nbr")
        node = graph[nid]
        assert node["process_id"] == "subtract"
        assert node["arguments"]["x"] == {"from_node": "pre_nbr"}
        assert node["arguments"]["y"] == {"from_node": "post_nbr"}

    def test_multiply_by_scalar(self):
        graph = _base_ndvi_graph()
        graph, nid = _graph_add_math(graph, "multiply", "reduce_time", 0.0001)
        node = graph[nid]
        assert node["process_id"] == "multiply"
        assert node["arguments"]["y"] == 0.0001

    def test_divide_node_to_node(self):
        graph = _base_load_graph()
        graph["band_a"] = {"process_id": "array_element", "arguments": {"data": {"from_node": "load"}, "label": "nir"}}
        graph["band_b"] = {"process_id": "array_element", "arguments": {"data": {"from_node": "load"}, "label": "red"}}
        graph, nid = _graph_add_math(graph, "divide", "band_a", "band_b")
        assert graph[nid]["arguments"]["y"] == {"from_node": "band_b"}

    def test_missing_input_raises(self):
        graph = _base_load_graph()
        with pytest.raises(ValueError, match="not found in graph"):
            _graph_add_math(graph, "add", "nonexistent", 5)


# ── TestGraphAddBandFilter ──

class TestGraphAddBandFilter:
    """Test graph_add_band_filter tool."""

    def test_extract_band(self):
        graph = _base_load_graph()
        graph, nid = _graph_add_band_filter(graph, "nir")
        assert nid == "get_nir_1"
        node = graph[nid]
        assert node["process_id"] == "array_element"
        assert node["arguments"]["label"] == "nir"
        assert node["arguments"]["data"] == {"from_node": "load"}

    def test_auto_wires_to_load(self):
        graph = _base_load_graph()
        graph, nid = _graph_add_band_filter(graph, "swir16")
        assert graph[nid]["arguments"]["data"] == {"from_node": "load"}

    def test_explicit_input(self):
        graph = _base_ndvi_graph()
        graph, nid = _graph_add_band_filter(graph, "red", input_node="load")
        assert graph[nid]["arguments"]["data"] == {"from_node": "load"}


# ── TestGraphAddMask ──

class TestGraphAddMask:
    """Test graph_add_mask tool."""

    def test_mask_with_threshold(self):
        graph = _base_ndvi_graph()
        graph, t_nid = _graph_add_threshold(graph, "gt", value=0.3, node_id="water_mask")
        graph, nid = _graph_add_mask(graph, data_node="reduce_time", mask_node="water_mask")
        node = graph[nid]
        assert node["process_id"] == "mask"
        assert node["arguments"]["data"] == {"from_node": "reduce_time"}
        assert node["arguments"]["mask"] == {"from_node": "water_mask"}

    def test_mask_with_replacement(self):
        graph = _base_ndvi_graph()
        graph["water_mask"] = {"process_id": "gt", "arguments": {"x": {"from_node": "ndvi"}, "y": 0.2}}
        graph, nid = _graph_add_mask(graph, data_node="ndvi", mask_node="water_mask", replacement=0)
        assert graph[nid]["arguments"]["replacement"] == 0

    def test_invalid_data_node_raises(self):
        graph = _base_ndvi_graph()
        with pytest.raises(ValueError, match="data_node.*not found"):
            _graph_add_mask(graph, data_node="nonexistent", mask_node="ndvi")


# ── TestGraphAddMerge ──

class TestGraphAddMerge:
    """Test graph_add_merge tool."""

    def test_merge_without_resolver(self):
        graph = {
            "load_pre": {
                "process_id": "load_collection",
                "arguments": {"id": "s2", "temporal_extent": ["2024-01-01", "2024-03-01"]},
            },
            "load_post": {
                "process_id": "load_collection",
                "arguments": {"id": "s2", "temporal_extent": ["2024-06-01", "2024-09-01"]},
            },
        }
        graph, nid = _graph_add_merge(graph, "load_pre", "load_post")
        node = graph[nid]
        assert node["process_id"] == "merge_cubes"
        assert node["arguments"]["cube1"] == {"from_node": "load_pre"}
        assert node["arguments"]["cube2"] == {"from_node": "load_post"}
        assert "overlap_resolver" not in node["arguments"]

    def test_merge_with_subtract_resolver(self):
        graph = {
            "pre_nbr": {"process_id": "ndvi", "arguments": {"data": {"from_node": "load"}}},
            "post_nbr": {"process_id": "ndvi", "arguments": {"data": {"from_node": "load"}}},
            "load": {"process_id": "load_collection", "arguments": {"id": "s2"}},
        }
        graph, nid = _graph_add_merge(graph, "pre_nbr", "post_nbr", overlap_resolver="subtract")
        node = graph[nid]
        assert "overlap_resolver" in node["arguments"]
        sub_graph = node["arguments"]["overlap_resolver"]["process_graph"]
        assert "subtract_overlap" in sub_graph
        assert sub_graph["subtract_overlap"]["result"] is True

    def test_merge_with_mean_resolver(self):
        graph = {
            "cube_a": {"process_id": "load_collection", "arguments": {"id": "s2"}},
            "cube_b": {"process_id": "load_collection", "arguments": {"id": "l8"}},
        }
        graph, nid = _graph_add_merge(graph, "cube_a", "cube_b", overlap_resolver="mean")
        sub_graph = graph[nid]["arguments"]["overlap_resolver"]["process_graph"]
        assert "add_overlap" in sub_graph
        assert "mean_overlap" in sub_graph

    def test_invalid_cube_raises(self):
        graph = _base_load_graph()
        with pytest.raises(ValueError, match="not found in graph"):
            _graph_add_merge(graph, "load", "nonexistent")


# ── TestGraphValidateFull ──

class TestGraphValidateFull:
    """Test graph_validate_full tool."""

    def test_valid_graph_passes(self):
        graph = _base_complete_graph()
        result = _graph_validate_full(graph)
        assert result["ready_to_submit"] is True
        assert len(result["errors"]) == 0
        assert result["node_count"] == 4
        assert "load" in result["graph_topology"]

    def test_missing_save_detected(self):
        graph = _base_ndvi_graph()
        result = _graph_validate_full(graph)
        assert result["ready_to_submit"] is False
        assert any("save_result" in e for e in result["errors"])
        # Should suggest a fix
        assert len(result["fixes"]) > 0
        assert result["fixes"][0]["node_id"] == "save"

    def test_broken_from_node_caught(self):
        graph = _base_ndvi_graph()
        graph["bad_node"] = {
            "process_id": "gt",
            "arguments": {"x": {"from_node": "nonexistent"}, "y": 0.5},
        }
        graph["save"] = {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "bad_node"}, "format": "GTiff"},
            "result": True,
        }
        result = _graph_validate_full(graph)
        assert result["ready_to_submit"] is False
        assert any("nonexistent" in e for e in result["errors"])

    def test_empty_graph(self):
        result = _graph_validate_full({})
        assert result["ready_to_submit"] is False
        assert result["node_count"] == 0

    def test_band_validation_with_invalid_band(self):
        """Validates that unknown band names are flagged as warnings."""
        graph = _base_load_graph()
        graph["load"]["arguments"]["bands"] = ["nir", "nonexistent_band_xyz"]
        graph["save"] = {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "load"}, "format": "GTiff"},
            "result": True,
        }
        result = _graph_validate_full(graph, collection="sentinel-2-l2a")
        # Should warn about nonexistent_band_xyz
        assert any("nonexistent_band_xyz" in w for w in result["warnings"])

    def test_topology_string(self):
        graph = _base_complete_graph()
        result = _graph_validate_full(graph)
        # Should be something like "load -> ndvi -> reduce_time -> save"
        assert "->" in result["graph_topology"]


# ── TestEndToEnd ──

class TestEndToEnd:
    """End-to-end workflow tests building real-world graphs."""

    def test_flood_detection_workflow(self):
        """Build flood detection: load -> MNDWI -> reduce_time -> threshold > 0.3 -> save."""
        # Start with base graph
        graph = {
            "load": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "spatial_extent": {"west": 72, "south": 19, "east": 73, "north": 20},
                    "temporal_extent": ["2024-07-01", "2024-08-01"],
                    "bands": ["green", "swir16"],
                },
            },
            "mndwi": {
                "process_id": "normalized_difference",
                "arguments": {
                    "x": {"from_node": "load"},
                    "y": {"from_node": "load"},
                },
            },
            "reduce_time": {
                "process_id": "reduce_dimension",
                "arguments": {
                    "data": {"from_node": "mndwi"},
                    "dimension": "time",
                    "reducer": {
                        "process_graph": {
                            "mean": {
                                "process_id": "mean",
                                "arguments": {"data": {"from_parameter": "data"}},
                                "result": True,
                            }
                        }
                    },
                },
            },
        }

        # Add threshold
        graph, t_nid = _graph_add_threshold(graph, "gt", value=0.3)
        assert t_nid == "gt_1"
        assert graph["gt_1"]["arguments"]["x"] == {"from_node": "reduce_time"}

        # Add save
        graph["save"] = {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": t_nid}, "format": "GTiff"},
            "result": True,
        }

        # Validate
        result = _graph_validate_full(graph)
        assert result["ready_to_submit"] is True
        assert result["node_count"] == 5

    def test_snow_cover_workflow(self):
        """Build snow cover: NDSI > 0.4 AND NDVI < 0.2."""
        graph = _base_load_graph()
        # Extract bands
        graph, nir_id = _graph_add_band_filter(graph, "nir", input_node="load")
        graph, green_id = _graph_add_band_filter(graph, "green", input_node="load")
        graph, swir_id = _graph_add_band_filter(graph, "swir16", input_node="load")

        # Compute pseudo-NDSI threshold (simplified)
        graph, ndsi_check = _graph_add_threshold(graph, "gt", value=0.4, input_node=green_id, node_id="ndsi_check")

        # Compute pseudo-NDVI threshold
        graph, ndvi_check = _graph_add_threshold(graph, "lt", value=0.2, input_node=nir_id, node_id="ndvi_check")

        # Combine with AND
        graph, snow_mask = _graph_add_logic(graph, "and", input_a="ndsi_check", input_b="ndvi_check", node_id="snow_mask")

        # Add save
        graph["save"] = {
            "process_id": "save_result",
            "arguments": {"data": {"from_node": "snow_mask"}, "format": "GTiff"},
            "result": True,
        }

        # Validate
        result = _graph_validate_full(graph)
        assert result["ready_to_submit"] is True
        assert result["node_count"] >= 7


# ── TestAsyncHandlers ──

class TestAsyncHandlers:
    """Test the async handler functions returned by create_graph_builder_tools."""

    def test_handler_count(self):
        tools = create_graph_builder_tools()
        assert len(tools) == 10
        expected = {
            "graph_add_threshold", "graph_add_logic", "graph_add_math",
            "graph_add_band_filter", "graph_add_mask", "graph_add_merge",
            "graph_validate_full",
            "openeo_process_node", "openeo_graph_inspect", "openeo_compile_scf",
        }
        assert set(tools.keys()) == expected

    def test_definition_count(self):
        assert len(GRAPH_BUILDER_TOOL_DEFINITIONS) == 10
        names = {d["name"] for d in GRAPH_BUILDER_TOOL_DEFINITIONS}
        assert names == {
            "graph_add_threshold", "graph_add_logic", "graph_add_math",
            "graph_add_band_filter", "graph_add_mask", "graph_add_merge",
            "graph_validate_full",
            "openeo_process_node", "openeo_graph_inspect", "openeo_compile_scf",
        }

    def test_threshold_handler_returns_json(self):
        tools = create_graph_builder_tools()
        result = asyncio.run(
            tools["graph_add_threshold"]({
                "graph": _base_ndvi_graph(),
                "operator": "gt",
                "value": 0.3,
            })
        )
        text = result["content"][0]["text"]
        data = json.loads(text)
        assert "graph" in data
        assert "last_node" in data
        assert data["last_node"] == "gt_1"

    def test_validate_handler_returns_json(self):
        tools = create_graph_builder_tools()
        result = asyncio.run(
            tools["graph_validate_full"]({
                "graph": _base_complete_graph(),
            })
        )
        text = result["content"][0]["text"]
        data = json.loads(text)
        assert data["ready_to_submit"] is True

    def test_error_handler_returns_message(self):
        tools = create_graph_builder_tools()
        result = asyncio.run(
            tools["graph_add_threshold"]({
                "graph": _base_ndvi_graph(),
                "operator": "invalid_op",
                "value": 0.5,
            })
        )
        text = result["content"][0]["text"]
        assert text.startswith("Error:")


# ── TestBuildGraphTopology ──

class TestBuildGraphTopology:
    """Test the topology string builder."""

    def test_linear_topology(self):
        graph = _base_ndvi_graph()
        topo = _build_graph_topology(graph)
        # Should show linear chain
        assert "load" in topo
        assert "->" in topo

    def test_empty_graph(self):
        assert _build_graph_topology({}) == "(empty)"

    def test_complete_graph(self):
        graph = _base_complete_graph()
        topo = _build_graph_topology(graph)
        assert "save" in topo
