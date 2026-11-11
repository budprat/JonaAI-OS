# ABOUTME: Ordered severity pipeline for Earth Intelligence signals (stages 1-2).
# ABOUTME: Each stage records (modifier, reason) so signal severity is explainable.
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Mapping, Optional


@dataclass(frozen=True)
class StageRecord:
    stage: str
    modifier: str  # resulting severity for this stage, or "none" when no change
    reason: str


@dataclass(frozen=True)
class SeverityDecision:
    severity: str
    provenance: List[StageRecord] = field(default_factory=list)

    def as_event_payload(self) -> List[dict]:
        return [
            {"stage": r.stage, "modifier": r.modifier, "reason": r.reason}
            for r in self.provenance
        ]


def _stage_base(result_info: Mapping[str, Any], stats: Mapping[str, Any]) -> StageRecord:
    """Stage 1 — identical logic to the historical _severity_from_result."""
    validation = result_info.get("validation")
    warnings: List[Any] = []
    if isinstance(validation, dict):
        warnings = validation.get("warnings") or []
    nan_percent = stats.get("nan_percent")
    if isinstance(nan_percent, (int, float)) and nan_percent >= 50:
        return StageRecord("base", "warning", f"nan_percent {nan_percent} >= 50")
    if warnings:
        return StageRecord("base", "warning", f"{len(warnings)} validation warning(s)")
    return StageRecord("base", "info", "no base warnings")


def _stage_seasonal_z(
    base_severity: str,
    value: float,
    baseline_info: Optional[Mapping[str, Any]],
) -> Optional[StageRecord]:
    """Stage 2 — identical logic to the worker z-block; None when not applicable."""
    if not baseline_info:
        return None
    baseline_value = baseline_info.get("previous_mean")
    baseline_std = baseline_info.get("previous_std")
    previous_count = int(baseline_info.get("previous_count") or 0)
    if not isinstance(baseline_value, (int, float)):
        return None
    if previous_count < 3 or not isinstance(baseline_std, (int, float)) or baseline_std <= 0:
        return None
    z_score = abs((value - float(baseline_value)) / baseline_std)
    if z_score >= 3:
        return StageRecord("seasonal-z", "critical", f"z={z_score:.2f} >= 3")
    if z_score >= 2 and base_severity == "info":
        return StageRecord("seasonal-z", "warning", f"z={z_score:.2f} >= 2 and base=info")
    return StageRecord("seasonal-z", "none", f"z={z_score:.2f} below escalation")


def evaluate_severity(
    result_info: Mapping[str, Any],
    stats: Mapping[str, Any],
    value: float,
    baseline_info: Optional[Mapping[str, Any]],
) -> SeverityDecision:
    """Compose stages 1-2 into a severity plus provenance.

    Behavior-identical to the historical inline worker logic: base severity
    from result/stats, then seasonal-z escalation from baseline stats. The
    z-stage only ever escalates, never de-escalates.
    """
    provenance: List[StageRecord] = []
    base = _stage_base(result_info, stats)
    provenance.append(base)
    severity = base.modifier

    z = _stage_seasonal_z(severity, value, baseline_info)
    if z is not None:
        provenance.append(z)
        if z.modifier == "critical":
            severity = "critical"
        elif z.modifier == "warning":
            severity = "warning"
    return SeverityDecision(severity=severity, provenance=provenance)
