"""SAR domain scientific invariants."""
from typing import List, Tuple, Optional, Dict


def validate_sar(scf: dict, dataset_semantics: Optional[Dict] = None) -> Tuple[List[dict], List[dict], List[dict]]:
    errors: List[dict] = []
    warnings: List[dict] = []
    required: List[dict] = []

    policy = scf.get("policy_profile", {}) or {}
    data = scf.get("data", {}) or {}
    transformations = scf.get("transformations", []) or []

    bands = data.get("bands") or []

    # Require explicit speckle filtering policy
    if not policy.get("speckle_filter"):
        required.append(
            {
                "code": "SpeckleFilterMissing",
                "message": "SAR workflows should define policy_profile.speckle_filter.",
            }
        )

    for t in transformations:
        if t.get("type") == "sar_backscatter":
            polarization = t.get("polarization")
            scale = t.get("scale")
            if not polarization:
                required.append(
                    {
                        "code": "SARPolarizationMissing",
                        "message": "sar_backscatter requires polarization field.",
                    }
                )
            else:
                if polarization not in bands:
                    warnings.append(
                        {
                            "code": "SARPolarizationNotInBands",
                            "message": f"Polarization '{polarization}' not listed in data.bands.",
                        }
                    )
            if scale not in ("linear", "db", None):
                errors.append(
                    {
                        "code": "SARScaleInvalid",
                        "message": "sar_backscatter scale must be 'linear' or 'db'.",
                    }
                )

    # If dataset semantics indicate SAR extensions, ensure domain matches
    if dataset_semantics:
        ext = dataset_semantics.get("extensions", {}) if isinstance(dataset_semantics, dict) else {}
        if ext and not ext.get("sar"):
            warnings.append(
                {
                    "code": "DatasetNotSAR",
                    "message": "Selected dataset does not advertise SAR extension.",
                }
            )

    return errors, warnings, required
