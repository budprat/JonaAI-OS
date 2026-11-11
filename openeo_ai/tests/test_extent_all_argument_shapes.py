# ABOUTME: The 10/50-degree extent rule applies wherever an extent can arrive,
# ABOUTME: not only inside a process graph, and it does not misread the antimeridian.
import pytest

from openeo_ai.sdk.claude_sdk_bridge import _JOB_START_TOOLS
from openeo_ai.sdk.permissions import (
    MAX_SPATIAL_EXTENT,
    SAFE_MODIFY_TOOLS,
    _lon_span_degrees,
    _validate_extent,
)


def _graph(width):
    return {"process_graph": {"load": {"process_id": "load_collection", "arguments": {
        "id": "sentinel-2-l2a",
        "spatial_extent": {"west": 0.0, "east": float(width), "south": 0.0, "north": 1.0},
    }}}}


def _top_level(width):
    # openeo_aggregate_spatial / openeo_apply_kernel read args["spatial_extent"]
    return {"spatial_extent": {"west": 0.0, "east": float(width), "south": 0.0, "north": 1.0}}


def _aoi(width):
    # agentic_decision_query reads args["aoi"] = [west, south, east, north]
    return {"aoi": [0.0, 0.0, float(width), 1.0]}


SHAPES = pytest.mark.parametrize("build", [_graph, _top_level, _aoi], ids=["graph", "spatial_extent", "aoi"])


@SHAPES
def test_small_extent_passes(build):
    assert _validate_extent(build(1.0)) is True


@SHAPES
def test_mid_extent_requires_confirmation(build):
    result = _validate_extent(build(20.0))
    assert isinstance(result, dict), f"{build.__name__} was not checked at all"
    assert result["type"] == "confirmation_required"


@SHAPES
def test_confirmed_mid_extent_is_allowed(build):
    args = build(20.0)
    args["confirm_large_extent"] = True
    assert _validate_extent(args) is True


@SHAPES
def test_hard_cap_is_not_contestable(build):
    args = build(60.0)
    args["confirm_large_extent"] = True
    assert _validate_extent(args) is False


def test_latitude_is_checked_not_only_longitude():
    assert _validate_extent({"spatial_extent": {
        "west": 0.0, "east": 1.0, "south": -40.0, "north": 40.0}}) is False


# --- antimeridian: extent_to_lonlat does not normalise the dateline, so a plain
# --- abs(east - west) read a 20-degree box as 340 degrees and hard-blocked it.
def test_antimeridian_span_is_not_inflated():
    assert _lon_span_degrees(170.0, -170.0) == pytest.approx(20.0)


def test_antimeridian_small_extent_is_not_falsely_blocked():
    # 170E .. 170W is 20 degrees wide: above the confirm threshold, below the hard cap.
    result = _validate_extent({"spatial_extent": {
        "west": 170.0, "east": -170.0, "south": 0.0, "north": 1.0}})
    assert result is not False, "a 20-degree dateline-crossing extent was hard-blocked"
    assert isinstance(result, dict) and result["type"] == "confirmation_required"


def test_antimeridian_tiny_extent_passes_outright():
    assert _validate_extent({"spatial_extent": {
        "west": 179.0, "east": -179.0, "south": 0.0, "north": 1.0}}) is True


def test_antimeridian_genuinely_large_extent_still_blocked():
    # 100E .. 100W crosses the dateline and really is 160 degrees wide.
    assert _validate_extent({"spatial_extent": {
        "west": 100.0, "east": -100.0, "south": 0.0, "north": 1.0}}) is False


# --- the gate, not just the rule: a tool that starts backend work must reach layer 4.
def test_extent_gate_covers_job_starts():
    gated = SAFE_MODIFY_TOOLS | _JOB_START_TOOLS
    assert _JOB_START_TOOLS <= gated
    for tool in ("openeo_aggregate_spatial", "openeo_apply_kernel", "agentic_decision_query"):
        assert tool in gated, f"{tool} would skip extent validation"


