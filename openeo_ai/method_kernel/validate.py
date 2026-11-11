# ABOUTME: Fail-closed validation for a MethodPlan before execution.
# ABOUTME: schema (Pydantic) → step-DAG validity → method_fit (right method for the question?).
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

from openeo_ai.agentic_eo.models import AnalysisType, MethodPlan, Variable


@dataclass(frozen=True)
class ValidationStage:
    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Finding:
    """One method_fit verdict. fail = structurally ill-posed (never execute);
    warn = runnable but caveated. detail is a human sentence, reused verbatim
    as the clarification / report caveat text."""
    severity: str  # "fail" | "warn"
    code: str
    detail: str


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    stages: List[ValidationStage] = field(default_factory=list)
    # Method_fit WARN details — runnable, but the report must caveat them.
    warnings: List[str] = field(default_factory=list)

    def failures(self) -> List[str]:
        return [f"{s.name}: {s.detail}" for s in self.stages if not s.ok]


def _validate_dag(plan: MethodPlan) -> ValidationStage:
    """Every step input references a known step id; no self-reference; non-empty."""
    if not plan.steps:
        return ValidationStage("dag", False, "plan has no steps")
    ids = {s.id for s in plan.steps}
    for s in plan.steps:
        if s.id in s.inputs:
            return ValidationStage("dag", False, f"step '{s.id}' references itself")
        for dep in s.inputs:
            if dep not in ids:
                return ValidationStage("dag", False, f"step '{s.id}' references unknown step '{dep}'")
    return ValidationStage("dag", True, f"{len(plan.steps)} steps, inputs resolve")


# Keyword heuristics for the query-vs-intent mismatch checks. The
# change-verb check FAILs (promoted from WARN by NU, 2026-07-23: a change
# question answered by a snapshot is a wrong answer, not a caveat); the
# water-term check stays a WARN.
_CHANGE_VERBS = (
    r"\bchange[ds]?\b", r"\bcompared?\b", r"\bbefore and after\b",
    r"\bdecline over\b", r"\bsince\b", r"\bvs\.?\b",
)
_WATER_TERMS = ("water", "drought", "reservoir", "irrigation", "moisture", "flood")


def method_fit(plan: MethodPlan) -> List[Finding]:
    """The methodologist's question, deterministically: is this the right method
    for the question? Reads plan.intent only — never mutates steps (plan_hash safe).
    """
    intent = plan.intent
    q = intent.query.lower()
    findings: List[Finding] = []

    # FAIL — structurally ill-posed: the method is undefined, do not execute.
    if intent.analysis_type in (AnalysisType.CHANGE, AnalysisType.ANOMALY) and (
        intent.baseline is None or intent.baseline.time is None
    ):
        findings.append(Finding("fail", "change_needs_baseline",
            f"A {intent.analysis_type.value} analysis needs a baseline window and none "
            f"is set — state the comparison period (e.g. 'compared to the same month "
            f"last year')."))
    if intent.time.end <= intent.time.start:
        findings.append(Finding("fail", "empty_window",
            f"The analysis window is empty ({intent.time.start} → {intent.time.end}) — "
            f"the end date must be after the start."))
    if (intent.analysis_type == AnalysisType.SNAPSHOT
            and any(re.search(p, q) for p in _CHANGE_VERBS)):
        findings.append(Finding("fail", "change_verb_snapshot",
            "The question asks about change but the plan is a single-window "
            "snapshot — a snapshot cannot answer a change question; add a "
            "baseline period."))

    # WARN — runnable but caveated.
    if (intent.analysis_type == AnalysisType.TREND
            and (intent.time.end - intent.time.start).days < 365):
        findings.append(Finding("warn", "subannual_trend",
            f"The trend window is only {(intent.time.end - intent.time.start).days} "
            f"days — a sub-annual slope reflects phenology, not a multi-year rate."))
    if intent.analysis_type == AnalysisType.SNAPSHOT and intent.baseline is not None:
        findings.append(Finding("warn", "snapshot_with_baseline",
            "A baseline period is set but a snapshot never uses it — probable "
            "misparse; ask a change question or drop the baseline."))

    # WARN — query-vs-intent mismatch (the high-value methodologist check).
    if intent.variable == Variable.NDVI and any(t in q for t in _WATER_TERMS):
        findings.append(Finding("warn", "index_question_mismatch",
            "The question mentions water/moisture but the method computes NDVI "
            "(vegetation vigour) — NDWI (open water) or NDMI (moisture) likely "
            "fits the question better."))

    return findings


def validate_plan(plan: MethodPlan) -> ValidationReport:
    """Validate a MethodPlan fail-closed. Any stage failing → ok=False.

    Stages: schema (a constructed MethodPlan already passed Pydantic) → step-DAG
    validity (inputs resolve, no self-reference, non-empty) → method_fit (is this the right method for the question? FAIL findings close the gate;
    WARN findings ride in `warnings` for the report's caveats). No repair loops
    here. A STAC feasibility probe would be a separate pre-flight
    data-adequacy check.
    """
    findings = method_fit(plan)
    fails = [f.detail for f in findings if f.severity == "fail"]
    warns = [f.detail for f in findings if f.severity == "warn"]
    stages: List[ValidationStage] = [
        ValidationStage("schema", True, "typed MethodPlan"),
        _validate_dag(plan),
        ValidationStage("method_fit", not fails,
                        "; ".join(fails) if fails
                        else f"{len(warns)} warning(s)" if warns
                        else "method fits the question"),
    ]
    return ValidationReport(ok=all(s.ok for s in stages), stages=stages, warnings=warns)
