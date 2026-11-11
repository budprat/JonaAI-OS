# ABOUTME: MethodPlan → ExecutionResult. Real STAC search + rasterio COG-overview reads over the AOI,
# ABOUTME: SCL masking, index derivation, per-scene AOI reduction, and change/trend/anomaly statistics.

from __future__ import annotations

import os

# GDAL tuning MUST be set before rasterio is imported (avoids per-open S3 LIST, bounds every HTTP read).
# Timeouts are load-bearing: without GDAL_HTTP_TIMEOUT a stalled /vsicurl socket blocks a worker
# forever. MULTIPLEX is OFF on purpose — HTTP/2 stream multiplexing over a shared connection is a
# known libcurl race under a thread pool (intermittent native crash); one connection per read is safer.
for _k, _v in {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
    "GDAL_HTTP_MULTIPLEX": "NO",
    "GDAL_HTTP_VERSION": "2",
    "VSI_CACHE": "TRUE",
    "GDAL_HTTP_MAX_RETRY": "2",
    "GDAL_HTTP_RETRY_DELAY": "1",
    "GDAL_HTTP_TIMEOUT": "20",         # per-request cap (s) — a healthy 128px read is <2s, so this
    "GDAL_HTTP_CONNECTTIMEOUT": "10",  # only bites a genuinely stalled socket: drop it, don't drag.
}.items():
    os.environ.setdefault(_k, _v)

from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from datetime import date  # noqa: E402
from typing import Dict, List, Optional, Tuple  # noqa: E402

import numpy as np  # noqa: E402
import rasterio  # noqa: E402
from pystac_client import Client  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.warp import transform_bounds  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402
from scipy import stats  # noqa: E402

from .decompose import _bbox_area_km2  # noqa: E402
from .indices import REQUIRED_BANDS, compute_index, reduce_aoi, water_area_ha  # noqa: E402
from .models import (  # noqa: E402
    AnalysisType,
    EOIntent,
    ExecutionResult,
    MethodPlan,
    SceneStat,
    Step,
    StepResult,
    TimeRange,
    Variable,
)

EARTH_SEARCH = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
OUT_SIZE = 128            # overview read size per band (AOI-median decision signal, not per-pixel maps)
MIN_VALID_FRACTION = 0.5
MAX_TARGET_ITEMS = 12
MAX_BASELINE_ITEMS = 12
MAX_TREND_ITEMS = 40
_SEARCH_POOL_FACTOR = 5
READ_WORKERS = 6


# --------------------------------------------------------------------------- #
# STAC discovery
# --------------------------------------------------------------------------- #

