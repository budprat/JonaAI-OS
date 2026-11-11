# ABOUTME: Tests that job results and tool outputs go to one durable folder, not /tmp, which is
# ABOUTME: wiped on reboot and is private to a service running with PrivateTmp.
import asyncio
import os
import time
from pathlib import Path

import pytest

from openeo_app.core.paths import LEGACY_RESULT_ROOT, log_root, result_root, user_outputs_root

REPO = Path(__file__).resolve().parents[2]


class TestLogRoot:
    """The backend's rotating logs must survive a restart, so they can't live in /tmp."""

    def test_default_is_under_home_not_tmp(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LOG_DIR", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert log_root() == tmp_path / ".openeo_logs"

    def test_setting_overrides_default(self, monkeypatch):
        monkeypatch.setenv("LOG_DIR", "/var/log/jonaai")
        assert log_root() == Path("/var/log/jonaai")

    def test_the_backend_configures_logging_from_log_root(self):
        source = (REPO / "openeo_app/app.py").read_text()
        assert "log_dir = log_root()" in source
        assert "/tmp/openeo_logs" not in source


class TestResultRoot:
    def test_default_is_under_home_not_tmp(self, monkeypatch, tmp_path):
        monkeypatch.delenv("RESULT_STORAGE_PATH", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert result_root() == tmp_path / ".openeo_results"

    def test_setting_overrides_default(self, monkeypatch):
        monkeypatch.setenv("RESULT_STORAGE_PATH", "/var/lib/jonaai/results")
        assert result_root() == Path("/var/lib/jonaai/results")
        assert user_outputs_root() == Path("/var/lib/jonaai/results/users")

    def test_blank_setting_keeps_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("RESULT_STORAGE_PATH", "  ")
        monkeypatch.setenv("HOME", str(tmp_path))
        assert result_root() == tmp_path / ".openeo_results"

    def test_no_module_writes_to_the_legacy_tmp_root(self):
        """Every writer asks result_root(); only paths.py may name the old /tmp folder."""
        offenders = []
        for folder in ("openeo_ai", "openeo_app", "scripts"):
            for source in (REPO / folder).rglob("*.py"):
                if "tests" in source.parts or source.name == "paths.py":
                    continue
                if str(LEGACY_RESULT_ROOT) in source.read_text(errors="ignore"):
                    offenders.append(str(source.relative_to(REPO)))
        assert offenders == []


class TestToolOutputs:
    def test_user_outputs_are_under_the_result_root(self, monkeypatch, tmp_path):
        from openeo_ai.storage import result_access

        monkeypatch.setenv("RESULT_STORAGE_PATH", str(tmp_path))
        # The module reads the root once, at import, as the service does at startup.
        monkeypatch.setattr(result_access, "USER_OUTPUT_ROOT", user_outputs_root())
        assert result_access.user_output_dir("a@b.co") == tmp_path / "users" / "a_b_co"

    def test_output_paths_must_stay_under_the_result_root(self, tmp_path):
        from openeo_ai.tools.path_security import PathSecurityError, validate_tool_output_path

        assert validate_tool_output_path(str(tmp_path / "users" / "u" / "x.png"), allowed_dir=tmp_path)
        with pytest.raises(PathSecurityError):
            validate_tool_output_path(str(LEGACY_RESULT_ROOT / "x.png"), allowed_dir=tmp_path)


class TestCleanup:
    def test_old_tool_outputs_expire_and_fresh_ones_stay(self, tmp_path):
        from openeo_app.storage.results import ResultStorage

        storage = ResultStorage(base_path=str(tmp_path))
        folder = tmp_path / "users" / "alice"
        folder.mkdir(parents=True)
        old, fresh = folder / "old.png", folder / "fresh.png"
        old.write_bytes(b"x")
        fresh.write_bytes(b"x")
        two_days_ago = time.time() - 2 * 24 * 3600
        os.utime(old, (two_days_ago, two_days_ago))

        storage.cleanup_old_results()

        assert not old.exists()
        assert fresh.exists()
        assert folder.exists()

    def test_storage_defaults_to_the_result_root(self, monkeypatch, tmp_path):
        from openeo_app.storage.results import ResultStorage

        monkeypatch.setenv("RESULT_STORAGE_PATH", str(tmp_path / "root"))
        assert ResultStorage().base_path == tmp_path / "root"


class TestJobResults:
    def test_results_are_found_under_the_configured_root(self, monkeypatch, tmp_path):
        from openeo_ai.tools.job_tools import JobTools

        monkeypatch.setenv("RESULT_STORAGE_PATH", str(tmp_path))
        job_dir = tmp_path / "results" / "job-1"
        job_dir.mkdir(parents=True)
        (job_dir / "result.tif").write_bytes(b"II*\x00")

        tools = JobTools.__new__(JobTools)
        result = asyncio.run(tools._get_results_request("job-1", ""))
        assert str(job_dir / "result.tif") in str(result)
