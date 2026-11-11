# ABOUTME: Phase 7 unit coverage for the agentic EO pipeline — pure functions only, no network.
# ABOUTME: Locks intent classification, plan DAG integrity, index math + BOA offset, Mann-Kendall,
# ABOUTME: the sub-annual phenology gate, per-scene read-error resilience, and change/anomaly stats.

from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pytest

from openeo_ai.agentic_eo import (
    build_plan,
    build_report,
    decompose,
    execute,
    run_query,
)
from openeo_ai.agentic_eo import (
    AnalysisType,
    AOISpec,
    BaselineSpec,
    EOIntent,
    ExecutionResult,
    MethodPlan,
    SceneStat,
    Step,
    StepKind,
    TimeRange,
    Variable,
)
from openeo_ai.agentic_eo.decompose import (
    _detect_analysis,
    _detect_cloud,
    _detect_time,
    _detect_variable,
)
from openeo_ai.agentic_eo.executor import (
    _analyze,
    _mann_kendall,
    _read_band,
    _record_chain,
    _resolve_scale_offset,
    _valid_values,
)
from openeo_ai.agentic_eo.indices import (
    REQUIRED_BANDS,
    WATER_NDWI_THRESHOLD,
    compute_index,
    reduce_aoi,
    safe_divide,
    to_reflectance,
    water_area_ha,
)
from openeo_ai.agentic_eo.report import _actions, _confidence, _fmt, _headline, _trend_change

# BOA conversion: value = DN*scale + offset. On earth-search v1 sentinel-2-l2a the COG DNs are
# pre-corrected (earthsearch:boa_offset_applied=True → effective offset 0); the -0.1 offset
# applies only to archives that still ship offset-uncorrected DNs.
BOA_SCALE = 0.0001
BOA_OFFSET = -0.1
BOA = (BOA_SCALE, BOA_OFFSET)
TODAY = date(2026, 7, 11)


# --------------------------------------------------------------------------- #
# Factories (synthesized typed inputs — allowed; no API calls)
# --------------------------------------------------------------------------- #

def _scene(day: date, value, cloud: float = 10.0, valid: float = 0.9,
           read_error=None) -> SceneStat:
    return SceneStat(
        item_id=f"S2_{day.isoformat()}",
        acquired=day,
        cloud_cover_pct=cloud,
        valid_fraction=valid if value is not None else 0.0,
        value=value,
        read_error=read_error,
    )


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


def _dn(refl: float, scale: float = BOA_SCALE, offset: float = BOA_OFFSET) -> int:
    """Inverse of the BOA conversion: the DN that decodes to a target reflectance."""
    return int(round((refl - offset) / scale))


def _filled(value, shape=(2, 2), dtype=np.uint16) -> np.ndarray:
    return np.full(shape, value, dtype=dtype)


# --------------------------------------------------------------------------- #
# Intent classification
# --------------------------------------------------------------------------- #

class TestVariableDetection:
    def test_fire_selects_nbr(self):
        assert _detect_variable("map the wildfire burn scar")[0] == Variable.NBR

    def test_water_selects_ndwi(self):
        assert _detect_variable("reservoir water extent")[0] == Variable.NDWI

    def test_moisture_selects_ndmi(self):
        assert _detect_variable("crop water stress and drought")[0] == Variable.NDMI

    def test_default_is_ndvi_and_not_explicit(self):
        var, explicit = _detect_variable("what is happening here")
        assert var == Variable.NDVI and explicit is False


class TestAnalysisDetection:
    def test_trend_phrase(self):
        assert _detect_analysis("ndvi over the last 8 months")[0] == AnalysisType.TREND

    def test_weak_change_verb(self):
        assert _detect_analysis("how did water level change")[0] == AnalysisType.CHANGE

    def test_anomaly_beats_change(self):
        # "compared to normal" lives in ANOMALY and must win over generic "compared to".
        assert _detect_analysis("ndvi compared to normal")[0] == AnalysisType.ANOMALY

    def test_change_beats_trend_on_explicit_comparison(self):
        # Explicit baseline comparison is a stronger intent than a bare trend word.
        assert _detect_analysis("ndvi trend compared to 2020")[0] == AnalysisType.CHANGE

    def test_default_is_snapshot(self):
        at, explicit = _detect_analysis("current ndvi here")
        assert at == AnalysisType.SNAPSHOT and explicit is False


class TestTimeDetection:
    def test_last_n_months(self):
        tr, explicit = _detect_time("over the last 3 months", TODAY)
        assert explicit and (TODAY - tr.start).days == 90 and tr.end == TODAY

    def test_between_years(self):
        tr, explicit = _detect_time("between 2020 and 2024", TODAY)
        assert explicit and tr.start == date(2020, 1, 1) and tr.end == date(2024, 12, 31)

    def test_since_year(self):
        tr, explicit = _detect_time("since 2019", TODAY)
        assert explicit and tr.start == date(2019, 1, 1) and tr.end == TODAY

    def test_in_month_year(self):
        tr, explicit = _detect_time("in march 2023", TODAY)
        assert explicit and tr.start == date(2023, 3, 1) and tr.end == date(2023, 3, 31)

    def test_default_last_182_days(self):
        tr, explicit = _detect_time("no time expressed", TODAY)
        assert explicit is False and (TODAY - tr.start).days == 182

    def test_absurd_n_years_is_capped_not_overflow(self):
        # An unbounded integer must be clamped before it reaches timedelta (max ~2.7e6 days).
        tr, explicit = _detect_time("last 9999999999 years", TODAY)
        assert explicit is True and tr.end == TODAY and tr.start < TODAY

    def test_out_of_range_year_falls_through_to_default(self):
        # Year 0000 is not a valid date() — parse must decline and fall back, not raise ValueError.
        tr, explicit = _detect_time("since 0000", TODAY)
        assert explicit is False and (TODAY - tr.start).days == 182


