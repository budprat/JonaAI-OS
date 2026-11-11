"""Optical domain scientific invariants."""
from datetime import datetime
from typing import List, Tuple, Optional, Dict


def _parse_date(value: str):
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _extract_formula_identifiers(formula: str) -> List[str]:
    identifiers = []
    current = ""
    for ch in formula:
        if ch.isalnum() or ch == "_":
            current += ch
        else:
            if current:
                identifiers.append(current)
                current = ""
    if current:
        identifiers.append(current)
    return identifiers


def validate_optical(scf: dict, dataset_semantics: Optional[Dict] = None) -> Tuple[List[dict], List[dict], List[dict]]:
    errors: List[dict] = []
    warnings: List[dict] = []
    required: List[dict] = []

    policy = scf.get("policy_profile", {}) or {}
    data = scf.get("data", {}) or {}
    quality = scf.get("quality_constraints", {}) or {}
    transformations = scf.get("transformations", []) or []

    bands = data.get("bands") or []
    alias_map = (dataset_semantics or {}).get("alias_map", {})
    dataset_bands = (dataset_semantics or {}).get("bands", [])
    dataset_common = {
        (b.get("common_name") or b.get("name")) for b in dataset_bands if b.get("name") or b.get("common_name")
    }

    # Cloud masking requirements
    if quality.get("cloud_mask") is True:
        if not policy.get("cloud_mask_algorithm"):
            required.append(
                {
                    "code": "CloudMaskAlgorithmMissing",
                    "message": "cloud_mask is true but policy_profile.cloud_mask_algorithm is missing.",
                }
            )
        if not policy.get("cloud_mask_classes"):
            required.append(
                {
                    "code": "CloudMaskClassesMissing",
                    "message": "cloud_mask is true but policy_profile.cloud_mask_classes is missing.",
                }
            )
        if quality.get("cloud_threshold") is None:
            required.append(
                {
                    "code": "CloudThresholdMissing",
                    "message": "cloud_mask is true but quality_constraints.cloud_threshold is missing.",
                }
            )

    # Spectral index requirements
    for t in transformations:
        if t.get("type") != "spectral_index":
            continue
        name = t.get("name")
        formula = t.get("formula") or ""

        if not policy.get("reflectance_scaling"):
            required.append(
                {
                    "code": "ReflectanceScalingMissing",
                    "message": "spectral_index requires policy_profile.reflectance_scaling to be explicit.",
                }
            )

        # Band requirements for NDVI
        # Whole tokens: "red" is a substring of "rededge", so a red-edge formula was
        # treated as NDVI here and then reported MissingBands for a band it never
        # needed. _extract_formula_identifiers is already used below for exactly this.
        formula_tokens = set(_extract_formula_identifiers(formula)) if formula else set()
        if name == "ndvi" or {"nir", "red"} <= formula_tokens:
            missing = [b for b in ["red", "nir"] if b not in bands]
            if missing:
                errors.append(
                    {
                        "code": "MissingBands",
                        "message": f"spectral_index requires bands {missing} not present in data.bands.",
                    }
                )

        # Validate identifiers against dataset semantics when available
        if alias_map:
            identifiers = _extract_formula_identifiers(formula) if formula else []
            unknown = []
            for ident in identifiers:
                if ident.isnumeric():
                    continue
                if ident not in alias_map and ident not in dataset_common:
                    unknown.append(ident)
            if unknown:
                warnings.append(
                    {
                        "code": "UnknownFormulaBands",
                        "message": f"Formula references unknown bands: {sorted(set(unknown))}",
                    }
                )

    # Temporal/spatial reductions must follow masking
    has_temporal_reduction = any(t.get("type") == "temporal_reduction" for t in transformations)
    has_spatial_reduction = any(t.get("type") == "spatial_reduction" for t in transformations)
    if (has_temporal_reduction or has_spatial_reduction) and not quality.get("cloud_mask"):
        errors.append(
            {
                "code": "CloudMaskRequired",
                "message": "Masking is required before reductions in optical workflows.",
            }
        )

    # Long temporal extents require explicit temporal policy
    temporal = data.get("temporal_extent") or {}
    start = _parse_date(temporal.get("start", ""))
    end = _parse_date(temporal.get("end", ""))
    has_temporal_ops = has_temporal_reduction or any(
        t.get("type") in {"temporal_resample", "climate_anomaly", "degree_days"} for t in transformations
    )

    if has_temporal_ops and not policy.get("temporal_step"):
        errors.append(
            {
                "code": "TemporalStepMissing",
                "message": "Temporal operations require policy_profile.temporal_step.",
            }
        )

    _ = (start, end)  # reserved for future temporal range checks

    # Harmonization required for multiple collections
    if isinstance(data.get("collection"), list) and len(data.get("collection")) > 1:
        if not policy.get("harmonization_policy") or policy.get("harmonization_policy") == "none":
            errors.append(
                {
                    "code": "HarmonizationMissing",
                    "message": "Multiple collections require policy_profile.harmonization_policy.",
                }
            )

    # Verify dataset has required bands for data.bands using alias map
    if alias_map:
        missing_bands = [b for b in bands if b not in alias_map and b not in dataset_common]
        if missing_bands:
            errors.append(
                {
                    "code": "DatasetMissingBands",
                    "message": f"Dataset does not advertise bands: {missing_bands}",
                }
            )

    return errors, warnings, required
