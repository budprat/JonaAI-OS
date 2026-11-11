"""Cryosphere domain scientific invariants."""
from typing import List, Tuple, Optional, Dict


def validate_cryosphere(scf: dict, dataset_semantics: Optional[Dict] = None) -> Tuple[List[dict], List[dict], List[dict]]:
    errors: List[dict] = []
    warnings: List[dict] = []
    required: List[dict] = []

    policy = scf.get("policy_profile", {}) or {}
    data = scf.get("data", {}) or {}
    transformations = scf.get("transformations", []) or []
    bands = data.get("bands") or []

    # Snow/ice indices often require green and swir
    for t in transformations:
        t_type = t.get("type")
        if t_type == "spectral_index":
            name = t.get("name")
            if name in {"ndsi", "snow_index"}:
                required_bands = ["green", "swir16"]
                missing = [b for b in required_bands if b not in bands]
                if missing:
                    errors.append(
                        {
                            "code": "MissingBands",
                            "message": f"Cryosphere index {name} requires bands {missing}.",
                        }
                    )
        if t_type == "snow_cover_classification":
            required_bands = ["green", "swir16"]
            missing = [b for b in required_bands if b not in bands]
            if missing:
                errors.append(
                    {
                        "code": "MissingBands",
                        "message": f"Snow cover classification requires bands {missing}.",
                    }
                )
            if t.get("threshold") is None:
                required.append(
                    {
                        "code": "SnowThresholdMissing",
                        "message": "snow_cover_classification requires a threshold.",
                    }
                )

    if not policy.get("cloud_mask_algorithm"):
        required.append(
            {
                "code": "CloudMaskAlgorithmMissing",
                "message": "Cryosphere workflows require explicit cloud mask algorithm.",
            }
        )

    return errors, warnings, required