class TestCloudDetection:
    def test_explicit_percent(self):
        assert _detect_cloud("cloud cover under 10%") == (10.0, True)

    def test_cloud_free_word(self):
        assert _detect_cloud("cloud-free scenes only")[0] == 5.0

    def test_default(self):
        assert _detect_cloud("no cloud constraint given") == (20.0, False)


class TestDecomposeOffline:
    def test_trend_query_parses_without_network(self):
        intent = decompose(
            "NDVI trend over the last 6 months in Fresno County",
            today=TODAY, use_llm=False, do_geocode=False,
        )
        assert intent.analysis_type == AnalysisType.TREND
        assert intent.variable == Variable.NDVI
        assert intent.clarification_needed is None
        assert intent.aoi.raw_text == "Fresno County"

    def test_missing_aoi_requests_clarification(self):
        intent = decompose("show me the ndvi trend", today=TODAY,
                            use_llm=False, do_geocode=False)
        assert intent.clarification_needed is not None

    def test_change_query_builds_previous_period_baseline(self):
        # An explicit baseline comparison ("compared to") routes to CHANGE. A bare
        # "over the last N months" phrase is deliberately TREND (specificity ordering),
        # so it is avoided here — this case exercises the CHANGE -> previous_period path.
        intent = decompose(
            "how did ndvi in Fresno County change compared to the previous period",
            today=TODAY, use_llm=False, do_geocode=False,
        )
        assert intent.analysis_type == AnalysisType.CHANGE
        assert intent.baseline is not None and intent.baseline.mode == "previous_period"
        assert intent.baseline.time.end < intent.time.start

    def test_anomaly_baseline_handles_leap_day(self):
        # A Feb-29 target window must shift to Feb-28 in the non-leap previous year, not raise.
        intent = decompose(
            "ndvi anomaly between 2024-02 and 2024-02 in Fresno County",
            today=TODAY, use_llm=False, do_geocode=False,
        )
        assert intent.analysis_type == AnalysisType.ANOMALY
        assert intent.baseline is not None and intent.baseline.mode == "previous_year"
        assert intent.time.end == date(2024, 2, 29)
        assert intent.baseline.time.end == date(2023, 2, 28)


# --------------------------------------------------------------------------- #
# Plan DAG
# --------------------------------------------------------------------------- #

class TestPlanDAG:
    def test_snapshot_has_no_baseline_chain(self):
        plan = build_plan(_intent(AnalysisType.SNAPSHOT))
        ids = {s.id for s in plan.steps}
        assert "discover_baseline" not in ids
        analyze = next(s for s in plan.steps if s.id == "analyze")
        assert analyze.inputs == ["target_aggregate"]

    def test_change_has_baseline_chain(self):
        plan = build_plan(_intent(AnalysisType.CHANGE, baseline=True))
        ids = {s.id for s in plan.steps}
        assert "discover_baseline" in ids
        analyze = next(s for s in plan.steps if s.id == "analyze")
        assert analyze.inputs == ["target_aggregate", "baseline_aggregate"]

    def test_every_input_references_a_real_step(self):
        plan = build_plan(_intent(AnalysisType.CHANGE, baseline=True))
        ids = {s.id for s in plan.steps}
        for s in plan.steps:
            for dep in s.inputs:
                assert dep in ids, f"{s.id} references unknown step {dep}"

    def test_plan_hash_is_deterministic(self):
        intent = _intent(AnalysisType.TREND)
        assert build_plan(intent).plan_hash == build_plan(intent).plan_hash

    def test_plan_hash_differs_by_analysis_type(self):
        h1 = build_plan(_intent(AnalysisType.SNAPSHOT)).plan_hash
        h2 = build_plan(_intent(AnalysisType.CHANGE, baseline=True)).plan_hash
        assert h1 != h2


# --------------------------------------------------------------------------- #
# Index math — reflectance, BOA offset, masking
# --------------------------------------------------------------------------- #

class TestSafeDivide:
    def test_zero_denominator_maps_to_nan(self):
        out = safe_divide(np.array([1.0, 2.0], dtype=np.float32),
                          np.array([0.0, 2.0], dtype=np.float32))
        assert np.isnan(out[0]) and out[1] == pytest.approx(1.0)


class TestReflectance:
    @pytest.mark.parametrize("dn,expected", [(1000, 0.0), (2000, 0.1), (6000, 0.5)])
    def test_boa_anchor_points(self, dn, expected):
        out = to_reflectance(np.array([dn]), BOA_SCALE, BOA_OFFSET)
        assert out[0] == pytest.approx(expected, abs=1e-6)


