# ABOUTME: Tests Earth-Agent Phase 0 guardrails: path validation, rate limits, temp cleanup.

import os
from pathlib import Path

import pytest

from openeo_ai.earth_agent.security import (
    EarthAgentRateLimiter,
    EarthAgentSecurityError,
    EnvironmentScrubber,
    PathValidator,
    TempDirManager,
)


def test_path_validator_allows_valid_input(tmp_path: Path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    raster = allowed_root / "result.tif"
    raster.write_bytes(b"ok")

    validator = PathValidator(allowed_base_dirs=[allowed_root])

    assert validator.validate_input_file(raster) == raster.resolve()


def test_path_validator_blocks_symlink_escape(tmp_path: Path):
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    allowed_root.mkdir()
    outside_root.mkdir()

    target = outside_root / "secret.tif"
    target.write_bytes(b"secret")
    symlink = allowed_root / "linked.tif"
    symlink.symlink_to(target)

    validator = PathValidator(allowed_base_dirs=[allowed_root])

    # Was match="(symlink|escapes allowed roots)". That alternation was satisfied by the
    # containment branch alone, so the test passed while the symlink check next to it
    # could never fire (P4-B3/B4). Asserting the specific cause is what makes it a test
    # of the symlink policy. Broader cases live in test_earth_agent_input_symlinks.py.
    with pytest.raises(EarthAgentSecurityError, match="symlinked path component"):
        validator.validate_input_file(symlink)


def test_relative_output_validation_blocks_traversal(tmp_path: Path):
    validator = PathValidator(allowed_base_dirs=[tmp_path])

    with pytest.raises(EarthAgentSecurityError, match="traversal"):
        validator.validate_relative_output_path("../escape.tif")


def test_environment_scrubber_only_keeps_allowlisted_values(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("SECRET_TOKEN", "leak")

    cleaned = EnvironmentScrubber.clean_env(extra_vars={"OPENAI_API_KEY"})

    assert cleaned["OPENAI_API_KEY"] == "test-key"
    assert cleaned["PATH"] == "/usr/bin"
    assert "SECRET_TOKEN" not in cleaned


def test_rate_limiter_enforces_ten_calls_per_minute():
    limiter = EarthAgentRateLimiter(max_calls=2, window_seconds=60)

    assert limiter.allow("earth_agent_analyze", now=0) is True
    assert limiter.allow("earth_agent_analyze", now=1) is True
    assert limiter.allow("earth_agent_analyze", now=2) is False


def test_temp_dir_manager_removes_expired_directories(tmp_path: Path):
    manager = TempDirManager(root_dir=tmp_path, ttl_seconds=10, quota_bytes=1024 * 1024)
    stale = tmp_path / "stale"
    fresh = tmp_path / "fresh"
    stale.mkdir()
    fresh.mkdir()
    (stale / "old.tif").write_bytes(b"old")
    (fresh / "new.tif").write_bytes(b"new")

    os.utime(stale, (0, 0))
    os.utime(stale / "old.tif", (0, 0))

    manager.cleanup(now=100)

    assert not stale.exists()
    assert fresh.exists()
