# ABOUTME: MCP tool surface for the agentic-EO pipeline — exposes run_query() to the chat/SDK flow
# ABOUTME: so the model can answer a question with a decision-grade report streamed over the WebSocket.

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Dict

from .pipeline import run_query

TOOL_NAME = "agentic_decision_query"

DECISION_QUERY_TOOL_DEFINITIONS = [
    {
        "name": TOOL_NAME,
        "description": (
            "Answer a natural-language Earth-observation question with a DECISION-GRADE report "
            "computed from real Sentinel-2 data. The report has a quantified headline, "
            "change-vs-baseline with a significance test, an honest confidence level, a concrete "
            "recommended action, and reproducible data lineage. Use this when the user wants a "
            "defensible decision (act / do-not-act, is there stress / drought / burn / water change) "
            "rather than a raw map or a single number. Handles snapshot, change, trend, and anomaly "
            "analyses and resolves the area of interest from the place named in the question. "
            "Pass the user's full question verbatim, including the location and any time range or "
            "comparison, as `query`. If the user gives explicit coordinates, pass them as `aoi` "
            "([west, south, east, north] in degrees) — that overrides place-name resolution and "
            "avoids geocoding errors."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The full natural-language question, including the place/area of interest "
                        "and any time window or baseline comparison."
                    ),
                },
                "aoi": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": (
                        "Optional explicit bounding box [west, south, east, north] in WGS84 "
                        "degrees. When given, geocoding is skipped and this area is used verbatim."
                    ),
                },
                "recipe_id": {
                    "type": "string",
                    "description": (
                        "Optional method cell to run, as '{variable}_{analysis_type}' "
                        "(variable ∈ ndvi/ndwi/ndmi/nbr/evi; analysis_type ∈ "
                        "snapshot/change/trend/anomaly), e.g. 'ndvi_change'. Pins the method "
                        "explicitly instead of inferring it from the question."
                    ),
                },
                "window": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional explicit analysis window [start, end] as ISO dates "
                        "(e.g. ['2024-01-01','2024-06-30']). Overrides any date parsed from the question."
                    ),
                },
                "thresholds": {
                    "type": "object",
                    "additionalProperties": {"type": "number"},
                    "description": (
                        "Optional per-run overrides for recipe decision gates, keyed by gate name "
                        "(e.g. {\"stress_dispatch_delta\": -0.15}). Absent gates use the recipe defaults."
                    ),
                },
            },
            "required": ["query"],
        },
    }
]


def create_decision_query_tools(config: Any = None) -> Dict[str, Callable]:
    """Return the handler map for the decision-query tool.

    The handler runs the synchronous pipeline off the event loop and returns the
    decision report as markdown (or the clarification prompt when the AOI is missing).
    use_llm is off: the rule-based parser is authoritative and avoids a nested model call.
    """

    async def _handler(args: Dict[str, Any]) -> Dict[str, Any]:
        query = str((args or {}).get("query", "")).strip()
        if not query:
            return {"content": [{"type": "text",
                                 "text": json.dumps({"error": "query is required"})}]}

        args = args or {}
        aoi_bbox = args.get("aoi") or None
        recipe_id = args.get("recipe_id") or None
        window = args.get("window") or None
        thresholds = args.get("thresholds") or None
        out = await asyncio.to_thread(run_query, query, None, False, True,
                                      aoi_bbox, recipe_id, window, thresholds)

        # Method_clarification (ill-posed method) returns report=None too.
        clarification = out.intent.clarification_needed or out.intent.method_clarification
        if clarification:
            text = "# Clarification needed\n\n" + clarification
            return {"content": [{"type": "text", "text": text}]}

        return {"content": [{"type": "text", "text": out.report.to_markdown()}]}

    return {TOOL_NAME: _handler}
