# ABOUTME: Verifies the judge retries transient Anthropic errors and still
# ABOUTME: fails open (swallows) when retries are exhausted.
import asyncio

import httpx
import pytest

from openeo_ai.evals import judge as judge_mod


async def _no_sleep(*_a, **_k):
    return None


class _LangfuseSentinel:
    pass


@pytest.mark.asyncio
async def test_judge_retries_then_parses(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(judge_mod, "get_langfuse_client", lambda: _LangfuseSentinel())

    import anthropic

    calls = {"n": 0}

    class _Block:
        text = '{"relevance": 1, "completeness": 1, "correctness": 1, "reasoning": "ok"}'

    class _Msg:
        content = [_Block()]

    class _Messages:
        async def create(self, **_kw):
            calls["n"] += 1
            if calls["n"] < 2:
                raise anthropic.APIConnectionError(
                    request=httpx.Request("POST", "https://api.anthropic.test")
                )
            return _Msg()

    class _Client:
        def __init__(self, **_kw):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)

    emitted = []
    monkeypatch.setattr(judge_mod, "emit_score", lambda **kw: emitted.append(kw))

    await judge_mod.judge_response(trace_id="t1", query="q", response="r")
    assert calls["n"] == 2
    assert any(e["name"] == "judge-relevance" for e in emitted)


@pytest.mark.asyncio
async def test_judge_fails_open_when_retries_exhausted(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(judge_mod, "get_langfuse_client", lambda: _LangfuseSentinel())

    import anthropic

    calls = {"n": 0}

    class _Messages:
        async def create(self, **_kw):
            calls["n"] += 1
            raise anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.test")
            )

    class _Client:
        def __init__(self, **_kw):
            self.messages = _Messages()

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Client)

    emitted = []
    monkeypatch.setattr(judge_mod, "emit_score", lambda **kw: emitted.append(kw))

    # Never raises — the judge fails open after retries are exhausted.
    await judge_mod.judge_response(trace_id="t1", query="q", response="r")
    assert calls["n"] == 2
    assert emitted == []


@pytest.mark.asyncio
async def test_judge_tags_budget_when_user_present(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(judge_mod, "get_langfuse_client", lambda: _LangfuseSentinel())
    tags = []
    monkeypatch.setattr(judge_mod.budget, "record_llm_call",
                        lambda user_id, joint, model, **kw: tags.append((user_id, joint)) or True)
    # Force the anthropic path to fail fast so we isolate the tag behavior.
    monkeypatch.setattr(judge_mod, "emit_score", lambda **kw: None)
    import anthropic

    class _Boom:
        def __init__(self, **_kw): ...
        @property
        def messages(self):
            raise anthropic.APIConnectionError(
                request=httpx.Request("POST", "https://api.anthropic.test")
            )

    monkeypatch.setattr(anthropic, "AsyncAnthropic", _Boom)
    await judge_mod.judge_response(trace_id="t", query="q", response="r", user_id="u5")
    assert ("u5", "judge") in tags