class TestComputeIndex:
    def test_ndvi_known_value(self):
        # nir reflectance 0.4, red 0.1 -> NDVI = 0.3/0.5 = 0.6
        bands = {"nir": _filled(_dn(0.4)), "red": _filled(_dn(0.1))}
        so = {"nir": BOA, "red": BOA}
        idx = compute_index(Variable.NDVI, bands, so)
        median, frac = reduce_aoi(idx)
        assert median == pytest.approx(0.6, abs=1e-4) and frac == 1.0

    def test_boa_offset_changes_the_answer(self):
        # Dropping the -0.1 offset (the classic silent bug) shifts NDVI by > 0.15.
        bands = {"nir": _filled(_dn(0.4)), "red": _filled(_dn(0.1))}
        with_offset, _ = reduce_aoi(compute_index(Variable.NDVI, bands,
                                                  {"nir": BOA, "red": BOA}))
        without_offset, _ = reduce_aoi(compute_index(Variable.NDVI, bands,
                                                     {"nir": (BOA_SCALE, 0.0),
                                                      "red": (BOA_SCALE, 0.0)}))
        assert abs(with_offset - without_offset) > 0.15

    def test_dn_zero_is_nodata(self):
        nir = _filled(_dn(0.4))
        nir[0, 0] = 0  # raw DN==0 -> nodata before offset
        bands = {"nir": nir, "red": _filled(_dn(0.1))}
        idx = compute_index(Variable.NDVI, bands, {"nir": BOA, "red": BOA})
        median, frac = reduce_aoi(idx)
        assert np.isnan(idx[0, 0]) and frac == 0.75 and median == pytest.approx(0.6, abs=1e-4)

    def test_scl_masks_cloud_pixels(self):
        bands = {"nir": _filled(_dn(0.4)), "red": _filled(_dn(0.1))}
        scl = _filled(4, dtype=np.uint8)  # 4 = vegetation, kept
        scl[0, 0] = 9                     # 9 = cloud high, rejected
        idx = compute_index(Variable.NDVI, bands, {"nir": BOA, "red": BOA}, scl=scl)
        _, frac = reduce_aoi(idx)
        assert np.isnan(idx[0, 0]) and frac == 0.75

    def test_evi_is_clipped_to_unit_range(self):
        # Reflectances chosen so the pre-clip EVI blows past 1.0.
        bands = {"nir": _filled(_dn(0.5)), "red": _filled(_dn(0.05)),
                 "blue": _filled(_dn(0.2))}
        so = {b: BOA for b in ("nir", "red", "blue")}
        idx = compute_index(Variable.EVI, bands, so)
        finite = idx[np.isfinite(idx)]
        assert finite.max() <= 1.0 and finite.min() >= -1.0

    def test_missing_band_raises(self):
        with pytest.raises(ValueError):
            compute_index(Variable.NDVI, {"nir": _filled(_dn(0.4))}, {"nir": BOA})


class TestReduceAOI:
    def test_all_nan_returns_none(self):
        idx = np.full((2, 2), np.nan, dtype=np.float32)
        median, frac = reduce_aoi(idx)
        assert median is None and frac == 0.0


# --------------------------------------------------------------------------- #
# Mann-Kendall
# --------------------------------------------------------------------------- #

class TestMannKendall:
    def test_monotonic_decline_is_significant(self):
        S, z, p = _mann_kendall([5.0, 4.0, 3.0, 2.0, 1.0])
        assert S < 0 and p < 0.05

    def test_monotonic_rise_is_significant(self):
        S, z, p = _mann_kendall([1.0, 2.0, 3.0, 4.0, 5.0])
        assert S > 0 and p < 0.05

    def test_flat_series_is_not_significant(self):
        S, z, p = _mann_kendall([3.0, 3.0, 3.0, 3.0])
        assert z == 0.0 and p == 1.0


# --------------------------------------------------------------------------- #
# The phenology gate — the headline defect this session fixed
# --------------------------------------------------------------------------- #

class TestPhenologyGate:
    def _sub_annual_decline(self):
        days = [date(2024, 4, 5), date(2024, 4, 20), date(2024, 5, 5), date(2024, 5, 20),
                date(2024, 6, 5), date(2024, 6, 20), date(2024, 7, 5), date(2024, 7, 20)]
        vals = [0.80, 0.75, 0.68, 0.60, 0.52, 0.45, 0.38, 0.30]
        return [_scene(d, v) for d, v in zip(days, vals)]

    def _multiyear_decline(self):
        days = [date(y, 6, 15) for y in range(2020, 2026)]
        vals = [0.70, 0.62, 0.55, 0.48, 0.40, 0.32]
        return [_scene(d, v) for d, v in zip(days, vals)]

    def test_sub_annual_is_not_multiyear(self):
        s, warns = _analyze(_intent(AnalysisType.TREND), self._sub_annual_decline(), [])
        assert s["multiyear"] is False
        assert any("WITHIN-SEASON" in w or "within-season" in w for w in warns)

    def test_sub_annual_headline_avoids_per_year_rate(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._sub_annual_decline(), [])
        headline = _headline(_intent(AnalysisType.TREND), s, "Fresno County", "NDVI")
        assert "within-season" in headline and "phenology" in headline
        assert "/yr" not in headline

    def test_sub_annual_actions_refuse_degradation_review(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._sub_annual_decline(), [])
        actions = _actions(_intent(AnalysisType.TREND), s, "Fresno County")
        assert any("Do NOT open a degradation review" in a for a in actions)

    def test_multiyear_is_flagged_multiyear(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._multiyear_decline(), [])
        assert s["multiyear"] is True and s["window_years"] >= 1.5

    def test_multiyear_headline_reports_per_year_rate(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._multiyear_decline(), [])
        headline = _headline(_intent(AnalysisType.TREND), s, "Fresno County", "NDVI")
        assert "/yr" in headline and "declining" in headline

    def test_multiyear_actions_open_degradation_review(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._multiyear_decline(), [])
        actions = _actions(_intent(AnalysisType.TREND), s, "Fresno County")
        assert any("land-degradation review" in a for a in actions)

    def test_trend_change_sub_annual_withholds_per_year_rate(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._sub_annual_decline(), [])
        joined = " ".join(_trend_change(_intent(AnalysisType.TREND), s))
        assert "/yr" not in joined
        assert "net change" in joined.lower()

    def test_trend_change_multiyear_reports_per_year_rate(self):
        s, _ = _analyze(_intent(AnalysisType.TREND), self._multiyear_decline(), [])
        assert any("/yr" in line for line in _trend_change(_intent(AnalysisType.TREND), s))


