# ABOUTME: Pin the kernel plan_hash (steps-only) BEFORE the envelope grows,
# ABOUTME: prove envelope fields don't shift it, and lock the method_kernel façade.
from datetime import date

from openeo_ai.agentic_eo.models import (
    AnalysisType, AOISpec, EOIntent, MethodPlan, TimeRange, Variable,
)
from openeo_ai.agentic_eo.planner import build_plan


def _canonical_intent() -> EOIntent:
    # Frozen inputs — must match the value pinned below. Do not edit casually.
    return EOIntent(
        query="synthetic",
        aoi=AOISpec(raw_text="Fresno County", bbox=[-120.5, 36.5, -119.5, 37.0],
                    resolved_name="Fresno County"),
        time=TimeRange(start=date(2024, 1, 1), end=date(2024, 6, 30)),
        variable=Variable.NDVI,
        analysis_type=AnalysisType.SNAPSHOT,
    )


def test_plan_hash_frozen_value():
    # A frozen value — if this changes, the kernel cache/trajectory keys shifted.
    # Guards against envelope fields silently entering the hashed payload.
    assert build_plan(_canonical_intent()).plan_hash == "f479b7f36d3982d5"


def test_plan_hash_is_steps_only():
    intent = _canonical_intent()
    plan = build_plan(intent)
    mutated_intent = intent.model_copy(update={"query": "different", "decision_context": "x"})
    twin = MethodPlan(intent=mutated_intent, steps=plan.steps)
    assert plan.plan_hash == twin.plan_hash


def test_envelope_fields_exist_and_do_not_change_hash():
    plan = build_plan(_canonical_intent())
    baseline_hash = plan.plan_hash
    enriched = MethodPlan(
        intent=plan.intent, steps=plan.steps,
        recipe_ref="ndvi.snapshot.v1", improvised=False,
    )
    assert enriched.recipe_ref == "ndvi.snapshot.v1"
    assert enriched.improvised is False
    assert enriched.plan_hash == baseline_hash


def test_methodplan_model_default_is_improvised():
    # The bare model envelope (no recipe applied) is improvised until something stamps it.
    plan = MethodPlan(intent=_canonical_intent(), steps=[])
    assert plan.recipe_ref is None
    assert plan.improvised is True


def test_build_plan_stamps_the_governing_recipe():
    # A registry-backed plan is stamped with its (variable × analysis_type) cell, not improvised.
    plan = build_plan(_canonical_intent())
    assert plan.recipe_ref == "ndvi_snapshot"
    assert plan.improvised is False


def test_method_kernel_facade_exports():
    from openeo_ai import method_kernel
    from openeo_ai.agentic_eo.models import MethodPlan as _MP
    from openeo_ai.agentic_eo.pipeline import run_query as _rq

    assert method_kernel.MethodPlan is _MP
    assert method_kernel.run_query is _rq
    # No bare plan_hash export — three hash surfaces exist; the kernel hash is
    # MethodPlan.plan_hash (a property), never an importable module function.
    assert not hasattr(method_kernel, "plan_hash")


# ── K2: explicit structured AOI (analyst supplies coordinates directly) ──────

import importlib  # noqa: E402

# The package __init__ re-exports the `decompose` function, shadowing the
# submodule under attribute access — import_module returns the real module.
decompose_mod = importlib.import_module("openeo_ai.agentic_eo.decompose")


def test_explicit_bbox_avoids_geocode(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("geocode must not run for an explicit bbox")
    monkeypatch.setattr(decompose_mod, "geocode_aoi", _boom)
    intent = decompose_mod.decompose("NDVI for my field", use_llm=False,
                                     aoi_bbox=[-119.9, 36.0, -119.7, 36.2])
    assert intent.aoi.bbox == [-119.9, 36.0, -119.7, 36.2]
    assert intent.aoi.provenance == "user-supplied"
    assert intent.clarification_needed is None


def test_invalid_bbox_clarifies(monkeypatch):
    monkeypatch.setattr(decompose_mod, "geocode_aoi", lambda *a, **k: None)
    intent = decompose_mod.decompose("NDVI", use_llm=False, aoi_bbox=[1, 2, 3])
    assert intent.clarification_needed is not None
    assert intent.aoi.provenance == "unresolved"


def test_geocoded_provenance_is_default(monkeypatch):
    from openeo_ai.agentic_eo.models import AOISpec
    monkeypatch.setattr(
        decompose_mod, "geocode_aoi",
        lambda *a, **k: AOISpec(raw_text="Fresno County",
                                bbox=[-120.0, 36.7, -119.8, 36.9], resolved_name="Fresno County"))
    intent = decompose_mod.decompose("NDVI for Fresno County", use_llm=False)
    assert intent.aoi.provenance == "geocoded"
    assert intent.clarification_needed is None


def test_run_query_threads_aoi_bbox(monkeypatch):
    from openeo_ai.agentic_eo import pipeline as pl
    captured = {}

    def _capture(query, **kw):
        captured.update(kw)
        return EOIntent(query=query, aoi=AOISpec(raw_text=""),
                        time=TimeRange(start=date(2024, 1, 1), end=date(2024, 1, 2)),
                        clarification_needed="stop-here")
    monkeypatch.setattr(pl, "decompose", _capture)
    pl.run_query("q", aoi_bbox=[-1.0, -1.0, 1.0, 1.0])
    assert captured.get("aoi_bbox") == [-1.0, -1.0, 1.0, 1.0]


def test_sdk_tool_advertises_and_forwards_aoi(monkeypatch):
    import asyncio
    import openeo_ai.agentic_eo.sdk_tool as st
    from openeo_ai.agentic_eo.pipeline import PipelineOutput

    d = st.DECISION_QUERY_TOOL_DEFINITIONS[0]
    assert "aoi" in d["input_schema"]["properties"]

    captured = {}

    def _capture_run(query, today=None, use_llm=True, do_geocode=True, aoi_bbox=None,
                     recipe_id=None, window=None, thresholds=None):
        captured["aoi_bbox"] = aoi_bbox
        captured["recipe_id"] = recipe_id
        captured["window"] = window
        captured["thresholds"] = thresholds
        return PipelineOutput(
            intent=EOIntent(query=query, aoi=AOISpec(raw_text=""),
                            time=TimeRange(start=date(2024, 1, 1), end=date(2024, 1, 2)),
                            clarification_needed="c"),
            plan=None, execution=None, report=None)
    monkeypatch.setattr(st, "run_query", _capture_run)
    handler = st.create_decision_query_tools()[st.TOOL_NAME]
    asyncio.run(handler({"query": "ndvi for my field", "aoi": [-119.9, 36.0, -119.7, 36.2],
                         "recipe_id": "ndvi_change", "window": ["2024-01-01", "2024-06-30"],
                         "thresholds": {"stress_dispatch_delta": -0.15}}))
    assert captured["aoi_bbox"] == [-119.9, 36.0, -119.7, 36.2]
    assert captured["recipe_id"] == "ndvi_change"
    assert captured["window"] == ["2024-01-01", "2024-06-30"]
    assert captured["thresholds"] == {"stress_dispatch_delta": -0.15}
