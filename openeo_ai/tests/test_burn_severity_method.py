# ABOUTME: Regressions from a live dNBR run that reported pre-fire NBR as burn severity:
# ABOUTME: template sign, method gate, dropped time steps, tool access, clarification, saving.
import asyncio
import json

import numpy as np
import pytest
import rasterio
import xarray as xr
from rasterio.transform import from_origin

BBOX = {"west": -118.55, "south": 34.03, "east": -118.49, "north": 34.09}
PRE = ["2024-11-01", "2024-11-30"]
POST = ["2025-01-10", "2025-01-25"]


def _burn_graph():
    from openeo_ai.tools.openeo_tools import OpenEOTools

    tools = OpenEOTools.__new__(OpenEOTools)
    return tools.build_algorithm_graph(
        algorithm="burn_severity", collection="sentinel-2-l2a", spatial_extent=BBOX,
        pre_event=PRE, post_event=POST,
    )


def _monthly_nbr_graph():
    return {
        "load": {"process_id": "load_collection", "arguments": {"id": "sentinel-2-l2a"}},
        "nbr": {"process_id": "normalized_difference", "arguments": {"x": {"from_node": "load"}}},
        "monthly": {"process_id": "aggregate_temporal_period", "arguments": {"data": {"from_node": "nbr"}}},
        "save": {"process_id": "save_result", "arguments": {"data": {"from_node": "monthly"}, "format": "GTiff"},
                 "result": True},
    }


class TestBurnSeverityTemplate:
    def test_is_pre_minus_post(self):
        graph = _burn_graph()
        change = graph["change"]["arguments"]
        assert change["cube1"] == {"from_node": "reduce_time1"}
        assert change["cube2"] == {"from_node": "reduce_time2"}
        resolver = change["overlap_resolver"]["process_graph"]["subtract"]["arguments"]
        assert resolver == {"x": {"from_parameter": "x"}, "y": {"from_parameter": "y"}}

    def test_keeps_windows_separate(self):
        graph = _burn_graph()
        assert graph["load1"]["arguments"]["temporal_extent"] == PRE
        assert graph["load2"]["arguments"]["temporal_extent"] == POST

    def test_urban_growth_stays_post_minus_pre(self):
        from openeo_ai.tools.openeo_tools import OpenEOTools

        tools = OpenEOTools.__new__(OpenEOTools)
        graph = tools.build_algorithm_graph(
            algorithm="urban_growth", collection="sentinel-2-l2a", spatial_extent=BBOX,
            pre_event=PRE, post_event=POST,
        )
        assert graph["change"]["arguments"]["cube1"] == {"from_node": "reduce_time2"}