# --------------------------------------------------------------------------- #
# Change / anomaly statistics
# --------------------------------------------------------------------------- #

class TestChangeStats:
    def _target(self):
        return [_scene(date(2024, 5, d), v, cloud=5.0)
                for d, v in [(5, 0.28), (12, 0.30), (19, 0.32), (26, 0.31)]]

    def _baseline(self, cloud=8.0):
        return [_scene(date(2023, 5, d), v, cloud=cloud)
                for d, v in [(5, 0.58), (12, 0.60), (19, 0.62), (26, 0.61)]]

    def test_delta_and_significance(self):
        s, _ = _analyze(_intent(AnalysisType.CHANGE, baseline=True),
                        self._target(), self._baseline())
        assert s["delta_median"] < -0.25 and s["significant"] is True

    def test_snapshot_without_baseline(self):
        s, warns = _analyze(_intent(AnalysisType.CHANGE, baseline=True),
                            self._target(), [])
        assert s["status"] == "snapshot_no_baseline"
        assert any("baseline" in w.lower() for w in warns)

    def test_cloud_asymmetry_warns(self):
        s, warns = _analyze(_intent(AnalysisType.CHANGE, baseline=True),
                            self._target(), self._baseline(cloud=45.0))
        assert any("cloud asymmetry" in w.lower() for w in warns)


# --------------------------------------------------------------------------- #
# Trend with too few points — theilslopes returns nan on a single point
# --------------------------------------------------------------------------- #

class TestTrendInsufficientData:
    def test_single_scene_is_flagged_insufficient(self):
        s, warns = _analyze(_intent(AnalysisType.TREND),
                            [_scene(date(2024, 5, 5), 0.5)], [])
        assert s.get("status") == "insufficient_for_trend"
        assert "sen_slope_per_year" not in s
        assert any("at least 2" in w for w in warns)

    def test_single_scene_headline_avoids_nan_rate(self):
        s, _ = _analyze(_intent(AnalysisType.TREND),
                        [_scene(date(2024, 5, 5), 0.5)], [])
        headline = _headline(_intent(AnalysisType.TREND), s, "Fresno County", "NDVI")
        assert "nan" not in headline.lower() and "/yr" not in headline


# --------------------------------------------------------------------------- #
# CHANGE-mode phenology gate — an adjacent sub-annual baseline is not season-matched
# --------------------------------------------------------------------------- #

class TestChangePhenologyGate:
    def _target(self):
        return [_scene(date(2024, 5, d), v, cloud=5.0)
                for d, v in [(5, 0.28), (12, 0.30), (19, 0.32), (26, 0.31)]]

    def _baseline(self):
        return [_scene(date(2023, 12, d), v, cloud=6.0)
                for d, v in [(5, 0.58), (12, 0.60), (19, 0.62), (26, 0.61)]]

    def test_sub_annual_change_refuses_field_dispatch(self):
        intent = _intent(AnalysisType.CHANGE, baseline=True)
        s, _ = _analyze(intent, self._target(), self._baseline())
        actions = _actions(intent, s, "Fresno County")
        assert not any("Dispatch a field scout" in a for a in actions)
        assert any("phenophase" in a.lower() for a in actions)

    def test_sub_annual_change_downgrades_confidence(self):
        intent = _intent(AnalysisType.CHANGE, baseline=True)
        s, _ = _analyze(intent, self._target(), self._baseline())
        ex = ExecutionResult(plan_hash="x", variable=Variable.NDVI,
                             analysis_type=AnalysisType.CHANGE)
        _, reasons = _confidence(intent, ex, s)
        assert any("phenolog" in r.lower() for r in reasons)

    def test_season_matched_anomaly_still_dispatches(self):
        # ANOMALY compares the same season one year earlier, so a large negative Δ is real.
        intent = _intent(AnalysisType.ANOMALY, baseline=True)
        s, _ = _analyze(intent, self._target(), self._baseline())
        actions = _actions(intent, s, "Fresno County")
        assert any("Dispatch a field scout" in a for a in actions)


# --------------------------------------------------------------------------- #
# BOA scale/offset — reflectance bands must fail loud, not silently drop the offset
# --------------------------------------------------------------------------- #

class TestReadBandScaleOffset:
    class _Asset:
        def __init__(self, extra_fields):
            self.extra_fields = extra_fields
            self.href = "https://example.invalid/nonexistent.tif"

    class _Item:
        def __init__(self, assets, properties=None):
            self.assets = assets
            self.properties = properties or {}

    def test_missing_scale_offset_on_reflectance_raises(self):
        item = self._Item({"red": self._Asset({"raster:bands": [{}]})})
        with pytest.raises(ValueError):
            _read_band(item, "red", [-120.5, 36.5, -119.5, 37.0])

    def test_missing_scale_offset_raises_even_when_offset_already_applied(self):
        # The pre-corrected flag must not weaken the fail-loud guard — scale is still required.
        item = self._Item({"red": self._Asset({"raster:bands": [{}]})},
                          properties={"earthsearch:boa_offset_applied": True})
        with pytest.raises(ValueError):
            _read_band(item, "red", [-120.5, 36.5, -119.5, 37.0])

    def test_boa_offset_applied_zeroes_the_offset(self):
        # earthsearch:boa_offset_applied=True → the archive already subtracted the BOA offset
        # from the COG DNs; applying the advertised -0.1 again double-subtracts and clips
        # water NDWI to exactly -1 (the Lake Oroville defect). Effective offset must be 0.
        rb = {"raster:bands": [{"scale": BOA_SCALE, "offset": BOA_OFFSET}]}
        item = self._Item({"red": self._Asset(rb)},
                          properties={"earthsearch:boa_offset_applied": True})
        assert _resolve_scale_offset(item, "red") == (BOA_SCALE, 0.0)

    def test_without_flag_metadata_offset_is_honored(self):
        rb = {"raster:bands": [{"scale": BOA_SCALE, "offset": BOA_OFFSET}]}
        item = self._Item({"red": self._Asset(rb)})
        assert _resolve_scale_offset(item, "red") == (BOA_SCALE, BOA_OFFSET)

    def test_flag_false_keeps_metadata_offset(self):
        rb = {"raster:bands": [{"scale": BOA_SCALE, "offset": BOA_OFFSET}]}
        item = self._Item({"red": self._Asset(rb)},
                          properties={"earthsearch:boa_offset_applied": False})
        assert _resolve_scale_offset(item, "red") == (BOA_SCALE, BOA_OFFSET)


