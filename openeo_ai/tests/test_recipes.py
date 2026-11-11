# ABOUTME: Unit tests for the decision-threshold recipe registry — proves policy numbers load
# ABOUTME: as data with their historical values and that the lookup contract is exact and fail-closed.

from __future__ import annotations

import pytest

from openeo_ai.agentic_eo.models import AnalysisType, Variable
from openeo_ai.agentic_eo.recipes import REGISTRY, Registry, load_registry


class TestRegistryLoad:
    def test_all_five_variables_have_a_recipe(self):
        for v in Variable:
            assert REGISTRY.get(v, AnalysisType.SNAPSHOT) is not None

    def test_registry_is_a_singleton_across_loads(self):
        assert load_registry() is load_registry()

    def test_scene_gate_min_valid_fraction_is_half(self):
        # The one knob build_plan bakes into the MASK step; every seed resolves to 0.5.
        assert REGISTRY.min_valid_fraction == 0.5


class TestThresholdValues:
    @pytest.mark.parametrize("variable,name,expected", [
        (Variable.NDVI, "stress_dispatch_delta", -0.20),
        (Variable.NDVI, "scout_flag_delta", -0.10),
        (Variable.NDVI, "healthy_greenup_delta", 0.05),
        (Variable.NDVI, "degrade_slope_per_year", -0.005),
        (Variable.NDMI, "moisture_deficit_delta", -0.05),
        (Variable.NDWI, "drought_drawdown_ha_per_week", -25.0),
        (Variable.NBR, "burn_ceiling", 0.1),
    ])
    def test_threshold_returns_historical_value(self, variable, name, expected):
        assert REGISTRY.threshold(variable, name) == expected

    def test_unknown_threshold_name_raises(self):
        with pytest.raises(KeyError):
            REGISTRY.threshold(Variable.NDVI, "no_such_gate")

    def test_variable_without_that_threshold_raises(self):
        with pytest.raises(KeyError):
            REGISTRY.threshold(Variable.NBR, "stress_dispatch_delta")


class TestRecipeRef:
    def test_recipe_ref_is_variable_and_analysis_type(self):
        assert REGISTRY.recipe_ref(Variable.NDVI, AnalysisType.CHANGE) == "ndvi_change"
        assert REGISTRY.recipe_ref(Variable.NDWI, AnalysisType.TREND) == "ndwi_trend"

    def test_recipe_ref_spans_every_cell(self):
        for v in Variable:
            for at in AnalysisType:
                ref = REGISTRY.recipe_ref(v, at)
                assert ref == f"{v.value}_{at.value}"


class TestRegistryType:
    def test_registry_is_a_registry(self):
        assert isinstance(REGISTRY, Registry)
