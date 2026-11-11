# ABOUTME: Deterministic Method Card render (trust by construction, not by prose).
# ABOUTME: MethodPlan + execution facts → a structured card + a report_sections entry.
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from openeo_ai.agentic_eo.models import ExecutionResult, MethodPlan
from openeo_ai.agentic_eo.report import _lineage


def render_method_card(
    plan: MethodPlan,
    execution: ExecutionResult,
    run_date: Optional[date] = None,
    cache: Optional[Dict[str, Any]] = None,
    fidelity_events: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Deterministically render what was done — data, method, fidelity, cache, integrity.

    NOT composed by an LLM. Same inputs → identical output (no timestamps, no RNG).
    Reuses the pipeline's data-lineage renderer and adds AOI provenance (K2), cache
    provenance, and fidelity events so a wrong AOI or a degraded read is visible.
    """
    intent = plan.intent
    return {
        "method": {
            "variable": intent.variable.value,
            "analysis_type": intent.analysis_type.value,
            "recipe_ref": plan.recipe_ref,
            "improvised": plan.improvised,
            "plan_hash": plan.plan_hash,
        },
        "aoi": {
            "name": intent.aoi.resolved_name or intent.aoi.raw_text,
            "bbox": intent.aoi.bbox,
            "provenance": intent.aoi.provenance,
        },
        "data_lineage": _lineage(intent, execution, execution.stats, run_date or date.today()),
        "fidelity_events": list(fidelity_events or []),
        "cache": dict(cache or {}),
        "integrity": f"{execution.pipeline_integrity:.0%} of steps passed validators",
    }


def method_card_markdown(card: Dict[str, Any]) -> str:
    """Deterministic markdown of a Method Card (stable key order)."""
    m, aoi = card["method"], card["aoi"]
    lines = [
        "## Method Card",
        "",
        f"- **method:** {m['variable'].upper()} {m['analysis_type']} "
        f"({'recipe ' + m['recipe_ref'] if m['recipe_ref'] else 'improvised'})",
        f"- **plan_hash:** {m['plan_hash']}",
        f"- **AOI:** {aoi['name']} | bbox {aoi['bbox']} | provenance: {aoi['provenance']}",
        f"- **pipeline integrity:** {card['integrity']}",
    ]
    cache = card.get("cache") or {}
    if cache.get("cache_hit"):
        lines.append(f"- **cache:** hit (source save {cache.get('source_save_id', '?')})")
    for ev in card.get("fidelity_events") or []:
        lines.append(f"- **fidelity:** {ev}")
    lines.append("")
    lines.append("### Data lineage")
    for k, v in card["data_lineage"].items():
        lines.append(f"- **{k}:** {v}")
    return "\n".join(lines)


def method_card_section(card: Dict[str, Any]) -> Dict[str, Any]:
    """Map a Method Card to a report_sections entry (no timestamp — the emitter adds it)."""
    return {
        "type": "method_card",
        "title": "Method Card",
        "content": method_card_markdown(card),
    }