def _search(client: Client, bbox: List[float], window: TimeRange,
            max_cloud: float, limit: int) -> list:
    search = client.search(
        collections=[COLLECTION],
        bbox=bbox,
        datetime=f"{window.start}/{window.end}",
        query={"eo:cloud_cover": {"lt": max_cloud}},
        sortby=[{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        max_items=limit * _SEARCH_POOL_FACTOR,
    )
    return _spread_over_time(list(search.items()), limit)


def _spread_over_time(items: list, limit: int) -> list:
    """Keep up to limit scenes, clearest first within equal slices of the period.

    The clearest scenes overall can cluster in one dry spell, which leaves a trend
    covering less of its window than was asked for.
    """
    if len(items) <= limit:
        return items
    ordered = sorted(items, key=lambda item: str(item.properties.get("datetime") or ""))
    slices: Dict[int, list] = {}
    for position, item in enumerate(ordered):
        slices.setdefault(position * limit // len(ordered), []).append(item)
    return [min(group, key=lambda item: item.properties.get("eo:cloud_cover", 100.0)) for group in slices.values()]


# --------------------------------------------------------------------------- #
# Pixel reads
# --------------------------------------------------------------------------- #

def _resolve_scale_offset(item, key: str) -> Tuple[float, float]:
    """Effective (scale, offset) for one asset read.

    Reflectance bands MUST carry scale/offset — a silent DN-as-reflectance fallback would drop
    the −0.1 BOA offset and bias every index without tripping the [-1,1] range guard.

    earthsearch:boa_offset_applied=True means the archive ALREADY subtracted the BOA offset from
    the COG pixel values (earth-search v1 sentinel-2-l2a rebase), so the offset still advertised
    in raster:bands must NOT be applied again — double-subtraction biases every band by ~−0.1
    (verified on live pixels: water green DN ~230, vegetation green DN ~520; the −1.000 NDWI clip).
    """
    rb = (item.assets[key].extra_fields.get("raster:bands") or [{}])[0]
    if key != "scl" and ("scale" not in rb or "offset" not in rb):
        raise ValueError(f"{key}: raster:bands scale/offset missing — refusing DN-as-reflectance fallback")
    scale = rb.get("scale", 1.0)
    offset = rb.get("offset", 0.0)
    if item.properties.get("earthsearch:boa_offset_applied"):
        offset = 0.0
    return scale, offset


def _read_band(item, key: str, bbox: List[float], out: int = OUT_SIZE) -> Tuple[np.ndarray, Tuple[float, float]]:
    asset = item.assets[key]
    scale, offset = _resolve_scale_offset(item, key)
    with rasterio.open(asset.href) as src:
        # proj:epsg is unreliable at item level in this archive → read CRS from the source.
        ub = transform_bounds("EPSG:4326", src.crs, *bbox, densify_pts=21)
        win = from_bounds(*ub, transform=src.transform)
        resamp = Resampling.nearest if key == "scl" else Resampling.average
        dn = src.read(1, window=win, out_shape=(out, out), resampling=resamp,
                      boundless=True, fill_value=0)
    return dn, (scale, offset)


def _process_scene(item, bbox: List[float], variable: Variable, aoi_area_km2: float) -> SceneStat:
    acquired = item.datetime.date() if item.datetime else _prop_date(item)
    cloud = float(item.properties.get("eo:cloud_cover", float("nan")))
    try:
        bands = REQUIRED_BANDS[variable]
        dn: Dict[str, np.ndarray] = {}
        scale_offset: Dict[str, Tuple[float, float]] = {}
        for b in bands:
            arr, so = _read_band(item, b, bbox)
            dn[b] = arr
            scale_offset[b] = so
        scl_arr, _ = _read_band(item, "scl", bbox)

        idx = compute_index(variable, dn, scale_offset, scl_arr)
        value, frac = reduce_aoi(idx)
        if variable == Variable.NDWI:
            # Water decision metric is derived surface area (ha), not the index value.
            value = water_area_ha(idx, aoi_area_km2)
        # Reject low-coverage scenes: keep the record (for honesty) but drop the value.
        kept_value = value if frac >= MIN_VALID_FRACTION else None
        return SceneStat(item_id=item.id, acquired=acquired, cloud_cover_pct=cloud,
                         valid_fraction=frac, value=kept_value)
    except Exception as exc:  # noqa: BLE001 — one flaky COG must not abort the whole analysis
        # A transient /vsicurl error (after GDAL retries) drops THIS scene, not the run.
        return SceneStat(item_id=item.id, acquired=acquired, cloud_cover_pct=cloud,
                         valid_fraction=0.0, value=None, read_error=f"{type(exc).__name__}: {exc}"[:200])


def _prop_date(item) -> date:
    dt = item.properties.get("datetime", "1970-01-01")
    return date.fromisoformat(dt[:10])


def _process_scenes(items: list, bbox: List[float], variable: Variable) -> List[SceneStat]:
    if not items:
        return []
    aoi_area_km2 = _bbox_area_km2(bbox)
    with ThreadPoolExecutor(max_workers=READ_WORKERS) as pool:
        stats_list = list(pool.map(lambda it: _process_scene(it, bbox, variable, aoi_area_km2), items))
    # chronological order for trend/series work
    return sorted(stats_list, key=lambda s: s.acquired)


# --------------------------------------------------------------------------- #
# Statistics
# --------------------------------------------------------------------------- #

def _valid_values(scenes: List[SceneStat]) -> List[Tuple[date, float]]:
    return [(s.acquired, s.value) for s in scenes if s.value is not None]


def _mann_kendall(values: List[float]) -> Tuple[int, float, float]:
    """Return (S, Z, two-sided p). Hand-rolled with tie correction; normal CDF via scipy."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    S = 0
    for i in range(n - 1):
        S += int(np.sum(np.sign(x[i + 1:] - x[i])))
    _, counts = np.unique(x, return_counts=True)
    tie = float(np.sum(counts * (counts - 1) * (2 * counts + 5)))
    var_s = (n * (n - 1) * (2 * n + 5) - tie) / 18.0
    if var_s <= 0:
        return S, 0.0, 1.0
    if S > 0:
        z = (S - 1) / np.sqrt(var_s)
    elif S < 0:
        z = (S + 1) / np.sqrt(var_s)
    else:
        z = 0.0
    p = 2.0 * stats.norm.sf(abs(z))
    return S, float(z), float(p)


def _analyze(intent: EOIntent, target: List[SceneStat],
             baseline: List[SceneStat]) -> Tuple[Dict[str, object], List[str]]:
    warnings: List[str] = []
    at = intent.analysis_type
    tvals = _valid_values(target)

    if not tvals:
        warnings.append("No clear scenes with sufficient valid-pixel coverage in the target window.")
        return {"status": "no_data"}, warnings

    t_dates, t_series = zip(*tvals)
    t_series = list(t_series)
    stats_out: Dict[str, object] = {
        "target_n_scenes": len(target),
        "target_n_valid": len(t_series),
        "target_median": float(np.median(t_series)),
        "target_mean": float(np.mean(t_series)),
        "target_std": float(np.std(t_series, ddof=1)) if len(t_series) > 1 else 0.0,
        "target_latest_value": float(t_series[-1]),
        "target_latest_date": str(t_dates[-1]),
    }

    if at == AnalysisType.TREND:
        if len(t_series) < 2:
            # theilslopes on a single point returns nan without raising → "+nan/yr" garbage.
            warnings.append(f"Only {len(t_series)} valid scene — a trend needs at least 2 points; "
                            f"reporting a snapshot instead.")
            stats_out["status"] = "insufficient_for_trend"
            return stats_out, warnings
        if len(t_series) < 5:
            warnings.append(f"Only {len(t_series)} valid scenes — trend estimate is weak (want ≥5).")
        years = np.array([d.toordinal() / 365.25 for d in t_dates])
        window_years = float(years[-1] - years[0])
        slope, intercept, lo, hi = stats.theilslopes(t_series, years)
        S, z, p = _mann_kendall(t_series)
        # A slope-per-year is only a real "rate" over a multi-year window. Below ~1.5 yr the
        # slope is annualised phenology — report the net change ACROSS the window instead, and
        # never apply multi-year degradation thresholds to it.
        multiyear = window_years >= 1.5
        stats_out.update({
            "sen_slope_per_year": float(slope),
            "sen_slope_ci95": [float(lo), float(hi)],
            "window_years": window_years,
            "net_change_over_window": float(slope * window_years),
            "multiyear": multiyear,
            "mk_S": S, "mk_z": z, "mk_p": p,
            "significant": bool(p < 0.05),
        })
        if not multiyear:
            if intent.variable == Variable.NDWI:
                warnings.append(
                    f"Trend window is {window_years:.2f} yr (<1.5 yr): the change reflects WITHIN-SEASON "
                    f"hydrology (refill/drawdown), not a multi-year trend. Interpret the net change across "
                    f"the window and compare to the same season last year before attributing drought or loss.")
            else:
                warnings.append(
                    f"Trend window is {window_years:.2f} yr (<1.5 yr): the slope reflects WITHIN-SEASON "
                    f"phenology, not multi-year degradation. Interpret the net change across the window, "
                    f"not the annualised rate, and compare to the same phenophase before attributing stress.")
        elif intent.variable != Variable.NDWI and abs(slope) < 0.02:
            # The 0.02–0.03 noise floor is an INDEX-unit bound; the NDWI series is hectares.
            warnings.append(f"Sen's slope {slope:+.3f}/yr is within the ~0.02–0.03 NDVI noise floor.")

    elif at in (AnalysisType.CHANGE, AnalysisType.ANOMALY):
        bvals = _valid_values(baseline)
        if not bvals:
            warnings.append("No clear baseline scenes — cannot prove change; falling back to a snapshot.")
            stats_out["status"] = "snapshot_no_baseline"
        else:
            _, b_series = zip(*bvals)
            b_series = list(b_series)
            delta = float(np.median(t_series) - np.median(b_series))
            stats_out.update({
                "baseline_n_valid": len(b_series),
                "baseline_median": float(np.median(b_series)),
                "delta_median": delta,
            })
            if len(t_series) >= 2 and len(b_series) >= 2:
                tt = stats.ttest_ind(t_series, b_series, equal_var=False)
                stats_out["welch_t"] = float(tt.statistic)
                stats_out["welch_p"] = float(tt.pvalue)
                stats_out["significant"] = bool(tt.pvalue < 0.05)
            else:
                warnings.append("Fewer than 2 scenes on one side — significance test skipped.")
                stats_out["significant"] = False
            # cloud asymmetry flag
            tc = np.mean([s.cloud_cover_pct for s in target if s.value is not None])
            bc = np.mean([s.cloud_cover_pct for s in baseline if s.value is not None])
            if abs(tc - bc) > 15:
                warnings.append(
                    f"Baseline/target cloud asymmetry ({bc:.0f}% vs {tc:.0f}%) — masking bias possible.")

    else:  # SNAPSHOT
        stats_out["status"] = "snapshot"

    return stats_out, warnings


# --------------------------------------------------------------------------- #
# Orchestration + per-step verification
# --------------------------------------------------------------------------- #

def execute(plan: MethodPlan) -> ExecutionResult:
    intent = plan.intent
    bbox = intent.aoi.bbox
    step_results: List[StepResult] = []
    warnings: List[str] = []

    def record(step: Step, ok: bool, detail: str = "", warns: Optional[List[str]] = None,
               provenance: Optional[dict] = None) -> None:
        step_results.append(StepResult(step_id=step.id, kind=step.kind, ok=ok, detail=detail,
                                       warnings=warns or [], provenance=provenance or {}))

    by_id = {s.id: s for s in plan.steps}

    # --- resolve_aoi ---
    aoi_ok = bool(bbox and len(bbox) == 4)
    record(by_id["resolve_aoi"], aoi_ok,
           detail=f"bbox={bbox}", provenance={"bbox": bbox, "name": intent.aoi.resolved_name})
    if not aoi_ok:
        return ExecutionResult(plan_hash=plan.plan_hash, variable=intent.variable,
                               analysis_type=intent.analysis_type, steps=step_results,
                               warnings=["AOI not resolved to a bbox — execution aborted."])

    client = Client.open(EARTH_SEARCH)

    # --- discover target ---
    is_trend = intent.analysis_type == AnalysisType.TREND
    tlimit = MAX_TREND_ITEMS if is_trend else MAX_TARGET_ITEMS
    target_items = _search(client, bbox, intent.time, intent.max_cloud_cover_pct, tlimit)
    dt = by_id["discover_target"]
    record(dt, len(target_items) > 0,
           detail=f"{len(target_items)} scenes",
           warns=[] if target_items else ["No scenes matched the target window/cloud filter."],
           provenance={"endpoint": EARTH_SEARCH, "collection": COLLECTION,
                       "datetime": f"{intent.time.start}/{intent.time.end}",
                       "max_cloud": intent.max_cloud_cover_pct,
                       "item_ids": [it.id for it in target_items]})

    target_scenes = _process_scenes(target_items, bbox, intent.variable)
    _record_chain(record, by_id, "target", target_scenes, intent.variable)

    # --- baseline (change/anomaly) ---
    baseline_scenes: List[SceneStat] = []
    if "discover_baseline" in by_id and intent.baseline and intent.baseline.time:
        b_items = _search(client, bbox, intent.baseline.time,
                          intent.max_cloud_cover_pct, MAX_BASELINE_ITEMS)
        db = by_id["discover_baseline"]
        record(db, len(b_items) > 0, detail=f"{len(b_items)} scenes",
               warns=[] if b_items else ["No baseline scenes matched."],
               provenance={"datetime": f"{intent.baseline.time.start}/{intent.baseline.time.end}",
                           "mode": intent.baseline.mode,
                           "item_ids": [it.id for it in b_items]})
        baseline_scenes = _process_scenes(b_items, bbox, intent.variable)
        _record_chain(record, by_id, "baseline", baseline_scenes, intent.variable)

    # --- analyze ---
    stats_out, an_warns = _analyze(intent, target_scenes, baseline_scenes)
    warnings.extend(an_warns)
    an = by_id["analyze"]
    record(an, stats_out.get("status") != "no_data", detail=str(stats_out.get("status", "ok")),
           warns=an_warns, provenance={"stats_keys": list(stats_out.keys())})

    # --- report (assembled downstream; mark ready) ---
    record(by_id["report"], True, detail="stats ready for report assembly")

    return ExecutionResult(
        plan_hash=plan.plan_hash,
        variable=intent.variable,
        analysis_type=intent.analysis_type,
        scenes=target_scenes,
        baseline_scenes=baseline_scenes,
        stats=stats_out,
        steps=step_results,
        warnings=warnings,
    )


def _record_chain(record, by_id, prefix: str, scenes: List[SceneStat], variable: Variable) -> None:
    """Fill load/mask/derive/aggregate StepResults from the processed scenes."""
    n = len(scenes)
    valid = [s for s in scenes if s.value is not None]
    read_failed = [s for s in scenes if s.read_error is not None]
    low_cov = n - len(valid) - len(read_failed)

    record(by_id[f"{prefix}_load"], n > 0, detail=f"read {n} scenes",
           provenance={"out_size": OUT_SIZE, "bands": list(REQUIRED_BANDS[variable]) + ["scl"]})

    mask_warns = []
    if low_cov:
        mask_warns.append(f"{low_cov}/{n} scenes rejected (valid fraction < {MIN_VALID_FRACTION}).")
    if read_failed:
        mask_warns.append(f"{len(read_failed)}/{n} scenes dropped on COG read error (transient network/IO).")
    record(by_id[f"{prefix}_mask"], len(valid) > 0, detail=f"{len(valid)}/{n} scenes kept",
           warns=mask_warns, provenance={"scl_keep": [4, 5, 6, 7]})

    # derive: sanity-check the index range on kept scenes (NDWI scenes carry area in ha, not an index)
    derive_warns = []
    vals = [s.value for s in valid]
    if variable != Variable.NDWI and vals and (min(vals) < -1.0 or max(vals) > 1.0):
        derive_warns.append("Index value outside [-1,1] — check reflectance offset.")
    record(by_id[f"{prefix}_derive"], len(valid) > 0, detail=f"{len(valid)} index values",
           warns=derive_warns,
           provenance={"variable": variable.value,
                       "value_range": [float(min(vals)), float(max(vals))] if vals else None})

    record(by_id[f"{prefix}_aggregate"], len(valid) > 0,
           detail=f"AOI medians for {len(valid)} scenes",
           provenance={"mean_valid_fraction":
                       float(np.mean([s.valid_fraction for s in valid])) if valid else 0.0})
