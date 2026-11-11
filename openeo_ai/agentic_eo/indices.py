# ABOUTME: Correctness-critical spectral-index core — reflectance conversion (BOA offset),
# ABOUTME: SCL cloud/shadow masking, safe division, and NaN-aware AOI reduction. Pure numpy, no network.

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

import numpy as np

from .models import Variable

# --------------------------------------------------------------------------- #
# Band requirements — Sentinel-2 L2A asset keys are lowercase common names.
# nir=B08(10m), red=B04, green=B03, blue=B02, swir16=B11(20m), swir22=B12(20m).
# --------------------------------------------------------------------------- #

REQUIRED_BANDS: Dict[Variable, Tuple[str, ...]] = {
    Variable.NDVI: ("nir", "red"),
    Variable.NDWI: ("green", "nir"),     # McFeeters — open water
    Variable.NDMI: ("nir", "swir16"),    # Gao — vegetation moisture (NOT water bodies)
    Variable.NBR: ("nir", "swir22"),     # burn severity
    Variable.EVI: ("nir", "red", "blue"),
}

# SCL classes to MASK OUT: 0=NoData, 1=Saturated/defective, 2=Cast/dark shadow,
# 3=Cloud shadow, 8=Cloud medium, 9=Cloud high, 10=Thin cirrus, 11=Snow/ice.
SCL_MASK_CLASSES = frozenset({0, 1, 2, 3, 8, 9, 10, 11})
# The complement — clear-sky valid surface: 4=Vegetation, 5=Not-vegetated(bare),
# 6=Water, 7=Unclassified. This is the general "usable pixel" keep-set.
SCL_KEEP_CLASSES = frozenset({4, 5, 6, 7})


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #

def safe_divide(num: np.ndarray, den: np.ndarray) -> np.ndarray:
    """num/den with 0-denominator → NaN (never inf, never a bogus finite value)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.divide(
            num, den,
            out=np.full_like(num, np.nan, dtype=np.float32),
            where=den != 0,
        )


def to_reflectance(dn: np.ndarray, scale: float, offset: float) -> np.ndarray:
    """
    DN → surface reflectance: value = DN*scale + offset.

    For the current earth-search Collection-1 archive this is DN*0.0001 - 0.1,
    i.e. (DN-1000)/10000. scale/offset MUST come from each item's raster:bands,
    never the collection default (which shows offset=0 — the classic silent bug).

    NOTE: this does not itself handle nodata. DN==0 is nodata and MUST be detected
    on the raw DN before this offset is applied (0 → -0.1 would look like valid water).
    """
    return dn.astype(np.float32) * np.float32(scale) + np.float32(offset)


# --------------------------------------------------------------------------- #
# Index formulas (operate on reflectance dicts keyed by band name)
# --------------------------------------------------------------------------- #

def _ndvi(r: Dict[str, np.ndarray]) -> np.ndarray:
    return safe_divide(r["nir"] - r["red"], r["nir"] + r["red"])


def _ndwi(r: Dict[str, np.ndarray]) -> np.ndarray:
    return safe_divide(r["green"] - r["nir"], r["green"] + r["nir"])


def _ndmi(r: Dict[str, np.ndarray]) -> np.ndarray:
    return safe_divide(r["nir"] - r["swir16"], r["nir"] + r["swir16"])


def _nbr(r: Dict[str, np.ndarray]) -> np.ndarray:
    return safe_divide(r["nir"] - r["swir22"], r["nir"] + r["swir22"])


def _evi(r: Dict[str, np.ndarray]) -> np.ndarray:
    # EVI = 2.5·(NIR−Red)/(NIR + 6·Red − 7.5·Blue + 1). Needs true reflectance.
    den = r["nir"] + 6.0 * r["red"] - 7.5 * r["blue"] + 1.0
    return 2.5 * safe_divide(r["nir"] - r["red"], den)


_INDEX_FUNCS: Dict[Variable, Callable[[Dict[str, np.ndarray]], np.ndarray]] = {
    Variable.NDVI: _ndvi,
    Variable.NDWI: _ndwi,
    Variable.NDMI: _ndmi,
    Variable.NBR: _nbr,
    Variable.EVI: _evi,
}


# --------------------------------------------------------------------------- #
# Public: DN bands → masked index array
# --------------------------------------------------------------------------- #

def compute_index(
    variable: Variable,
    dn_bands: Dict[str, np.ndarray],
    scale_offset: Dict[str, Tuple[float, float]],
    scl: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Compute a masked spectral index from raw DN arrays.

    Pipeline (order matters — every step guards a silent failure):
      1. nodata mask from raw DN==0 on EVERY required band (BEFORE offset).
      2. DN → reflectance per band using that item's scale/offset.
      3. compute the index with safe division (0-denominator → NaN).
      4. clip to [-1, 1] (defensive; catches atmospheric/EVI-denominator blowups).
      5. apply nodata ∪ (not SCL-keep) → NaN (never 0 — 0 is a real index value).

    Returns a float32 array; invalid pixels are NaN.
    """
    if variable not in _INDEX_FUNCS:
        raise ValueError(f"Unsupported index: {variable}")

    needed = REQUIRED_BANDS[variable]
    missing = [b for b in needed if b not in dn_bands]
    if missing:
        raise ValueError(f"{variable.value} needs bands {needed}; missing {missing}")

    shape = dn_bands[needed[0]].shape

    # 1. nodata from raw DN==0 across all needed bands
    nodata = np.zeros(shape, dtype=bool)
    for b in needed:
        nodata |= (dn_bands[b] == 0)

    # 2. reflectance per band
    refl: Dict[str, np.ndarray] = {}
    for b in needed:
        s, o = scale_offset[b]
        refl[b] = to_reflectance(dn_bands[b], s, o)

    # 3. index
    idx = _INDEX_FUNCS[variable](refl)

    # 4. clip
    idx = np.clip(idx, -1.0, 1.0)

    # 5. SCL keep-mask + nodata → NaN
    if scl is not None:
        keep = np.isin(scl, list(SCL_KEEP_CLASSES))
    else:
        keep = np.ones(shape, dtype=bool)
    idx = idx.copy()
    idx[nodata | ~keep] = np.nan
    return idx.astype(np.float32)


def reduce_aoi(idx: np.ndarray) -> Tuple[Optional[float], float]:
    """
    Reduce a masked index array over the AOI.

    Returns (median, valid_fraction). median is None when no valid pixels remain.
    NaN-aware: masked pixels never contribute to the median.
    """
    valid = np.isfinite(idx)
    frac = float(valid.mean()) if valid.size else 0.0
    value = float(np.nanmedian(idx)) if valid.any() else None
    return value, frac


# McFeeters: open water is NDWI > 0; the boundary value itself is a mixed pixel, not water.
WATER_NDWI_THRESHOLD = 0.0


def water_area_ha(idx: np.ndarray, aoi_area_km2: float) -> Optional[float]:
    """
    Open-water surface area (hectares) from a masked NDWI array over the AOI.

    The water decision metric is derived surface area, not the index value: water pixels are
    NDWI > WATER_NDWI_THRESHOLD among VALID pixels, and their fraction is scaled to the AOI
    bbox area (masked pixels are assumed to share the valid-area water fraction, so a cloudier
    scene does not read as a spurious area dip). None when no valid pixels remain.
    """
    valid = np.isfinite(idx)
    if not valid.any():
        return None
    frac = float((idx[valid] > WATER_NDWI_THRESHOLD).mean())
    return frac * aoi_area_km2 * 100.0
