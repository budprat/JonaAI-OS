# ABOUTME: Typed scoring of process-graph artifacts captured during eval runs.
# ABOUTME: Pulls collection id / temporal / bbox from the load_collection node.
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _load_collection_args(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for event in events:
        if event.get("type") != "process_graph":
            continue
        data = event.get("data") or {}
        for node in data.get("nodes") or []:
            if node.get("process_id") == "load_collection":
                args = node.get("arguments")
                return args if isinstance(args, dict) else {}
    return None


def _bbox_from_extent(extent: Any) -> Optional[List[float]]:
    if not isinstance(extent, dict):
        return None
    try:
        return [
            float(extent["west"]),
            float(extent["south"]),
            float(extent["east"]),
            float(extent["north"]),
        ]
    except (KeyError, TypeError, ValueError):
        return None


def score_process_graph(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return typed artifact facts from a captured process graph, or all-None."""
    args = _load_collection_args(events)
    if args is None:
        return {"collection": None, "temporal": None, "bbox": None}
    collection = args.get("id")
    temporal = args.get("temporal_extent")
    return {
        "collection": collection if isinstance(collection, str) else None,
        "temporal": temporal if isinstance(temporal, list) else None,
        "bbox": _bbox_from_extent(args.get("spatial_extent")),
    }