# --------------------------------------------------------------------------- #
# Per-scene resilience
# --------------------------------------------------------------------------- #

class TestResilience:
    def test_valid_values_drops_none(self):
        scenes = [_scene(date(2024, 1, 1), 0.5),
                  _scene(date(2024, 1, 8), None),
                  _scene(date(2024, 1, 15), 0.4)]
        assert [v for _, v in _valid_values(scenes)] == [0.5, 0.4]

    def test_record_chain_splits_low_coverage_from_read_errors(self):
        plan = build_plan(_intent(AnalysisType.SNAPSHOT))
        by_id = {s.id: s for s in plan.steps}
        recorded = []

        def record(step, ok, detail="", warns=None, provenance=None):
            recorded.append(
                Step  # placeholder to keep signature honest; real result below
            ) if False else None
            recorded.append((step.id, warns or []))

        scenes = [
            _scene(date(2024, 1, 1), 0.5),                        # valid
            _scene(date(2024, 1, 8), 0.4),                        # valid
            _scene(date(2024, 1, 15), None),                      # low coverage
            _scene(date(2024, 1, 22), None, read_error="HTTPError: 503"),  # read failure
        ]
        _record_chain(record, by_id, "target", scenes, Variable.NDVI)

        mask_warns = next(w for sid, w in recorded if sid == "target_mask")
        joined = " ".join(mask_warns).lower()
        assert "rejected" in joined
        assert "dropped on cog read error" in joined
        assert "1/4" in joined


# --------------------------------------------------------------------------- #
# Water metric — decision value is derived surface area (ha), not the NDWI value
# --------------------------------------------------------------------------- #

class TestWaterAreaMetric:
    def test_area_from_known_fractions(self):
        idx = np.array([[0.5, -0.3], [np.nan, 0.2]], dtype=np.float32)
        # 3 valid pixels, 2 water (NDWI > 0) → 2/3 of a 30 km² AOI = 20 km² = 2000 ha
        assert water_area_ha(idx, 30.0) == pytest.approx(2000.0)

    def test_all_masked_returns_none(self):
        idx = np.full((2, 2), np.nan, dtype=np.float32)
        assert water_area_ha(idx, 30.0) is None

    def test_threshold_is_strictly_positive(self):
        # NDWI == 0.0 is the land/water boundary, not water.
        idx = np.array([[WATER_NDWI_THRESHOLD, -0.1]], dtype=np.float32)
        assert water_area_ha(idx, 100.0) == 0.0


class TestWaterAreaReporting:
    def test_fmt_uses_hectares_for_ndwi_and_index_otherwise(self):
        assert _fmt(Variable.NDWI, 5300.0) == "5,300 ha"
        assert _fmt(Variable.NDWI, -350.0, signed=True) == "-350 ha"
        assert _fmt(Variable.NDVI, 0.5) == "0.500"
        assert _fmt(Variable.NDVI, -0.119, signed=True) == "-0.119"

    def test_ndwi_change_headline_reports_hectares(self):
        intent = _intent(AnalysisType.CHANGE, variable=Variable.NDWI, baseline=True)
        s = {"delta_median": -350.0, "baseline_median": 5300.0, "target_median": 4950.0,
             "welch_p": 0.010, "significant": True}
        h = _headline(intent, s, "Lake Oroville", "open-water surface area (NDWI-derived)")
        assert "ha" in h
        assert "-350" in h and "5,300" in h and "4,950" in h

    def test_ndwi_delta_action_uses_area(self):
        intent = _intent(AnalysisType.CHANGE, variable=Variable.NDWI, baseline=True)
        s = {"delta_median": -350.0, "baseline_median": 5300.0, "target_median": 4950.0,
             "welch_p": 0.010, "significant": True}
        actions = _actions(intent, s, "Lake Oroville")
        assert any("350 ha" in a for a in actions)

    def test_ndwi_sustained_drawdown_triggers_action(self):
        # -2600 ha/yr ≈ -50 ha/week — beyond the -25 ha/wk drawdown trigger (RESEARCH §4).
        intent = _intent(AnalysisType.TREND, variable=Variable.NDWI)
        s = {"sen_slope_per_year": -2600.0, "significant": True,
             "window_years": 2.0, "multiyear": True, "mk_p": 0.001}
        actions = _actions(intent, s, "Lake Oroville")
        assert any("ha/week" in a and "drawdown" in a.lower() for a in actions)


class TestNoiseFloorGate:
    @staticmethod
    def _flat_multiyear_series(base: float):
        # 12 scenes over ~1.8 yr with a slope of ~0.006/yr — under the 0.02 index noise floor.
        return [_scene(date(2024, 1, 1) + timedelta(days=60 * i), base + i * 0.001)
                for i in range(12)]

    def test_ndvi_tiny_multiyear_slope_warns_noise_floor(self):
        _, warns = _analyze(_intent(AnalysisType.TREND), self._flat_multiyear_series(0.5), [])
        assert any("noise floor" in w for w in warns)

    def test_ndwi_area_slope_never_warns_index_noise_floor(self):
        # The NDWI series is hectares, not an index — the 0.02 index noise floor does not apply.
        _, warns = _analyze(_intent(AnalysisType.TREND, variable=Variable.NDWI),
                            self._flat_multiyear_series(5000.0), [])
        assert not any("noise floor" in w for w in warns)


