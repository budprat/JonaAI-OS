"""SCF (Scientific Computation Framework) for OpenEO process graphs.

Ported from upstream openeo-fastapi-STAC/openeo_fastapi/scf/.
Provides:
- Scientific invariant validation (optical, SAR, cryosphere, climate)
- Cost estimation
- Band/index semantics
- SCF-to-process-graph lowering (compiler)
"""

from .validator import validate_scf, validate_structure, validate_policy_profile
from .lowering import lower_scf_to_pg, NodeBuilder, LoweringError
from .models import SCFValidationResponse, SCFLoweringResponse, SCFCompileResponse
from .hash import hash_json

__all__ = [
    "validate_scf",
    "validate_structure",
    "validate_policy_profile",
    "lower_scf_to_pg",
    "NodeBuilder",
    "LoweringError",
    "SCFValidationResponse",
    "SCFLoweringResponse",
    "SCFCompileResponse",
    "hash_json",
]
