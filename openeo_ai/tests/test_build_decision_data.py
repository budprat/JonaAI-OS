# ABOUTME: _build_decision_data revives the dead health_score path by unwrapping the
# ABOUTME: 2-item interpret_result MCP content, and threads computed confidence/coverage.

from __future__ import annotations

import json

from openeo_ai.sdk.claude_sdk_bridge import _build_decision_data


def _mcp(result_json: dict) -> dict:
    # The real interpret_result shape: [markdown text, json text].
    return {"content": [
        {"type": "text", "text": "# Interpretation\nSome markdown."},
        {"type": "text", "text": json.dumps(result_json)},
    ]}


def test_decision_data_from_live_mcp_content():
    rj = {"index_name": "ndvi",
          "statistics": {"mean": 0.42, "coverage_pct": 95.2},
          "quality": {"grade": "B"},
          "interpretation": {"grade": "B"}}
    d = _build_decision_data("interpret_result", _mcp(rj))
    assert d is not None                       # was None before the shape fix
    assert d["health_score"] == 42
    assert d["coverage"] == 95.2
    assert d["confidence"] == "B"


def test_plain_dict_still_works():
    # A tool that returns a bare stats dict (no MCP wrapper) keeps working.
    d = _build_decision_data("raster_statistics",
                             {"index_name": "ndvi", "statistics": {"mean": 0.6}})
    assert d is not None and d["health_score"] == 60


def test_non_decision_tool_returns_none():
    assert _build_decision_data("some_tool", {"content": [{"type": "text", "text": "no json here"}]}) is None