class TestWaterAreaPlanAndChain:
    def test_ndwi_plan_derives_water_area(self):
        plan = build_plan(_intent(AnalysisType.SNAPSHOT, variable=Variable.NDWI))
        by_id = {s.id: s for s in plan.steps}
        assert by_id["target_aggregate"].params["reducer"] == "water_area_ha"
        assert by_id["target_derive"].params["water_threshold"] == WATER_NDWI_THRESHOLD

    def test_ndvi_plan_keeps_median_reducer(self):
        plan = build_plan(_intent(AnalysisType.SNAPSHOT))
        by_id = {s.id: s for s in plan.steps}
        assert by_id["target_aggregate"].params["reducer"] == "median"
        assert "water_threshold" not in by_id["target_derive"].params

    def test_record_chain_area_values_skip_index_range_guard(self):
        # 5300 ha is a perfectly good area value — it must not trip the [-1,1] index guard.
        plan = build_plan(_intent(AnalysisType.SNAPSHOT, variable=Variable.NDWI))
        by_id = {s.id: s for s in plan.steps}
        recorded = []

        def record(step, ok, detail="", warns=None, provenance=None):
            recorded.append((step.id, warns or []))

        scenes = [_scene(date(2024, 1, 1), 5300.0), _scene(date(2024, 1, 8), 5170.0)]
        _record_chain(record, by_id, "target", scenes, Variable.NDWI)
        derive_warns = next(w for sid, w in recorded if sid == "target_derive")
        assert derive_warns == []


# --------------------------------------------------------------------------- #
# MCP tool surface — run_query exposed to the chat/SDK flow
# --------------------------------------------------------------------------- #

class TestDecisionQueryTool:
    def test_tool_definition_shape(self):
        from openeo_ai.agentic_eo.sdk_tool import DECISION_QUERY_TOOL_DEFINITIONS, TOOL_NAME
        assert len(DECISION_QUERY_TOOL_DEFINITIONS) == 1
        d = DECISION_QUERY_TOOL_DEFINITIONS[0]
        assert d["name"] == TOOL_NAME
        assert d["input_schema"]["required"] == ["query"]
        assert "query" in d["input_schema"]["properties"]

    def test_handler_returns_clarification_when_aoi_absent(self):
        import asyncio
        from openeo_ai.agentic_eo.sdk_tool import create_decision_query_tools, TOOL_NAME
        handler = create_decision_query_tools()[TOOL_NAME]
        out = asyncio.run(handler({"query": "show me the ndvi trend"}))
        assert "Clarification needed" in out["content"][0]["text"]

    def test_handler_rejects_blank_query(self):
        import asyncio
        from openeo_ai.agentic_eo.sdk_tool import create_decision_query_tools, TOOL_NAME
        handler = create_decision_query_tools()[TOOL_NAME]
        out = asyncio.run(handler({"query": "   "}))
        assert "error" in out["content"][0]["text"]

    def test_tool_registered_in_app_tool_definitions(self):
        from openeo_ai.sdk.client import TOOL_DEFINITIONS
        from openeo_ai.agentic_eo.sdk_tool import TOOL_NAME
        assert any(t["name"] == TOOL_NAME for t in TOOL_DEFINITIONS)

    def test_handler_surfaces_method_clarification(self, monkeypatch):
        # An ill-posed method returns report=None — the handler must
        # surface the clarification, not crash on report.to_markdown().
        import asyncio
        import openeo_ai.agentic_eo.sdk_tool as sdk_tool
        from openeo_ai.agentic_eo.pipeline import PipelineOutput
        from openeo_ai.tests.method_card_helpers import make_plan

        plan = make_plan()
        plan.intent.method_clarification = (
            "A change analysis needs a baseline window and none is set.")
        canned = PipelineOutput(intent=plan.intent, plan=plan,
                                execution=None, report=None)
        monkeypatch.setattr(sdk_tool, "run_query", lambda *a, **k: canned)

        handler = sdk_tool.create_decision_query_tools()[sdk_tool.TOOL_NAME]
        out = asyncio.run(handler({"query": "how did vegetation change in Kings County"}))
        text = out["content"][0]["text"]
        assert "Clarification needed" in text
        assert "baseline" in text


# --------------------------------------------------------------------------- #
# Pre-model decision-grade router (deterministic, mirrors the Sentinel safety-net)
# --------------------------------------------------------------------------- #

