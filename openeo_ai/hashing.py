# ABOUTME: Canonical JSON hashing for Earth Intelligence coordination keys.
# ABOUTME: One module owns canonicalization so cache + trajectory keys never diverge.
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Optional


def hash_json(value: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Return the SHA-256 of a canonical JSON encoding, or None for empty input.

    Byte-for-byte identical to the historical worker._hash_json so existing
    hashes stay stable across the extraction.
    """
    if not value:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def graph_hash(graph: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Hash a process graph alone (structure only)."""
    return hash_json(graph)


def method_hash(
    graph: Optional[Mapping[str, Any]],
    thresholds: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Analytics/trajectory key: graph + thresholds, resolved inputs excluded.

    Two runs of the same method over different AOIs share a method_hash.
    """
    if not graph:
        return None
    return hash_json({"graph": graph, "thresholds": thresholds or {}})


def plan_hash(
    graph: Optional[Mapping[str, Any]],
    resolved_inputs: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Cache key: graph + resolved inputs + thresholds.

    Byte-identical executions share a plan_hash; differing inputs diverge it.
    """
    if not graph:
        return None
    return hash_json(
        {"graph": graph, "inputs": resolved_inputs or {}, "thresholds": thresholds or {}}
    )
