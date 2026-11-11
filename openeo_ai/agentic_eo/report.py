# ABOUTME: ExecutionResult + EOIntent → DecisionReport. Enforces the 5 decision-grade gates:
# ABOUTME: quantified headline, change-vs-baseline w/ significance, honest uncertainty, action, lineage.

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from .models import (
    AnalysisType,
    DecisionReport,
    EOIntent,
    ExecutionResult,
    Variable,
)
from .recipes import REGISTRY

_VAR_LABEL = {
    Variable.NDVI: "vegetation vigour (NDVI)",
    Variable.NDWI: "open-water surface area (NDWI-derived)",
    Variable.NDMI: "vegetation moisture (NDMI)",
    Variable.NBR: "burn index (NBR)",
    Variable.EVI: "canopy vigour (EVI)",
}


def _fmt(v: Variable, x: float, signed: bool = False) -> str:
    """Format a metric value with its unit — hectares for the water-area metric, unitless index otherwise."""
    if v == Variable.NDWI:
        return f"{x:+,.0f} ha" if signed else f"{x:,.0f} ha"
    return f"{x:+.3f}" if signed else f"{x:.3f}"


def build_report(intent: EOIntent, ex: ExecutionResult, run_date: Optional[date] = None,
                 method_warnings: Optional[List[str]] = None) -> DecisionReport:
    run_date = run_date or date.today()
    s = ex.stats
    aoi = intent.aoi.resolved_name or intent.aoi.raw_text or "the AOI"
    period = f"{intent.time.start} … {intent.time.end}"
    label = _VAR_LABEL[intent.variable]

    if s.get("status") == "no_data":
        return _no_data_report(intent, ex, aoi, period, label, run_date)

    headline = _headline(intent, s, aoi, label)
    evidence = _evidence(intent, ex, s)
    trend_change = _trend_change(intent, s)
    confidence, conf_reasons = _confidence(intent, ex, s)
    caveats = _caveats(intent, ex, s)
    # Method_fit WARNs — runnable, but the mismatch must be disclosed.
    caveats += [f"Method check: {w}" for w in (method_warnings or [])]
    actions = _actions(intent, s, aoi)
    lineage = _lineage(intent, ex, s, run_date)

    return DecisionReport(
        headline=headline,
        decision_context=intent.decision_context,
        evidence=evidence,
        trend_change=trend_change,
        confidence=confidence,
        confidence_reasons=conf_reasons,
        caveats=caveats,
        recommended_actions=actions,
        data_lineage=lineage,
    )


# --------------------------------------------------------------------------- #
# Gate 1 — quantified headline tied to a decision
# --------------------------------------------------------------------------- #

def _headline(intent: EOIntent, s: Dict[str, Any], aoi: str, label: str) -> str:
    at = intent.analysis_type
    v = intent.variable
    if at == AnalysisType.TREND and "sen_slope_per_year" in s:
        slope = s["sen_slope_per_year"]
        sig = "significant" if s.get("significant") else "not statistically significant"
        p = s.get("mk_p", float("nan"))
        if s.get("multiyear", True):
            if not s.get("significant"):
                return (f"{label} over {aoi} shows no clear trend ({_fmt(v, slope, signed=True)}/yr, "
                        f"Mann-Kendall p={p:.3f}, {sig}).")
            direction = "declining" if slope < 0 else "rising" if slope > 0 else "flat"
            return (f"{label} over {aoi} is {direction} at {_fmt(v, slope, signed=True)}/yr "
                    f"(Mann-Kendall p={p:.3f}, {sig}).")
        # Sub-annual window: the annualised slope is seasonality, not a multi-year rate.
        # Report the NET change across the window and label it as a within-season signal.
        net = s.get("net_change_over_window", slope * s.get("window_years", 0.0))
        months = s.get("window_years", 0.0) * 12
        direction = "fell" if net < 0 else "rose" if net > 0 else "was flat"
        season = ("seasonal hydrology (refill/drawdown), not a multi-year trend"
                  if v == Variable.NDWI
                  else "phenology (green-up/senescence), not a multi-year degradation rate")
        return (f"{label} over {aoi} {direction} {_fmt(v, net, signed=True)} across the "
                f"~{months:.0f}-month window (Mann-Kendall p={p:.3f}, {sig}) — a within-season "
                f"change, likely {season}.")
    if at in (AnalysisType.CHANGE, AnalysisType.ANOMALY) and "delta_median" in s:
        d = s["delta_median"]
        verb = "rose" if d > 0 else "fell" if d < 0 else "was unchanged"
        base = "the preceding period" if at == AnalysisType.CHANGE else "the same season last year"
        sig = f"significant, p={s['welch_p']:.3f}" if s.get("significant") else \
              (f"not significant, p={s['welch_p']:.3f}" if "welch_p" in s else "significance untested")
        return (f"{label} over {aoi} {verb} {_fmt(v, d, signed=True)} vs {base} "
                f"(median {_fmt(v, s.get('baseline_median', float('nan')))} → "
                f"{_fmt(v, s['target_median'])}; {sig}).")
    # snapshot / fallback
    return (f"{label} over {aoi} is {_fmt(v, s['target_median'])} "
            f"(latest clear scene {s.get('target_latest_date')}: "
            f"{_fmt(v, s.get('target_latest_value', float('nan')))}).")


