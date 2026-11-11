"""Band math written the standard openEO way: a reducer over bands using array_element."""
import dask.array as da
import numpy as np
import rioxarray  # noqa: F401
import pytest
import xarray as xr

from openeo_app.execution.executor import ProcessGraphExecutor


def _cube(*_args, **_kwargs):
    values = np.zeros((2, 3, 4, 5), dtype="float32")
    values[0] = 0.1  # red
    values[1] = 0.5  # nir
    return xr.DataArray(
        da.from_array(values, chunks=(1, 3, 2, 2)).rechunk({0: -1}),
        dims=("bands", "time", "latitude", "longitude"),
        coords={
            "bands": ["red", "nir"],
            "time": np.array(["2023-07-01", "2023-07-06", "2023-07-11"], dtype="datetime64[ns]"),
            "latitude": np.linspace(52.0, 52.3, 4),
            "longitude": np.linspace(13.0, 13.4, 5),
        },
    )


def _element(selector):
    return {"process_id": "array_element", "arguments": {"data": {"from_parameter": "data"}, **selector}}


def _graph(nir, red):
    return {
        "load": {"process_id": "load_collection", "arguments": {"id": "x"}},
        "ndvi": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": "load"},
                "dimension": "bands",
                "reducer": {"process_graph": {
                    "n": _element(nir),
                    "r": _element(red),
                    "d": {
                        "process_id": "normalized_difference",
                        "arguments": {"x": {"from_node": "n"}, "y": {"from_node": "r"}},
                        "result": True,
                    },
                }},
            },
        },
        "median": {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": "ndvi"},
                "dimension": "t",
                "reducer": {"process_graph": {
                    "m": {"process_id": "median", "arguments": {"data": {"from_parameter": "data"}}, "result": True},
                }},
            },
            "result": True,
        },
    }


@pytest.mark.parametrize(
    "nir,red",
    [({"label": "nir"}, {"label": "red"}), ({"index": 1}, {"index": 0})],
)
def test_ndvi_by_label_and_index(nir, red):
    executor = ProcessGraphExecutor(stac_api_url="https://example.invalid/")
    executor.register_process("load_collection", _cube)
    result = executor.execute(_graph(nir, red), parameters={})
    assert set(result.dims) == {"latitude", "longitude"}
    assert np.allclose(np.asarray(result), (0.5 - 0.1) / (0.5 + 0.1))


def test_unknown_band_fails_rather_than_using_the_first():
    executor = ProcessGraphExecutor(stac_api_url="https://example.invalid/")
    executor.register_process("load_collection", _cube)
    with pytest.raises(ValueError, match="isn't in this data"):
        executor.execute(_graph({"label": "swir"}, {"label": "red"}), parameters={})


def _cube_for(bands):
    def load(*_args, **_kwargs):
        values = {"blue": 0.05, "red": 0.1, "green": 0.08, "nir": 0.5, "swir16": 0.2, "swir22": 0.15, "scl": 4.0}
        layers = np.stack([np.full((2, 4, 5), values[band], dtype="float32") for band in bands])
        return xr.DataArray(
            da.from_array(layers, chunks=(-1, 2, 2, 2)),
            dims=("bands", "time", "latitude", "longitude"),
            coords={
                "bands": list(bands),
                "time": np.array(["2023-07-01", "2023-07-06"], dtype="datetime64[ns]"),
                "latitude": np.linspace(52.09, 52.0, 4),
                "longitude": np.linspace(13.0, 13.1, 5),
            },
        ).rio.write_crs("EPSG:4326")
    return load


def _tools():
    from openeo_ai.tools.openeo_tools import OpenEOTools

    return OpenEOTools.__new__(OpenEOTools)


EXTENT = {"west": 13.0, "south": 52.0, "east": 13.1, "north": 52.09}
PERIOD = ["2023-07-01", "2023-07-20"]


def _run_template(graph, bands):
    executor = ProcessGraphExecutor(stac_api_url="https://example.invalid/")
    executor.register_process("load_collection", _cube_for(bands))
    return executor.execute(graph, parameters={})


@pytest.mark.parametrize("cloud_mask", [False, True])
def test_evi_template_computes_the_formula(cloud_mask):
    bands = ["blue", "green", "red", "nir", "swir16", "swir22"] + (["scl"] if cloud_mask else [])
    result = _run_template(
        _tools()._build_evi_graph("sentinel-2-l2a", EXTENT, PERIOD, "GTiff", cloud_mask=cloud_mask), bands
    )
    expected = 2.5 * (0.5 - 0.1) / (0.5 + 6 * 0.1 - 7.5 * 0.05 + 1)
    assert np.allclose(np.asarray(result), expected)


def test_multi_index_template_keeps_one_labeled_band_per_index():
    result = _run_template(
        _tools()._build_multi_index_graph("sentinel-2-l2a", EXTENT, PERIOD, "GTiff", ["ndvi", "ndwi"]),
        ["green", "nir", "red"],
    )
    assert list(result["bands"].values) == ["ndvi", "ndwi"]
    assert np.allclose(np.asarray(result.sel(bands="ndvi")), (0.5 - 0.1) / (0.5 + 0.1))
    assert np.allclose(np.asarray(result.sel(bands="ndwi")), (0.08 - 0.5) / (0.08 + 0.5))


def test_zonal_stats_template_reports_each_geometry():
    inside = {"type": "Polygon", "coordinates": [[[13.0, 52.0], [13.1, 52.0], [13.1, 52.09], [13.0, 52.09], [13.0, 52.0]]]}
    rows = _run_template(
        _tools()._build_zonal_stats_graph(
            collection="sentinel-2-l2a", spatial_extent=EXTENT, temporal_extent=PERIOD,
            output_format="JSON", geometries=inside, reducer="mean", index_name="ndvi",
        ),
        ["nir", "red"],
    )
    assert len(rows) == 1
    assert rows[0]["value"] == pytest.approx((0.5 - 0.1) / (0.5 + 0.1), abs=1e-5)
    assert rows[0]["valid_pixels"] > 0


def test_a_geometry_outside_the_cube_reports_no_value():
    from openeo_app.execution.executor import _import_processes

    processes = _import_processes()
    cube = _cube_for(["nir"])().isel(bands=0, time=0, drop=True)
    far = {"type": "Polygon", "coordinates": [[[100, 10], [101, 10], [101, 11], [100, 11], [100, 10]]]}
    median = lambda data=None, **_: np.nanmedian(data)  # noqa: E731
    median.__name__ = "median"
    assert processes["aggregate_spatial"](cube, far, median) == [{"geometry": 0, "value": None, "valid_pixels": 0}]


def test_paid_bucket_collections_explain_themselves():
    from openeo_app.core.exceptions import classify_error

    message = classify_error(RuntimeError("AccessDenied: Anonymous users cannot invoke requests against Requester Pays buckets."))
    assert "sentinel-2-l2a" in message["user_message"]
