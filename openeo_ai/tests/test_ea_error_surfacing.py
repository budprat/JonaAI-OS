# ABOUTME: EA subprocess failures must surface the real error — the FastMCP
# ABOUTME: startup banner filled the first-500-chars window and hid a 401 for days.
import asyncio

import pytest

from openeo_ai.earth_agent.agent_runner import (
    EarthAgentRunner,
    _subprocess_error_detail,
)

_BANNER = (
    "╭─ FastMCP 2.0 " + "─" * 60 + "╮\n"
    + ("│" + " " * 76 + "│\n") * 8
    + "╰" + "─" * 76 + "╯\n"
    + "[07/24/26 10:08:39] INFO     Starting MCP server 'FastMCP' with stdio\n"
)
_ERROR_LINE = (
    '{"error": "Error code: 401 - authentication_error: API key is invalid."}'
)


def test_error_detail_prefers_last_error_json_over_banner():
    detail = _subprocess_error_detail(_BANNER + _ERROR_LINE + "\n")
    assert "API key is invalid" in detail
    assert "FastMCP" not in detail


def test_error_detail_without_json_uses_stderr_tail_not_head():
    stderr = _BANNER + "RuntimeError: something specific broke at the end\n"
    detail = _subprocess_error_detail(stderr)
    assert "something specific broke" in detail


@pytest.mark.asyncio
async def test_analyze_raises_with_the_real_error(monkeypatch, tmp_path):
    # PathValidator only admits the app's own result roots — use them.
    monkeypatch.setenv("RESULT_STORAGE_PATH", str(tmp_path / "results-root"))
    runner = EarthAgentRunner(earth_agent_root=tmp_path)
    job_dir = tmp_path / "results-root" / "results" / "test-ea-error-surfacing"
    job_dir.mkdir(parents=True, exist_ok=True)
    tif = job_dir / "result.tif"
    tif.write_bytes(b"II*\x00")

    async def _failing_run(command):
        return ("", _BANNER + _ERROR_LINE + "\n", 1)

    monkeypatch.setattr(runner, "_run_command", _failing_run)
    with pytest.raises(RuntimeError) as exc_info:
        await runner.analyze(query="mean ndvi?", input_files=[str(tif)])
    assert "API key is invalid" in str(exc_info.value)
    assert "FastMCP" not in str(exc_info.value)
