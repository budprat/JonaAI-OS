# ABOUTME: A node id collision in SCF lowering escaped as a bare ValueError; it is a
# ABOUTME: LoweringError now, so it lands in the errors list like every other failure.
import pytest

from openeo_ai.validation.graph_validator import (
    build_default_process_specs,
    get_custom_process_specs,
)
from openeo_ai.validation.scf.lowering import LoweringError, NodeBuilder, lower_scf_to_pg


def _specs():
    specs = build_default_process_specs()
    specs.update(get_custom_process_specs())
    return specs


def _scf(transformations):
    return {
        "scf_version": "1.0",
        "domain_profile": "optical",
        "policy_profile": {
            "policy_version": "1", "resampling_method": "bilinear",
            "harmonization_policy": "none", "reflectance_scaling": "none",
            "missing_data_policy": "skip", "default_output_format": "GTiff",
        },
        "data": {
            "collection": "SENTINEL2_L2A", "bands": ["nir", "red"],
            "spatial_extent": {"west": 0, "south": 0, "east": 1, "north": 1},
            "temporal_extent": {"start": "2024-01-01", "end": "2024-02-01"},
        },
        "transformations": transformations,
        "output": {"type": "raster", "dimensions": ["x", "y"], "format": "GTiff"},
    }


def test_a_single_index_still_lowers():
    pg, _, errors, pg_hash = lower_scf_to_pg(
        _scf([{"id": "t1", "type": "spectral_index", "name": "ndvi"}]), process_specs=_specs()
    )
    assert errors == []
    assert pg and pg_hash


def test_two_identical_indices_report_an_error_instead_of_raising():
    """Was: ValueError("Node id collision: ndvi") straight out of lower_scf_to_pg.

    Now caught one step earlier by the chained-index guard, so the code is
    UnsupportedIndexChain. The collision guard itself is still pinned directly by
    test_the_builder_raises_a_lowering_error_not_a_valueerror.
    """
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([
            {"id": "t1", "type": "spectral_index", "name": "ndvi"},
            {"id": "t2", "type": "spectral_index", "name": "ndvi"},
        ]),
        process_specs=_specs(),
    )
    assert [e.code for e in errors] == ["UnsupportedIndexChain"]


def test_two_different_indices_do_not_silently_produce_a_meaningless_graph():
    """ndvi then ndsi returned errors=[] and a graph whose band_green/band_swir16
    read from_node "ndvi" — bands extracted out of a single-band index raster, with
    the first index left dangling and unused."""
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([
            {"id": "t1", "type": "spectral_index", "name": "ndvi"},
            {"id": "t2", "type": "spectral_index", "name": "ndsi"},
        ]),
        process_specs=_specs(),
    )
    assert [e.code for e in errors] == ["UnsupportedIndexChain"]
    assert "one index per request" in errors[0].message


def test_no_array_element_ever_reads_from_an_index_node():
    """The invariant behind the guard, asserted on the graph itself."""
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([{"id": "t1", "type": "spectral_index", "name": "ndvi"}]),
        process_specs=_specs(),
    )
    assert errors == []
    nodes = pg.get("process_graph", pg)
    index_ids = {n for n, b in nodes.items()
                 if b["process_id"] in {"normalized_difference", "ndvi", "ndsi"}}
    for name, body in nodes.items():
        if body["process_id"] == "array_element":
            source = body["arguments"].get("data", {}).get("from_node")
            assert source not in index_ids, f"{name} reads bands from index node {source}"


def test_an_index_followed_by_a_temporal_reduction_still_lowers():
    """The guard must not refuse a legitimate composition."""
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([
            {"id": "t1", "type": "spectral_index", "name": "ndvi"},
            {"id": "t2", "type": "temporal_reduction", "reducer": "mean", "dimension": "time"},
        ]),
        process_specs=_specs(),
    )
    assert errors == []
    assert pg and len(pg.get("process_graph", pg)) >= 6


def test_the_builder_raises_a_lowering_error_not_a_valueerror():
    builder = NodeBuilder()
    builder.add("ndvi", {}, node_id="ndvi")
    with pytest.raises(LoweringError) as caught:
        builder.add("ndvi", {}, node_id="ndvi")
    assert caught.value.code == "NodeIdCollision"


def test_auto_numbered_ids_do_not_collide():
    """The default id path counts per process, so repeats are fine there."""
    builder = NodeBuilder()
    first = builder.add("multiply", {})
    second = builder.add("multiply", {})
    assert first != second
    assert set(builder.nodes) == {first, second}


def test_the_compile_tool_reports_it_as_a_lowering_error():
    """graph_builder_tools.py:1067 does not guard the call, so this used to escape."""
    from openeo_ai.tools.graph_builder_tools import _openeo_compile_scf

    result = _openeo_compile_scf(_scf([
        {"id": "t1", "type": "spectral_index", "name": "ndvi"},
        {"id": "t2", "type": "spectral_index", "name": "ndvi"},
    ]))
    assert result["valid"] is False
    assert any("UnsupportedIndexChain" in e for e in result["lowering_errors"])


# --- band_math alias substitution: whole names only, order-independent ---
def test_aliases_rename_whole_band_names_not_substrings():
    """Was expr.replace() per alias: "rededge - red" became "B04edge - B04" when the
    map happened to list "red" first. Latent (no caller passes band_alias_map yet)."""
    from openeo_ai.validation.scf.lowering import _apply_band_aliases

    forward = {"red": "B04", "rededge": "B05"}
    backward = {"rededge": "B05", "red": "B04"}
    assert _apply_band_aliases("rededge - red", forward) == "B05 - B04"
    assert _apply_band_aliases("rededge - red", backward) == "B05 - B04"


def test_alias_substitution_is_order_independent():
    from openeo_ai.validation.scf.lowering import _apply_band_aliases
    import itertools

    pairs = [("red", "B04"), ("rededge", "B05"), ("nir", "B08")]
    expression = "(nir - rededge) / (nir + red)"
    results = {
        _apply_band_aliases(expression, dict(order))
        for order in itertools.permutations(pairs)
    }
    assert results == {"(B08 - B05) / (B08 + B04)"}, results


def test_an_empty_alias_map_leaves_the_expression_alone():
    from openeo_ai.validation.scf.lowering import _apply_band_aliases
    assert _apply_band_aliases("(B08 - B04) / (B08 + B04)", {}) == "(B08 - B04) / (B08 + B04)"


def test_numbers_and_operators_are_never_treated_as_band_names():
    from openeo_ai.validation.scf.lowering import _apply_band_aliases
    assert _apply_band_aliases("B08 * 2.5 + 1e3", {"B08": "nir"}) == "nir * 2.5 + 1e3"


def test_band_math_still_lowers_with_aliases_applied():
    pg, _, errors, _ = lower_scf_to_pg(
        _scf([{"id": "t1", "type": "band_math", "expression": "(nir - red) / (nir + red)"}]),
        process_specs=_specs(),
        band_alias_map={"red": "B04", "rededge": "B05", "nir": "B08"},
    )
    assert errors == []
    labels = {b["arguments"].get("label") for b in pg.get("process_graph", pg).values()
              if b["process_id"] == "array_element"}
    assert labels == {"B08", "B04"}, labels
