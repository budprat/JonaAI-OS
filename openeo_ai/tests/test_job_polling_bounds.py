# ABOUTME: Bounds the openeo_create_job status-poll loop — a wall-clock 120s deadline
# ABOUTME: and a short per-request timeout on poll-path status GETs (EA-routing defect).
import asyncio
import json

import httpx
import pytest

from openeo_ai.tools import job_tools as job_tools_mod
from openeo_ai.tools.job_tools import (
    _CREATE_JOB_POLL_BUDGET_S,
    JobTools,
    create_job_tools,
)

_REAL_SLEEP = asyncio.sleep
_UNSET = object()


class _Config:
    openeo_url = "http://127.0.0.1:8080/openeo/1.1.0"
    sqlite_path = ":memory:"


class _Clock:
    """Simulated monotonic clock so slow polls cost no real wall time."""

    def __init__(self):
        self.now = 0.0

    def advance(self, seconds):
        self.now += seconds

    def monotonic(self):
        return self.now


class _Resp:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=None)


class _RecordingClient:
    """In-memory replacement for the httpx client that records GET timeouts."""

    def __init__(self):
        self.get_calls = []

    async def post(self, url, **kwargs):
        if url.endswith("/jobs"):
            return _Resp({}, status_code=201,
                         headers={"Location": f"{url}/job-1"})
        return _Resp({}, status_code=202)

    async def get(self, url, timeout=_UNSET, **kwargs):
        self.get_calls.append({"url": url, "timeout": timeout})
        return _Resp({"id": "job-1", "status": "canceled"})


@pytest.mark.asyncio
async def test_create_job_poll_honors_wall_clock_deadline(monkeypatch):
    """A stalled status GET must consume the 120s budget, not extend it.

    Each simulated poll costs 60s (a stalled request). The loop must stop once
    120s of wall clock is spent — not run a fixed 12 iterations (~14 simulated
    minutes), which is what starved the ml specialist in the t2_ea_handoff run.
    """
    clock = _Clock()
    calls = {"status": 0}

    async def _create(self, title, description, process_graph):
        return {"id": "job-1"}

    async def _start(self, job_id):
        return {"id": job_id, "status": "running"}

    async def _status(self, job_id, **kwargs):
        calls["status"] += 1
        clock.advance(60.0)  # a stalled poll burns a full minute
        return {"id": job_id, "status": "running"}

    async def _instant_sleep(seconds):
        clock.advance(seconds)
        await _REAL_SLEEP(0)

    monkeypatch.setattr(job_tools_mod, "time", clock, raising=False)
    monkeypatch.setattr(JobTools, "create", _create)
    monkeypatch.setattr(JobTools, "start", _start)
    monkeypatch.setattr(JobTools, "get_status", _status)
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    handler = create_job_tools(_Config())["openeo_create_job"]
    result = await handler({"title": "NDVI", "process_graph": {"n": {"process_id": "x"}}})

    payload = json.loads(result["content"][0]["text"])
    assert payload.get("_polling_timeout") is True
    # Wall-clock bounded: each simulated iteration costs 70s, so the budget
    # admits ~budget/70 in-loop polls + 1 final fetch. The old
    # iteration-count loop performed 13 regardless and fails this assertion.
    max_calls = _CREATE_JOB_POLL_BUDGET_S // 70 + 3
    assert calls["status"] <= max_calls, (
        f"{calls['status']} status calls — poll budget is not wall-clock"
    )
    assert clock.now <= _CREATE_JOB_POLL_BUDGET_S + 200, (
        f"poll phase consumed {clock.now:.0f}s simulated time"
    )


def test_poll_budget_admits_real_job_durations():
    """Observed live: this AOI's jobs take 78-261s. A 120s budget made the
    turn end before real jobs finished (t1_ndvi failed with no map, and the
    Porto Velho job completed 2 minutes after its turn gave up)."""
    assert _CREATE_JOB_POLL_BUDGET_S >= 300


@pytest.mark.asyncio
async def test_poll_status_gets_short_timeout_direct_status_keeps_default(monkeypatch):
    """Poll-path status GETs carry a ~10s timeout; the standalone
    openeo_get_job_status path keeps the client's 60s default."""
    recording = _RecordingClient()

    async def _instant_sleep(seconds):
        await _REAL_SLEEP(0)

    monkeypatch.setattr(JobTools, "client", property(lambda self: recording))
    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    handler = create_job_tools(_Config())["openeo_create_job"]
    await handler({"title": "NDVI", "process_graph": {"n": {"process_id": "x"}}})

    poll_calls = [c for c in recording.get_calls if c["url"].endswith("/jobs/job-1")]
    assert poll_calls, "poll loop issued no status GET"
    assert all(c["timeout"] == 10.0 for c in poll_calls), (
        f"poll-path timeouts: {[c['timeout'] for c in poll_calls]} — expected 10.0s"
    )

    # Direct status path: client default (60s) must be preserved.
    tools = JobTools(openeo_url=_Config.openeo_url)
    await tools.get_status("job-1")
    direct = recording.get_calls[-1]
    assert direct["timeout"] is httpx.USE_CLIENT_DEFAULT or direct["timeout"] is _UNSET
