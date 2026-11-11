# ABOUTME: Method Kernel façade — the agentic_eo package IS the kernel.
# ABOUTME: Re-exports the typed IR + entrypoint so callers import openeo_ai.method_kernel.
#
# Deliberately NOT exported: a bare `plan_hash`. Three hash surfaces exist
# (MethodPlan.plan_hash 16-char steps-only @property; hashing.plan_hash 64-char
# graph+inputs; ExecutionResult.plan_hash str). The kernel hash is the property
# on MethodPlan — importing a bare name would silently bind the wrong one.

from openeo_ai.agentic_eo.models import (
    AnalysisType,
    AOISpec,
    DecisionReport,
    EOIntent,
    ExecutionResult,
    MethodPlan,
    Variable,
)
from openeo_ai.agentic_eo.pipeline import run_query

__all__ = [
    "MethodPlan",
    "EOIntent",
    "AOISpec",
    "ExecutionResult",
    "DecisionReport",
    "AnalysisType",
    "Variable",
    "run_query",
]
