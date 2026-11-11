# ABOUTME: Fail-open invariant tests for openeo_ai.langfuse_setup.
# Protects the guarantee that telemetry failures never become user-facing errors.

"""Fail-open invariant tests.

Every public helper in `openeo_ai.langfuse_setup` must be safe to call from
the user query path even when Langfuse is misconfigured, offline, or buggy.
These tests drive that invariant by calling each helper with:

    1. No Langfuse client available (`get_langfuse_client` returns None)
    2. A failing client that raises on every method (simulated network error)

Neither scenario is allowed to raise to the caller. Helpers must return
None, False, or a safe fallback value. If any of these tests fail, the
fail-open guarantee in langfuse_setup's module docstring is broken and
telemetry failures can crash user queries.
"""

from __future__ import annotations

import pytest

from openeo_ai import langfuse_setup


# ── Scenario 1: no client available ───────────────────────────────────


class TestHelpersWhenLangfuseUnavailable:
    """All helpers must tolerate get_langfuse_client() returning None."""

    @pytest.fixture(autouse=True)
    def _langfuse_unavailable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(langfuse_setup, "get_langfuse_client", lambda: None)

    def test_current_trace_id_returns_none(self) -> None:
        assert langfuse_setup.current_trace_id() is None

    def test_set_root_trace_name_no_raise(self) -> None:
        langfuse_setup.set_root_trace_name(
            name="unit.trace",
            input_data={"prompt": "x"},
            output_data={"ok": True},
        )

    def test_annotate_tool_span_no_raise(self) -> None:
        langfuse_setup.annotate_tool_span(
            tool_name="unit_tool",
            args={"a": 1, "big_payload": "x" * 10000},
            result={"ok": True},
        )

    def test_annotate_tool_span_with_error_no_raise(self) -> None:
        langfuse_setup.annotate_tool_span(
            tool_name="unit_tool",
            args={"a": 1},
            result=None,
            error="tool blew up",
        )

    def test_emit_score_returns_false(self) -> None:
        assert langfuse_setup.emit_score("unit_metric", 1.0) is False

    def test_mask_tool_args_still_works(self) -> None:
        # Masking is pure — doesn't touch Langfuse — must still work.
        out = langfuse_setup.mask_tool_args("unit_tool", {"api_key": "secret", "x": 1})
        assert out["api_key"] == "<redacted>"
        assert out["x"] == 1


# ── Scenario 2: client whose methods always raise ─────────────────────


class _FailingClient:
    """A Langfuse client impl where every method throws.

    Models real-world failure modes: network partition, OTel version
    mismatch, ingestion rejection, closed-span errors. None of these
    exceptions are allowed to propagate out of langfuse_setup helpers.
    """

    base_url = "https://broken"

    def get_current_trace_id(self):  # noqa: D401
        raise RuntimeError("network down")

    def update_current_span(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("span closed")

    def start_observation(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("OTel misconfigured")

    def create_score(self, **kwargs):  # noqa: ANN003
        raise RuntimeError("ingestion rejected")


class TestHelpersWhenLangfuseRaises:
    """All helpers must catch exceptions from a broken Langfuse client."""

    @pytest.fixture(autouse=True)
    def _langfuse_failing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            langfuse_setup, "get_langfuse_client", lambda: _FailingClient()
        )

    def test_current_trace_id_swallows_exception(self) -> None:
        assert langfuse_setup.current_trace_id() is None

    def test_set_root_trace_name_swallows_exception(self) -> None:
        langfuse_setup.set_root_trace_name(name="unit.trace", input_data={"x": 1})

    def test_annotate_tool_span_swallows_exception(self) -> None:
        langfuse_setup.annotate_tool_span(
            tool_name="unit_tool",
            args={"a": 1},
            result={"ok": True},
        )

    def test_emit_score_returns_false_on_exception(self) -> None:
        assert langfuse_setup.emit_score("unit_metric", 1.0, trace_id="external-trace") is False


# ── Truncation metadata contract ──────────────────────────────────────


class TestTruncationMetadata:
    """Verifies the mask_tool_args truncation-with-meta contract added 2026-04-11."""

    def test_short_string_unchanged(self) -> None:
        out = langfuse_setup.mask_tool_args("t", {"k": "hello"})
        assert out["k"] == "hello"

    def test_long_string_returns_meta_dict(self) -> None:
        long = "x" * 1500
        out = langfuse_setup.mask_tool_args("t", {"k": long})
        k = out["k"]
        assert isinstance(k, dict)
        assert k["_truncated"] is True
        assert k["_orig_len"] == 1500
        assert k["_kept_len"] == 500
        assert len(k["text"]) == 500
        assert isinstance(k["_sha256"], str)
        assert len(k["_sha256"]) == 16  # 16-hex prefix

    def test_identical_long_strings_dedupe_by_hash(self) -> None:
        # Deduplication signal: identical payloads produce identical hashes.
        long = "y" * 2000
        a = langfuse_setup.mask_tool_args("t1", {"k": long})["k"]
        b = langfuse_setup.mask_tool_args("t2", {"k": long})["k"]
        assert a["_sha256"] == b["_sha256"]
