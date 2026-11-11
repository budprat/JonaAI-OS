# ABOUTME: Golden table proving the severity pipeline is behavior-identical to
# ABOUTME: the historical inline worker severity logic (base + seasonal-z).
import pytest

from openeo_ai.intelligence.severity_pipeline import _stage_base


_BASE_CASES = [
    ("clean", {}, {"nan_percent": 5.0}, "info"),
    ("high-nan", {}, {"nan_percent": 50.0}, "warning"),
    ("just-under-nan", {}, {"nan_percent": 49.9}, "info"),
    ("validation-warn", {"validation": {"warnings": ["x"]}}, {"nan_percent": 1.0}, "warning"),
    ("empty-warnings", {"validation": {"warnings": []}}, {"nan_percent": 1.0}, "info"),
    ("no-stats", {}, {}, "info"),
]


@pytest.mark.parametrize(
    "case_id,result_info,stats,expected",
    _BASE_CASES,
    ids=[c[0] for c in _BASE_CASES],
)
def test_stage_base_matches_history(case_id, result_info, stats, expected):
    rec = _stage_base(result_info, stats)
    assert rec.stage == "base"
    assert rec.modifier == expected


from openeo_ai.intelligence.severity_pipeline import evaluate_severity  # noqa: E402


def _baseline(mean, std, count):
    return {"previous_mean": mean, "previous_std": std, "previous_count": count}


# (result_info, stats, value, baseline_info, expected_severity)
_FULL_CASES = [
    ("info-no-baseline", {}, {"nan_percent": 5.0}, 0.5, None, "info"),
    ("warn-nan-no-baseline", {}, {"nan_percent": 60.0}, 0.5, None, "warning"),
    ("z-critical-from-info", {}, {"nan_percent": 5.0}, 1.0, _baseline(0.5, 0.1, 5), "critical"),
    ("z-critical-from-warn", {}, {"nan_percent": 60.0}, 1.0, _baseline(0.5, 0.1, 5), "critical"),
    ("z-mid-from-info-warns", {}, {"nan_percent": 5.0}, 0.75, _baseline(0.5, 0.1, 5), "warning"),
    ("z-mid-from-warn-stays", {}, {"nan_percent": 60.0}, 0.75, _baseline(0.5, 0.1, 5), "warning"),
    ("z-low-stays-info", {}, {"nan_percent": 5.0}, 0.55, _baseline(0.5, 0.1, 5), "info"),
    ("count-under-3-no-z", {}, {"nan_percent": 5.0}, 5.0, _baseline(0.5, 0.1, 2), "info"),
    ("std-zero-no-z", {}, {"nan_percent": 5.0}, 5.0, _baseline(0.5, 0.0, 5), "info"),
    ("baseline-mean-none-no-z", {}, {"nan_percent": 5.0}, 5.0, _baseline(None, 0.1, 5), "info"),
    ("baseline-info-none", {}, {"nan_percent": 5.0}, 5.0, None, "info"),
]


@pytest.mark.parametrize(
    "case_id,result_info,stats,value,baseline_info,expected",
    _FULL_CASES,
    ids=[c[0] for c in _FULL_CASES],
)
def test_evaluate_severity_matches_history(case_id, result_info, stats, value, baseline_info, expected):
    decision = evaluate_severity(result_info, stats, value, baseline_info)
    assert decision.severity == expected
    assert decision.provenance[0].stage == "base"


def test_provenance_serializes_to_event_payload():
    decision = evaluate_severity({}, {"nan_percent": 5.0}, 1.0, _baseline(0.5, 0.1, 5))
    payload = decision.as_event_payload()
    assert payload[0]["stage"] == "base"
    assert payload[-1]["stage"] == "seasonal-z"
    assert payload[-1]["modifier"] == "critical"


def test_worker_has_no_local_severity_helper():
    from openeo_ai.intelligence import worker
    assert not hasattr(worker, "_severity_from_result")
