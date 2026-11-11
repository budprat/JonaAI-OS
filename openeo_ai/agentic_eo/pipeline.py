# ABOUTME: End-to-end glue — natural-language query → intent → plan → execution → decision report.
# ABOUTME: run_query() is the single entrypoint; returns the intent, plan, execution, and report.

from __future__ import annotations

import os
from datetime import date
from typing import Any, Dict, List, NamedTuple, Optional

from .decompose import decompose
from .executor import execute
from .models import DecisionReport, EOIntent, ExecutionResult, MethodPlan
from .planner import build_plan
from .report import build_report


class PipelineOutput(NamedTuple):
    intent: EOIntent
    plan: Optional[MethodPlan]
    execution: Optional[ExecutionResult]
    report: Optional[DecisionReport]


def _method_fit_gate_on() -> bool:
    """Rollback escape: JONA_METHOD_FIT_GATE=0/false/no/off → observe mode
    (validation still runs and WARNs, but a FAIL never blocks execution)."""
    return os.getenv("JONA_METHOD_FIT_GATE", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def run_query(
    query: str,
    today: Optional[date] = None,
    use_llm: bool = True,
    do_geocode: bool = True,
    aoi_bbox: Optional[list] = None,
    recipe_id: Optional[str] = None,
    window: Optional[List[Any]] = None,
    thresholds: Optional[Dict[str, float]] = None,
) -> PipelineOutput:
    """
    Run the full agentic EO loop on a natural-language question.

    If decomposition needs clarification (e.g. no resolvable AOI), returns early with
    only the intent populated — the caller surfaces intent.clarification_needed.

    aoi_bbox ([west, south, east, north]) overrides geocoding — the analyst supplies
    the area directly, sidestepping location-resolution errors.

    Structured controls: recipe_id pins the method cell, window pins the time
    range, thresholds override recipe gate values for this run.
    """
    intent = decompose(query, today=today, use_llm=use_llm, do_geocode=do_geocode,
                       aoi_bbox=aoi_bbox, recipe_id=recipe_id, window=window, thresholds=thresholds)
    if intent.clarification_needed:
        return PipelineOutput(intent=intent, plan=None, execution=None, report=None)

    plan = build_plan(intent)

    # Method-aptness gate: validate before spending STAC/pixel work. An
    # ill-posed method clarifies instead of executing; WARNs ride into the
    # report's caveats. Import is function-local — the method_kernel façade
    # imports this module, so a top-level import would be circular.
    from openeo_ai.method_kernel.validate import validate_plan
    validation = validate_plan(plan)
    if not validation.ok and _method_fit_gate_on():
        intent.method_clarification = "; ".join(
            s.detail for s in validation.stages if not s.ok)
        return PipelineOutput(intent=intent, plan=plan, execution=None, report=None)

    execution = execute(plan)
    report = build_report(intent, execution, run_date=today,
                          method_warnings=validation.warnings)
    return PipelineOutput(intent=intent, plan=plan, execution=execution, report=report)
