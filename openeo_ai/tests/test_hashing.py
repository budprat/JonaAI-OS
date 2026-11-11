# ABOUTME: Locks hashing.hash_json byte-identical to the historical worker algorithm.
# ABOUTME: Independent inline oracle so the guarantee survives future worker edits.
import hashlib
import json
from datetime import datetime

import pytest

from openeo_ai.hashing import hash_json


def _oracle(value):
    # Verbatim reproduction of the historical worker._hash_json algorithm.
    if not value:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_CORPUS = [
    ("none", None),
    ("empty", {}),
    ("single", {"a": 1}),
    ("key-order", {"b": 2, "a": 1}),
    ("nested", {"a": {"nested": [3, 2, 1]}}),
    ("float-vs-int", {"x": 1.0}),
    ("datetime", {"t": datetime(2026, 7, 11, 12, 0, 0)}),
]


@pytest.mark.parametrize("case_id,value", _CORPUS, ids=[c[0] for c in _CORPUS])
def test_hash_json_matches_oracle(case_id, value):
    assert hash_json(value) == _oracle(value)


def test_hash_json_key_order_independent():
    assert hash_json({"a": 1, "b": 2}) == hash_json({"b": 2, "a": 1})


def test_hash_json_none_and_empty_are_none():
    assert hash_json(None) is None
    assert hash_json({}) is None


def test_worker_hash_json_is_the_shared_function():
    from openeo_ai.intelligence import worker
    assert worker._hash_json is hash_json


from openeo_ai.hashing import graph_hash, method_hash, plan_hash  # noqa: E402

_G = {"process_graph": {"load": {"process_id": "load_collection"}}}


def test_graph_hash_is_hash_json_of_graph():
    assert graph_hash(_G) == hash_json(_G)


def test_plan_hash_diverges_on_inputs():
    a = plan_hash(_G, {"aoi": "A"}, {"z": 3})
    b = plan_hash(_G, {"aoi": "B"}, {"z": 3})
    assert a != b and a is not None


def test_method_hash_ignores_inputs():
    # Same graph + thresholds => same method_hash regardless of AOI/window.
    assert method_hash(_G, {"z": 3}) == method_hash(dict(_G), {"z": 3})


def test_plan_and_method_hash_differ():
    assert plan_hash(_G, {"aoi": "A"}, {"z": 3}) != method_hash(_G, {"z": 3})


def test_derived_hashes_none_for_empty_graph():
    assert graph_hash(None) is None
    assert method_hash(None) is None
    assert plan_hash(None) is None
