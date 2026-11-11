"""Validation package for OpenEO process graphs.

Provides deterministic validation (structure, dependencies, type inference)
and optional LLM-assisted diagnostics.

Ported from upstream openeo-fastapi-STAC/openeo_fastapi/client/graph_validation.py
"""

from .graph_validator import (
    GraphValidator,
    GraphValidationResult,
    ValueType,
    LLMIssue,
    LLMValidationResult,
    build_default_process_specs,
    get_custom_process_specs,
)
from .llm_validator import (
    ClaudeProcessGraphValidator,
    LLMValidationConfig,
)
from .dataset_intelligence import DatasetIntelligenceService

# SCF (Scientific Computation Framework)
from .scf import validate_scf, lower_scf_to_pg, hash_json

__all__ = [
    "GraphValidator",
    "GraphValidationResult",
    "ValueType",
    "LLMIssue",
    "LLMValidationResult",
    "build_default_process_specs",
    "get_custom_process_specs",
    "ClaudeProcessGraphValidator",
    "LLMValidationConfig",
    "DatasetIntelligenceService",
    "validate_scf",
    "lower_scf_to_pg",
    "hash_json",
]
