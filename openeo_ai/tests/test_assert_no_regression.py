# ABOUTME: The eval gate must fail closed cleanly (no KeyError), distinguish
# ABOUTME: traces-without-scores (Langfuse ingestion lag, retry) from no traces.
from openeo_ai.evals import assert_no_regression as gate


def _fetch(mapping):
    """Build a _fetch_scores_for_tag replacement from {tag: (n_traces, scores)}."""
    def _f(tag):
        return mapping[tag]
    return _f


def test_missing_baseline_fails_closed(monkeypatch):
    monkeypatch.setattr(gate, "_fetch_scores_for_tag", _fetch({
        "production": (0, {}), "pr-1": (5, {"judge-relevance": [1.0]}),
    }))
    result = gate.compare("production", "pr-1", 0.05)
    assert result["passed"] is False
    assert result["reason"] == "no-baseline"
    assert result["regressions"] == []  # main() iterates this — KeyError crashed CI


def test_missing_baseline_allowed_when_flagged(monkeypatch):
    monkeypatch.setattr(gate, "_fetch_scores_for_tag", _fetch({
        "production": (0, {}), "pr-1": (5, {"judge-relevance": [1.0]}),
    }))
    result = gate.compare("production", "pr-1", 0.05, allow_missing_baseline=True)
    assert result["passed"] is True
    assert result["reason"] == "no-baseline"


def test_score_ingestion_lag_retries_then_fails_closed_with_honest_reason(monkeypatch):
    """Observed live (first real run of the PR gate): 63 traces found for the PR
    tag but zero scores — Langfuse ingests scores asynchronously and the gate
    ran seconds after the experiment. The gate said 'No traces' (wrong) and
    then crashed with KeyError('regressions')."""
    calls = {"fetch": 0, "sleep": []}

    def _f(tag):
        if tag == "production":
            return (10, {"judge-relevance": [0.9]})
        calls["fetch"] += 1
        return (63, {})  # traces exist, scores never arrive

    monkeypatch.setattr(gate, "_fetch_scores_for_tag", _f)
    monkeypatch.setattr(gate.time, "sleep", lambda s: calls["sleep"].append(s))

    result = gate.compare("production", "pr-53", 0.05,
                          score_wait_attempts=3, score_wait_seconds=7)
    assert calls["fetch"] == 3, "should retry the score fetch while traces exist"
    assert calls["sleep"] == [7, 7]
    assert result["passed"] is False
    assert result["reason"] == "no-current-scores"
    assert result["regressions"] == []


def test_scores_arriving_on_retry_allow_normal_comparison(monkeypatch):
    state = {"n": 0}

    def _f(tag):
        if tag == "production":
            return (10, {"judge-relevance": [0.9, 0.8]})
        state["n"] += 1
        if state["n"] < 2:
            return (63, {})
        return (63, {"judge-relevance": [0.88, 0.9]})

    monkeypatch.setattr(gate, "_fetch_scores_for_tag", _f)
    monkeypatch.setattr(gate.time, "sleep", lambda s: None)

    result = gate.compare("production", "pr-53", 0.05, score_wait_seconds=1)
    assert result["passed"] is True
    assert result["details"]["judge-relevance"]["status"] == "ok"


def test_genuinely_no_current_traces_reports_that_reason(monkeypatch):
    monkeypatch.setattr(gate, "_fetch_scores_for_tag", _fetch({
        "production": (10, {"judge-relevance": [0.9]}), "pr-1": (0, {}),
    }))
    monkeypatch.setattr(gate.time, "sleep", lambda s: None)
    result = gate.compare("production", "pr-1", 0.05)
    assert result["passed"] is False
    assert result["reason"] == "no-current-traces"
    assert result["regressions"] == []


def test_real_regression_still_detected(monkeypatch):
    monkeypatch.setattr(gate, "_fetch_scores_for_tag", _fetch({
        "production": (10, {"judge-relevance": [0.9, 0.9]}),
        "pr-1": (10, {"judge-relevance": [0.5, 0.6]}),
    }))
    result = gate.compare("production", "pr-1", 0.05)
    assert result["passed"] is False
    assert any("judge-relevance" in r for r in result["regressions"])