class TestDecisionGradeRouter:
    def test_explicit_decision_grade_phrase_routes(self):
        from openeo_ai.sdk.claude_sdk_bridge import _is_decision_grade_query
        assert _is_decision_grade_query("give me a decision-grade NDVI report for Fresno County")

    def test_framing_plus_eo_signal_routes(self):
        from openeo_ai.sdk.claude_sdk_bridge import _is_decision_grade_query
        assert _is_decision_grade_query("Should I worry about vegetation stress in Fresno County?")

    def test_bare_analysis_does_not_route(self):
        from openeo_ai.sdk.claude_sdk_bridge import _is_decision_grade_query
        assert not _is_decision_grade_query("show me an ndvi map of Fresno County")

    def test_general_chat_does_not_route(self):
        from openeo_ai.sdk.claude_sdk_bridge import _is_decision_grade_query
        assert not _is_decision_grade_query("what collections are available?")

    def test_emit_streams_report_events_and_closes_turn(self):
        import asyncio
        from openeo_ai.sdk.claude_sdk_bridge import _emit_decision_report

        class _Capture:
            def __init__(self):
                self.events = []

            async def send_json(self, obj):
                self.events.append(obj)

        ws = _Capture()
        # No AOI in the query → the pipeline returns a clarification with no network call.
        asyncio.run(_emit_decision_report(ws, "give me a decision-grade ndvi report"))
        types = [e["type"] for e in ws.events]
        assert types[0] == "thinking"
        assert "tool_start" in types and "tool_result" in types
        assert types[-1] == "done"
        tr = next(e for e in ws.events if e["type"] == "tool_result")
        assert tr["tool_name"] == "agentic_decision_query"
        assert "Clarification needed" in tr["result"]
        # A clarification carries no plan/execution, so no Method Card.
        assert "report_sections" not in types

    def test_emit_success_path_emits_method_card_report_sections(self, monkeypatch):
        # The decision-grade route must surface the K4 Method Card as a
        # live report_sections event so the frontend Report panel renders it.
        import asyncio
        import openeo_ai.agentic_eo as agentic_eo
        from openeo_ai.agentic_eo.pipeline import PipelineOutput
        from openeo_ai.agentic_eo.report import build_report
        from openeo_ai.sdk.claude_sdk_bridge import _emit_decision_report
        from openeo_ai.tests.method_card_helpers import (
            RUN_DATE, make_execution, make_plan,
        )

        plan = make_plan()
        execution = make_execution(plan)
        execution.stats.update({"target_median": 0.21})
        report = build_report(plan.intent, execution, run_date=RUN_DATE)
        canned = PipelineOutput(intent=plan.intent, plan=plan,
                                execution=execution, report=report)
        monkeypatch.setattr(agentic_eo, "run_query", lambda *a, **k: canned)

        class _Capture:
            def __init__(self):
                self.events = []

            async def send_json(self, obj):
                self.events.append(obj)

        ws = _Capture()
        asyncio.run(_emit_decision_report(
            ws, "give me a decision-grade ndvi report for Kings County"))
        types = [e["type"] for e in ws.events]
        # The card lands after the tool_result and before the turn closes.
        assert "report_sections" in types
        assert types.index("report_sections") > types.index("tool_result")
        assert types.index("report_sections") < types.index("done")
        rs = next(e for e in ws.events if e["type"] == "report_sections")
        # live=True tells the frontend to persist these sections with the run
        # (saved-job replay emits the same event type without the flag).
        assert rs["live"] is True
        assert len(rs["sections"]) == 1
        section = rs["sections"][0]
        assert section["type"] == "method_card"
        assert section["title"] == "Method Card"
        assert "## Method Card" in section["content"]
        assert plan.plan_hash in section["content"]
        # The deterministic render has no timestamp/toolName — the emitter adds
        # both. The frontend feeds the stamp to new Date(), so pin ISO-8601.
        datetime.fromisoformat(section["timestamp"])
        assert section["toolName"] == "agentic_decision_query"

    def test_emit_error_path_emits_no_report_sections(self, monkeypatch):
        import asyncio
        import openeo_ai.agentic_eo as agentic_eo
        from openeo_ai.sdk.claude_sdk_bridge import _emit_decision_report

        def _boom(*a, **k):
            raise RuntimeError("pipeline exploded")

        monkeypatch.setattr(agentic_eo, "run_query", _boom)

        class _Capture:
            def __init__(self):
                self.events = []

            async def send_json(self, obj):
                self.events.append(obj)

        ws = _Capture()
        asyncio.run(_emit_decision_report(
            ws, "give me a decision-grade ndvi report for Kings County"))
        types = [e["type"] for e in ws.events]
        assert "report_sections" not in types
        assert "error" in types
        assert types[-1] == "done"

    def test_emit_surfaces_method_clarification_without_method_card(self, monkeypatch):
        # The gate returns plan-but-no-report — the bridge must stream
        # the clarification (not an error, no Method Card) and close the turn.
        import asyncio
        import openeo_ai.agentic_eo as agentic_eo
        from openeo_ai.agentic_eo.pipeline import PipelineOutput
        from openeo_ai.sdk.claude_sdk_bridge import _emit_decision_report
        from openeo_ai.tests.method_card_helpers import make_plan

        plan = make_plan()
        plan.intent.method_clarification = (
            "A change analysis needs a baseline window and none is set.")
        canned = PipelineOutput(intent=plan.intent, plan=plan,
                                execution=None, report=None)
        monkeypatch.setattr(agentic_eo, "run_query", lambda *a, **k: canned)

        class _Capture:
            def __init__(self):
                self.events = []

            async def send_json(self, obj):
                self.events.append(obj)

        ws = _Capture()
        asyncio.run(_emit_decision_report(
            ws, "how did vegetation change in Kings County"))
        types = [e["type"] for e in ws.events]
        assert "report_sections" not in types
        assert "error" not in types
        assert types[-1] == "done"
        tr = next(e for e in ws.events if e["type"] == "tool_result")
        assert "Clarification needed" in tr["result"]
        assert "baseline" in tr["result"]


# --------------------------------------------------------------------------- #
# Method-aptness gate wired into run_query
# --------------------------------------------------------------------------- #

