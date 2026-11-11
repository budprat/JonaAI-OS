# ABOUTME: Verifies the bridge tags LLM call sites with the session's user id,
# ABOUTME: fail-open, via budget.record_llm_call.
import pytest

from openeo_ai.sdk import claude_sdk_bridge as bridge_mod


@pytest.mark.asyncio
async def test_record_llm_budget_passes_user_and_joint(monkeypatch):
    captured = []
    monkeypatch.setattr(
        bridge_mod.budget, "record_llm_call",
        lambda user_id, joint, model, **kw: captured.append((user_id, joint, model, kw)) or True,
    )
    b = bridge_mod.ClaudeSDKBridge.__new__(bridge_mod.ClaudeSDKBridge)
    b._user_refs = {"sess-1": {"value": "user-9"}}
    await b._record_llm_budget("sess-1", "planner", "claude-haiku-4-5")
    assert captured == [("user-9", "planner", "claude-haiku-4-5", {"session_id": "sess-1"})]


@pytest.mark.asyncio
async def test_record_llm_budget_fail_open(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("down")

    monkeypatch.setattr(bridge_mod.budget, "record_llm_call", _boom)
    b = bridge_mod.ClaudeSDKBridge.__new__(bridge_mod.ClaudeSDKBridge)
    b._user_refs = {}
    # No session/user, DB raises — must not propagate.
    await b._record_llm_budget("missing", "session", "claude-haiku-4-5")
