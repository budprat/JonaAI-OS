# ABOUTME: The SCF cost estimate measured longitude with abs(east-west), so a request
# ABOUTME: crossing the antimeridian came out 17x too large and was classed "large".
import typing

import pytest

from openeo_ai.validation.scf.tools.cost_estimation import (
    _lon_span_degrees,
    _parse_period,
    estimate,
)


def _scf(west, south, east, north, bands=("B04", "B08")):
    return {
        "data": {
            "spatial_extent": {"west": west, "south": south, "east": east, "north": north},
            "temporal_extent": {"start": "2024-01-01", "end": "2024-01-11"},
            "bands": list(bands),
        },
        "policy_profile": {},
    }


def test_the_span_wraps_at_the_antimeridian():
    assert _lon_span_degrees(west=170.0, east=-170.0) == pytest.approx(20.0)
    assert _lon_span_degrees(west=10.0, east=30.0) == pytest.approx(20.0)
    assert _lon_span_degrees(west=100.0, east=-100.0) == pytest.approx(160.0)


def test_a_dateline_request_costs_the_same_as_the_equivalent_one_elsewhere():
    """Was 40,183,227,294 pixels vs 2,363,719,252 — a 17x (340/20) overestimate."""
    across = estimate(_scf(170.0, -18.0, -170.0, -17.0))
    plain = estimate(_scf(10.0, -18.0, 30.0, -17.0))
    assert across["estimated_pixels"] == plain["estimated_pixels"]
    assert across["estimated_mb"] == plain["estimated_mb"]
    assert across["runtime_class"] == plain["runtime_class"]


def test_a_genuinely_wide_dateline_request_is_still_expensive():
    narrow = estimate(_scf(179.0, 0.0, -179.0, 1.0))
    wide = estimate(_scf(100.0, 0.0, -100.0, 1.0))
    assert wide["estimated_pixels"] > narrow["estimated_pixels"] * 50


def test_a_missing_extent_is_reported_not_guessed():
    assert estimate({"data": {"spatial_extent": {"west": 0}}})["error"] == "Missing spatial extent"
    assert estimate({})["error"] == "Missing spatial extent"


def test_the_return_value_type_is_typing_any_not_the_builtin():
    """Was Dict[str, any] — the builtin function `any`.

    Correction to this commit's own first draft: get_type_hints does not raise on it,
    it resolves to Dict[str, <built-in function any>]. Asserting only that
    get_type_hints succeeds would pass before and after, so assert the resolved value
    type IS typing.Any.
    """
    value_type = typing.get_args(typing.get_type_hints(estimate)["return"])[1]
    assert value_type is typing.Any, repr(value_type)


@pytest.mark.parametrize("period,days", [
    ("P1M", 30), ("P12M", 360), ("P1Y", 365),
    ("PT1M", None),   # minutes, not months — correctly excluded
    ("P1Y6M", None),  # mixed: degrades to the cadence heuristic rather than guessing
    ("P30D", None), (None, None), ("", None),
])
def test_period_parsing_is_conservative(period, days):
    assert _parse_period(period) == days


def test_more_bands_cost_more():
    one = estimate(_scf(0.0, 0.0, 1.0, 1.0, bands=("B04",)))
    four = estimate(_scf(0.0, 0.0, 1.0, 1.0, bands=("B02", "B03", "B04", "B08")))
    assert four["estimated_cells"] == one["estimated_cells"] * 4
