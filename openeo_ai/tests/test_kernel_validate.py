# ABOUTME: The kernel's fail-closed plan validation chain.
# ABOUTME: schema (given) → step-DAG validity → optional STAC probe → verdict.
from datetime import date

from openeo_ai.agentic_eo.models import (
    AnalysisType, AOISpec, EOIntent, MethodPlan, Step, StepKind, TimeRange, Variable,
)
from openeo_ai.agentic_eo.planner import build_plan
from openeo_ai.method_kernel.validate import validate_plan


def _intent():
    return EOIntent(
        query="ndvi",
        aoi=AOISpec(raw_text="X", bbox=[-119.9, 36.0, -119.7, 36.2], provenance="user-supplied"),
        time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
        variable=Variable.NDVI, analysis_type=AnalysisType.SNAPSHOT,
    )


def test_valid_plan_passes():
    report = validate_plan(build_plan(_intent()))
    assert report.ok is True
    assert all(s.ok for s in report.stages)


def test_dangling_input_fails_closed():
    plan = MethodPlan(intent=_intent(), steps=[
        Step(id="a", kind=StepKind.LOAD, description="load"),
        Step(id="b", kind=StepKind.DERIVE, description="derive", inputs=["ghost"]),
    ])
    report = validate_plan(plan)
    assert report.ok is False
    assert any(not s.ok and s.name == "dag" for s in report.stages)


def test_self_reference_fails_closed():
    plan = MethodPlan(intent=_intent(), steps=[
        Step(id="a", kind=StepKind.LOAD, description="load", inputs=["a"]),
    ])
    report = validate_plan(plan)
    assert report.ok is False


def test_empty_steps_fails_closed():
    report = validate_plan(MethodPlan(intent=_intent(), steps=[]))
    assert report.ok is False


def test_stac_probe_off_by_default_no_network():
    # Default: no probe stage runs (deterministic, no network in unit tests).
    report = validate_plan(build_plan(_intent()))
    assert not any(s.name == "stac" for s in report.stages)


# --------------------------------------------------------------------------- #
# The method_fit stage rides in validate_plan
# --------------------------------------------------------------------------- #

def _intent_with(**overrides):
    base = dict(
        query="ndvi",
        aoi=AOISpec(raw_text="X", bbox=[-119.9, 36.0, -119.7, 36.2], provenance="user-supplied"),
        time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
        variable=Variable.NDVI, analysis_type=AnalysisType.SNAPSHOT,
    )
    base.update(overrides)
    return EOIntent(**base)


def test_ill_posed_method_fails_validate_plan():
    # Well-FORMED (schema+dag pass) but ill-POSED: change with no baseline.
    report = validate_plan(build_plan(_intent_with(analysis_type=AnalysisType.CHANGE)))
    assert report.ok is False
    stage = next(s for s in report.stages if s.name == "method_fit")
    assert stage.ok is False
    assert "baseline" in stage.detail


def test_method_fit_warn_does_not_fail_validate_plan():
    # index_question_mismatch stays a WARN: water question, NDVI method.
    report = validate_plan(build_plan(
        _intent_with(query="is the reservoir shrinking")))
    assert report.ok is True
    assert any("NDVI" in w for w in report.warnings)


def test_change_verb_snapshot_fails_validate_plan():
    # Promoted WARN → FAIL (NU, 2026-07-23): the change-question-vs-snapshot
    # mismatch closes the gate instead of riding as a caveat.
    report = validate_plan(build_plan(
        _intent_with(query="how did vegetation change since last year")))
    assert report.ok is False
    stage = next(s for s in report.stages if s.name == "method_fit")
    assert stage.ok is False
    assert "snapshot" in stage.detail


def test_well_posed_plan_passes_with_clean_method_fit_stage():
    report = validate_plan(build_plan(_intent_with()))
    assert report.ok is True
    assert any(s.name == "method_fit" and s.ok for s in report.stages)
    assert report.warnings == []
