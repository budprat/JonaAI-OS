# ABOUTME: Tests for enhanced ambiguity detection in strategy_tools.py.
# Covers confidence scoring, multi-intent detection, and clarification
# question generation for Earth Observation queries.

"""
Tests for ambiguity detection features in strategy_tools.py:
- Per-component confidence scoring (domain, spatial, temporal, index, output)
- Multi-intent detection (change_detection, comparison, time_series, etc.)
- Clarification threshold and question builder
"""

import pytest

from openeo_ai.tools.strategy_tools import (
    _score_domain_confidence,
    _score_spatial_confidence,
    _score_temporal_confidence,
    _score_index_confidence,
    _score_output_confidence,
    _detect_multiple_intents,
    plan_analysis_strategy,
    _build_clarification_questions,
)


# ============================================================================
# Confidence Scoring Tests
# ============================================================================

class TestConfidenceScoring:
    """Tests for per-component confidence scoring functions."""

    def test_confidence_complete_query(self):
        """Complete query with location, time, and index should have all
        confidence scores >= 0.7."""
        result = plan_analysis_strategy("NDVI for Berlin January 2024")
        scores = result["confidence_scores"]
        assert scores["domain"] >= 0.7, f"domain confidence too low: {scores['domain']}"
        assert scores["spatial"] >= 0.7, f"spatial confidence too low: {scores['spatial']}"
        assert scores["temporal"] >= 0.7, f"temporal confidence too low: {scores['temporal']}"
        assert scores["index"] >= 0.7, f"index confidence too low: {scores['index']}"

    def test_confidence_missing_location(self):
        """Query without any location should have low spatial confidence."""
        score = _score_spatial_confidence("Calculate NDVI")
        assert score < 0.5, f"spatial confidence should be low without location: {score}"

    def test_confidence_ambiguous_analysis(self):
        """Vague query should yield low domain confidence."""
        # "Analyze this area" has no domain keywords, so classify_domain
        # returns None -> domain confidence = 0.0
        result = plan_analysis_strategy("Analyze this area")
        assert result["confidence_scores"]["domain"] < 0.5, (
            f"domain confidence should be low for vague query: "
            f"{result['confidence_scores']['domain']}"
        )

    def test_confidence_explicit_dates(self):
        """Query with ISO dates should have temporal confidence >= 0.8."""
        score = _score_temporal_confidence(
            "NDVI from 2024-01-01 to 2024-03-31", "single_snapshot"
        )
        assert score >= 0.8, f"temporal confidence should be high with ISO dates: {score}"

    def test_confidence_explicit_index(self):
        """Query explicitly naming an index should have index confidence = 1.0."""
        score = _score_index_confidence("Calculate NDVI", ["ndvi"])
        assert score == 1.0, f"index confidence should be 1.0 for explicit index: {score}"

    def test_confidence_no_index(self):
        """Query without any index mention should have index confidence < 0.5."""
        score = _score_index_confidence("Analyze Berlin", [])
        assert score < 0.5, f"index confidence should be low with no index: {score}"

    def test_confidence_explicit_output(self):
        """Query with explicit output keyword should have output confidence >= 0.8."""
        score = _score_output_confidence("Download NDVI map as geotiff")
        assert score >= 0.8, f"output confidence should be high for explicit output: {score}"


# ============================================================================
# Multi-Intent Detection Tests
# ============================================================================

class TestMultiIntentDetection:
    """Tests for compound intent detection in queries."""

    def test_single_intent(self):
        """Simple index calculation should detect 0 or 1 intents."""
        intents = _detect_multiple_intents("Calculate NDVI")
        assert len(intents) <= 1, f"expected 0-1 intents for simple query, got {len(intents)}"

    def test_change_detection_intent(self):
        """Query about vegetation change should detect change_detection intent."""
        intents = _detect_multiple_intents("Show vegetation change over time")
        intent_types = [i["type"] for i in intents]
        assert "change_detection" in intent_types, (
            f"expected change_detection intent, got {intent_types}"
        )

    def test_comparison_intent(self):
        """Query with 'compare' should detect comparison intent."""
        intents = _detect_multiple_intents("Compare NDVI before and after the fire")
        intent_types = [i["type"] for i in intents]
        assert "comparison" in intent_types, (
            f"expected comparison intent, got {intent_types}"
        )

    def test_multiple_intents(self):
        """Query with multiple intent signals should detect multiple intents."""
        intents = _detect_multiple_intents(
            "Compare vegetation change trend over time"
        )
        assert len(intents) >= 2, (
            f"expected multiple intents for compound query, got {len(intents)}: "
            f"{[i['type'] for i in intents]}"
        )


# ============================================================================
# Clarification Threshold Tests
# ============================================================================

