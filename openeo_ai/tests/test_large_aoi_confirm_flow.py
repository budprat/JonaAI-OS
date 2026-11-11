# ABOUTME: C22 — the 10–50° AOI confirmation is contestable via confirm_large_extent
# ABOUTME: after explicit user consent in chat; the >50° hard cap never is.
from openeo_ai.sdk.permissions import _validate_extent


def _args(width, confirm=None):
    args = {
        "process_graph": {
            "load": {
                "process_id": "load_collection",
                "arguments": {
                    "id": "sentinel-2-l2a",
                    "spatial_extent": {
                        "west": 0.0, "east": float(width),
                        "south": 0.0, "north": 1.0,
                    },
                },
            },
        },
    }
    if confirm is not None:
        args["confirm_large_extent"] = confirm
    return args


def test_small_extent_passes():
    assert _validate_extent(_args(1.0)) is True


def test_large_extent_requires_confirmation():
    result = _validate_extent(_args(20.0))
    assert isinstance(result, dict)
    assert result["type"] == "confirmation_required"


def test_confirmed_large_extent_is_allowed():
    assert _validate_extent(_args(20.0, confirm=True)) is True


def test_hard_cap_is_not_contestable():
    # >50 degrees stays blocked even with the confirmation flag.
    assert _validate_extent(_args(60.0, confirm=True)) is False


def test_confirmation_message_names_the_retry_flag():
    # The bridge hook denies with this dict's message; the model (and user)
    # must learn the narrow-or-confirm protocol from it.
    result = _validate_extent(_args(20.0))
    assert "confirm_large_extent" in result["message"]
    assert "narrow" in result["message"].lower()
