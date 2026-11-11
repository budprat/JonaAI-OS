"""SCF structural validation and scientific invariants aggregation."""
from typing import List, Tuple, Optional

from openeo_fastapi.api.types import Error
from openeo_fastapi.api.models import ValidationIssue
from openeo_ai.validation.scf.invariants import validate_invariants
from openeo_ai.validation.scf.typed_plan import validate_typed_plan

REQUIRED_POLICY_KEYS = {
    "policy_version",
    "resampling_method",
    "harmonization_policy",
    "reflectance_scaling",
    "missing_data_policy",
    "default_output_format",
}


def _require(obj: dict, key: str, expected_type, errors: List[Error], label: str):
    if key not in obj:
        errors.append(Error(code="MissingField", message=f"{label} missing required field '{key}'."))
        return None
    value = obj.get(key)
    if not isinstance(value, expected_type):
        errors.append(Error(code="InvalidField", message=f"{label} field '{key}' has invalid type."))
        return None
    return value


def validate_structure(scf: dict) -> List[Error]:
    errors: List[Error] = []

    if not isinstance(scf, dict):
        return [Error(code="InvalidSCF", message="SCF must be an object.")]

    _require(scf, "scf_version", str, errors, "scf")
    _require(scf, "domain_profile", str, errors, "scf")
    policy = _require(scf, "policy_profile", dict, errors, "scf") or {}
    data = _require(scf, "data", dict, errors, "scf") or {}
    transformations = _require(scf, "transformations", list, errors, "scf") or []
    output = _require(scf, "output", dict, errors, "scf") or {}

    if data:
        _require(data, "collection", (str, list), errors, "data")
        bands = _require(data, "bands", list, errors, "data") or []
        if bands and not all(isinstance(b, str) for b in bands):
            errors.append(Error(code="InvalidField", message="data.bands must be list of strings."))
        spatial = _require(data, "spatial_extent", dict, errors, "data") or {}
        if spatial:
            for key in ["west", "south", "east", "north"]:
                if key not in spatial:
                    errors.append(Error(code="MissingField", message=f"data.spatial_extent missing '{key}'."))
        temporal = _require(data, "temporal_extent", dict, errors, "data") or {}
        if temporal:
            for key in ["start", "end"]:
                if key not in temporal:
                    errors.append(Error(code="MissingField", message=f"data.temporal_extent missing '{key}'."))

    if transformations:
        for idx, t in enumerate(transformations):
            if not isinstance(t, dict):
                errors.append(Error(code="InvalidField", message=f"transformations[{idx}] must be object."))
                continue
            if "id" not in t or "type" not in t:
                errors.append(Error(code="MissingField", message=f"transformations[{idx}] missing id/type."))

    if output:
        _require(output, "type", str, errors, "output")
        dims = _require(output, "dimensions", list, errors, "output") or []
        if dims and not all(isinstance(d, str) for d in dims):
            errors.append(Error(code="InvalidField", message="output.dimensions must be list of strings."))

    return errors


def validate_policy_profile(scf: dict) -> List[ValidationIssue]:
    required: List[ValidationIssue] = []
    policy = scf.get("policy_profile", {}) or {}

    for key in sorted(REQUIRED_POLICY_KEYS):
        if key not in policy or policy.get(key) in (None, ""):
            required.append(
                ValidationIssue(
                    code="PolicyMissing",
                    message=f"policy_profile.{key} is required.",
                    severity="required",
                )
            )

    # output.format must be explicit, or policy default must exist
    output = scf.get("output", {}) or {}
    if not output.get("format") and not policy.get("default_output_format"):
        required.append(
            ValidationIssue(
                code="OutputFormatMissing",
                message="output.format is missing and policy_profile.default_output_format is not set.",
                severity="required",
            )
        )

    return required


def validate_scf(
    scf: dict,
    dataset_semantics: Optional[dict] = None,
) -> Tuple[List[Error], List[ValidationIssue], List[ValidationIssue]]:
    errors = validate_structure(scf)
    warnings: List[ValidationIssue] = []
    required = validate_policy_profile(scf)

    plan_errors = validate_typed_plan(scf)
    errors.extend(plan_errors)

    inv_errors, inv_warnings, inv_required = validate_invariants(scf, dataset_semantics=dataset_semantics)
    errors.extend([Error(code=e["code"], message=e["message"]) for e in inv_errors])
    warnings.extend(
        [
            ValidationIssue(code=w["code"], message=w["message"], severity="warning")
            for w in inv_warnings
        ]
    )
    required.extend(
        [
            ValidationIssue(code=r["code"], message=r["message"], severity="required")
            for r in inv_required
        ]
    )

    return errors, warnings, required
