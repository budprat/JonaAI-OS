# ABOUTME: The data-specialist abort check must trust real tool outputs, not the
# ABOUTME: model's prose — a false "no results" abort skips ml/qa and the EA step.
import asyncio
import inspect
import threading

import pytest

from openeo_ai.sdk import claude_sdk_bridge as bridge_mod
from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
from openeo_ai.sdk.client import OpenEOAIConfig

SESSION = "sess-1"


def _bridge():
    return ClaudeSDKBridge(OpenEOAIConfig(api_key="test-key", sqlite_path=":memory:"))


def _detect(bridge, result, user="user-1"):
    return asyncio.run(bridge._data_results_detected(result, SESSION, user))


def test_summary_phrase_detects_results():
    bridge = _bridge()
    assert _detect(bridge, "Available result files:\n- result.tif") is True


def test_tool_output_path_detects_results_despite_prose_summary(monkeypatch):
    """Regression for the false abort observed live (t2_ea_handoff, 2026-07-23):
    openeo_get_results returned an output_path, but the specialist's summary
    didn't contain the magic phrase and the run's fresh user had no archived
    jobs — the heuristic aborted ml/qa, so earth_agent_analyze never ran."""
    bridge = _bridge()
    bridge._sessions.setdefault(SESSION, {})["turn_tool_results"] = [
        '{"id": "job-1", "status": "finished", '
        '"output_path": "/tmp/openeo_results/results/job-1/result.tif"}'
    ]

    def _no_archive(*args, **kwargs):
        raise AssertionError("archive fallback consulted despite tool-output signal")

    monkeypatch.setattr("openeo_ai.storage.job_archive.list_jobs", _no_archive)
    assert _detect(bridge, "The NDVI job completed and the file was downloaded.") is True


def test_no_signal_anywhere_reports_no_results(monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(
        "openeo_ai.storage.job_archive.list_jobs", lambda **kwargs: []
    )
    assert _detect(bridge, "All jobs failed with errors.") is False


def test_archive_fallback_runs_off_event_loop(monkeypatch):
    bridge = _bridge()
    seen = {}

    def _archive(**kwargs):
        seen["thread"] = threading.get_ident()
        return [{"save_id": "abc"}]

    monkeypatch.setattr("openeo_ai.storage.job_archive.list_jobs", _archive)

    async def _run():
        loop_thread = threading.get_ident()
        ok = await bridge._data_results_detected("no phrase here", SESSION, "user-1")
        return ok, loop_thread

    ok, loop_thread = asyncio.run(_run())
    assert ok is True
    assert seen["thread"] != loop_thread, "archive fallback ran on the event loop"


def test_specialist_loop_uses_the_detector():
    src = inspect.getsource(bridge_mod)
    assert "self._data_results_detected(" in src, (
        "the data-specialist abort check no longer calls _data_results_detected"
    )


def test_abort_message_is_honest_about_still_running_jobs():
    """Observed live (Porto Velho multi-turn): the job outlived the 120s poll
    budget, the abort said 'All analysis jobs failed' — and the job completed
    successfully two minutes later. Still-running is not failed."""
    import json as _json
    from openeo_ai.sdk.claude_sdk_bridge import _data_abort_message

    running = [_json.dumps({"id": "j1", "status": "running", "_polling_timeout": True})]
    msg = _data_abort_message(running)
    assert "still running" in msg
    assert "failed" not in msg.lower()

    failed = [_json.dumps({"id": "j1", "status": "error"})]
    msg2 = _data_abort_message(failed)
    assert "failed" in msg2.lower()


def test_abort_branch_uses_the_honest_message():
    src = inspect.getsource(bridge_mod)
    assert "_data_abort_message(" in src.replace("def _data_abort_message(", ""), (
        "the abort branch no longer uses _data_abort_message"
    )