# --------------------------------------------------------------------------- #
# Gate 3 — evidence & Gate 2 — trend/change
# --------------------------------------------------------------------------- #

def _evidence(intent: EOIntent, ex: ExecutionResult, s: Dict[str, Any]) -> List[str]:
    v = intent.variable
    if v == Variable.NDWI:
        metric_line = (f"Median open-water surface area = {_fmt(v, s['target_median'])} "
                       f"(mean {_fmt(v, s.get('target_mean', float('nan')))}, "
                       f"scene-to-scene σ {_fmt(v, s.get('target_std', 0))}).")
    else:
        metric_line = (f"AOI median {v.value.upper()} = {s['target_median']:.3f} "
                       f"(mean {s.get('target_mean', float('nan')):.3f}, "
                       f"scene-to-scene σ {s.get('target_std', 0):.3f}).")
    ev = [
        f"Target window: {s.get('target_n_valid', 0)} clear scenes used "
        f"of {s.get('target_n_scenes', 0)} discovered.",
        metric_line,
    ]
    if "baseline_n_valid" in s:
        ev.append(f"Baseline window: {s['baseline_n_valid']} clear scenes, "
                  f"median {_fmt(v, s['baseline_median'])}.")
    valid = [sc for sc in ex.scenes if sc.value is not None]
    if valid:
        mvf = sum(sc.valid_fraction for sc in valid) / len(valid)
        ev.append(f"Mean valid-pixel fraction after cloud masking: {mvf:.0%}.")
    return ev


