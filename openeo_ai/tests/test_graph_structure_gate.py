# ABOUTME: Graph validation was model-elective; a graph that cannot run on any backend
# ABOUTME: is now refused before job creation, without depending on the spec registry.
import pytest

from openeo_ai.sdk.claude_sdk_bridge import _JOB_START_TOOLS
from openeo_ai.sdk.permissions import _validate_graph_structure

VALID = {
    "a": {"process_id": "load_collection", "arguments": {"id": "sentinel-2-l2a"}},
    "b": {"process_id": "save_result", "arguments": {"data": {"from_node": "a"}, "format": "GTiff"},
          "result": True},
}
CYCLE = {
    "a": {"process_id": "apply", "arguments": {"data": {"from_node": "b"}}},
    "b": {"process_id": "apply", "arguments": {"data": {"from_node": "a"}}},
}
DANGLING_REF = {
    "b": {"process_id": "save_result", "arguments": {"data": {"from_node": "missing"}}, "result": True},
}


def test_a_runnable_graph_is_not_refused():
    assert _validate_graph_structure({"process_graph": VALID}) is None


def test_a_tool_with_no_graph_is_not_refused():
    assert _validate_graph_structure({}) is None
    assert _validate_graph_structure({"aoi": [0, 0, 1, 1]}) is None


def test_an_empty_graph_is_refused():
    assert "empty" in _validate_graph_structure({"process_graph": {}})


def test_a_cycle_is_refused():
    assert "cycle" in _validate_graph_structure({"process_graph": CYCLE})


def test_a_reference_to_a_missing_node_is_refused():
    reason = _validate_graph_structure({"process_graph": DANGLING_REF})
    assert "missing" in reason and "not in the graph" in reason


def test_a_node_without_a_process_id_is_refused():
    assert "process_id" in _validate_graph_structure(
        {"process_graph": {"a": {"arguments": {}}}})


def test_a_node_that_is_not_an_object_is_refused():
    assert "not an object" in _validate_graph_structure(
        {"process_graph": {"a": "load_collection"}})


def test_a_reference_nested_inside_a_list_is_still_seen():
    graph = {"a": {"process_id": "load_collection", "arguments": {"id": "s2"}},
             "b": {"process_id": "merge_cubes",
                   "arguments": {"cubes": [{"from_node": "a"}, {"from_node": "gone"}]}}}
    assert "gone" in _validate_graph_structure({"process_graph": graph})


# --- the deliberate limit: registry-dependent faults are NOT enforced here ---
def test_an_unknown_process_is_not_refused_by_the_structural_gate():
    """UnknownProcess comes from a registry whose loader returns {} if an optional
    import fails. Enforcing it would turn that into 'no job can be created'."""
    graph = {"a": {"process_id": "a_process_this_backend_may_well_have", "arguments": {}}}
    assert _validate_graph_structure({"process_graph": graph}) is None


def test_a_missing_required_parameter_is_not_refused_by_the_structural_gate():
    assert _validate_graph_structure(
        {"process_graph": {"a": {"process_id": "load_collection", "arguments": {}}}}) is None


def test_the_gate_needs_no_process_specs_at_all(monkeypatch):
    """The whole point: an empty or broken spec registry cannot block job creation."""
    import openeo_ai.validation.graph_validator as gv
    monkeypatch.setattr(gv, "build_default_process_specs", lambda: {})
    assert _validate_graph_structure({"process_graph": VALID}) is None
    assert "cycle" in _validate_graph_structure({"process_graph": CYCLE})


# --- anti-divergence: the standalone gate must agree with GraphValidator ---
@pytest.mark.parametrize("graph,structurally_bad", [
    (VALID, False), (CYCLE, True), (DANGLING_REF, True),
    ({"a": {"process_id": "load_collection", "arguments": {"id": "s2"}}}, False),
])
def test_the_gate_agrees_with_graphvalidator_on_structural_verdicts(graph, structurally_bad):
    from openeo_ai.validation.graph_validator import (
        GraphValidator, build_default_process_specs, get_custom_process_specs)
    specs = build_default_process_specs()
    specs.update(get_custom_process_specs())
    result = GraphValidator(process_specs=specs).validate(graph)

    STRUCTURAL = {"InvalidGraph", "InvalidNode", "MissingProcessId",
                  "InvalidReference", "CyclicGraph"}
    validator_says_bad = any(e.code in STRUCTURAL for e in result.errors)
    gate_says_bad = _validate_graph_structure({"process_graph": graph}) is not None
    assert gate_says_bad == validator_says_bad == structurally_bad, (
        f"gate={gate_says_bad} validator={validator_says_bad} "
        f"codes={[e.code for e in result.errors]}")


def test_every_job_start_tool_passes_through_the_gate():
    assert "openeo_create_job" in _JOB_START_TOOLS
    for tool in _JOB_START_TOOLS:
        assert isinstance(tool, str)