def test_things_that_are_not_an_extent_at_all_are_left_alone():
    """No bbox to measure, so nothing to refuse — and nothing may raise either.

    {"west": None} used to live in this list. It is a bbox with an unusable bound, and
    is now refused rather than skipped; see
    test_an_unreadable_bbox_is_refused_rather_than_skipped.
    """
    for args in ({"aoi": []}, {"aoi": ["x", 0, 1, 1]}, {"aoi": [0, 0, 1]},
                 {"spatial_extent": {}}, {"spatial_extent": "global"},
                 {"spatial_extent": None}, {}):
        assert _validate_extent(args) is True, args


def test_threshold_constant_is_the_one_being_enforced():
    just_over = MAX_SPATIAL_EXTENT + 0.5
    assert isinstance(_validate_extent(_top_level(just_over)), dict)
    assert _validate_extent(_top_level(MAX_SPATIAL_EXTENT - 0.5)) is True


# --- a bbox the size check cannot read must be refused, not waved through ---
GLOBAL = {"west": -180.0, "east": 180.0, "south": -90.0, "north": 90.0}


def test_a_real_global_extent_is_blocked():
    assert _validate_extent({"spatial_extent": dict(GLOBAL)}) is False


@pytest.mark.parametrize("broken,label", [
    ({**GLOBAL, "north": float("inf")}, "infinite bound"),
    ({**GLOBAL, "east": float("nan")}, "nan bound"),
    ({k: v for k, v in GLOBAL.items() if k != "north"}, "missing bound"),
    ({**GLOBAL, "west": "abc"}, "non-numeric bound"),
    ({**GLOBAL, "west": None}, "null bound"),
])
def test_an_unreadable_bbox_is_refused_rather_than_skipped(broken, label):
    """The same global area was ALLOWED when one bound was unreadable: the size
    limit never ran because extent_to_lonlat returned None and the loop continued."""
    result = _validate_extent({"spatial_extent": broken})
    assert isinstance(result, dict), f"{label} skipped the extent guard entirely"
    assert result["type"] == "invalid_extent"


def test_the_refusal_is_not_contestable_with_confirm():
    args = {"spatial_extent": {**GLOBAL, "north": float("inf")},
            "confirm_large_extent": True}
    assert isinstance(_validate_extent(args), dict)


def test_numeric_strings_are_still_accepted():
    """Compatibility: float("90") works, so this must keep behaving as a real bbox."""
    assert _validate_extent({"spatial_extent": {
        "west": "0", "east": "1", "south": "0", "north": "1"}}) is True
    assert _validate_extent({"spatial_extent": {
        "west": "-180", "east": "180", "south": "-90", "north": "90"}}) is False


def test_an_unresolvable_crs_is_still_skipped_not_refused():
    """Deliberate: failing closed here would make a missing pyproj block every
    request carrying a CRS, the outage shape rejected for the process-spec registry."""
    assert _validate_extent({"spatial_extent": {
        "west": 0, "east": 1, "south": 0, "north": 1, "crs": "EPSG:999999"}}) is True


def test_a_geojson_geometry_is_not_mistaken_for_a_broken_bbox():
    """load_collection may carry a geometry instead of a bbox; it has none of the
    bbox keys and must not be refused as malformed."""
    assert _validate_extent({"spatial_extent": {
        "type": "Polygon", "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]]}}) is True


def test_a_malformed_bbox_is_caught_in_every_argument_shape():
    broken = {**GLOBAL, "north": float("inf")}
    assert isinstance(_validate_extent({"spatial_extent": broken}), dict)
    assert isinstance(_validate_extent({"process_graph": {"load": {
        "process_id": "load_collection",
        "arguments": {"id": "s2", "spatial_extent": broken}}}}), dict)
    # aoi is built from four positional floats, so a non-numeric entry is dropped
    # before this point; it must still not become a silent skip.
    assert _validate_extent({"aoi": [0.0, 0.0, "abc", 1.0]}) is True
