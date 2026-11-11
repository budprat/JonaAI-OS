# ABOUTME: Agentic Earth Observation — NL query → typed intent → plan → real Sentinel-2 processing →
# ABOUTME: decision-grade insight. Public surface: run_query, decompose, build_plan, execute, build_report.

from __future__ import annotations

from .decompose import decompose
from .executor import execute
from .models import (
    AnalysisType,
    AOISpec,
    BaselineSpec,
    DecisionReport,
    EOIntent,
    ExecutionResult,
    MethodPlan,
    SceneStat,
    Step,
    StepKind,
    StepResult,
    TimeRange,
    Variable,
)
from .pipeline import PipelineOutput, run_query
from .planner import build_plan
from .report import build_report

__all__ = [
    "run_query", "PipelineOutput", "decompose", "build_plan", "execute", "build_report",
    "EOIntent", "AOISpec", "TimeRange", "BaselineSpec", "Variable", "AnalysisType",
    "MethodPlan", "Step", "StepKind", "StepResult", "SceneStat", "ExecutionResult", "DecisionReport",
]
