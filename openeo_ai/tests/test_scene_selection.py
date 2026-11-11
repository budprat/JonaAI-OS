"""Scene selection, data notes, CRS handling, and the load-size estimate."""
from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from openeo_app.core.extent import extent_to_lonlat, load_resolution
from openeo_app.core.job_notes import add_job_note, start_job_notes
from openeo_app.processes.load_collection import (
    _drop_duplicate_scenes,
    _scene_limit_note,
    _select_stac_items,
)
from openeo_ai.sdk.claude_sdk_bridge import _SCENE_NOTE_RE
from openeo_ai.sdk.permissions import _validate_extent
from openeo_ai.utils.geocoding import constrain_bbox_to_target_size


def _item(day, cloud, name=None, href="https://cog/B04.tif"):
    return SimpleNamespace(
        id=name or f"S2A_33UUU_{day.replace('-', '')}_0_L2A",
        properties={"datetime": f"{day}T10:00:00Z", "eo:cloud_cover": cloud},
        assets={"red": SimpleNamespace(href=href)},
    )


def _season(days=120, cloud=lambda i: 10.0):
    start = date(2023, 5, 1)
    return [_item((start + timedelta(days=i * 5)).isoformat(), cloud(i)) for i in range(days // 5)]


def test_small_search_keeps_everything():
    items = _season(days=20)
    assert len(_select_stac_items(items, 5)) == 4


def test_selection_spans_the_period_when_clear_scenes_cluster():
    # The clearest scenes all sit at the start: picking by cloud alone would use one month.
    items = _season(cloud=lambda i: float(i))
    chosen = sorted(item.properties["datetime"][:10] for item in _select_stac_items(items, 5))
    assert len(chosen) == 5
    assert (date.fromisoformat(chosen[-1]) - date.fromisoformat(chosen[0])).days > 60


def test_selection_skips_cloudy_days_when_clear_ones_can_fill_the_limit():
    items = _season(cloud=lambda i: 90.0 if i % 2 else 5.0)
    assert all(item.properties["eo:cloud_cover"] == 5.0 for item in _select_stac_items(items, 5))


def test_limit_counts_dates_and_keeps_each_dates_tiles_together():
    items = []
    for offset in range(8):
        day = (date(2023, 6, 1) + timedelta(days=offset * 5)).isoformat()
        items += [_item(day, 5.0, f"S2A_33UU{tile}_{day}_0_L2A") for tile in "AB"]
    per_day = {}
    for item in _select_stac_items(items, 5):
        per_day[item.properties["datetime"]] = per_day.get(item.properties["datetime"], 0) + 1
    assert len(per_day) == 5
    assert set(per_day.values()) == {2}


def test_one_date_with_many_tiles_keeps_them_all():
    items = [_item("2023-06-01", 5.0, f"S2A_33UU{tile}_0_L2A") for tile in "ABCDEFG"]
    assert len(_select_stac_items(items, 5)) == 7


def test_catalog_duplicates_count_once_and_prefer_the_cog():
    jp2 = _item("2024-01-12", 5.0, "S2B_42QZK_20240112_1_L2A", href="s3://sentinel-s2-l2a/x.jp2")
    cog = _item("2024-01-12", 5.0, "S2B_42QZK_20240112_0_L2A")
    assert [item.id for item in _drop_duplicate_scenes([jp2, cog])] == [cog.id]
    only_jp2 = _item("2024-01-17", 5.0, "S2B_42QZK_20240117_1_L2A", href="s3://sentinel-s2-l2a/y.jp2")
    assert _drop_duplicate_scenes([only_jp2]) == [only_jp2]


def test_note_states_counts_and_dates_and_is_found_in_tool_output():
    available = _season(days=50)
    note = _scene_limit_note(available[:2], available, False, 5)
    expected = "Used 2 of 10 available dates (2023-05-01, 2023-05-06)"
    assert note.startswith(expected)
    assert _SCENE_NOTE_RE.findall(f'{{"warnings": ["{note}"]}}') == [expected]


def test_job_notes_collect_only_inside_a_job():
    add_job_note("ignored")
    notes = start_job_notes()
    add_job_note("kept")
    add_job_note("kept")
    assert notes == ["kept"]


def test_utm_extent_converts_to_degrees():
    lonlat = extent_to_lonlat(
        {"west": 500000, "east": 502000, "south": 5800000, "north": 5802000, "crs": "EPSG:32633"}
    )
    assert lonlat["east"] - lonlat["west"] == pytest.approx(0.029, abs=0.002)
    assert 52 < lonlat["south"] < 53


def test_unreadable_extent_is_none():
    assert extent_to_lonlat({"west": 1, "east": 2, "south": 3, "north": 4, "crs": "not-a-crs"}) is None
    assert extent_to_lonlat({"west": None, "east": 2, "south": 3, "north": 4}) is None


def _graph(extent):
    return {"process_graph": {"l": {"process_id": "load_collection", "arguments": {"spatial_extent": extent}}}}


def test_extent_guard_reads_crs():
    utm = {"west": 500000, "east": 502000, "south": 5800000, "north": 5802000, "crs": "EPSG:32633"}
    assert _validate_extent(_graph(utm)) is True
    assert _validate_extent(_graph({"west": -100, "east": 100, "south": 0, "north": 10})) is False


def test_extent_guard_reads_a_graph_given_as_json_text():
    import json

    wide = json.dumps(_graph({"west": -100, "east": 100, "south": 0, "north": 10}))
    assert _validate_extent({"process_graph": wide}) is False


def test_load_grid_is_square_on_the_ground():
    step_x, step_y = load_resolution(59.9, 60.1)
    assert step_x == pytest.approx(0.0002, rel=0.01)
    assert step_y == 0.0001
    assert load_resolution() == (0.0001, 0.0001)
    assert load_resolution(89.9, 90.0)[0] == pytest.approx(0.001)


def test_size_estimate_warns_when_a_job_would_be_rejected(monkeypatch):
    monkeypatch.setenv("OPENEO_MAX_LOAD_ELEMENTS", "40000000")
    berlin = {"west": 13.1, "east": 13.75, "south": 52.3, "north": 52.7}
    _, info = constrain_bbox_to_target_size(berlin)
    assert info["load_check"]["fits"] is False
    assert "m per pixel" in info["load_check"]["note"]
    _, huge = constrain_bbox_to_target_size({"west": 10, "east": 11.5, "south": 50, "north": 51.5})
    assert "rejected" in huge["load_check"]["note"]
    _, small = constrain_bbox_to_target_size({"west": 13.1, "east": 13.12, "south": 52.3, "north": 52.32})
    assert small["load_check"]["fits"] is True


def test_decision_route_reads_worded_spans_and_seasons():
    from openeo_ai.agentic_eo.decompose import _detect_time, decompose

    today = date(2026, 11, 7)
    span, explicit = _detect_time("declined over the last three dry seasons?", today)
    assert explicit and span.start == today - timedelta(days=1095)
    span, explicit = _detect_time("change over the past year", today)
    assert explicit and span.start == today - timedelta(days=365)
    span, explicit = _detect_time("last 6 months", today)
    assert explicit and span.start == today - timedelta(days=180)

    intent = decompose(
        "Has vegetation around Porto Velho declined over the last three dry seasons?",
        today=today, use_llm=False, do_geocode=False,
    )
    assert (intent.time.end - intent.time.start).days == 1095
    assert any("whole span" in note for note in intent.assumptions)
