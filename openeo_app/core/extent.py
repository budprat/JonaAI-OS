# ABOUTME: Normalizes spatial extents to lon/lat degrees and picks a load grid whose
# ABOUTME: pixels are about the same ground size at every latitude.
"""Spatial extent helpers shared by the load path and the agent's permission checks."""

import math
from typing import Any, Dict, Optional, Tuple

BASE_RESOLUTION_DEG = 0.0001  # ~11 m north-south
_MAX_LON_STRETCH = 10.0  # keeps the longitude step sane near the poles


def _is_lonlat(crs: Any) -> bool:
    if crs is None:
        return True
    text = str(crs).strip().upper()
    if text in {"", "4326", "EPSG:4326", "WGS84", "CRS84", "OGC:CRS84"}:
        return True
    from pyproj import CRS

    return CRS.from_user_input(crs).to_epsg() == 4326


def extent_to_lonlat(extent: Any) -> Optional[Dict[str, float]]:
    """Return an extent as west/south/east/north in EPSG:4326 degrees.

    Accepts a dict or an object with those attributes. Returns None when the extent
    has no usable numeric bounds or its CRS can't be understood.
    """
    get = extent.get if isinstance(extent, dict) else lambda key, default=None: getattr(extent, key, default)
    try:
        west, south = float(get("west")), float(get("south"))
        east, north = float(get("east")), float(get("north"))
    except (TypeError, ValueError):
        return None
    crs = get("crs", None)
    try:
        if not _is_lonlat(crs):
            from pyproj import CRS, Transformer

            transformer = Transformer.from_crs(CRS.from_user_input(crs), "EPSG:4326", always_xy=True)
            west, south, east, north = transformer.transform_bounds(west, south, east, north, densify_pts=21)
    except Exception:
        return None
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        return None
    return {"west": west, "south": south, "east": east, "north": north}


def load_resolution(south: Optional[float] = None, north: Optional[float] = None) -> Tuple[float, float]:
    """Return (x, y) step in degrees so pixels are roughly square on the ground.

    A degree of longitude shrinks with latitude, so a fixed step gives narrower pixels
    and more of them away from the equator.
    """
    if south is None or north is None:
        return BASE_RESOLUTION_DEG, BASE_RESOLUTION_DEG
    middle = math.radians((south + north) / 2)
    stretch = min(1 / max(math.cos(middle), 1e-6), _MAX_LON_STRETCH)
    return BASE_RESOLUTION_DEG * stretch, BASE_RESOLUTION_DEG
