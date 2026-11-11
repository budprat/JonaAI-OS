# ABOUTME: Pixel-truth CI — pins real recorded sentinel-2-l2a values through the
# ABOUTME: unmodified executor read path (BOA offset, windowed read, SCL-masked NDVI).
import json
import os
from pathlib import Path

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "pixel_truth"
BBOX = [75.0, 30.5, 75.2, 30.7]

_LIVE_PIXEL = bool(os.environ.get("JONA_LIVE_PIXEL_CHECK"))


def _load_item():
    import pystac

    item = pystac.Item.from_dict(json.loads((FIXTURE_DIR / "item.json").read_text()))
    for key in ("nir", "red", "scl"):
        item.assets[key].href = str(FIXTURE_DIR / f"{key}.tif")
    return item


def _pins():
    return json.loads((FIXTURE_DIR / "pins.json").read_text())


def test_recorded_item_carries_reflectance_metadata():
    # The recorded STAC document is verbatim from Earth Search: reflectance
    # bands must carry raster:bands scale+offset, and the archive's
    # boa_offset_applied marker must be present (the 2026-07-11 offset trap).
    item = _load_item()
    for key in ("nir", "red"):
        rb = (item.assets[key].extra_fields.get("raster:bands") or [{}])[0]
        assert "scale" in rb and "offset" in rb
    assert item.properties.get("earthsearch:boa_offset_applied") is True


def test_boa_offset_honored_on_recorded_item():
    from openeo_ai.agentic_eo.executor import _resolve_scale_offset

    item = _load_item()
    scale, offset = _resolve_scale_offset(item, "red")
    assert scale == pytest.approx(0.0001)
    # boa_offset_applied=True → the advertised offset must NOT be applied again.
    assert offset == 0.0


def test_read_band_returns_recorded_dns():
    from openeo_ai.agentic_eo.executor import _read_band

    item = _load_item()
    pins = _pins()
    for key in ("nir", "red", "scl"):
        arr, _ = _read_band(item, key, BBOX)
        assert arr.shape == (128, 128)
        for spot in pins["spots"][key]:
            r, c, dn = spot["row"], spot["col"], spot["dn"]
            assert int(arr[r, c]) == int(dn), f"{key}[{r},{c}]"


def test_process_scene_pins_aoi_median_ndvi():
    from openeo_ai.agentic_eo.decompose import _bbox_area_km2
    from openeo_ai.agentic_eo.executor import _process_scene
    from openeo_ai.agentic_eo.models import Variable

    item = _load_item()
    pins = _pins()
    stat = _process_scene(item, BBOX, Variable.NDVI, _bbox_area_km2(BBOX))
    assert stat.read_error is None
    assert stat.value == pytest.approx(pins["ndvi_median"], abs=1e-6)
    assert stat.valid_fraction == pytest.approx(pins["valid_fraction"], abs=1e-6)
    # Independent physical sanity (guards against pin-whatever-it-outputs):
    # mid-June Punjab is pre-monsoon fallow — low but positive NDVI, in range.
    assert -1.0 <= stat.value <= 1.0
    assert stat.value > 0.05
    # The AOI crosses the tile edge: coverage hovers just above the keep
    # threshold, which is exactly the masking behavior worth pinning.
    assert stat.valid_fraction >= 0.5


@pytest.mark.skipif(not _LIVE_PIXEL,
                    reason="live network variant; set JONA_LIVE_PIXEL_CHECK=1")
def test_live_same_item_reproduces_pin():
    from pystac_client import Client

    from openeo_ai.agentic_eo.decompose import _bbox_area_km2
    from openeo_ai.agentic_eo.executor import EARTH_SEARCH, _process_scene
    from openeo_ai.agentic_eo.models import Variable

    pins = _pins()
    items = list(Client.open(EARTH_SEARCH).search(ids=[pins["item_id"]]).items())
    assert len(items) == 1
    stat = _process_scene(items[0], BBOX, Variable.NDVI, _bbox_area_km2(BBOX))
    assert stat.value == pytest.approx(pins["ndvi_median"], abs=1e-3)
