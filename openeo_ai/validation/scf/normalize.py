"""Normalize SCF for semantic hashing and deterministic compilation."""
from __future__ import annotations

from typing import Any, Dict


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned[key] = _clean(item)
        return cleaned
    if isinstance(value, list):
        return [_clean(item) for item in value if item is not None]
    return value


def normalize_scf(scf: dict) -> dict:
    """Return a normalized SCF without nulls and with stable casing for profiles."""
    if not isinstance(scf, dict):
        return scf
    normalized = _clean(scf)
    domain = normalized.get("domain_profile")
    if isinstance(domain, str):
        normalized["domain_profile"] = domain.lower()
    return normalized
