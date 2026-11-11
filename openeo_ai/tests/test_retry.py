# ABOUTME: Verifies retryable() retries transient errors, propagates terminal
# ABOUTME: errors immediately, and caps exponential backoff.
import asyncio

import pytest

from openeo_ai.retry import retryable


class RetryableBoom(Exception):
    pass


class TerminalBoom(Exception):
    pass


async def _no_sleep(*_a, **_k):
    return None


@pytest.mark.asyncio
async def test_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    @retryable(max_attempts=3, retry_on=(RetryableBoom,))
    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RetryableBoom()
        return "ok"

    assert await flaky() == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_terminal_propagates_immediately(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    @retryable(max_attempts=3, retry_on=(RetryableBoom,))
    async def boom():
        calls["n"] += 1
        raise TerminalBoom()

    with pytest.raises(TerminalBoom):
        await boom()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_exhausts_and_reraises(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    calls = {"n": 0}

    @retryable(max_attempts=2, retry_on=(RetryableBoom,))
    async def always():
        calls["n"] += 1
        raise RetryableBoom()

    with pytest.raises(RetryableBoom):
        await always()
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_backoff_is_capped(monkeypatch):
    delays = []

    async def _capture(seconds):
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _capture)
    calls = {"n": 0}

    @retryable(max_attempts=5, base_delay_seconds=1.0, max_delay_seconds=2.0, retry_on=(RetryableBoom,))
    async def always():
        calls["n"] += 1
        raise RetryableBoom()

    with pytest.raises(RetryableBoom):
        await always()
    assert delays == [1.0, 2.0, 2.0, 2.0]  # 1, 2, capped, capped (4 sleeps before 5th attempt)
