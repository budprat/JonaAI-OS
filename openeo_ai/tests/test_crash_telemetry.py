# ABOUTME: Tests that crash telemetry is on under uvicorn's import path, writes where it's told,
# ABOUTME: survives a real native crash, and never stops startup when its path is unusable.
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from openeo_ai import crash_telemetry

REPO = Path(__file__).resolve().parents[2]


def _run(code, fault_log, timeout=60):
    env = {**os.environ, "PYTHONPATH": f"{REPO}:{REPO / 'openeo-fastapi'}", "JONA_FAULT_LOG": str(fault_log)}
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=REPO,
                          capture_output=True, text=True, timeout=timeout)


class TestTarget:
    def test_production_dir_first_then_tmp(self, monkeypatch, tmp_path):
        monkeypatch.delenv("JONA_FAULT_LOG", raising=False)
        production, development = tmp_path / "var-lib-jonaai", tmp_path / "tmp"
        development.mkdir()
        monkeypatch.setattr(crash_telemetry, "PRODUCTION_DIR", production)
        monkeypatch.setattr(crash_telemetry, "DEVELOPMENT_DIR", development)

        handle, target = crash_telemetry._choose()  # no production dir: a laptop
        handle.close()
        assert target == str(development / crash_telemetry.FAULT_LOG_NAME)

        production.mkdir()
        handle, target = crash_telemetry._choose()  # the server
        handle.close()
        assert target == str(production / crash_telemetry.FAULT_LOG_NAME)

    def test_the_setting_overrides_the_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("JONA_FAULT_LOG", str(tmp_path / "custom.log"))
        handle, target = crash_telemetry._choose()
        handle.close()
        assert target == str(tmp_path / "custom.log")

    @pytest.mark.parametrize("unusable", ["/no/such/dir/fault.log", "/proc/jonaai-fault.log"])
    def test_an_unusable_path_falls_back_to_stderr(self, monkeypatch, unusable):
        monkeypatch.setenv("JONA_FAULT_LOG", unusable)
        handle, target = crash_telemetry._choose()
        assert (handle, target) == (sys.stderr, "stderr")


class TestInAProcess:
    def test_a_native_crash_leaves_a_traceback(self, tmp_path):
        log = tmp_path / "fault.log"
        result = _run("from openeo_ai import crash_telemetry as c; c.enable()\n"
                      "import faulthandler; faulthandler._sigsegv()", log)
        assert result.returncode == -signal.SIGSEGV
        assert "Fatal Python error: Segmentation fault" in log.read_text()

    def test_sigusr1_dumps_every_thread_and_keeps_running(self, tmp_path):
        log = tmp_path / "fault.log"
        result = _run("from openeo_ai import crash_telemetry as c; c.enable()\n"
                      "import os, signal, time; os.kill(os.getpid(), signal.SIGUSR1); time.sleep(0.2)\n"
                      "print('still running')", log)
        assert result.returncode == 0 and "still running" in result.stdout
        assert "Current thread" in log.read_text()

    def test_an_unusable_path_never_stops_startup(self):
        result = _run("from openeo_ai import crash_telemetry as c\n"
                      "import faulthandler; print(c.enable(), faulthandler.is_enabled())",
                      "/proc/jonaai-fault.log")
        assert result.returncode == 0
        assert result.stdout.split() == ["stderr", "True"]

    def test_off_leaves_faulthandler_alone(self):
        result = _run("from openeo_ai import crash_telemetry as c\n"
                      "import faulthandler; print(c.enable(), faulthandler.is_enabled())", "off")
        assert result.stdout.split() == ["None", "False"]

    def test_on_when_uvicorn_imports_the_app(self, tmp_path):
        # uvicorn imports openeo_ai.web_interface:asgi_app; __main__ never runs there.
        log = tmp_path / "fault.log"
        result = _run("import openeo_ai.web_interface\n"
                      "import faulthandler; from openeo_ai import crash_telemetry as c\n"
                      "print(faulthandler.is_enabled(), c.target())", log, timeout=180)
        assert result.returncode == 0, result.stderr[-2000:]
        assert result.stdout.split()[-2:] == ["True", str(log)]

    def test_still_on_when_run_as_a_script(self, tmp_path):
        # The laptop's path: python -m openeo_ai.web_interface (scripts/run-backend.sh).
        log = tmp_path / "fault.log"
        env = {**os.environ, "PYTHONPATH": f"{REPO}:{REPO / 'openeo-fastapi'}", "JONA_FAULT_LOG": str(log)}
        result = subprocess.run([sys.executable, "-m", "openeo_ai.web_interface", "--help"], env=env,
                                cwd=REPO, capture_output=True, text=True, timeout=180)
        assert result.returncode == 0, result.stderr[-2000:]
        assert f"Crash telemetry: {log}" in result.stderr
