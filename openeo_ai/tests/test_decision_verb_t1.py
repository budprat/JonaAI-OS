# ABOUTME: The decision verb becomes callable with explicit recipe_id / window / thresholds.
# ABOUTME: Proves recipe resolution, explicit aoi/window honored, per-run threshold override, structured errors.

from __future__ import annotations

from datetime import date

from openeo_ai.agentic_eo.decompose import decompose
from openeo_ai.agentic_eo.models import (
    AnalysisType,
    AOISpec,
    BaselineSpec,
    EOIntent,
    TimeRange,
    Variable,
)
from openeo_ai.agentic_eo.recipes import REGISTRY
from openeo_ai.agentic_eo.report import _actions

_BBOX = [-119.9, 36.0, -119.7, 36.2]


def _decompose(query="status of the area", **kw):
    # Deterministic path: no LLM, explicit bbox so geocoding never runs.
    return decompose(query, today=date(2025, 7, 1), use_llm=False,
                     do_geocode=False, aoi_bbox=_BBOX, **kw)


class TestRecipeResolution:
    def test_recipe_id_sets_variable_and_analysis_type(self):
        intent = _decompose(recipe_id="ndwi_trend")
        assert intent.variable is Variable.NDWI
        assert intent.analysis_type is AnalysisType.TREND
        assert intent.clarification_needed is None

    def test_recipe_id_overrides_the_keyword_parser(self):
        # The prose screams NDVI/snapshot; the explicit recipe must win.
        intent = _decompose("vegetation greenness right now", recipe_id="nbr_change")
        assert intent.variable is Variable.NBR
        assert intent.analysis_type is AnalysisType.CHANGE

    def test_recipe_id_drives_baseline_derivation(self):
        # A change cell must still get its previous-period baseline built.
        intent = _decompose(recipe_id="ndvi_change")
        assert intent.baseline is not None
        assert intent.baseline.mode == "previous_period"

    def test_unknown_recipe_id_returns_structured_error(self):
        intent = _decompose(recipe_id="ndvi_forecast")
        assert intent.clarification_needed is not None
        assert "ndvi_forecast" in intent.clarification_needed

    def test_registry_parses_and_rejects_recipe_ids(self):
        assert REGISTRY.parse_recipe_id("ndvi_change") == (Variable.NDVI, AnalysisType.CHANGE)
        assert REGISTRY.parse_recipe_id("bogus_thing") is None
        assert REGISTRY.parse_recipe_id("ndvi") is None


class TestExplicitWindow:
    def test_window_honored_over_parsed_time(self):
        intent = _decompose("ndvi last 3 months", window=["2024-03-01", "2024-03-31"])
        assert intent.time.start == date(2024, 3, 1)
        assert intent.time.end == date(2024, 3, 31)
        assert not any("Time range not stated" in a for a in intent.assumptions)

    def test_window_accepts_date_objects(self):
        intent = _decompose(window=[date(2024, 1, 1), date(2024, 6, 30)])
        assert intent.time == TimeRange(start=date(2024, 1, 1), end=date(2024, 6, 30))

    def test_malformed_window_returns_structured_error(self):
        intent = _decompose(window=["2024-06-30", "2024-01-01"])  # end before start
        assert intent.clarification_needed is not None
        assert "window" in intent.clarification_needed.lower()


class TestThresholdOverride:
    def _intent(self, overrides):
        return EOIntent(
            query="synthetic",
            aoi=AOISpec(raw_text="Fresno County", bbox=_BBOX, resolved_name="Fresno County"),
            time=TimeRange(start=date(2024, 1, 1), end=date(2024, 6, 30)),
            variable=Variable.NDVI,
            analysis_type=AnalysisType.ANOMALY,
            baseline=BaselineSpec(mode="previous_year",
                                  time=TimeRange(start=date(2023, 1, 1), end=date(2023, 6, 30))),
            threshold_overrides=overrides,
        )

    def _stats(self, delta):
        return {"delta_median": delta, "baseline_median": 0.6, "target_median": 0.6 + delta,
                "welch_p": 0.01, "significant": True}

    def test_override_tightens_the_dispatch_gate(self):
        # Δ-0.16 does NOT dispatch under the default -0.20, but DOES under an override of -0.15.
        base = _actions(self._intent(None), self._stats(-0.16), "Fresno County")
        tuned = _actions(self._intent({"stress_dispatch_delta": -0.15}),
                         self._stats(-0.16), "Fresno County")
        assert not any("Dispatch a field scout" in a for a in base)
        assert any("Dispatch a field scout" in a for a in tuned)

    def test_absent_overrides_are_behavior_identical(self):
        assert _actions(self._intent(None), self._stats(-0.25), "Fresno County") == \
               _actions(self._intent({}), self._stats(-0.25), "Fresno County")


class TestToolSurface:
    def test_schema_exposes_the_new_params(self):
        from openeo_ai.agentic_eo.sdk_tool import DECISION_QUERY_TOOL_DEFINITIONS
        props = DECISION_QUERY_TOOL_DEFINITIONS[0]["input_schema"]["properties"]
        assert "recipe_id" in props
        assert "window" in props
        assert "thresholds" in props
        # query stays the only required field — the verb still answers a plain question.
        assert DECISION_QUERY_TOOL_DEFINITIONS[0]["input_schema"]["required"] == ["query"]
