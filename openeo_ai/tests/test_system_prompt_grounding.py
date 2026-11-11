# ABOUTME: The system prompt carries ONE confidence-grounding clause (merged into the
# ABOUTME: DATA-INTEGRITY block), not a duplicate rule or a second CONFIDENCE RATING section.

from __future__ import annotations

from openeo_ai.sdk.system_prompts import SYSTEM_PROMPT


def test_confidence_grounding_clause_present():
    # The novel negative-grounding clause for confidence/score language.
    assert "CONFIDENCE AND SCORE GROUNDING" in SYSTEM_PROMPT
    assert "assess_result_quality" in SYSTEM_PROMPT


def test_no_duplicate_data_integrity_block():
    assert SYSTEM_PROMPT.count("=== ABSOLUTE RULE — DATA INTEGRITY ===") == 1


def test_did_not_duplicate_the_confidence_rating_section():
    # The existing HIGH/MODERATE/LOW rating block stays singular.
    assert SYSTEM_PROMPT.count("CONFIDENCE RATING:") == 1


def test_per_class_table_clause_present():
    # Per-class area/percentage tables only from tool output.
    assert "PER-CLASS TABLE RULE" in SYSTEM_PROMPT
    assert "threshold_classify" in SYSTEM_PROMPT
