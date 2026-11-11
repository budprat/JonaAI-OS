# ABOUTME: Index classification used substring tests, so "red" inside "rededge" made a
# ABOUTME: red-edge formula build NDVI silently, reading a band never requested.
import pytest

from openeo_ai.validation.graph_validator import (
    build_default_process_specs,
    get_custom_process_specs,
)
from openeo_ai.validation.scf.invariants.optical import validate_optical
from openeo_ai.validation.scf.lowering import _formula_tokens, lower_scf_to_pg


def _specs():
    specs = build_default_process_specs()
    specs.update(get_custom_process_specs())
    return specs


def _scf(transformations, bands):
    return {
        "scf_version": "1.0", "domain_profile": "optical",
        "policy_profile": {
            "policy_version": "1", "resampling_method": "bilinear",
            "harmonization_policy": "none", "reflectance_scaling": "none",
            "missing_data_policy": "skip", "default_output_format": "GTiff",
        },
        "data": {
            "collection": "SENTINEL2_L2A", "bands": list(bands),
            "spatial_extent": {"west": 0, "south": 0, "east": 1, "north": 1},
            "temporal_extent": {"start": "2024-01-01", "end": "2024-02-01"},
        },
        "transformations": transformations,
        "output": {"type": "raster", "dimensions": ["x", "y"], "format": "GTiff"},
    }


def _built(pg):
    nodes = pg.get("process_graph", pg) if pg else {}
    return sorted(n["arguments"].get("label") for n in nodes.values()
                  if n["process_id"] == "array_element")


def test_tokens_do_not_see_red_inside_rededge():
    tokens = _formula_tokens("(nir - rededge) / (nir + rededge)")
    assert tokens == {"nir", "rededge"}
    assert "red" not in tokens


def test_a_red_edge_index_is_refused_not_built_as_ndvi():
    """Was: errors=[] and a node literally named "ndvi" reading a `red` band that the
    request never listed in data.bands."""
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([{"id": "t", "type": "spectral_index", "name": "ndre",
               "formula": "(nir - rededge) / (nir + rededge)"}], ["nir", "rededge"]),
        process_specs=_specs(),
    )
    assert [e.code for e in errors] == ["UnsupportedIndex"]
    assert "red" not in _built(pg)


@pytest.mark.parametrize("transformation,bands,expected", [
    ({"name": "ndvi"}, ["nir", "red"], ["nir", "red"]),
    ({"name": None, "formula": "(nir - red) / (nir + red)"}, ["nir", "red"], ["nir", "red"]),
    ({"name": "ndsi"}, ["green", "swir16"], ["green", "swir16"]),
    ({"name": None, "formula": "(green - swir16) / (green + swir16)"},
     ["green", "swir16"], ["green", "swir16"]),
    ({"name": None, "formula": "(green - swir22) / (green + swir22)"},
     ["green", "swir22"], ["green", "swir16"]),  # swir matched by prefix, label from alias default
])
def test_the_supported_indices_still_classify(transformation, bands, expected):
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([{"id": "t", "type": "spectral_index", **transformation}], bands),
        process_specs=_specs(),
    )
    assert errors == [], [e.code for e in errors]
    assert _built(pg) == sorted(expected)


def test_a_formula_using_only_sensor_band_names_is_refused():
    """(B08 - B04) names no common band, so it is not classifiable — unchanged."""
    _, _, errors, _ = lower_scf_to_pg(
        _scf([{"id": "t", "type": "spectral_index", "name": "custom",
               "formula": "(B08 - B04) / (B08 + B04)"}], ["B08", "B04"]),
        process_specs=_specs(),
    )
    assert [e.code for e in errors] == ["UnsupportedIndex"]


# --- the same heuristic in the optical invariant ---
def test_the_invariant_does_not_demand_red_for_a_red_edge_formula():
    """optical.py:85 reported MissingBands ['red'] for a formula that needs no red."""
    errors, _, _ = validate_optical(_scf(
        [{"id": "t", "type": "spectral_index", "name": "ndre",
          "formula": "(nir - rededge) / (nir + rededge)"}], ["nir", "rededge"]))
    assert not [e for e in errors if e["code"] == "MissingBands"], errors


def test_the_invariant_still_demands_red_and_nir_for_ndvi():
    errors, _, _ = validate_optical(_scf(
        [{"id": "t", "type": "spectral_index", "name": "ndvi"}], ["nir"]))
    missing = [e for e in errors if e["code"] == "MissingBands"]
    assert missing and "red" in missing[0]["message"]
