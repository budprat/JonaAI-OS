# ABOUTME: Shared builders for Method Kernel test objects (plan, execution, run date).
# ABOUTME: Used by test_method_card.py and test_agentic_eo.py — real models throughout.
from datetime import date

from openeo_ai.agentic_eo.models import (
    AnalysisType, AOISpec, EOIntent, ExecutionResult,
    StepResult, StepKind, TimeRange, Variable,
)
from openeo_ai.agentic_eo.planner import build_plan

RUN_DATE = date(2026, 7, 13)


def make_plan(provenance="geocoded", bbox=None):
    intent = EOIntent(
        query="ndvi",
        aoi=AOISpec(raw_text="Kings County", bbox=bbox or [-119.9, 36.0, -119.7, 36.2],
                    resolved_name="Kings County", provenance=provenance),
        time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
        variable=Variable.NDVI, analysis_type=AnalysisType.SNAPSHOT,
    )
    return build_plan(intent)


def make_execution(plan):
    return ExecutionResult(
        plan_hash=plan.plan_hash, variable=Variable.NDVI,
        analysis_type=AnalysisType.SNAPSHOT,
        steps=[StepResult(step_id="discover_target", kind=StepKind.DISCOVER, ok=True,
                          provenance={"item_ids": ["S2B_10SGF_20250709"]}),
               StepResult(step_id="report", kind=StepKind.REPORT, ok=True)],
        stats={"value": 0.21},
    )