def _trend_change(intent: EOIntent, s: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    v = intent.variable
    if "sen_slope_per_year" in s:
        if s.get("multiyear", True):
            lo, hi = s.get("sen_slope_ci95", [float("nan"), float("nan")])
            out.append(f"Sen's slope: {_fmt(v, s['sen_slope_per_year'], signed=True)}/yr "
                       f"(95% CI {_fmt(v, lo, signed=True)} … {_fmt(v, hi, signed=True)}).")
        else:
            # Sub-annual window: the annualised /yr rate is seasonality, not a long-term rate —
            # report the net change across the window and withhold the misleading per-year figure.
            net = s.get("net_change_over_window", s["sen_slope_per_year"] * s.get("window_years", 0.0))
            months = s.get("window_years", 0.0) * 12
            season = "seasonal hydrology" if v == Variable.NDWI else "phenology"
            out.append(f"Net change: {_fmt(v, net, signed=True)} over the ~{months:.0f}-month window "
                       f"(annualised per-year rate withheld — a within-season signal, likely {season}).")
        out.append(f"Mann-Kendall: S={s.get('mk_S')}, Z={s.get('mk_z', 0):.2f}, p={s.get('mk_p', 1):.3f}.")
    if "delta_median" in s:
        out.append(f"Δ median: {_fmt(v, s['delta_median'], signed=True)} "
                   f"({_fmt(v, s.get('baseline_median', float('nan')))} → {_fmt(v, s['target_median'])}).")
        if "welch_p" in s:
            out.append(f"Welch t-test on per-scene medians: t={s['welch_t']:.2f}, p={s['welch_p']:.3f}.")
    if not out:
        out.append("Single-window snapshot — no change/trend statistic computed.")
    return out


# --------------------------------------------------------------------------- #
# Gate 3 — honest confidence
# --------------------------------------------------------------------------- #

def _confidence(intent: EOIntent, ex: ExecutionResult, s: Dict[str, Any]):
    reasons: List[str] = []
    levels = ["Low", "Medium", "High"]
    idx = 2  # start High

    n_valid = s.get("target_n_valid", 0)
    if n_valid < 3:
        idx = min(idx, 0)
        reasons.append(f"Only {n_valid} valid scene(s) — very thin evidence.")
    elif n_valid < 6:
        idx = min(idx, 1)
        reasons.append(f"{n_valid} valid scenes — moderate temporal sampling.")
    else:
        reasons.append(f"{n_valid} valid scenes — good temporal sampling.")

    valid = [sc for sc in ex.scenes if sc.value is not None]
    if valid:
        mvf = sum(sc.valid_fraction for sc in valid) / len(valid)
        if mvf < 0.7:
            idx = max(0, idx - 1)
            reasons.append(f"Mean valid-pixel fraction {mvf:.0%} (<70%) — masking may bias the median.")

    at = intent.analysis_type
    if at in (AnalysisType.CHANGE, AnalysisType.ANOMALY, AnalysisType.TREND):
        if s.get("significant") is True:
            reasons.append("Change/trend is statistically significant (p<0.05).")
        elif s.get("significant") is False:
            idx = max(0, idx - 1)
            reasons.append("Change/trend is NOT statistically significant — treat direction as tentative.")

    if at == AnalysisType.CHANGE and intent.variable == Variable.NDVI:
        season_matched = intent.baseline is not None and intent.baseline.mode == "previous_year"
        window_days = (intent.time.end - intent.time.start).days
        if not season_matched and window_days < 365:
            idx = max(0, idx - 1)
            reasons.append("Adjacent sub-annual baseline (not season-matched) — the NDVI Δ is "
                           "phenology-confounded; confidence downgraded.")

    if any("cloud asymmetry" in w for w in ex.warnings):
        idx = max(0, idx - 1)
        reasons.append("Baseline/target cloud asymmetry — comparison partly confounded.")

    return levels[idx], reasons


# --------------------------------------------------------------------------- #
# Gate 3 — caveats & alternative explanations
# --------------------------------------------------------------------------- #

def _caveats(intent: EOIntent, ex: ExecutionResult, s: Dict[str, Any]) -> List[str]:
    # Defaulted-field disclosures travel with the report (gate 3 honesty).
    caveats = [f"Assumption: {a}" for a in intent.assumptions]
    caveats += list(dict.fromkeys(w for st in ex.steps for w in st.warnings))  # dedup step warnings
    caveats += [w for w in ex.warnings if w not in caveats]
    at = intent.analysis_type
    if at in (AnalysisType.CHANGE, AnalysisType.SNAPSHOT):
        caveats.append("Seasonal/phenology confound: compare like phenophase before attributing change to stress.")
    if intent.variable == Variable.NDVI:
        caveats.append("NDVI saturates above ~0.8 in dense canopy; use EVI where vigour is very high.")
    if intent.variable == Variable.NDWI:
        caveats.append("Surface area is derived from NDWI>0 pixel counts on a coarse overview grid — "
                       "shoreline mixed pixels limit precision; treat changes smaller than a few grid "
                       "cells as noise and corroborate with gauge data.")
    caveats.append(f"AOI reduced at a {ex_out_size()}px overview — a spatial-median signal, not a per-field map.")
    return caveats


def ex_out_size() -> int:
    from .executor import OUT_SIZE
    return OUT_SIZE


# --------------------------------------------------------------------------- #
# Gate 4 — recommended action (target + verb + timeframe + trigger + ground truth)
# --------------------------------------------------------------------------- #

def _actions(intent: EOIntent, s: Dict[str, Any], aoi: str) -> List[str]:
    at = intent.analysis_type
    var = intent.variable
    actions: List[str] = []

    overrides = intent.threshold_overrides or {}

    def thr(variable: Variable, name: str) -> float:
        # A per-run override wins; otherwise the recipe registry is authoritative.
        # Threshold names are unique across variables, so name-keyed overrides are unambiguous.
        return overrides[name] if name in overrides else REGISTRY.threshold(variable, name)

    delta = s.get("delta_median")
    slope = s.get("sen_slope_per_year")

    if var == Variable.NDVI and delta is not None:
        stress_dispatch = thr(Variable.NDVI, "stress_dispatch_delta")
        scout_flag = thr(Variable.NDVI, "scout_flag_delta")
        healthy_greenup = thr(Variable.NDVI, "healthy_greenup_delta")
        season_matched = intent.baseline is not None and intent.baseline.mode == "previous_year"
        window_days = (intent.time.end - intent.time.start).days
        seasonal_confound = at == AnalysisType.CHANGE and not season_matched and window_days < 365
        if seasonal_confound and delta <= scout_flag:
            # Adjacent sub-annual baseline = a different phenophase, so a negative NDVI Δ is
            # phenology-confounded. Never fire an urgent stress dispatch on it (same guard as TREND).
            actions.append(f"Do NOT dispatch on Δ{delta:+.2f} alone: the baseline is the adjacent sub-annual "
                           f"period (a different phenophase), so a drop this size is phenology-confounded "
                           f"(green-up/senescence/harvest). Re-compare {aoi} against the SAME phenophase one "
                           f"year earlier before attributing vegetation stress or acting on irrigation/inputs.")
        elif delta <= stress_dispatch:
            actions.append(f"Dispatch a field scout to {aoi} within 1 week: Δ{delta:+.2f} signals significant "
                           f"vegetation stress with likely yield impact. Ground-truth with an in-field inspection "
                           f"before acting on irrigation/inputs.")
        elif delta <= scout_flag:
            actions.append(f"Flag {aoi} for scouting in the next 2 weeks: Δ{delta:+.2f} is anomaly-worthy but "
                           f"below the yield-impact threshold. Confirm with the next clear acquisition.")
        elif delta >= healthy_greenup:
            actions.append(f"No intervention: {aoi} greened up Δ{delta:+.2f}, consistent with healthy growth.")
        else:
            actions.append(f"Continue routine monitoring of {aoi}; Δ{delta:+.2f} is within normal variation.")
    if var == Variable.NDVI and slope is not None:
        degrade_slope = thr(Variable.NDVI, "degrade_slope_per_year")
        multiyear = s.get("multiyear", True)
        wy = s.get("window_years", 0.0)
        if multiyear and slope <= degrade_slope and s.get("significant"):
            actions.append(f"Open a land-degradation review for {aoi}: sustained {slope:+.3f}/yr decline "
                           f"over {wy:.1f} yr. Cross-check land-use/management records and confirm with a field survey.")
        elif multiyear and slope > 0 and s.get("significant"):
            actions.append(f"{aoi} shows a significant multi-year recovery trend ({slope:+.3f}/yr) — "
                           f"maintain current management.")
        elif not multiyear and s.get("significant"):
            # Sub-annual window — degradation thresholds do not apply; this is a phenology signal.
            net = s.get("net_change_over_window", slope * wy)
            actions.append(f"Do NOT open a degradation review on this window: the {net:+.3f} NDVI change over "
                           f"~{wy * 12:.0f} months is a within-season signal (green-up/senescence). Re-check {aoi} "
                           f"against the SAME phenophase last year to separate normal seasonality from real stress "
                           f"before intervening.")
    if var == Variable.NDMI and (delta is not None
                                 and delta < thr(Variable.NDMI, "moisture_deficit_delta")):
        actions.append(f"Moisture deficit at {aoi} (Δ{delta:+.2f}) — prioritise irrigation scheduling and "
                       f"verify with soil-moisture probes before allocating water.")
    if var == Variable.NDWI and delta is not None:
        verb = "shrank" if delta < 0 else "expanded"
        actions.append(f"Water surface at {aoi} {verb} by {abs(delta):,.0f} ha vs baseline — corroborate the "
                       f"extent with a gauge/field reading before declaring drawdown or flooding.")
    if var == Variable.NDWI and slope is not None and s.get("significant"):
        # RESEARCH §4 drought trigger: sustained drawdown beyond the recipe threshold = active drawdown.
        drought = thr(Variable.NDWI, "drought_drawdown_ha_per_week")
        rate_wk = slope / 52.18
        if rate_wk <= drought:
            actions.append(f"Active drawdown at {aoi}: water surface is losing ~{abs(rate_wk):,.0f} ha/week "
                           f"(Mann-Kendall p<0.05), beyond the −{abs(drought):.0f} ha/week drought trigger. Verify against "
                           f"gauge records within 1 week and review allocations before the next release decision.")
        else:
            actions.append(f"Water surface at {aoi} is changing at {rate_wk:+,.1f} ha/week — within the "
                           f"seasonal range; continue monitoring against gauge data.")
    if var == Variable.NBR and s.get("target_median", 0) < thr(Variable.NBR, "burn_ceiling"):
        actions.append(f"Low NBR at {aoi} is consistent with a recent burn — compute dNBR against a pre-event "
                       f"scene and validate severity on the ground.")

    if not actions:
        actions.append(f"Monitor {aoi} on the next clear acquisition; current signal does not cross an action threshold. "
                       f"Ground-truth any suspected change before intervening.")
    return actions


# --------------------------------------------------------------------------- #
# Gate 5 — data lineage
# --------------------------------------------------------------------------- #

def _lineage(intent: EOIntent, ex: ExecutionResult, s: Dict[str, Any], run_date: date) -> Dict[str, Any]:
    target_ids = _ids_from(ex, "discover_target")
    baseline_ids = _ids_from(ex, "discover_baseline")
    lineage = {
        "sensor / product": "Sentinel-2 L2A (surface reflectance), Collection sentinel-2-l2a",
        "source": "Element84 Earth Search v1 (earth-search.aws.element84.com), public COGs",
        "AOI": (f"{intent.aoi.resolved_name or intent.aoi.raw_text} | bbox {intent.aoi.bbox} "
                f"| provenance: {intent.aoi.provenance}"),
        "target period": f"{intent.time.start} … {intent.time.end}",
        "cloud filter": f"eo:cloud_cover < {intent.max_cloud_cover_pct:.0f}%",
        "cloud mask": "Scene Classification Layer (SCL), keep {4,5,6,7}",
        "reflectance": ("per-item raster:bands scale/offset; earthsearch:boa_offset_applied items "
                        "are pre-corrected → effective offset 0 (reflectance = DN/10000)"),
        "index formula": _formula(intent.variable),
        "target scene ids": target_ids or "(none)",
        "plan_hash": ex.plan_hash,
        "pipeline_integrity": f"{ex.pipeline_integrity:.0%} of steps passed validators",
        "run date": str(run_date),
    }
    if baseline_ids:
        lineage["baseline period"] = (f"{intent.baseline.time.start} … {intent.baseline.time.end}"
                                      if intent.baseline and intent.baseline.time else "n/a")
        lineage["baseline scene ids"] = baseline_ids
    return lineage


def _ids_from(ex: ExecutionResult, step_id: str) -> List[str]:
    for st in ex.steps:
        if st.step_id == step_id:
            return st.provenance.get("item_ids", [])
    return []


def _formula(v: Variable) -> str:
    return {
        Variable.NDVI: "(NIR−Red)/(NIR+Red)",
        Variable.NDWI: "(Green−NIR)/(Green+NIR); water = NDWI > 0; area = water-pixel fraction × AOI bbox area (ha)",
        Variable.NDMI: "(NIR−SWIR16)/(NIR+SWIR16)",
        Variable.NBR: "(NIR−SWIR22)/(NIR+SWIR22)",
        Variable.EVI: "2.5·(NIR−Red)/(NIR+6·Red−7.5·Blue+1)",
    }[v]


def _no_data_report(intent, ex, aoi, period, label, run_date) -> DecisionReport:
    return DecisionReport(
        headline=f"No decision-grade signal: no clear {label} scenes over {aoi} for {period}.",
        decision_context=intent.decision_context,
        evidence=[f"STAC returned {len(ex.scenes)} scene(s); none met the "
                  f"valid-pixel threshold after cloud masking."],
        trend_change=["Not computable — insufficient clear data."],
        confidence="Low",
        confidence_reasons=["No usable scenes in the requested window/cloud budget."],
        caveats=["Widen the date range, relax the cloud threshold, or shrink the AOI, then re-run."]
        + [w for w in ex.warnings],
        recommended_actions=[f"Re-run for {aoi} with a wider window or higher cloud tolerance before deciding."],
        data_lineage=_lineage(intent, ex, ex.stats, run_date),
    )
