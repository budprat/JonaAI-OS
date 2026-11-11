# ABOUTME: Golden dataset of reference JonaAI queries for regression testing (Phase G).

"""
Golden dataset — ~20 reference queries covering JonaAI's major use cases.

Used by:
    - scripts/create_golden_dataset.py (uploads to Langfuse as a dataset)
    - openeo_ai.evals.run_experiment (runs each query and scores the trace)
    - .github/workflows/evals.yml (CI gate on PRs)

Each entry describes:
    query            – natural-language prompt sent through the chat
    category         – thematic bucket for filtering (ndvi, change, multiband…)
    expected_tools   – tool names the query should trigger at least once
    expected_output  – what the final response should look like
    notes            – reviewer hints / why this case is important

Keep the dataset curated and small. It's a smoke test, not exhaustive.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List


@dataclass(frozen=True)
class GoldenQuery:
    """One reference query + its expectations."""
    id: str
    category: str
    query: str
    expected_tools: List[str]
    expected_output: str
    notes: str = ""
    expected_collection: str = ""
    expected_bbox: tuple = ()
    expected_temporal: tuple = ()
    # The tier this query should route to (simple|moderate|complex). Empty = unscored.
    # One shared field so the misroute-rate metric and persona-lift harness don't collide.
    expected_tier: str = ""

    def as_langfuse_input(self) -> dict:
        """Serialize for Langfuse create_dataset_item(input=...)."""
        return {"query": self.query, "category": self.category}

    def as_langfuse_expected(self) -> dict:
        """Serialize for Langfuse create_dataset_item(expected_output=...)."""
        return {
            "expected_tools": self.expected_tools,
            "expected_output": self.expected_output,
        }

    def as_metadata(self) -> dict:
        """Serialize for create_dataset_item(metadata=...)."""
        return {"notes": self.notes}


GOLDEN_QUERIES: List[GoldenQuery] = [
    # ── Basic NDVI (smoke test) ─────────────────────────────────────────
    GoldenQuery(
        id="ndvi-kharg-2024q1",
        category="ndvi",
        query="Show NDVI for Kharg Island in January 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="map visualization with ndvi colormap",
        notes="The minimum viable pipeline. If this breaks everything breaks.",
    ),
    GoldenQuery(
        id="ndvi-paris-june",
        category="ndvi",
        query="What does NDVI look like for Paris in June 2024?",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="map with paris bounds and vegetation index",
    ),
    GoldenQuery(
        id="ndvi-cerrado-2023",
        category="ndvi",
        query="Vegetation health in the Brazilian Cerrado across all of 2023",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="temporal NDVI aggregation",
    ),

    # ── Change detection ─────────────────────────────────────────────────
    GoldenQuery(
        id="change-rio-vegetation",
        category="change",
        query="Compare vegetation in Rio state between 2022 and 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="before/after comparison or difference raster",
        notes="Tests multi-temporal handling.",
    ),
    GoldenQuery(
        id="change-amazon-2020-2024",
        category="change",
        query="Has the Amazon rainforest in Rondonia shrunk between 2020 and 2024?",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="forest change analysis with clear answer",
    ),

    # ── Multi-band / false color ─────────────────────────────────────────
    GoldenQuery(
        id="false-color-tanganyika",
        category="multiband",
        query="Make a false color composite of Lake Tanganyika",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="RGB map with false color bands (nir/red/green)",
    ),
    GoldenQuery(
        id="true-color-dubai",
        category="multiband",
        query="Show me a true color satellite image of Dubai from 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="RGB map with natural color bands",
    ),

    # ── Water indices ────────────────────────────────────────────────────
    GoldenQuery(
        id="ndwi-caspian",
        category="water",
        query="Calculate NDWI for the north Caspian sea for September 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="NDWI map highlighting water bodies",
    ),
    GoldenQuery(
        id="water-change-aral",
        category="water",
        query="Has the Aral Sea continued to shrink? Compare 2020 to 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="water mask change",
    ),

    # ── Quality metrics ──────────────────────────────────────────────────
    GoldenQuery(
        id="quality-cloud-paris",
        category="quality",
        query="What's the cloud cover for Sentinel-2 over Paris in the last week?",
        expected_tools=["openeo_quality_metrics"],
        expected_output="cloud cover percentage and observation count",
    ),
    GoldenQuery(
        id="quality-coverage-iran",
        category="quality",
        query="How many Sentinel-2 observations are available over Iran in 2024?",
        expected_tools=["openeo_quality_metrics"],
        expected_output="count + coverage stats",
    ),

    # ── Fire / burn scar ─────────────────────────────────────────────────
    GoldenQuery(
        id="nbr-california",
        category="fire",
        query="Map burn scars from the 2024 California wildfires using NBR",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="NBR or dNBR burn severity map",
    ),

    # ── Urban ────────────────────────────────────────────────────────────
    GoldenQuery(
        id="ndbi-mumbai",
        category="urban",
        query="Show built-up area expansion in Mumbai using NDBI, 2020 vs 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="NDBI difference or overlay",
    ),

    # ── Agriculture ──────────────────────────────────────────────────────
    GoldenQuery(
        id="evi-corn-belt",
        category="agriculture",
        query="Map EVI for the US corn belt during the 2024 growing season",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="EVI map with vegetation vigor",
    ),

    # ── SAR (Sentinel-1) ─────────────────────────────────────────────────
    GoldenQuery(
        id="sar-flood-bangladesh",
        category="sar",
        query="Detect flood extent in Bangladesh using Sentinel-1 SAR for July 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="SAR-derived flood mask",
        notes="Tests Sentinel-1 collection routing.",
    ),

    # ── Error / clarification paths ──────────────────────────────────────
    GoldenQuery(
        id="error-gibberish-location",
        category="error",
        query="Show me NDVI for Atlantis in the year 2525",
        expected_tools=[],
        expected_output="clarification or graceful error — not a silent wrong answer",
        notes="Tests that invalid location/time triggers clarification, not garbage.",
    ),
    GoldenQuery(
        id="error-ambiguous-index",
        category="error",
        query="Give me the index for this area",
        expected_tools=[],
        expected_output="clarification asking which index + which area",
        notes="Tests clarification on ambiguous input.",
    ),

    # ── Meta / discovery ─────────────────────────────────────────────────
    GoldenQuery(
        id="discovery-collections",
        category="discovery",
        query="What satellite data collections are available?",
        expected_tools=["openeo_discovery_collections"],
        expected_output="list of STAC collections with brief descriptions",
    ),
    GoldenQuery(
        id="discovery-bands-sentinel2",
        category="discovery",
        query="What bands does Sentinel-2 have?",
        expected_tools=["openeo_discovery_collections"],
        expected_output="list of S2 bands with descriptions",
    ),

    # ── Geocoding / region resolution ────────────────────────────────────
    GoldenQuery(
        id="geocode-kruger",
        category="geocoding",
        query="NDVI for Kruger National Park in dry season 2024",
        expected_tools=["openeo_raw_graph", "openeo_execute"],
        expected_output="NDVI map with kruger park bounds correctly resolved",
        notes="Tests geocoding of named protected areas.",
    ),
]


def query_by_id(query_id: str) -> GoldenQuery:
    """Look up a query by its id. Raises KeyError if missing."""
    for q in GOLDEN_QUERIES:
        if q.id == query_id:
            return q
    raise KeyError(query_id)


def as_dicts() -> list[dict]:
    """Serialize all queries as plain dicts (for JSON/reporting)."""
    return [asdict(q) for q in GOLDEN_QUERIES]
