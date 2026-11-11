# ABOUTME: The nightly eval scores exp-ungrounded-numbers from captured WS events,
# ABOUTME: reconstructing per-turn tool outputs from tool_result events (no bridge plumbing).

from __future__ import annotations

from openeo_ai.evals import assert_no_regression as gate
from openeo_ai.evals.run_experiment import _score_ungrounded_numbers


class TestScoreUngroundedNumbers:
    def test_flags_ungrounded_trust_claim(self):
        events = [
            {"type": "text_delta", "content": "confidence 87%"},
            {"type": "tool_result", "tool_name": "raster_statistics", "result": "mean 0.42"},
        ]
        assert _score_ungrounded_numbers(events) == 1.0

    def test_clean_when_number_appears_in_tool_result(self):
        events = [
            {"type": "text_delta", "content": "coverage 95.2%"},
            {"type": "tool_result", "tool_name": "x", "result": "coverage_pct 95.2"},
        ]
        assert _score_ungrounded_numbers(events) == 0.0

    def test_no_trust_claim_scores_zero(self):
        events = [{"type": "text_delta", "content": "the field looks healthy"}]
        assert _score_ungrounded_numbers(events) == 0.0

    def test_grounded_prose_does_not_false_positive(self):
        # Guards against a false-positive-heavy metric: every stated number is in a tool result.
        events = [
            {"type": "text_delta", "content": "NDVI mean 0.42, coverage 95%, quality score 0.9."},
            {"type": "tool_result", "tool_name": "stats", "result": "mean 0.42 coverage_pct 95 quality 0.9"},
        ]
        assert _score_ungrounded_numbers(events) == 0.0


class TestGateRegistration:
    def test_ungrounded_numbers_is_a_negative_dimension(self):
        assert "exp-ungrounded-numbers" in gate.NEGATIVE_DIMENSIONS
