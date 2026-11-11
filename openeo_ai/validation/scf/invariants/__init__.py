"""Scientific invariants registry."""
from typing import Callable, Dict, Optional

from openeo_ai.validation.scf.invariants.optical import validate_optical
from openeo_ai.validation.scf.invariants.sar import validate_sar
from openeo_ai.validation.scf.invariants.climate import validate_climate
from openeo_ai.validation.scf.invariants.cryosphere import validate_cryosphere

INVARIANTS_REGISTRY: Dict[str, Callable] = {
    "optical": validate_optical,
    "sar": validate_sar,
    "climate": validate_climate,
    "cryosphere": validate_cryosphere,
}


def validate_invariants(scf: dict, dataset_semantics: Optional[dict] = None):
    domain = scf.get("domain_profile")
    validator = INVARIANTS_REGISTRY.get(domain)
    if not validator:
        return [], [
            {
                "code": "UnknownDomain",
                "message": f"No invariants registered for domain '{domain}'.",
            }
        ], []
    return validator(scf, dataset_semantics=dataset_semantics)
