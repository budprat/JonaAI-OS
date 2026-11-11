# ABOUTME: Index formula templates substituted band aliases with a substring replace
# ABOUTME: over an alias_map that deliberately holds overlapping, identity-mapped keys.
import typing

import pytest

from openeo_ai.validation.scf.tools.band_index_semantics import (
    INDEX_TEMPLATES,
    BandIndexSemanticsService,
    _rename_whole_bands,
)


def _service():
    return BandIndexSemanticsService(dataset_service=None)


def test_whole_band_names_are_renamed_not_substrings():
    aliases = {"red": "B04", "rededge": "B05", "nir": "B08"}
    assert _rename_whole_bands("(nir - rededge) / (nir + red)", aliases) == "(B08 - B05) / (B08 + B04)"


def test_renaming_is_order_independent():
    import itertools
    pairs = [("red", "B04"), ("rededge", "B05"), ("nir", "B08")]
    formula = "(nir - rededge) / (nir + red)"
    assert {
        _rename_whole_bands(formula, dict(order)) for order in itertools.permutations(pairs)
    } == {"(B08 - B05) / (B08 + B04)"}


def test_identity_entries_are_harmless():
    """alias_map holds every real band name mapped to itself (lines 42-43)."""
    aliases = {"red": "red", "nir": "nir", "B04": "B04"}
    assert _rename_whole_bands("(nir - red) / (nir + red)", aliases) == "(nir - red) / (nir + red)"


def test_numbers_and_operators_survive():
    formula = INDEX_TEMPLATES["evi"]["formula"]
    out = _rename_whole_bands(formula, {"red": "B04", "nir": "B08", "blue": "B02"})
    assert out == "2.5 * (B08 - B04) / (B08 + 6*B04 - 7.5*B02 + 1)"
    assert "2.5" in out and "7.5" in out and "+ 1)" in out


def test_an_empty_alias_map_leaves_every_template_alone():
    mapped = _service()._map_index_templates({})
    for name, template in INDEX_TEMPLATES.items():
        assert mapped[name]["formula"] == template["formula"]


@pytest.mark.parametrize("index", sorted(INDEX_TEMPLATES))
def test_every_template_maps_all_of_its_required_bands(index):
    """No required band may survive un-renamed when an alias exists for it."""
    aliases = {"red": "B04", "nir": "B08", "blue": "B02", "green": "B03", "swir16": "B11"}
    mapped = _service()._map_index_templates(aliases)[index]
    for band in INDEX_TEMPLATES[index]["required_bands"]:
        assert band not in mapped["formula"], f"{band} was left un-renamed in {index}"
    assert mapped["required_bands"] == INDEX_TEMPLATES[index]["required_bands"]


def test_the_return_value_type_is_typing_any_not_the_builtin():
    """Four methods were annotated Dict[str, any] — the builtin function `any`.

    get_type_hints does NOT raise on that (checked on this Python), it resolves to
    Dict[str, <built-in function any>], which no type checker can use. So assert the
    resolved argument IS typing.Any; a bare get_type_hints call passes either way and
    would be a vacuous test.
    """
    for method in ("get_band_semantics", "get_band_semantics_sync",
                   "validate_index", "validate_index_sync"):
        hints = typing.get_type_hints(getattr(BandIndexSemanticsService, method))
        value_type = typing.get_args(hints["return"])[1]
        assert value_type is typing.Any, f"{method} -> {value_type!r}"
