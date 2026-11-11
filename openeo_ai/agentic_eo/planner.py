# ABOUTME: EOIntent → MethodPlan. A template library keyed by analysis_type compiles a typed
# ABOUTME: step DAG (resolve→discover→load→mask→derive→aggregate→analyze→report); executor runs it.

from __future__ import annotations

from typing import List, Tuple

from .indices import REQUIRED_BANDS, WATER_NDWI_THRESHOLD
from .models import (
    AnalysisType,
    EOIntent,
    MethodPlan,
    Step,
    StepKind,
    Variable,
)
from .recipes import REGISTRY

STAC_COLLECTION = "sentinel-2-l2a"


def _bands_for(intent: EOIntent) -> Tuple[str, ...]:
    return REQUIRED_BANDS[intent.variable] + ("scl",)


def _process_chain(prefix: str, discover_id: str, intent: EOIntent) -> List[Step]:
    """The per-window pixel chain: load → mask → derive → aggregate. Applied per scene by executor."""
    bands = _bands_for(intent)
    load = Step(
        id=f"{prefix}_load", kind=StepKind.LOAD,
        description=f"Read bands {bands} for each discovered scene over the AOI window (COG overviews).",
        params={"bands": list(bands)}, inputs=[discover_id],
    )
    mvf = REGISTRY.min_valid_fraction
    mask = Step(
        id=f"{prefix}_mask", kind=StepKind.MASK,
        description=f"Apply SCL cloud/shadow/cirrus/snow mask; reject scenes with valid fraction < {mvf}.",
        params={"scl_keep": [4, 5, 6, 7], "min_valid_fraction": mvf}, inputs=[f"{prefix}_load"],
    )
    if intent.variable == Variable.NDWI:
        # Water decision metric is derived surface area, not the index value (RESEARCH §4).
        derive = Step(
            id=f"{prefix}_derive", kind=StepKind.DERIVE,
            description=("Compute NDWI with per-item reflectance and safe division, then classify "
                         f"open water (NDWI > {WATER_NDWI_THRESHOLD})."),
            params={"variable": intent.variable.value, "water_threshold": WATER_NDWI_THRESHOLD},
            inputs=[f"{prefix}_mask"],
        )
        agg = Step(
            id=f"{prefix}_aggregate", kind=StepKind.AGGREGATE,
            description=("Reduce each scene to open-water surface area (ha) from the water-pixel "
                         "fraction over the AOI, plus the valid-pixel fraction."),
            params={"reducer": "water_area_ha"}, inputs=[f"{prefix}_derive"],
        )
    else:
        derive = Step(
            id=f"{prefix}_derive", kind=StepKind.DERIVE,
            description=f"Compute {intent.variable.value.upper()} with BOA-offset reflectance and safe division.",
            params={"variable": intent.variable.value}, inputs=[f"{prefix}_mask"],
        )
        agg = Step(
            id=f"{prefix}_aggregate", kind=StepKind.AGGREGATE,
            description="Reduce each scene to an AOI median (NaN-aware) + valid-pixel fraction.",
            params={"reducer": "median"}, inputs=[f"{prefix}_derive"],
        )
    return [load, mask, derive, agg]


def build_plan(intent: EOIntent) -> MethodPlan:
    """Compile an intent into a typed MethodPlan. Assumes intent.clarification_needed is None."""
    steps: List[Step] = []

    steps.append(Step(
        id="resolve_aoi", kind=StepKind.RESOLVE_AOI,
        description=f"Resolve AOI '{intent.aoi.resolved_name or intent.aoi.raw_text}' to a WGS84 bbox.",
        params={"bbox": intent.aoi.bbox, "name": intent.aoi.resolved_name}, inputs=[],
    ))

    target_discover = Step(
        id="discover_target", kind=StepKind.DISCOVER,
        description=(f"STAC search {STAC_COLLECTION} over the AOI for "
                     f"{intent.time.start}…{intent.time.end}, cloud < {intent.max_cloud_cover_pct:.0f}%."),
        params={
            "collection": STAC_COLLECTION,
            "datetime": f"{intent.time.start}/{intent.time.end}",
            "max_cloud_cover_pct": intent.max_cloud_cover_pct,
            "role": "target",
        },
        inputs=["resolve_aoi"],
    )
    steps.append(target_discover)
    steps.extend(_process_chain("target", "discover_target", intent))

    analyze_inputs = ["target_aggregate"]

    # Change / anomaly need a baseline window with its own discover + chain.
    if intent.analysis_type in (AnalysisType.CHANGE, AnalysisType.ANOMALY) and intent.baseline and intent.baseline.time:
        b = intent.baseline.time
        steps.append(Step(
            id="discover_baseline", kind=StepKind.DISCOVER,
            description=(f"STAC search {STAC_COLLECTION} for the baseline window "
                         f"{b.start}…{b.end} ({intent.baseline.mode}), cloud < {intent.max_cloud_cover_pct:.0f}%."),
            params={
                "collection": STAC_COLLECTION,
                "datetime": f"{b.start}/{b.end}",
                "max_cloud_cover_pct": intent.max_cloud_cover_pct,
                "role": "baseline",
            },
            inputs=["resolve_aoi"],
        ))
        steps.extend(_process_chain("baseline", "discover_baseline", intent))
        analyze_inputs = ["target_aggregate", "baseline_aggregate"]

    steps.append(Step(
        id="analyze", kind=StepKind.ANALYZE,
        description=_analyze_description(intent),
        params={"analysis_type": intent.analysis_type.value}, inputs=analyze_inputs,
    ))

    steps.append(Step(
        id="report", kind=StepKind.REPORT,
        description="Assemble the decision-grade report (5 gates: headline, change-vs-baseline, "
                    "uncertainty, recommended action, data lineage).",
        params={}, inputs=["analyze"],
    ))

    plan = MethodPlan(intent=intent, steps=steps)
    ref = REGISTRY.recipe_ref(intent.variable, intent.analysis_type)
    if ref:
        plan.recipe_ref = ref
        plan.improvised = False
    return plan


def _analyze_description(intent: EOIntent) -> str:
    at = intent.analysis_type
    v = intent.variable.value.upper()
    if at == AnalysisType.SNAPSHOT:
        return f"Report the latest clear-scene AOI {v} and the period median."
    if at == AnalysisType.CHANGE:
        return (f"Compare target vs baseline {v} (Δmedian); Welch t-test on per-scene AOI medians "
                f"for significance; flag baseline-vs-target cloud asymmetry.")
    if at == AnalysisType.TREND:
        if intent.variable == Variable.NDWI:
            trig = REGISTRY.threshold(Variable.NDWI, "drought_drawdown_ha_per_week")
            return ("Fit Sen's slope to the water-area (ha) scene series and test monotonicity with "
                    f"Mann-Kendall; compare the drawdown rate to the −{abs(trig):.0f} ha/week drought trigger.")
        return (f"Fit Sen's slope to the {v} scene series and test monotonicity with Mann-Kendall; "
                f"report slope per year and p-value against the ~0.02–0.03 noise floor.")
    if at == AnalysisType.ANOMALY:
        return (f"Compute the {v} deviation of the target window from the same-season prior-year "
                f"baseline; express as Δ and relative to scene spread.")
    return f"Analyze {v}."
