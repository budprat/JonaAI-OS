# ABOUTME: Corrects upstream spatial resampling when the source and target cubes name their
# ABOUTME: spatial dimensions differently (latitude/longitude versus y/x).
"""Fixed resampling processes.

Upstream `resample_cube_spatial` restores the source's dimension order after
reprojecting. When the target uses y/x and the source latitude/longitude, those names
no longer exist and the job fails. Upstream `resample_spatial` keeps the names
latitude/longitude on a cube it has just projected to metres.
"""


def fixed_resampling(upstream_cube_spatial, upstream_spatial):
    """Wrap the discovered upstream processes.

    They come from the executor's process discovery because importing the upstream
    package directly fails when its optional UDF dependency isn't installed.
    """

    def resample_cube_spatial(data, target, method="near", options=None, **kwargs):
        """Align `data` to `target`'s grid, using `target`'s dimension names in the result."""
        pairs = ((data.openeo.y_dim, target.openeo.y_dim), (data.openeo.x_dim, target.openeo.x_dim))
        renames = {source: wanted for source, wanted in pairs if source and wanted and source != wanted}
        if renames:
            data = data.rename(renames)
        return upstream_cube_spatial(data, target, method=method, options=options)

    def resample_spatial(data, projection=None, resolution=0, method="near", align="upper-left", **kwargs):
        """Resample, naming spatial dimensions y/x whenever the result is in projected units."""
        result = upstream_spatial(
            data, projection=projection, resolution=resolution, method=method, align=align
        )
        crs = result.odc.geobox.crs if result.odc.geobox is not None else None
        if crs is not None:
            # Upstream leaves the source CRS here after reprojecting.
            result.attrs["crs"] = str(crs)
        if crs is not None and crs.projected:
            renames = {old: new for old, new in (("latitude", "y"), ("longitude", "x")) if old in result.dims}
            if renames:
                result = result.rename(renames)
        return result

    return resample_cube_spatial, resample_spatial