class TestMethodGate:
    PROMPT = "Analyze burn severity using dNBR for Los Angeles comparing November 2024 vs January 2025"

    def test_rejects_burn_severity_without_subtraction(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        graph = _monthly_nbr_graph()
        graph["save"]["arguments"]["format"] = "netCDF"
        assert "no subtraction" in check_graph_method(self.PROMPT, graph)

    def test_accepts_the_template_including_as_json_string(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        assert check_graph_method(self.PROMPT, _burn_graph()) is None
        assert check_graph_method(self.PROMPT, json.dumps(_burn_graph())) is None
        assert check_graph_method(self.PROMPT, {"process_graph": _burn_graph()}) is None

    def test_rejects_series_saved_to_single_period_format(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        assert "first period" in check_graph_method("monthly NDVI for Berlin", _monthly_nbr_graph())

    def test_allows_series_saved_to_netcdf(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        graph = _monthly_nbr_graph()
        graph["save"]["arguments"]["format"] = "netCDF"
        assert check_graph_method("monthly NDVI for Berlin", graph) is None

    def test_allows_series_reduced_before_saving(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        graph = _monthly_nbr_graph()
        graph["reduce"] = {"process_id": "reduce_dimension", "arguments": {"data": {"from_node": "monthly"}}}
        graph["save"]["arguments"]["data"] = {"from_node": "reduce"}
        assert check_graph_method("monthly NDVI for Berlin", graph) is None

    def test_ignores_unparseable_graph(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        assert check_graph_method(self.PROMPT, "not json") is None


class TestDroppedTimeSteps:
    def _series(self):
        return xr.DataArray(
            np.ones((3, 4, 4), dtype="float32"),
            dims=("t", "y", "x"),
            coords={
                "t": np.array(["2024-11-01", "2024-12-01", "2025-01-01"], dtype="datetime64[ns]"),
                "y": [34.0, 33.9, 33.8, 33.7],
                "x": [-118.5, -118.4, -118.3, -118.2],
            },
        )

    def test_writer_tags_file_and_reports_warning(self, tmp_path):
        from openeo_app.storage.results import ResultStorage
        from openeo_ai.tools.raster_provenance import time_slice_warning

        path = tmp_path / "result.tif"
        note = ResultStorage.__new__(ResultStorage)._save_geotiff(self._series(), path)
        assert "1 of 3" in note
        warning = time_slice_warning(str(path))
        assert "one of 3" in warning and "2024-11-01" in warning

    def test_single_time_step_is_not_flagged(self, tmp_path):
        from openeo_app.storage.results import ResultStorage
        from openeo_ai.tools.raster_provenance import time_slice_warning

        path = tmp_path / "result.tif"
        data = self._series().isel(t=[0])
        assert ResultStorage.__new__(ResultStorage)._save_geotiff(data, path) is None
        assert time_slice_warning(str(path)) is None

    def test_analysis_tools_surface_the_warning(self, tmp_path):
        from openeo_ai.sdk.inprocess_tools import _wrap_handler
        from openeo_app.storage.results import ResultStorage

        path = tmp_path / "result.tif"
        ResultStorage.__new__(ResultStorage)._save_geotiff(self._series(), path)

        async def handler(args):
            return {"content": [{"type": "text", "text": "stats"}]}

        result = asyncio.run(_wrap_handler("result_inspect", handler)({"file_path": str(path)}))
        assert "one of 3" in result["content"][-1]["text"]

    def test_clean_raster_gets_no_warning(self, tmp_path):
        from openeo_ai.sdk.inprocess_tools import _wrap_handler

        path = tmp_path / "clean.tif"
        with rasterio.open(path, "w", driver="GTiff", height=1, width=1, count=1, dtype="float32",
                           transform=from_origin(0, 0, 1, 1)) as ds:
            ds.write(np.ones((1, 1, 1), dtype="float32"))

        async def handler(args):
            return {"content": [{"type": "text", "text": "stats"}]}

        result = asyncio.run(_wrap_handler("result_inspect", handler)({"file_path": str(path)}))
        assert len(result["content"]) == 1


class TestToolAccess:
    def test_burn_template_and_validator_are_in_main_sessions(self):
        from openeo_ai.sdk.client import CORE_TOOL_NAMES

        assert {"openeo_algorithm", "openeo_validate_graph"} <= CORE_TOOL_NAMES


class TestClarificationReachesUser:
    def test_question_tool_is_not_pre_approved(self):
        from openeo_ai.sdk.claude_sdk_bridge import _core_allowed_tools

        assert "mcp__openeo__AskUserQuestion" not in _core_allowed_tools()

    def test_empty_answer_is_not_presented_as_a_choice(self):
        from openeo_ai.sdk.inprocess_tools import _ask_user_question_tool

        result = asyncio.run(_ask_user_question_tool().handler({"questions": []}))
        assert "no answer" in result["content"][0]["text"]


class TestAutoSave:
    @pytest.mark.parametrize("path,expected", [
        ("/tmp/openeo_results/a/result.tif", True),
        ("/var/lib/jonaai/results/results/a/result.tif", True),
        ("/var/lib/jonaai/.openeo_jobs/results/a/result.tif", False),
    ])
    def test_results_under_configured_storage_are_archived(self, monkeypatch, path, expected):
        from openeo_ai.sdk.viz_raster import _is_unarchived_result

        monkeypatch.setenv("RESULT_STORAGE_PATH", "/var/lib/jonaai/results")
        assert _is_unarchived_result(path) is expected


class TestBurnGateSignAndSeparateRasters:
    PRE = ["2023-07-01", "2023-07-31"]
    POST = ["2023-09-01", "2023-09-30"]

    def _diff(self, first, second, via_merge):
        loads = {
            "pre": {"process_id": "load_collection", "arguments": {"temporal_extent": first}},
            "post": {"process_id": "load_collection", "arguments": {"temporal_extent": second}},
            "reduce_pre": {"process_id": "reduce_dimension", "arguments": {"data": {"from_node": "pre"}}},
            "reduce_post": {"process_id": "reduce_dimension", "arguments": {"data": {"from_node": "post"}}},
        }
        if via_merge:
            loads["diff"] = {
                "process_id": "merge_cubes",
                "arguments": {
                    "cube1": {"from_node": "reduce_pre"},
                    "cube2": {"from_node": "reduce_post"},
                    "overlap_resolver": {"process_graph": {"s": {
                        "process_id": "subtract",
                        "arguments": {"x": {"from_parameter": "x"}, "y": {"from_parameter": "y"}},
                        "result": True,
                    }}},
                },
            }
        else:
            loads["diff"] = {
                "process_id": "subtract",
                "arguments": {"x": {"from_node": "reduce_pre"}, "y": {"from_node": "reduce_post"}},
            }
        return loads

    def test_pre_minus_post_passes(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        assert check_graph_method("dNBR for Maui", self._diff(self.PRE, self.POST, True)) is None
        assert check_graph_method("dNBR for Maui", self._diff(self.PRE, self.POST, False)) is None

    def test_post_minus_pre_is_denied_in_both_forms(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        for via_merge in (True, False):
            denial = check_graph_method("burn severity for Maui", self._diff(self.POST, self.PRE, via_merge))
            assert "regrowth" in denial

    def test_sign_rule_applies_only_to_burn_questions(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        assert check_graph_method("NDVI change for Maui", self._diff(self.POST, self.PRE, True)) is None

    def test_separate_nbr_rasters_are_allowed_when_asked_for(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        no_diff = {"l": {"process_id": "load_collection", "arguments": {}}}
        prompt = "Compute NBR for Maui for July and for September and report the dNBR burn severity"
        assert check_graph_method(prompt, no_diff) is None

    def test_plain_dnbr_question_still_needs_a_subtraction(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        no_diff = {"l": {"process_id": "load_collection", "arguments": {}}}
        assert "no subtraction" in check_graph_method("Burn severity using dNBR for LA", no_diff)
        assert "no subtraction" in check_graph_method("delta NBR for LA", no_diff)

    def test_end_of_run_check_flags_a_burn_run_with_no_difference(self):
        from openeo_ai.sdk.claude_sdk_bridge import _method_gap_payload

        prompt = "Compute NBR for July and September and report the dNBR burn severity"
        assert _method_gap_payload(prompt, ["load_collection"], ["openeo_create_job"])["type"] == "method_gap"
        # Band math in NBR lists subtract too, so only a real comparison of dates counts.
        assert _method_gap_payload(prompt, ["subtract"], ["openeo_create_job"])["type"] == "method_gap"
        assert _method_gap_payload(prompt, ["subtract"], ["openeo_create_job"], True) is None


class TestReviewCases:
    PRE = ["2023-07-01", "2023-07-31"]
    POST = ["2023-09-01", "2023-09-30"]

    def _one_load(self, first, second):
        return {
            "l": {"process_id": "load_collection", "arguments": {}},
            "a": {"process_id": "filter_temporal", "arguments": {"data": {"from_node": "l"}, "extent": first}},
            "b": {"process_id": "filter_temporal", "arguments": {"data": {"from_node": "l"}, "extent": second}},
            "d": {"process_id": "subtract", "arguments": {"x": {"from_node": "a"}, "y": {"from_node": "b"}}},
        }

    def test_sign_is_checked_when_one_load_is_filtered_twice(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        assert "regrowth" in check_graph_method("dNBR for Maui", self._one_load(self.POST, self.PRE))
        assert check_graph_method("dNBR for Maui", self._one_load(self.PRE, self.POST)) is None

    def test_band_math_subtract_is_not_a_comparison_of_dates(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        in_callback = {
            "l": {"process_id": "load_collection", "arguments": {"temporal_extent": self.PRE}},
            "r": {"process_id": "reduce_dimension", "arguments": {"data": {"from_node": "l"}, "reducer": {"process_graph": {
                "s": {"process_id": "subtract", "arguments": {"x": 1, "y": 2}, "result": True}}}}},
        }
        assert "no subtraction" in check_graph_method("burn severity for Maui", in_callback)
        same_period = {
            "l": {"process_id": "load_collection", "arguments": {"temporal_extent": self.PRE}},
            "n": {"process_id": "array_element", "arguments": {"data": {"from_node": "l"}, "label": "nir"}},
            "w": {"process_id": "array_element", "arguments": {"data": {"from_node": "l"}, "label": "swir22"}},
            "s": {"process_id": "subtract", "arguments": {"x": {"from_node": "n"}, "y": {"from_node": "w"}}},
        }
        assert "no subtraction" in check_graph_method("burn severity for Maui", same_period)

    def test_band_reduction_does_not_protect_a_monthly_series(self):
        from openeo_ai.sdk.method_gate import check_graph_method

        graph = {
            "l": {"process_id": "load_collection", "arguments": {}},
            "m": {"process_id": "aggregate_temporal_period", "arguments": {"data": {"from_node": "l"}, "period": "month"}},
            "b": {"process_id": "reduce_dimension", "arguments": {"data": {"from_node": "m"}, "dimension": "bands"}},
            "s": {"process_id": "save_result", "arguments": {"data": {"from_node": "b"}, "format": "GTiff"}},
        }
        assert "first" in check_graph_method("monthly NDVI", graph)
