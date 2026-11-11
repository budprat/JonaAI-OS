# ABOUTME: Tests for the EARTH_INTELLIGENCE_WORKER opt-out gate — the worker
# ABOUTME: runs by default; only explicit 0/false/no/off disables. Gates agree.

"""Worker default-gate tests (decision ratified 2026-07-10: on by default).

Imports are deferred to function level: importing openeo_ai.web_interface at
collection time poisons unrelated tests (observed 2026-07-07 with the
learned_patterns suite).
"""

import pytest


def _web_gate():
    from openeo_ai.web_interface import _worker_enabled

    return _worker_enabled


def _tools_gate():
    from openeo_ai.tools.monitor_tools import _worker_active

    return _worker_active


class TestWorkerEnabledDefault:
    """web_interface._worker_enabled — the authoritative startup gate."""

    def test_unset_enables_worker(self, monkeypatch):
        monkeypatch.delenv("EARTH_INTELLIGENCE_WORKER", raising=False)
        assert _web_gate()() is True

    def test_empty_string_enables_worker(self, monkeypatch):
        monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "")
        assert _web_gate()() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "FALSE", "Off", "NO"])
    def test_explicit_off_values_disable(self, value, monkeypatch):
        monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", value)
        assert _web_gate()() is False

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "TRUE"])
    def test_affirmative_values_enable(self, value, monkeypatch):
        monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", value)
        assert _web_gate()() is True

    def test_unrecognized_value_enables(self, monkeypatch):
        # Opt-out semantics: anything outside the explicit off-set runs the worker.
        monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", "maybe")
        assert _web_gate()() is True


class TestGateParity:
    """monitor_tools._worker_active must mirror the startup gate exactly,
    else the worker_active note in monitoring tool responses lies."""

    @pytest.mark.parametrize(
        "value",
        [None, "", "0", "false", "no", "off", "1", "true", "yes", "on", "weird"],
    )
    def test_tools_gate_matches_web_gate(self, value, monkeypatch):
        if value is None:
            monkeypatch.delenv("EARTH_INTELLIGENCE_WORKER", raising=False)
        else:
            monkeypatch.setenv("EARTH_INTELLIGENCE_WORKER", value)
        assert _tools_gate()() == _web_gate()()
