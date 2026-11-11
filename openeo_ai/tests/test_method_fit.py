# ABOUTME: Method_fit(plan): is this the right method for the question?
# ABOUTME: FAIL = structurally ill-posed (never execute); WARN = runnable but caveated.
from datetime import date

from openeo_ai.agentic_eo.models import (
    AnalysisType, AOISpec, BaselineSpec, EOIntent, TimeRange, Variable,
)
from openeo_ai.agentic_eo.planner import build_plan
from openeo_ai.method_kernel.validate import method_fit


def _intent(**overrides):
    base = dict(
        query="ndvi condition in Kings County",
        aoi=AOISpec(raw_text="Kings County", bbox=[-119.9, 36.0, -119.7, 36.2],
                    resolved_name="Kings County", provenance="user-supplied"),
        time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
        variable=Variable.NDVI,
        analysis_type=AnalysisType.SNAPSHOT,
    )
    base.update(overrides)
    return EOIntent(**base)


def _findings(intent):
    return method_fit(build_plan(intent))


def _codes(findings):
    return {f.code for f in findings}


# --------------------------------------------------------------------------- #
# FAIL — structurally ill-posed (do not execute)
# --------------------------------------------------------------------------- #

def test_change_without_baseline_fails():
    fs = _findings(_intent(analysis_type=AnalysisType.CHANGE, baseline=None))
    assert any(f.code == "change_needs_baseline" and f.severity == "fail" for f in fs)


def test_anomaly_with_baseline_missing_time_fails():
    fs = _findings(_intent(analysis_type=AnalysisType.ANOMALY,
                           baseline=BaselineSpec(mode="previous_year", time=None)))
    assert any(f.code == "change_needs_baseline" and f.severity == "fail" for f in fs)


def test_empty_window_fails():
    fs = _findings(_intent(time=TimeRange(start=date(2025, 7, 31), end=date(2025, 7, 1))))
    assert any(f.code == "empty_window" and f.severity == "fail" for f in fs)


def test_zero_length_window_fails():
    fs = _findings(_intent(time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 1))))
    assert any(f.code == "empty_window" and f.severity == "fail" for f in fs)


# --------------------------------------------------------------------------- #
# WARN — runnable but caveated
# --------------------------------------------------------------------------- #

def test_subannual_trend_warns():
    fs = _findings(_intent(analysis_type=AnalysisType.TREND,
                           time=TimeRange(start=date(2025, 1, 1), end=date(2025, 6, 30))))
    assert any(f.code == "subannual_trend" and f.severity == "warn" for f in fs)


def test_multiyear_trend_does_not_warn():
    fs = _findings(_intent(analysis_type=AnalysisType.TREND,
                           time=TimeRange(start=date(2023, 1, 1), end=date(2025, 6, 30))))
    assert "subannual_trend" not in _codes(fs)


def test_snapshot_with_baseline_warns():
    fs = _findings(_intent(baseline=BaselineSpec(
        mode="explicit", time=TimeRange(start=date(2024, 7, 1), end=date(2024, 7, 31)))))
    assert any(f.code == "snapshot_with_baseline" and f.severity == "warn" for f in fs)


# --------------------------------------------------------------------------- #
# Query-vs-intent mismatch (the methodologist check)
# --------------------------------------------------------------------------- #

def test_change_verb_with_snapshot_fails():
    # Promoted WARN → FAIL (NU, 2026-07-23): a change question answered by a
    # snapshot is a wrong answer, not a caveat — clarify instead of executing.
    fs = _findings(_intent(query="how did vegetation change in Kings County since last year"))
    assert any(f.code == "change_verb_snapshot" and f.severity == "fail" for f in fs)


def test_change_verb_with_change_type_does_not_warn():
    fs = _findings(_intent(
        query="how did vegetation change in Kings County since last year",
        analysis_type=AnalysisType.CHANGE,
        baseline=BaselineSpec(mode="previous_period",
                              time=TimeRange(start=date(2025, 6, 1), end=date(2025, 6, 30)))))
    assert "change_verb_snapshot" not in _codes(fs)


def test_water_question_with_ndvi_warns():
    fs = _findings(_intent(query="is the reservoir shrinking near Kings County"))
    assert any(f.code == "index_question_mismatch" and f.severity == "warn" for f in fs)


def test_water_question_with_ndwi_does_not_warn():
    fs = _findings(_intent(query="is the reservoir shrinking near Kings County",
                           variable=Variable.NDWI))
    assert "index_question_mismatch" not in _codes(fs)


# --------------------------------------------------------------------------- #
# Well-posed plans return no findings
# --------------------------------------------------------------------------- #

def test_well_posed_snapshot_is_clean():
    assert _findings(_intent()) == []


def test_well_posed_change_is_clean():
    fs = _findings(_intent(
        query="compare vegetation in Kings County to last month",
        analysis_type=AnalysisType.CHANGE,
        baseline=BaselineSpec(mode="previous_period",
                              time=TimeRange(start=date(2025, 6, 1), end=date(2025, 6, 30)))))
    assert fs == []


def test_well_posed_trend_is_clean():
    fs = _findings(_intent(analysis_type=AnalysisType.TREND,
                           time=TimeRange(start=date(2023, 7, 1), end=date(2025, 7, 1))))
    assert fs == []
