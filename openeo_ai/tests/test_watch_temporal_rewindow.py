# ABOUTME: Tests the watch temporal re-windowing fix — a recurring Watch must
# ABOUTME: analyse the LATEST imagery each run, not the frozen dates baked in.

from datetime import datetime, timedelta

from openeo_ai.intelligence.worker import (
    _reroll_load_collection_dates,
    _rolling_temporal_window,
)


def _graph(temporal):
    return {
        "load": {"process_id": "load_collection",
                 "arguments": {"id": "sentinel-2-l2a", "temporal_extent": temporal}},
        "save": {"process_id": "save_result", "arguments": {}},
    }


def test_reroll_rewrites_load_collection_and_leaves_input_untouched():
    original = _graph(["2026-07-01", "2026-07-08"])
    out, n = _reroll_load_collection_dates(original, "2026-06-24", "2026-07-08")
    assert n == 1
    assert out["load"]["arguments"]["temporal_extent"] == ["2026-06-24", "2026-07-08"]
    # deep copy — the stored graph is never mutated
    assert original["load"]["arguments"]["temporal_extent"] == ["2026-07-01", "2026-07-08"]


def test_reroll_handles_process_graph_wrapper():
    wrapped = {"process_graph": _graph(["2026-01-01", "2026-01-07"])}
    out, n = _reroll_load_collection_dates(wrapped, "2026-06-24", "2026-07-08")
    assert n == 1
    assert out["process_graph"]["load"]["arguments"]["temporal_extent"] == ["2026-06-24", "2026-07-08"]


def test_reroll_noop_when_no_load_collection():
    out, n = _reroll_load_collection_dates({"x": {"process_id": "ndvi", "arguments": {}}}, "a", "b")
    assert n == 0


def test_window_ends_now_and_sizes_to_cadence():
    # The whole point of the fix: the window must end "now", never stay frozen.
    now = datetime.utcnow().date()
    start, end = _rolling_temporal_window("weekly", None)
    assert end == now.isoformat()
    # weekly (7d) floored at the 14-day Sentinel-2 revisit gap
    assert start == (now - timedelta(days=14)).isoformat()


def test_window_respects_temporal_policy_lookback():
    now = datetime.utcnow().date()
    start, _end = _rolling_temporal_window("weekly", {"lookback_days": 60})
    assert start == (now - timedelta(days=60)).isoformat()


def test_window_defaults_when_no_cadence():
    now = datetime.utcnow().date()
    start, _end = _rolling_temporal_window(None, None)
    assert start == (now - timedelta(days=30)).isoformat()