class TestMethodFitGate:
    @staticmethod
    def _ill_posed_intent():
        # A change question parsed to CHANGE but with no baseline window —
        # well-formed, ill-posed. Without the gate this executes and returns
        # a confident wrong answer.
        from openeo_ai.agentic_eo.models import (
            AnalysisType, AOISpec, EOIntent, TimeRange, Variable,
        )
        return EOIntent(
            query="how did vegetation change in Kings County",
            aoi=AOISpec(raw_text="Kings County", bbox=[-119.9, 36.0, -119.7, 36.2],
                        resolved_name="Kings County", provenance="user-supplied"),
            time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
            variable=Variable.NDVI, analysis_type=AnalysisType.CHANGE, baseline=None,
        )

    def test_ill_posed_intent_clarifies_instead_of_executing(self, monkeypatch):
        import openeo_ai.agentic_eo.pipeline as pipeline

        reached = []
        monkeypatch.setattr(pipeline, "decompose",
                            lambda *a, **k: self._ill_posed_intent())
        monkeypatch.setattr(pipeline, "execute", lambda plan: reached.append(plan))

        out = pipeline.run_query("how did vegetation change in Kings County",
                                 use_llm=False, do_geocode=False)
        assert reached == []  # no wasted STAC/pixel execution
        assert out.plan is not None
        assert out.execution is None and out.report is None
        assert out.intent.method_clarification
        assert "baseline" in out.intent.method_clarification

    def test_gate_env_escape_reaches_execution(self, monkeypatch):
        # JONA_METHOD_FIT_GATE=0 → observe mode: the FAIL never blocks.
        import openeo_ai.agentic_eo.pipeline as pipeline

        class _Reached(Exception):
            pass

        def _hit(plan):
            raise _Reached()

        monkeypatch.setenv("JONA_METHOD_FIT_GATE", "0")
        monkeypatch.setattr(pipeline, "decompose",
                            lambda *a, **k: self._ill_posed_intent())
        monkeypatch.setattr(pipeline, "execute", _hit)

        with pytest.raises(_Reached):
            pipeline.run_query("how did vegetation change in Kings County",
                               use_llm=False, do_geocode=False)

    def test_well_posed_intent_executes_normally(self, monkeypatch):
        import openeo_ai.agentic_eo.pipeline as pipeline
        from openeo_ai.tests.method_card_helpers import (
            RUN_DATE, make_execution, make_plan,
        )

        plan = make_plan()
        execution = make_execution(plan)
        execution.stats.update({"target_median": 0.21})
        monkeypatch.setattr(pipeline, "decompose", lambda *a, **k: plan.intent)
        monkeypatch.setattr(pipeline, "execute", lambda p: execution)

        out = pipeline.run_query("ndvi", today=RUN_DATE,
                                 use_llm=False, do_geocode=False)
        assert out.intent.method_clarification is None
        assert out.execution is execution
        assert out.report is not None
        # Clean plan: the report caveats carry no method-check lines (Task 4).
        assert not any(c.startswith("Method check:") for c in out.report.caveats)

    def test_method_warns_surface_in_report_caveats(self, monkeypatch):
        # Water question with an NDVI method: runnable (WARN, not FAIL), but
        # the report must caveat the index mismatch.
        import openeo_ai.agentic_eo.pipeline as pipeline
        from openeo_ai.agentic_eo.models import (
            AnalysisType, AOISpec, EOIntent, TimeRange, Variable,
        )
        from openeo_ai.tests.method_card_helpers import RUN_DATE, make_execution
        from openeo_ai.agentic_eo.planner import build_plan

        intent = EOIntent(
            query="is there drought stress in Kings County",
            aoi=AOISpec(raw_text="Kings County", bbox=[-119.9, 36.0, -119.7, 36.2],
                        resolved_name="Kings County", provenance="user-supplied"),
            time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
            variable=Variable.NDVI, analysis_type=AnalysisType.SNAPSHOT,
        )
        execution = make_execution(build_plan(intent))
        execution.stats.update({"target_median": 0.21})
        monkeypatch.setattr(pipeline, "decompose", lambda *a, **k: intent)
        monkeypatch.setattr(pipeline, "execute", lambda p: execution)

        out = pipeline.run_query(intent.query, today=RUN_DATE,
                                 use_llm=False, do_geocode=False)
        assert out.report is not None
        assert any(c.startswith("Method check:") and "NDVI" in c
                   for c in out.report.caveats)

    def test_change_verb_snapshot_misparse_clarifies(self, monkeypatch):
        # Spec Task 5 acceptance, enabled by the WARN→FAIL promotion (NU,
        # 2026-07-23): "how did vegetation change in X?" misparsed to SNAPSHOT
        # clarifies instead of returning a confident wrong answer.
        import openeo_ai.agentic_eo.pipeline as pipeline
        from openeo_ai.agentic_eo.models import (
            AnalysisType, AOISpec, EOIntent, TimeRange, Variable,
        )

        intent = EOIntent(
            query="how did vegetation change in Kings County",
            aoi=AOISpec(raw_text="Kings County", bbox=[-119.9, 36.0, -119.7, 36.2],
                        resolved_name="Kings County", provenance="user-supplied"),
            time=TimeRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
            variable=Variable.NDVI, analysis_type=AnalysisType.SNAPSHOT,
        )
        reached = []
        monkeypatch.setattr(pipeline, "decompose", lambda *a, **k: intent)
        monkeypatch.setattr(pipeline, "execute", lambda plan: reached.append(plan))

        out = pipeline.run_query(intent.query, use_llm=False, do_geocode=False)
        assert reached == []
        assert out.execution is None and out.report is None
        assert out.intent.method_clarification
        assert "snapshot" in out.intent.method_clarification
        assert "baseline" in out.intent.method_clarification


# --------------------------------------------------------------------------- #
# Public surface
# --------------------------------------------------------------------------- #

def test_public_surface_is_importable_and_callable():
    for obj in (run_query, decompose, build_plan, execute, build_report):
        assert callable(obj)
    # Model types resolve from the package root.
    assert MethodPlan and ExecutionResult and SceneStat and StepKind
