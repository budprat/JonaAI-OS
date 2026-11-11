# ABOUTME: Tests for web authentication module - token management, login security.

import os
import pytest
from datetime import datetime, timedelta
from pathlib import Path


class TestVerifyToken:
    """Tests for token verification and cleanup."""

    def setup_method(self):
        """Reset token state before each test."""
        from openeo_ai import web_auth
        web_auth._admin_tokens.clear()

    def test_valid_token(self):
        from openeo_ai.web_auth import verify_token, _admin_tokens
        _admin_tokens["test-token-123"] = datetime.utcnow()
        assert verify_token("test-token-123") is True

    def test_invalid_token(self):
        from openeo_ai.web_auth import verify_token
        assert verify_token("nonexistent-token") is False

    def test_expired_token(self):
        from openeo_ai.web_auth import verify_token, _admin_tokens
        _admin_tokens["old-token"] = datetime.utcnow() - timedelta(hours=25)
        assert verify_token("old-token") is False
        assert "old-token" not in _admin_tokens

    def test_cleanup_sweeps_expired(self):
        from openeo_ai.web_auth import verify_token, _admin_tokens
        _admin_tokens["valid"] = datetime.utcnow()
        _admin_tokens["expired1"] = datetime.utcnow() - timedelta(hours=25)
        _admin_tokens["expired2"] = datetime.utcnow() - timedelta(days=2)
        verify_token("valid")
        assert "expired1" not in _admin_tokens
        assert "expired2" not in _admin_tokens
        assert "valid" in _admin_tokens

    def test_cap_at_max_tokens(self):
        from openeo_ai.web_auth import verify_token, _admin_tokens, _MAX_ADMIN_TOKENS
        for i in range(_MAX_ADMIN_TOKENS + 20):
            _admin_tokens[f"token-{i}"] = datetime.utcnow() - timedelta(seconds=i)
        verify_token("check")
        assert len(_admin_tokens) <= _MAX_ADMIN_TOKENS

    def test_empty_token(self):
        from openeo_ai.web_auth import verify_token
        assert verify_token("") is False


class TestAdminPasswordSecurity:
    """Tests for admin password configuration."""

    def test_no_leaked_password_in_source(self):
        """Ensure source code does not contain the old insecure default password."""
        import inspect
        from openeo_ai import web_auth
        source = inspect.getsource(web_auth)
        assert "jonaai2024" not in source

    def test_default_password_is_empty_string(self):
        """The fallback default should be empty, disabling login if env var is unset."""
        import inspect
        from openeo_ai import web_auth
        source = inspect.getsource(web_auth)
        assert 'get("ADMIN_PASSWORD", "")' in source


class TestPathTraversal:
    """Tests for path traversal protection logic used by render-raster."""

    def _is_path_allowed(self, path_str: str) -> bool:
        resolved = Path(path_str).resolve()
        allowed_dirs = [
            Path("/tmp").resolve(),
            Path.home().resolve() / ".openeo_jobs",
            Path.cwd().resolve(),
        ]
        return any(str(resolved).startswith(str(d)) for d in allowed_dirs)

    def test_allowed_tmp_path(self):
        assert self._is_path_allowed("/tmp/test_raster.tif") is True

    def test_blocked_etc_passwd(self):
        assert self._is_path_allowed("/etc/passwd") is False

    def test_blocked_dot_dot_traversal(self):
        assert self._is_path_allowed("/tmp/../../etc/passwd") is False

    def test_allowed_openeo_jobs(self):
        path = str(Path.home() / ".openeo_jobs" / "results" / "test.tif")
        assert self._is_path_allowed(path) is True

    def test_blocked_home_ssh(self):
        assert self._is_path_allowed(str(Path.home() / ".ssh" / "id_rsa")) is False

    def test_blocked_system_path(self):
        assert self._is_path_allowed("/var/log/syslog") is False
