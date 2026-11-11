# ABOUTME: Zonal statistics per geometry, replacing upstream aggregate_spatial, whose
# ABOUTME: xvec call fails on process-graph reducers.
"""Fixed aggregate_spatial returning one row of statistics per geometry and period."""

import logging

import geopandas as gpd
import numpy as np
import shapely.geometry
import xarray as xr
from rioxarray.exceptions import NoDataInBounds, OneDimensionalRaster

from .reduce_dimension_fix import _get_reduce_function, resolve_dimension

logger = logging.getLogger(__name__)


def _as_geodataframe(geometries) -> gpd.GeoDataFrame:
    if isinstance(geometries, gpd.GeoDataFrame):
        return geometries
    if isinstance(geometries, xr.Dataset):
        return geometries.xvec.to_geodataframe()
    if isinstance(geometries, dict):
        if geometries.get("type") == "FeatureCollection":
            return gpd.GeoDataFrame.from_features(geometries, crs="EPSG:4326")
        if geometries.get("type") == "Feature":
            geometries = geometries["geometry"]
        return gpd.GeoDataFrame(geometry=[shapely.geometry.shape(geometries)], crs="EPSG:4326")
    raise ValueError(f"Unsupported geometries for aggregate_spatial: {type(geometries).__name__}")


def load_geojson(data, properties=None, **kwargs):
    """Pass GeoJSON through to aggregate_spatial, which reads every geometry type."""
    return data


def aggregate_spatial(data, geometries, reducer, **kwargs):
    """Reduce the cube over each geometry. A geometry outside the cube gets a null value."""
    reduce_func = _get_reduce_function(reducer)
    if reduce_func is None:
        raise ValueError("aggregate_spatial supports mean, median, min, max, and sum reducers.")

    crs = data.rio.crs
    if crs is None:
        crs = "EPSG:4326"
        data = data.rio.write_crs(crs)
    gdf = _as_geodataframe(geometries).to_crs(crs)
    x_dim, y_dim = resolve_dimension("x", data), resolve_dimension("y", data)
    data = data.rio.set_spatial_dims(x_dim=x_dim, y_dim=y_dim)
    other_dims = [dim for dim in data.dims if dim not in (x_dim, y_dim)]

    rows = []
    for index, geometry in enumerate(gdf.geometry):
        try:
            clipped = data.rio.clip([geometry], drop=True, all_touched=True)
        except (NoDataInBounds, OneDimensionalRaster) as exc:
            logger.info(f"aggregate_spatial: geometry {index} has no data: {exc}")
            clipped = None
        # Outside the handler: a failed read must fail the job, not look like no data.
        clipped = clipped.compute() if clipped is not None else None
        for labels in _label_combinations(data, other_dims):
            row = {"geometry": index, **{dim: str(value) for dim, value in labels.items()}}
            if clipped is None:
                row.update(value=None, valid_pixels=0)
            else:
                values = np.asarray(clipped.sel(labels).values, dtype="float64")
                valid = int(np.isfinite(values).sum())
                row.update(value=float(reduce_func(values)) if valid else None, valid_pixels=valid)
            rows.append(row)
    return rows


def _label_combinations(data, dims):
    combinations = [{}]
    for dim in dims:
        combinations = [{**combo, dim: value} for combo in combinations for value in data[dim].values]
    return combinations
