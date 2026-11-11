"""Typed plan derivation and validation for SCF."""
from __future__ import annotations

from typing import Dict, List, Set

from openeo_fastapi.api.types import Error


DIMENSION_MAP = {
    "time": "t",
    "temporal": "t",
    "t": "t",
    "bands": "bands",
    "x": "x",
    "y": "y",
}


def _normalize_dim(name: str | None) -> str | None:
    if not name:
        return None
    return DIMENSION_MAP.get(name, name)


def _initial_dims(scf: dict) -> Set[str]:
    dims = {"x", "y"}
    data = scf.get("data") or {}
    if data.get("temporal_extent"):
        dims.add("t")
    if data.get("bands"):
        dims.add("bands")
    return dims


def build_typed_plan(scf: dict) -> Dict[str, object]:
    """Build a minimal typed plan from SCF."""
    data = scf.get("data") or {}
    output = scf.get("output") or {}
    dims = sorted(list(_initial_dims(scf)))
    operations: List[Dict[str, object]] = []
    for t in scf.get("transformations") or []:
        operations.append(
            {
                "op": t.get("type"),
                "args": {k: v for k, v in (t or {}).items() if k not in {"id", "type"}},
                "output_dims": None,
            }
        )
    return {
        "collection": data.get("collection"),
        "initial_dims": dims,
        "operations": operations,
        "output_dims": output.get("dimensions") or [],
    }


def validate_typed_plan(scf: dict) -> List[Error]:
    """Validate dimension legality based on a typed plan view."""
    errors: List[Error] = []
    if not isinstance(scf, dict):
        return errors

    dims = set(_initial_dims(scf))
    for idx, t in enumerate(scf.get("transformations") or []):
        t_type = t.get("type")
        if not t_type:
            continue

        if t_type == "temporal_reduction":
            dim = _normalize_dim(t.get("dimension") or "t")
            if dim not in dims:
                errors.append(
                    Error(
                        code="InvalidDimension",
                        message=f"transformations[{idx}] cannot reduce missing dimension '{dim}'.",
                    )
                )
            else:
                dims.discard(dim)

        elif t_type == "temporal_resample":
            dim = _normalize_dim(t.get("dimension") or "t")
            if dim not in dims:
                errors.append(
                    Error(
                        code="InvalidDimension",
                        message=f"transformations[{idx}] cannot resample missing dimension '{dim}'.",
                    )
                )

        elif t_type == "degree_days":
            dim = _normalize_dim(t.get("dimension") or "t")
            if dim not in dims:
                errors.append(
                    Error(
                        code="InvalidDimension",
                        message=f"transformations[{idx}] cannot reduce missing dimension '{dim}'.",
                    )
                )
            else:
                dims.discard(dim)

        elif t_type == "spatial_reduction":
            missing = [d for d in ("x", "y") if d not in dims]
            if missing:
                errors.append(
                    Error(
                        code="InvalidDimension",
                        message=f"transformations[{idx}] cannot reduce missing spatial dimensions {missing}.",
                    )
                )
            dims.discard("x")
            dims.discard("y")

    output_dims = set((scf.get("output") or {}).get("dimensions") or [])
    if output_dims and not output_dims.issubset(dims):
        errors.append(
            Error(
                code="OutputDimensionMismatch",
                message=f"output.dimensions {sorted(list(output_dims))} not compatible with computed dims {sorted(list(dims))}.",
            )
        )

    return errors
