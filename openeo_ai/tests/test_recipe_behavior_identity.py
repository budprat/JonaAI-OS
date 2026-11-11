# ABOUTME: Proves the recipe registry is a provenance-and-DRY layer, not a behavior change —
# ABOUTME: same intent yields the identical executable plan + identical action gates as the literals it replaced.

from __future__ import annotations

from datetime import date

import pytest

from openeo_ai.agentic_eo.models import (
    AOISpec,
    AnalysisType,
    BaselineSpec,
    EOIntent,
    StepKind,
    TimeRange,
    Variable,
)
from openeo_ai.agentic_eo.planner import build_plan
from openeo_ai.agentic_eo.recipes import REGISTRY
from openeo_ai.agentic_eo.report import _actions


def _intent(analysis: AnalysisType, variable: Variable = Variable.NDVI,
            baseline: bool = False) -> EOIntent:
    base = None
    if baseline:
        base = BaselineSpec(
            mode="previous_period",
            time=TimeRange(start=date(2023, 1, 1), end=date(2023, 6, 30)),
        )
    return EOIntent(
        query="synthetic",
        aoi=AOISpec(raw_text="Fresno County", bbox=[-120.5, 36.5, -119.5, 37.0],
                    resolved_name="Fresno County"),
        time=TimeRange(start=date(2024, 1, 1), end=date(2024, 6, 30)),
        variable=variable,
        analysis_type=analysis,
        baseline=base,
    )


_CELLS = [(v, at) for v in Variable for at in AnalysisType]


class TestPlanIsUnchanged:
    def test_canonical_plan_hash_is_frozen(self):
        # The frozen plan-hash anchor — if the registry shifted a hashed step param this breaks.
        canonical = EOIntent(
            query="ndvi over fresno",
            aoi=AOISpec(raw_text="Fresno County", bbox=[-120.5, 36.5, -119.5, 37.0],
                        resolved_name="Fresno County"),
            time=TimeRange(start=date(2024, 1, 1), end=date(2024, 6, 30)),
            variable=Variable.NDVI,
            analysis_type=AnalysisType.SNAPSHOT,
        )
        assert build_plan(canonical).plan_hash == "f479b7f36d3982d5"

    @pytest.mark.parametrize("variable,analysis", _CELLS)
    def test_mask_gate_still_serializes_half(self, variable, analysis):
        plan = build_plan(_intent(analysis, variable=variable, baseline=True))
        mask = next(s for s in plan.steps if s.kind is StepKind.MASK)
        assert mask.params["min_valid_fraction"] == 0.5

    @pytest.mark.parametrize("variable,analysis", _CELLS)
    def test_recipe_ref_is_stamped_without_touching_the_hash(self, variable, analysis):
        plan = build_plan(_intent(analysis, variable=variable, baseline=True))
        assert plan.recipe_ref == f"{variable.value}_{analysis.value}"
        assert plan.improvised is False
        # recipe_ref lives outside the steps-only hash payload by construction.
        twin = build_plan(_intent(analysis, variable=variable, baseline=True))
        assert plan.plan_hash == twin.plan_hash


class TestActionGatesFireAtHistoricalBoundaries:
    """Each gate must switch exactly at the number the literal used — proves no value drift."""

    def _delta_actions(self, delta, variable=Variable.NDVI, at=AnalysisType.ANOMALY):
        # ANOMALY with previous_year baseline avoids the phenology guard, exercising the raw ladder.
        intent = _intent(at, variable=variable, baseline=True)
        if variable == Variable.NDVI:
            intent.baseline.mode = "previous_year"
        s = {"delta_median": delta, "baseline_median": 0.6, "target_median": 0.6 + delta,
             "welch_p": 0.01, "significant": True}
        return _actions(intent, s, "Fresno County")

    def test_ndvi_stress_dispatch_boundary(self):
        assert any("Dispatch a field scout" in a for a in self._delta_actions(-0.20))
        assert not any("Dispatch a field scout" in a for a in self._delta_actions(-0.19))

    def test_ndvi_scout_flag_boundary(self):
        assert any("Flag" in a and "scouting" in a for a in self._delta_actions(-0.10))
        assert not any("Flag" in a and "scouting" in a for a in self._delta_actions(-0.09))

    def test_ndvi_healthy_greenup_boundary(self):
        assert any("greened up" in a for a in self._delta_actions(0.05))
        assert not any("greened up" in a for a in self._delta_actions(0.04))

    def test_ndmi_moisture_deficit_boundary(self):
        assert any("Moisture deficit" in a for a in self._delta_actions(-0.06, variable=Variable.NDMI))
        assert not any("Moisture deficit" in a for a in self._delta_actions(-0.05, variable=Variable.NDMI))

    def test_ndvi_degradation_slope_boundary(self):
        intent = _intent(AnalysisType.TREND)
        base = {"multiyear": True, "window_years": 3.0, "significant": True}
        fires = _actions(intent, {**base, "sen_slope_per_year": -0.005}, "Fresno County")
        holds = _actions(intent, {**base, "sen_slope_per_year": -0.004}, "Fresno County")
        assert any("land-degradation review" in a for a in fires)
        assert not any("land-degradation review" in a for a in holds)

    def test_ndwi_drought_drawdown_boundary(self):
        intent = _intent(AnalysisType.TREND, variable=Variable.NDWI)
        # slope in ha/yr; /52.18 -> ha/week. -25 ha/wk == -1304.5 ha/yr.
        past = _actions(intent, {"sen_slope_per_year": -25.0 * 52.18 - 1, "significant": True}, "Lake Oroville")
        before = _actions(intent, {"sen_slope_per_year": -25.0 * 52.18 + 1, "significant": True}, "Lake Oroville")
        assert any("Active drawdown" in a for a in past)
        assert not any("Active drawdown" in a for a in before)

    def test_nbr_burn_ceiling_boundary(self):
        intent = _intent(AnalysisType.SNAPSHOT, variable=Variable.NBR)
        assert any("recent burn" in a for a in _actions(intent, {"target_median": 0.09}, "Paradise"))
        assert not any("recent burn" in a for a in _actions(intent, {"target_median": 0.10}, "Paradise"))


class TestDroughtProseIsByteIdentical:
    def test_prose_keeps_the_unicode_minus_glyph(self):
        intent = _intent(AnalysisType.TREND, variable=Variable.NDWI)
        actions = _actions(intent, {"sen_slope_per_year": -3000.0, "significant": True}, "Lake Oroville")
        drawdown = next(a for a in actions if "Active drawdown" in a)
        assert "−25 ha/week drought trigger" in drawdown  # U+2212, not ASCII hyphen
        assert REGISTRY.threshold(Variable.NDWI, "drought_drawdown_ha_per_week") == -25.0