class TestClarificationThreshold:
    """Tests for clarification triggering and question formatting."""

    def test_no_clarification_when_clear(self):
        """Fully specified query should not need clarification."""
        result = plan_analysis_strategy("NDVI for Berlin January 2024")
        assert result["clarification_needed"] is False, (
            f"clear query should not need clarification; "
            f"scores={result['confidence_scores']}"
        )

    def test_clarification_when_ambiguous(self):
        """Highly ambiguous query should trigger clarification."""
        result = plan_analysis_strategy("Analyze this")
        assert result["clarification_needed"] is True, (
            f"ambiguous query should need clarification; "
            f"scores={result['confidence_scores']}"
        )

    def test_clarification_question_format(self):
        """Clarification questions should match AskUserQuestion schema:
        each must have 'question', 'header', and 'options' keys."""
        result = plan_analysis_strategy("Analyze this")
        questions = result["clarification_questions"]
        assert len(questions) > 0, "expected at least one clarification question"
        for q in questions:
            assert "question" in q, f"question missing 'question' key: {q}"
            assert "header" in q, f"question missing 'header' key: {q}"
            assert "options" in q, f"question missing 'options' key: {q}"
            assert isinstance(q["options"], list), (
                f"options should be a list: {type(q['options'])}"
            )


# ============================================================================
# Additional Confidence Edge Cases
# ============================================================================

class TestConfidenceEdgeCases:
    """Edge cases for confidence scoring helpers."""

    def test_spatial_confidence_with_coordinates(self):
        """Coordinates in query should yield spatial confidence = 1.0."""
        score = _score_spatial_confidence("NDVI at 13.4, 52.5")
        assert score == 1.0, f"coordinates should give 1.0: {score}"

    def test_spatial_confidence_with_bbox_keyword(self):
        """'bbox' keyword should yield spatial confidence >= 0.9."""
        score = _score_spatial_confidence("NDVI with bbox 13.0,52.0,13.5,52.5")
        assert score >= 0.9, f"bbox keyword should give >= 0.9: {score}"

    def test_spatial_confidence_vague_reference(self):
        """'this area' should give low but nonzero spatial confidence."""
        score = _score_spatial_confidence("NDVI for this area")
        assert 0.0 < score < 0.5, f"vague reference should give 0 < score < 0.5: {score}"

    def test_temporal_confidence_month_year(self):
        """'January 2024' should yield temporal confidence >= 0.9."""
        score = _score_temporal_confidence("NDVI January 2024", "single_snapshot")
        assert score >= 0.9, f"month+year should give >= 0.9: {score}"

    def test_temporal_confidence_relative_time(self):
        """'last month' should yield temporal confidence ~0.7."""
        score = _score_temporal_confidence("NDVI last month", "single_snapshot")
        assert score >= 0.6, f"relative time should give >= 0.6: {score}"

    def test_temporal_confidence_no_time(self):
        """Query with no temporal reference and single_snapshot strategy
        should have temporal confidence = 0.0."""
        score = _score_temporal_confidence("Calculate NDVI for Berlin", "single_snapshot")
        assert score == 0.0, f"no time reference should give 0.0: {score}"

    def test_domain_confidence_none_domain(self):
        """None domain should yield domain confidence = 0.0."""
        score = _score_domain_confidence("random query", None)
        assert score == 0.0, f"None domain should give 0.0: {score}"

    def test_domain_confidence_strong_keywords(self):
        """Two or more domain keywords should yield confidence = 1.0."""
        # "vegetation" and "ndvi" are both vegetation domain keywords
        score = _score_domain_confidence("vegetation ndvi analysis", "vegetation")
        assert score == 1.0, f"multiple domain keywords should give 1.0: {score}"

    def test_output_confidence_no_output_keyword(self):
        """Query without output keywords should get default 0.5."""
        score = _score_output_confidence("NDVI for Berlin")
        assert score == 0.5, f"no output keyword should give 0.5: {score}"

    def test_index_confidence_inferred(self):
        """Indices present but not explicitly named in query should give 0.5."""
        # indices list has ["ndvi"] but query doesn't contain "ndvi"
        score = _score_index_confidence("Analyze vegetation in Berlin", ["ndvi"])
        assert score == 0.5, f"inferred index should give 0.5: {score}"


# ============================================================================
# Clarification Question Builder Tests
# ============================================================================

class TestClarificationBuilder:
    """Tests for _build_clarification_questions helper."""

    def test_spatial_clarification_question(self):
        """Spatial component should produce location question."""
        questions = _build_clarification_questions(["spatial"], "Analyze NDVI", None)
        assert len(questions) == 1
        assert "area" in questions[0]["question"].lower()

    def test_temporal_clarification_question(self):
        """Temporal component should produce time period question."""
        questions = _build_clarification_questions(["temporal"], "NDVI for Berlin", None)
        assert len(questions) == 1
        assert "time" in questions[0]["question"].lower()

    def test_max_two_questions(self):
        """Builder should return at most 2 questions even if more components
        need clarification."""
        questions = _build_clarification_questions(
            ["spatial", "temporal", "analysis_type", "output"],
            "Analyze",
            None,
        )
        assert len(questions) <= 2, f"expected max 2 questions, got {len(questions)}"

    def test_index_question_populates_from_domain(self):
        """Index clarification with a domain should populate domain-specific
        index options."""
        questions = _build_clarification_questions(["index"], "Analyze", "vegetation")
        assert len(questions) == 1
        labels = [opt["label"] for opt in questions[0]["options"]]
        # vegetation domain has NDVI as first index
        assert any("NDVI" in lbl for lbl in labels), (
            f"expected NDVI in vegetation index options: {labels}"
        )
