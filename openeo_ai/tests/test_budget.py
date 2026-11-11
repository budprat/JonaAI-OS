# ABOUTME: Covers budget observe-mode logic: skip-no-user, tag metadata, insert
# ABOUTME: shape, and fail-open. DB round-trip is proven live (see plan Task 17).
import json

import pytest

from openeo_ai import budget


def test_skip_when_no_user(monkeypatch):
    touched = {"n": 0}
    monkeypatch.setattr(budget._pg_compat, "connect", lambda *a, **k: touched.__setitem__("n", touched["n"] + 1))
    assert budget.record_llm_call(None, "session", "claude-haiku-4-5") is False
    assert touched["n"] == 0


def test_metadata_includes_joint_and_model():
    md = budget._build_llm_metadata("planner", "claude-haiku-4-5", "p1", "w1", "s1")
    assert md == {
        "joint": "planner",
        "model": "claude-haiku-4-5",
        "plan_id": "p1",
        "watch_id": "w1",
        "session_id": "s1",
    }


def test_metadata_omits_empty_optionals():
    md = budget._build_llm_metadata("session", "claude-haiku-4-5", None, None, None)
    assert md == {"joint": "session", "model": "claude-haiku-4-5"}


def test_build_insert_shape():
    sql, params = budget._build_insert("u1", {"joint": "direct", "model": "m"})
    assert "INSERT INTO usage_events" in sql
    assert params[0] == "u1"
    assert params[1] == "llm_call"
    assert params[2] == 1
    assert json.loads(params[3]) == {"joint": "direct", "model": "m"}


def test_metadata_passes_through_validator_and_direct_joints():
    for joint in ("validator", "direct"):
        md = budget._build_llm_metadata(joint, "m", None, None, None)
        assert md == {"joint": joint, "model": "m"}


def test_fail_open_on_db_error(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(budget._pg_compat, "connect", _boom)
    assert budget.record_llm_call("u1", "session", "claude-haiku-4-5") is False
