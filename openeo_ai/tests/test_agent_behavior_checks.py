"""Deterministic checks around agent behavior: plans, answers, grounding, saves, coarsening."""
import pytest

from openeo_ai.sdk import viz_raster
from openeo_ai.sdk.claude_sdk_bridge import (
    _answers_by_question,
    _build_post_job_analysis_prompt,
    _flag_unknown_plan_args,
    _grounding_payload,
)
from openeo_app.processes.load_collection import _coarsening_factor


def test_unknown_plan_args_are_moved_aside_and_real_ones_kept():
    plan = {"steps": [
        {"tool": "openeo_algorithm", "args": {"algorithm": "burn_severity", "pre_fire_period": ["2023-01-01"]}},
        {"tool": "made_up_tool", "args": {"anything": 1}},
        {"tool": "openeo_algorithm"},
    ]}
    _flag_unknown_plan_args(plan)
    assert plan["steps"][0]["args"] == {"algorithm": "burn_severity"}
    assert plan["steps"][0]["unrecognized_args"] == {"pre_fire_period": ["2023-01-01"]}
    assert plan["steps"][1]["args"] == {"anything": 1}


def test_mcp_prefixed_plan_tools_are_checked_too():
    plan = {"steps": [{"tool": "mcp__openeo_data__openeo_algorithm", "args": {"nope": 1, "algorithm": "x"}}]}
    _flag_unknown_plan_args(plan)
    assert plan["steps"][0]["unrecognized_args"] == {"nope": 1}


def test_card_answers_become_keyed_by_question_text():
    questions = [{"question": "Which field?"}, {"question": "Which layers?"}]
    answers = {"0": "My own north field", "1": ["NDVI", "NBR"]}
    assert _answers_by_question(questions, answers) == {
        "Which field?": "My own north field",
        "Which layers?": "NDVI, NBR",
    }


def test_unusual_answer_shapes_do_not_raise():
    assert _answers_by_question([{"question": "Q"}], {"7": "x", "name": "y"}) == {"7": "x", "name": "y"}
    assert _answers_by_question([], None) == {}


def test_numbers_the_user_gave_are_not_flagged():
    text = "You listed 12,000 plots, with 95% confidence."
    assert _grounding_payload(text, ["no such number"]) is not None
    assert _grounding_payload(text, ["no such number", "my notes: 12,000 plots at 95% confidence"]) is None


def test_post_job_guidance_says_it_is_internal():
    assert "Don't mention this checklist" in _build_post_job_analysis_prompt("/tmp/result.tif")


def test_one_file_is_saved_once(monkeypatch, tmp_path):
    source = tmp_path / "result.tif"
    source.write_bytes(b"x")
    saves = []

    def fake_save(**kwargs):
        saves.append(kwargs)
        return f"save-{len(saves)}"

    monkeypatch.setattr("openeo_ai.storage.job_archive.save_job", fake_save)
    monkeypatch.setattr(viz_raster, "_archived_results", {})
    first = viz_raster._archive_result(str(source), "A", user_id="u1")
    second = viz_raster._archive_result(str(source), "A again", user_id="u1")
    other_user = viz_raster._archive_result(str(source), "A", user_id="u2")
    assert first == second == "save-1"
    assert other_user == "save-2"
    assert len(saves) == 2


def test_no_coarsening_when_a_load_fits():
    assert _coarsening_factor({"bands": 2, "time": 1, "latitude": 100, "longitude": 100}, 5, 1_000_000) == 1.0


def test_coarsening_brings_a_large_load_under_the_limit():
    sizes = {"bands": 3, "time": 5, "latitude": 3000, "longitude": 3000}
    factor = _coarsening_factor(sizes, 5, 40_000_000)
    assert 1 < factor < 4
    assert 3 * 5 * (3000 / factor) ** 2 <= 40_000_000


def test_before_and_after_loads_of_a_comparison_share_a_grid():
    # Different numbers of dates must not change the grid, or they couldn't be subtracted.
    before = {"bands": 3, "time": 5, "latitude": 3000, "longitude": 3000}
    after = {"bands": 3, "time": 2, "latitude": 3000, "longitude": 3000}
    assert _coarsening_factor(before, 5, 40_000_000) == _coarsening_factor(after, 5, 40_000_000)


def test_very_large_load_exceeds_the_coarsening_allowance():
    sizes = {"bands": 3, "time": 5, "latitude": 30000, "longitude": 30000}
    assert _coarsening_factor(sizes, 5, 40_000_000) > 4
