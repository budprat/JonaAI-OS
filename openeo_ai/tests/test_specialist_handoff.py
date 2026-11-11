# ABOUTME: The specialist→orchestrator handoff can carry the specialist's final prose
# ABOUTME: (flagged off by default), so orchestrator synthesis sees findings, not just metadata.

from __future__ import annotations

import pytest

from openeo_ai.sdk.claude_sdk_bridge import _maybe_prose_line, _prose_handoff_enabled


class TestProseLine:
    def test_included_when_enabled(self):
        line = _maybe_prose_line("Methodologist", "The trend is significant at p<0.05.", enabled=True)
        assert line is not None
        assert "Methodologist" in line and "significant" in line

    def test_none_when_disabled(self):
        assert _maybe_prose_line("Methodologist", "findings here", enabled=False) is None

    def test_none_when_prose_empty(self):
        assert _maybe_prose_line("Methodologist", "", enabled=True) is None

    def test_prose_is_bounded(self):
        line = _maybe_prose_line("Scout", "y" * 5000, enabled=True, limit=2000)
        # header + at most `limit` chars of prose
        assert line.count("y") == 2000


class TestFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("JONA_SPECIALIST_PROSE_HANDOFF", raising=False)
        assert _prose_handoff_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, val):
        monkeypatch.setenv("JONA_SPECIALIST_PROSE_HANDOFF", val)
        assert _prose_handoff_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", ""])
    def test_falsey_values_stay_off(self, monkeypatch, val):
        monkeypatch.setenv("JONA_SPECIALIST_PROSE_HANDOFF", val)
        assert _prose_handoff_enabled() is False
