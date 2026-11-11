# ABOUTME: The deterministic Method Card render (trust by construction).
# ABOUTME: Same inputs → byte-identical card; AOI provenance, cache, fidelity disclosed.
from openeo_ai.method_kernel.method_card import (
    method_card_section, render_method_card,
)
from openeo_ai.tests.method_card_helpers import (
    RUN_DATE as _RUN_DATE,
    make_execution as _execution,
    make_plan as _plan,
)


def test_card_is_deterministic():
    p = _plan()
    ex = _execution(p)
    a = render_method_card(p, ex, run_date=_RUN_DATE)
    b = render_method_card(p, ex, run_date=_RUN_DATE)
    assert a == b


def test_card_discloses_aoi_provenance():
    p = _plan(provenance="user-supplied")
    card = render_method_card(p, _execution(p), run_date=_RUN_DATE)
    assert card["aoi"]["provenance"] == "user-supplied"


def test_card_discloses_cache_and_fidelity():
    p = _plan()
    card = render_method_card(
        p, _execution(p), run_date=_RUN_DATE,
        cache={"cache_hit": True, "source_save_id": "abc"},
        fidelity_events=["AOI reduced at a 128px overview"],
    )
    assert card["cache"]["cache_hit"] is True
    assert card["fidelity_events"] == ["AOI reduced at a 128px overview"]


def test_card_carries_method_and_plan_hash():
    p = _plan()
    card = render_method_card(p, _execution(p), run_date=_RUN_DATE)
    assert card["method"]["variable"] == "ndvi"
    assert card["method"]["plan_hash"] == p.plan_hash
    # The registry now governs this cell, so the card stamps the recipe instead of "improvised".
    assert card["method"]["improvised"] is False
    assert card["method"]["recipe_ref"] == "ndvi_snapshot"


def test_section_shape_is_report_sections_compatible():
    p = _plan(provenance="user-supplied")
    card = render_method_card(p, _execution(p), run_date=_RUN_DATE)
    section = method_card_section(card)
    assert section["type"] == "method_card"
    assert section["title"] == "Method Card"
    assert isinstance(section["content"], str)
    # provenance is visible in the rendered content
    assert "user-supplied" in section["content"]
    # deterministic: no timestamp in the render (emitter adds it)
    assert "timestamp" not in section
