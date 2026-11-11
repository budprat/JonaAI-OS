"""Hypothesis and comparison tool."""
from __future__ import annotations

from typing import Any, Dict, List

from openeo_ai.validation.scf.hash import canonical_json, hash_json


def _diff_dict(a: Any, b: Any, path: str = "") -> List[Dict[str, Any]]:
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        keys = set(a.keys()) | set(b.keys())
        for key in sorted(keys):
            sub_path = f"{path}/{key}" if path else f"/{key}"
            if key not in a:
                diffs.append({"path": sub_path, "change": "added", "value": b.get(key)})
            elif key not in b:
                diffs.append({"path": sub_path, "change": "removed", "value": a.get(key)})
            else:
                diffs.extend(_diff_dict(a.get(key), b.get(key), sub_path))
    elif isinstance(a, list) and isinstance(b, list):
        if a != b:
            diffs.append({"path": path, "change": "list_changed", "value": {"from": a, "to": b}})
    else:
        if a != b:
            diffs.append({"path": path, "change": "value_changed", "value": {"from": a, "to": b}})
    return diffs


def compare(a: dict, b: dict) -> Dict[str, Any]:
    return {
        "hash_a": hash_json(a) if a else None,
        "hash_b": hash_json(b) if b else None,
        "equivalent": canonical_json(a) == canonical_json(b),
        "diffs": _diff_dict(a, b),
    }
