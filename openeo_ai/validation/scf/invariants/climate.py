"""Climate domain scientific invariants."""
from typing import List, Tuple, Optional, Dict


def validate_climate(scf: dict, dataset_semantics: Optional[Dict] = None) -> Tuple[List[dict], List[dict], List[dict]]:
    errors: List[dict] = []
    warnings: List[dict] = []
    required: List[dict] = []

    policy = scf.get("policy_profile", {}) or {}
    data = scf.get("data", {}) or {}
    transformations = scf.get("transformations", []) or []

    if any(t.get("type") in {"temporal_reduction", "temporal_resample", "degree_days"} for t in transformations):
        if not policy.get("temporal_step"):
            errors.append(
                {
                    "code": "TemporalStepMissing",
                    "message": "Climate temporal operations require policy_profile.temporal_step.",
                }
            )

    if isinstance(data.get("collection"), list) and len(data.get("collection")) > 1:
        if not policy.get("harmonization_policy") or policy.get("harmonization_policy") == "none":
            errors.append(
                {
                    "code": "HarmonizationMissing",
                    "message": "Multiple climate datasets require harmonization policy.",
                }
            )

    if not policy.get("missing_data_policy"):
        errors.append(
            {
                "code": "MissingDataPolicy",
                "message": "Climate analyses require explicit missing_data_policy.",
            }
        )

    return errors, warnings, required
