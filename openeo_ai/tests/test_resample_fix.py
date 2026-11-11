"""Spatial resampling across latitude/longitude and y/x dimension names."""
import numpy as np
import odc.geo.xr  # noqa: F401
import rioxarray  # noqa: F401
from odc.geo.geobox import GeoBox
from odc.geo.xr import xr_zeros

from openeo_app.execution.executor import _import_processes

_processes = _import_processes()
resample_cube_spatial = _processes["resample_cube_spatial"]
resample_spatial = _processes["resample_spatial"]


def _cube(crs="EPSG:4326", bbox=(10, 50, 10.02, 50.02), resolution=0.0001):
    box = GeoBox.from_bbox(bbox, crs, resolution=resolution)
    return xr_zeros(box, dtype="float32").expand_dims(time=[np.datetime64("2023-01-01")]).expand_dims(bands=["red"])


def test_projected_result_is_named_y_x_and_carries_its_crs():
    result = resample_spatial(_cube(), projection="EPSG:32632", resolution=20)
    assert {"y", "x"} <= set(result.dims)
    assert result.odc.geobox.crs.epsg == 32632
    assert "32632" in str(result.attrs["crs"])


def test_lonlat_result_keeps_its_names():
    result = resample_spatial(_cube(), resolution=0.0002)
    assert {"latitude", "longitude"} <= set(result.dims)


def test_lonlat_cube_aligns_to_projected_target():
    # The audit failure: Dimensions {'latitude', 'longitude'} do not exist.
    target = resample_spatial(_cube(), projection="EPSG:32632", resolution=20)
    result = resample_cube_spatial(_cube(), target)
    assert result.sizes["x"] == target.sizes["x"] and result.sizes["y"] == target.sizes["y"]
    assert result.odc.geobox.crs.epsg == 32632


def test_lonlat_cube_aligns_to_lonlat_target():
    result = resample_cube_spatial(_cube(), _cube(resolution=0.0002))
    assert {"latitude", "longitude"} <= set(result.dims)
