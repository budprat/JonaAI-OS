# ABOUTME: Offline unit tests for the persona A/B harness plumbing: dataset shape,
# ABOUTME: arm→tier mapping, routing extraction, lift math, score aggregation, and force_tier.

from __future__ import annotations

from collections import Counter

from openeo_ai.evals.persona_ab import (
    ARMS,
    PERSONA_QUERIES,
    PERSONAS,
    _aggregate_score,
    _arm_to_tier,
    _capped_queries,
    _compute_lift,
    _specialists_from_events,
    aggregate_by_persona,
    format_lift_table,
    format_verdict_table,
)
from openeo_ai.sdk.claude_sdk_bridge import _resolve_tier


class TestPersonaSet:
    def test_four_research_personas_sentinel_exempt(self):
        assert PERSONAS == ["data_scout", "methodologist", "publisher", "timeseries"]
        assert "sentinel" not in PERSONAS  # spine-critical, exempt
        for compute in ("data", "viz", "ml", "qa"):
            assert compute not in PERSONAS

    def test_every_persona_has_a_query(self):
        covered = {pq["persona"] for pq in PERSONA_QUERIES}
        assert covered == set(PERSONAS)

    def test_multiple_queries_per_persona(self):
        # A roster verdict needs a mean over several queries, not n=1.
        counts = Counter(pq["persona"] for pq in PERSONA_QUERIES)
        assert set(counts) == set(PERSONAS)
        assert all(c >= 3 for c in counts.values())

    def test_no_cap_returns_all_by_default(self):
        assert _capped_queries(PERSONA_QUERIES) == PERSONA_QUERIES


class TestAggregate:
    def _rows(self):
        return [
            {"persona": "publisher", "baseline_score": 0.1, "delegated_score": 0.3,
             "lift": 0.2, "persona_fired": True, "timed_out": {"orchestrator_only": False, "with_delegation": False}},
            {"persona": "publisher", "baseline_score": 0.2, "delegated_score": 0.4,
             "lift": 0.2, "persona_fired": False, "timed_out": {"orchestrator_only": False, "with_delegation": True}},
        ]

    def test_rolls_up_mean_lift_fired_rate_timeouts(self):
        agg = aggregate_by_persona(self._rows())
        assert len(agg) == 1
        a = agg[0]
        assert a["persona"] == "publisher"
        assert a["n_queries"] == 2
        assert a["fired_rate"] == 0.5      # 1 of 2 fired
        assert a["mean_lift"] == 0.2
        assert a["n_timeouts"] == 1        # one query hit a timeout in some arm

    def test_verdict_table_renders(self):
        table = format_verdict_table(aggregate_by_persona(self._rows()))
        assert "publisher" in table and "fired_rate" in table


class TestIncrementalPersistence:
    def test_append_and_load_roundtrip(self, tmp_path):
        from openeo_ai.evals.persona_ab import _append_jsonl, load_rows_jsonl
        p = str(tmp_path / "rows.jsonl")
        _append_jsonl(p, {"persona": "publisher", "lift": 0.2})
        _append_jsonl(p, {"persona": "timeseries", "lift": 0.23})
        rows = load_rows_jsonl(p)
        assert len(rows) == 2
        assert rows[0]["persona"] == "publisher" and rows[1]["lift"] == 0.23

    def test_load_missing_file_is_empty(self, tmp_path):
        from openeo_ai.evals.persona_ab import load_rows_jsonl
        assert load_rows_jsonl(str(tmp_path / "nope.jsonl")) == []

    def test_partial_rows_still_aggregate(self, tmp_path):
        # The point of incremental writing: a killed hang leaves some rows, and the
        # verdict must still compute from whatever finished.
        from openeo_ai.evals.persona_ab import _append_jsonl, load_rows_jsonl
        p = str(tmp_path / "partial.jsonl")
        _append_jsonl(p, {"persona": "publisher", "baseline_score": 0.1, "delegated_score": 0.3,
                          "lift": 0.2, "persona_fired": True, "timed_out": {}})
        agg = aggregate_by_persona(load_rows_jsonl(p))
        assert len(agg) == 1 and agg[0]["mean_lift"] == 0.2


class TestArmMapping:
    def test_arms_are_two(self):
        assert ARMS == ("orchestrator_only", "with_delegation")

    def test_with_delegation_is_complex(self):
        assert _arm_to_tier("with_delegation") == "complex"

    def test_orchestrator_only_is_moderate(self):
        assert _arm_to_tier("orchestrator_only") == "moderate"


class TestRoutingExtraction:
    def test_reads_delegated_specialists(self):
        events = [
            {"type": "specialist_delegated", "specialist": "methodologist"},
            {"type": "tool_result", "tool_name": "x", "result": "y"},
            {"type": "specialist_delegated", "specialist": "data"},
        ]
        assert _specialists_from_events(events) == {"methodologist", "data"}

    def test_empty_when_none_delegated(self):
        assert _specialists_from_events([{"type": "text_delta", "content": "hi"}]) == set()


class TestLiftMath:
    def test_positive_lift(self):
        assert _compute_lift(0.60, 0.75) == 0.15

    def test_negative_lift(self):
        assert _compute_lift(0.80, 0.70) == -0.10

    def test_aggregate_averages_three_dims(self):
        assert _aggregate_score({"relevance": 1.0, "completeness": 0.5, "correctness": 0.9}) == 0.8

    def test_aggregate_clamps_and_handles_missing(self):
        assert _aggregate_score({"relevance": 2.0}) == 1.0   # clamped
        assert _aggregate_score(None) == 0.0
        assert _aggregate_score({}) == 0.0


class TestForceTierOverride:
    def test_override_wins(self):
        assert _resolve_tier("anything at all", "complex") == "complex"
        assert _resolve_tier("anything at all", "moderate") == "moderate"

    def test_invalid_override_falls_back_to_classifier(self):
        # A bogus arm must not silently force a tier — it classifies instead.
        from openeo_ai.sdk.claude_sdk_bridge import _classify_query_complexity
        prompt = "what is the ndvi over fresno"
        assert _resolve_tier(prompt, "bogus") == _classify_query_complexity(prompt)
        assert _resolve_tier(prompt, None) == _classify_query_complexity(prompt)


class TestLiftTable:
    def test_table_renders_rows(self):
        rows = [{"persona": "methodologist", "persona_fired": True,
                 "baseline_score": 0.6, "delegated_score": 0.75, "lift": 0.15}]
        table = format_lift_table(rows)
        assert "methodologist" in table and "0.150" in table
