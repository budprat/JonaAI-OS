# ABOUTME: One-off live recorder for the pixel-truth CI fixture — captures a real
# ABOUTME: sentinel-2-l2a item + identity-read band windows + pinned executor outputs.
"""Run once (network required):  .venv/bin/python scripts/record_pixel_fixture.py

Records into openeo_ai/tests/fixtures/pixel_truth/:
  item.json  — the STAC item document verbatim (metadata incl. raster:bands)
  {nir,red,scl}.tif — the executor's own 128x128 windowed DN reads, written with
                      the exact UTM bounds geometry so the test's re-read through
                      the unmodified _read_band is an identity read
  pins.json  — spot DNs + the live _process_scene NDVI median / valid fraction
"""
import json
from pathlib import Path

import rasterio
from pystac_client import Client
from rasterio.transform import from_bounds as transform_from_bounds
from rasterio.warp import transform_bounds

from openeo_ai.agentic_eo.decompose import _bbox_area_km2
from openeo_ai.agentic_eo.executor import EARTH_SEARCH, _process_scene, _read_band, _search
from openeo_ai.agentic_eo.models import TimeRange, Variable

BBOX = [75.0, 30.5, 75.2, 30.7]
WINDOW = TimeRange(start=__import__("datetime").date(2024, 6, 1),
                   end=__import__("datetime").date(2024, 6, 30))
OUT = Path("openeo_ai/tests/fixtures/pixel_truth")
SPOTS = [(0, 0), (32, 96), (64, 64), (96, 32), (127, 127)]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    client = Client.open(EARTH_SEARCH)
    items = _search(client, BBOX, WINDOW, max_cloud=10.0, limit=3)
    if not items:
        raise SystemExit("no low-cloud items found — widen the search")
    item = items[0]
    print(f"item: {item.id}  cloud={item.properties.get('eo:cloud_cover')}")

    (OUT / "item.json").write_text(json.dumps(item.to_dict(), indent=1))

    pins = {"item_id": item.id, "bbox": BBOX, "spots": {}}
    for key in ("nir", "red", "scl"):
        arr, _ = _read_band(item, key, BBOX)
        with rasterio.open(item.assets[key].href) as src:
            crs = src.crs
        ub = transform_bounds("EPSG:4326", crs, *BBOX, densify_pts=21)
        profile = {
            "driver": "GTiff", "height": 128, "width": 128, "count": 1,
            "dtype": arr.dtype.name, "crs": crs,
            "transform": transform_from_bounds(*ub, 128, 128),
        }
        with rasterio.open(OUT / f"{key}.tif", "w", **profile) as dst:
            dst.write(arr, 1)
        pins["spots"][key] = [
            {"row": r, "col": c, "dn": int(arr[r, c])} for r, c in SPOTS
        ]
        print(f"  {key}: dtype={arr.dtype.name} spots={pins['spots'][key]}")

    stat = _process_scene(item, BBOX, Variable.NDVI, _bbox_area_km2(BBOX))
    if stat.read_error or stat.value is None:
        raise SystemExit(f"live _process_scene did not produce a value: {stat}")
    pins["ndvi_median"] = stat.value
    pins["valid_fraction"] = stat.valid_fraction
    (OUT / "pins.json").write_text(json.dumps(pins, indent=1))
    print(f"pinned: ndvi_median={stat.value:.6f} valid_fraction={stat.valid_fraction:.6f}")
    print(f"wrote {OUT}/")


if __name__ == "__main__":
    main()
