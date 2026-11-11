# ABOUTME: Analysis strategy planner tool. Maps natural language EO queries
# to structured analysis strategies (domain, indices, temporal approach,
# tool sequence). Includes confidence scoring, multi-intent detection,
# clarification question builder, and ClarificationNeed dataclass.
# Uses domain_knowledge.py for knowledge base.

"""
Analysis strategy planner for Earth Observation queries.

Maps user queries to structured strategies using keyword matching and
the domain_knowledge.py knowledge base. Pure logic — no HTTP calls,
no file I/O, no job execution.

Enhanced with:
- ClarificationNeed: structured description of missing user input
- Confidence scoring: per-component confidence (0.0-1.0)
- Multi-intent detection: recognizes compound queries
- Clarification builder: generates AskUserQuestion-compatible questions
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Lazy imports to avoid circular dependency:
#   strategy_tools → sdk.domain_knowledge → sdk.__init__ → sdk.client → strategy_tools


def _get_domain_knowledge():
    """Lazy import of domain_knowledge to avoid circular import."""
    from openeo_ai.sdk.domain_knowledge import (
        DOMAIN_CONTEXTS,
        _DOMAIN_KEYWORDS,
        classify_domain,
    )
    return DOMAIN_CONTEXTS, _DOMAIN_KEYWORDS, classify_domain


# --- ClarificationNeed ---

@dataclass
class ClarificationNeed:
    """Structured description of what needs user input."""
    component: str          # "spatial"|"temporal"|"analysis_type"|"collection"|"output"
    question: str           # Human-readable question
    options: List[Dict]     # [{label, description}]
    confidence: float       # 0.0-1.0
    impact: str             # "high"|"medium"|"low"
    default: Optional[str]  # Sensible default if user doesn't answer


# --- Temporal Strategy Detection ---

TEMPORAL_STRATEGIES = {
    "single_snapshot": {
        "description": "Single-date analysis (most recent cloud-free composite)",
        "temporal_hint": "Use 1-3 month window for cloud-free composite",
        "pattern": "PATTERN 1/3/4",
    },
    "two_date_comparison": {
        "description": "Before/after change detection using two temporal windows",
        "temporal_hint": "Define pre-event and post-event date ranges separately",
        "pattern": "PATTERN 6",
    },
    "monthly_composites": {
        "description": "Monthly aggregated composites over a period",
        "temporal_hint": "Use aggregate_temporal_period with period='month'",
        "pattern": "PATTERN 7",
    },
    "seasonal_comparison": {
        "description": "Same-season comparison across years",
        "temporal_hint": "Match seasons (e.g., Jun-Aug 2023 vs Jun-Aug 2024)",
        "pattern": "PATTERN 6",
    },
    "continuous_time_series": {
        "description": "Dense time series for trend analysis",
        "temporal_hint": "6+ months for trend, 12+ months for phenology",
        "pattern": "PATTERN 7 + temporal_trend_analysis",
    },
    "anomaly_detection": {
        "description": "Compare current period against historical baseline",
        "temporal_hint": "Need baseline (3+ years) and current window",
        "pattern": "PATTERN 7 + temporal_anomaly_detect",
    },
}

_TEMPORAL_KEYWORDS = {
    "two_date_comparison": [
        "before and after", "pre and post", "change detection",
        "compared to", "difference between", "pre-fire", "post-fire",
        "pre-flood", "post-flood", "before the", "after the",
    ],
    "monthly_composites": [
        "monthly", "each month", "per month", "month by month",
        "monthly composite", "monthly average",
    ],
    "seasonal_comparison": [
        "seasonal", "same season", "year over year", "yoy",
        "compared to last year", "vs last year", "annual comparison",
    ],
    "continuous_time_series": [
        "trend", "time series", "over time", "temporal profile",
        "progression", "evolution", "trajectory",
    ],
    "anomaly_detection": [
        "anomaly", "unusual", "abnormal", "deviation",
        "baseline", "historical average", "normal",
        "is this normal", "compared to average",
    ],
}


def _detect_temporal_strategy(query: str) -> str:
    """Detect temporal strategy from query keywords. Returns strategy key."""
    query_lower = query.lower()
    scores: dict[str, int] = {}
    for strategy, keywords in _TEMPORAL_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in query_lower)
        if score > 0:
            scores[strategy] = score
    if scores:
        return max(scores, key=lambda k: scores[k])
    return "single_snapshot"


# --- Multi-index detection ---

_INDEX_KEYWORDS = {
    "ndvi": ["ndvi", "vegetation index"],
    "ndwi": ["ndwi", "water index"],
    "ndmi": ["ndmi", "moisture index"],
    "ndbi": ["ndbi", "built-up index"],
    "nbr": ["nbr", "burn ratio"],
    "ndsi": ["ndsi", "snow index"],
    "evi": ["evi", "enhanced vegetation"],
    "bsi": ["bsi", "bare soil"],
    "savi": ["savi", "soil adjusted"],
    "ndre": ["ndre", "red edge"],
    "gndvi": ["gndvi", "green ndvi"],
    "ndci": ["ndci", "chlorophyll index"],
    "mndwi": ["mndwi", "modified water"],
    "lai": ["lai", "leaf area"],
}


def _detect_requested_indices(query: str) -> List[str]:
    """Extract explicitly mentioned indices from query."""
    query_lower = query.lower()
    found = []
    # Check longer names first to avoid substring collision
    for idx in sorted(_INDEX_KEYWORDS.keys(), key=len, reverse=True):
        for kw in _INDEX_KEYWORDS[idx]:
            if kw in query_lower and idx not in found:
                found.append(idx)
                break
    return found


# --- Complexity detection ---

def _assess_complexity(
    domain: Optional[str],
    temporal_strategy: str,
    requested_indices: List[str],
    query: str,
) -> str:
    """Classify query complexity: simple, moderate, complex."""
    query_lower = query.lower()
    complexity_score = 0

    # Multiple indices = more complex
    if len(requested_indices) >= 3:
        complexity_score += 2
    elif len(requested_indices) >= 2:
        complexity_score += 1

    # Temporal complexity
    if temporal_strategy in ("anomaly_detection", "continuous_time_series"):
        complexity_score += 2
    elif temporal_strategy in ("two_date_comparison", "seasonal_comparison"):
        complexity_score += 1

    # Multi-step keywords
    multi_step_kw = [
        "classify", "segment", "threshold", "categorize",
        "statistics", "histogram", "correlat", "compare",
        "and then", "followed by", "step by step",
    ]
    complexity_score += sum(1 for kw in multi_step_kw if kw in query_lower)

    if complexity_score >= 4:
        return "complex"
    elif complexity_score >= 2:
        return "moderate"
    return "simple"


# --- Confidence Scoring ---

def _score_domain_confidence(query: str, domain: Optional[str]) -> float:
    """Score confidence in domain classification.
    1.0=explicit domain keyword, 0.6=inferred from index, 0.3=weak signal, 0.0=none
    """
    if domain is None:
        return 0.0
    query_lower = query.lower()
    # Direct domain keyword match = high confidence
    _, _DOMAIN_KEYWORDS, _ = _get_domain_knowledge()
    domain_keywords = _DOMAIN_KEYWORDS.get(domain, [])
    direct_matches = sum(1 for kw in domain_keywords if kw in query_lower)
    if direct_matches >= 2:
        return 1.0
    if direct_matches == 1:
        return 0.8
    # Domain inferred from index only
    return 0.6


def _score_spatial_confidence(query: str) -> float:
    """Score confidence in spatial extent extraction.
    1.0=explicit coords, 0.8=clear place name, 0.3=vague reference, 0.0=none
    """
    # Check for coordinate patterns
    coord_pattern = r'-?\d+\.?\d*\s*,\s*-?\d+\.?\d*'
    if re.search(coord_pattern, query):
        return 1.0
    # Check for bbox-like patterns
    bbox_pattern = r'bbox|bounding box|extent'
    if re.search(bbox_pattern, query, re.IGNORECASE):
        return 0.9
    # Check for clear place names (capitalized words after spatial prepositions)
    place_pattern = r'\b(?:in|near|around|of|at|for)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)'
    if re.search(place_pattern, query):
        return 0.8
    # Vague spatial references
    vague_spatial = ["this area", "the region", "here", "there", "my area", "the site"]
    if any(vs in query.lower() for vs in vague_spatial):
        return 0.3
    return 0.0


def _score_temporal_confidence(query: str, strategy: str) -> float:
    """Score confidence in temporal extent extraction.
    1.0=explicit dates, 0.7=NL time reference, 0.3=relative time, 0.0=none
    """
    query_lower = query.lower()
    # ISO date pattern
    if re.search(r'\d{4}-\d{2}-\d{2}', query):
        return 1.0
    # Month + year (e.g. "January 2024", "Jan 2024")
    if re.search(
        r'(?:january|february|march|april|may|june|july|august|september|'
        r'october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|'
        r'nov|dec)\s+\d{4}',
        query_lower,
    ):
        return 0.9
    # Year only
    if re.search(r'\b20[12]\d\b', query):
        return 0.7
    # Relative time ("last month", "past year", "recent")
    relative_patterns = [
        "last month", "past year", "last year", "recent",
        "this year", "last 6 months", "past 3 months",
    ]
    if any(rp in query_lower for rp in relative_patterns):
        return 0.7
    # Seasonal references
    season_patterns = [
        "summer", "winter", "spring", "autumn", "fall",
        "monsoon", "dry season", "wet season", "growing season",
    ]
    if any(sp in query_lower for sp in season_patterns):
        return 0.5
    # Strategy implies temporal need but no date given
    if strategy != "single_snapshot":
        return 0.3
    return 0.0


def _score_index_confidence(query: str, indices: List[str]) -> float:
    """Score confidence in index selection.
    1.0=explicit name, 0.7=unambiguous intent, 0.4=ambiguous, 0.0=none
    """
    if not indices:
        return 0.0
    query_lower = query.lower()
    # Check if index was explicitly named
    for idx in indices:
        if idx.lower() in query_lower:
            return 1.0
    # Index inferred from domain but not named
    return 0.5


def _score_output_confidence(query: str) -> float:
    """Score confidence in output type.
    0.8+=explicit output request, 0.5=inferred, 0.3=ambiguous
    """
    query_lower = query.lower()
    explicit_outputs = {
        "map": ["show map", "display map", "visualize", "show on map", "map view"],
        "statistics": ["statistics", "stats", "numbers", "values", "calculate"],
        "time_series": ["time series", "chart", "graph", "plot", "trend line"],
        "comparison": ["compare", "side by side", "difference"],
        "download": ["download", "export", "save as", "geotiff"],
    }
    for output_type, keywords in explicit_outputs.items():
        if any(kw in query_lower for kw in keywords):
            return 0.9
    # Default to map visualization (most common)
    return 0.5


# --- Multi-Intent Detection ---

COMPOUND_INTENTS = {
    "change_detection": {
        "keywords": [
            "change", "before after", "before and after",
            "difference", "loss", "gain", "changed",
        ],
    },
    "time_series": {
        "keywords": [
            "trend", "over time", "monthly", "each month",
            "temporal profile", "progression",
        ],
    },
    "comparison": {
        "keywords": [
            "compare", "versus", "vs", "difference between", "against",
        ],
    },
    "classification": {
        "keywords": [
            "classify", "land cover", "land use", "segment",
            "categorize", "classes",
        ],
    },
    "monitoring": {
        "keywords": [
            "monitor", "track", "watch", "observe", "ongoing",
        ],
    },
}


def _detect_multiple_intents(query: str) -> List[Dict[str, str]]:
    """Detect when a query contains multiple analysis intents."""
    query_lower = query.lower()
    intents = []
    for intent_type, meta in COMPOUND_INTENTS.items():
        for kw in meta["keywords"]:
            if kw in query_lower:
                intents.append({"type": intent_type, "keyword": kw})
                break
    return intents


# --- Clarification Question Builder ---

_CLARIFICATION_TEMPLATES = {
    "spatial": {
        "question": "What area would you like to analyze?",
        "options": [
            {"label": "Enter location name", "description": "e.g., Berlin, Kerala, Amazon Basin"},
            {"label": "Enter coordinates", "description": "Provide bounding box as west,south,east,north"},
            {"label": "Global/regional", "description": "Broad area analysis (may be slow)"},
        ],
    },
    "temporal": {
        "question": "What time period should I analyze?",
        "options": [
            {"label": "Recent (last 3 months)", "description": "Most recent cloud-free composite"},
            {"label": "Past year", "description": "Full year analysis from 12 months ago to now"},
            {"label": "Specific dates", "description": "Provide exact start and end dates"},
        ],
    },
    "analysis_type": {
        "question": "What type of analysis would you like?",
        "options": [
            {"label": "Vegetation health (NDVI)", "description": "Assess plant health and greenness"},
            {"label": "Water detection (NDWI)", "description": "Map water bodies and moisture"},
            {"label": "Change detection", "description": "Compare two time periods for changes"},
            {"label": "Let me describe", "description": "I'll provide more details"},
        ],
    },
    "index": {
        "question": "Which spectral index should I use?",
        "options": [],  # Populated dynamically from domain
    },
    "output": {
        "question": "What output format do you prefer?",
        "options": [
            {"label": "Interactive map (Recommended)", "description": "View results on a web map"},
            {"label": "Statistics only", "description": "Get numerical summary without visualization"},
            {"label": "Download GeoTIFF", "description": "Save result as a downloadable raster file"},
        ],
    },
}


def _build_clarification_questions(
    components_needing_clarification: List[str],
    query: str,
    domain: Optional[str],
) -> List[Dict]:
    """Build AskUserQuestion-compatible questions for missing components."""
    questions = []
    for component in components_needing_clarification[:2]:  # Max 2 questions
        template = _CLARIFICATION_TEMPLATES.get(component)
        if not template:
            continue
        q = {
            "question": template["question"],
            "header": component[:12].title(),
            "options": list(template["options"]),  # copy
            "multiSelect": False,
        }
        # Dynamic option population for index
        if component == "index" and domain:
            DOMAIN_CONTEXTS, _, _ = _get_domain_knowledge()
            ctx = DOMAIN_CONTEXTS.get(domain, {})
            indices = ctx.get("indices", [])[:4]
            q["options"] = [
                {"label": idx, "description": f"Recommended for {domain} analysis"}
                for idx in indices
            ]
        questions.append(q)
    return questions


# --- Tool sequence builder ---

def _build_tool_sequence(
    domain: Optional[str],
    temporal_strategy: str,
    requested_indices: List[str],
    complexity: str,
    query: str,
) -> List[Dict[str, str]]:
    """Build ordered tool call sequence with rationale."""
    steps = []
    query_lower = query.lower()

    # Step 1: Always start with collection recommendation
    steps.append({
        "tool": "recommend_collection",
        "reason": "Select optimal satellite collection for this domain",
    })

    # Step 2: Resolve location if mentioned
    location_kw = [
        "in ", "near ", "around ", "of ", "at ", "for ",
    ]
    if any(kw in query_lower for kw in location_kw):
        steps.append({
            "tool": "openeo_resolve_location",
            "reason": "Geocode the mentioned location to bbox coordinates",
        })

    # Step 3: Parse temporal extent
    steps.append({
        "tool": "openeo_parse_temporal",
        "reason": "Convert date references to ISO date range",
    })

    # Step 4: Quality check
    steps.append({
        "tool": "openeo_quality_metrics",
        "reason": "Verify data availability and cloud cover for AOI/dates",
    })

    # Step 5: Generate process graph
    if len(requested_indices) >= 2:
        steps.append({
            "tool": "openeo_generate_graph",
            "reason": f"Generate multi-index graph for {', '.join(requested_indices).upper()}",
            "hint": f"Use description: 'multi-index {' and '.join(requested_indices)}'",
        })
    elif temporal_strategy == "two_date_comparison":
        steps.append({
            "tool": "openeo_generate_graph",
            "reason": "Generate change detection graph (two temporal windows)",
            "hint": "Use description: 'change detection' with appropriate index",
        })
    else:
        default_idx = "ndvi"
        DOMAIN_CONTEXTS, _, _ = _get_domain_knowledge()
        if domain and DOMAIN_CONTEXTS.get(domain, {}).get("default_index"):
            default_idx = DOMAIN_CONTEXTS[domain]["default_index"].lower()
        steps.append({
            "tool": "openeo_generate_graph",
            "reason": f"Generate {default_idx.upper()} process graph",
        })

    # Step 6: Create and run job
    steps.append({"tool": "openeo_create_job", "reason": "Submit process graph as batch job"})
    steps.append({"tool": "openeo_start_job", "reason": "Start job execution"})
    steps.append({"tool": "openeo_get_results", "reason": "Download results when complete"})

    # Step 7: Post-processing — result_inspect populates Data Table + Visualization panels
    steps.append({
        "tool": "result_inspect",
        "reason": "Inspect result file — populates Data Table and Visualization panels",
    })

    # Step 7b: Domain-aware interpretation (MANDATORY per SYSTEM_PROMPT)
    steps.append({
        "tool": "interpret_result",
        "reason": "Domain-aware interpretation with grades and category breakdown",
    })

    # Step 7c: Quality gate (MANDATORY per SYSTEM_PROMPT)
    steps.append({
        "tool": "assess_result_quality",
        "reason": "Mandatory quality gate — validate result against domain expectations",
    })

    # Threshold classification if relevant
    classify_kw = ["classify", "segment", "threshold", "categorize", "areas where", "show me only"]
    if any(kw in query_lower for kw in classify_kw):
        steps.append({
            "tool": "threshold_classify",
            "reason": "Classify result into categories based on thresholds",
        })

    # Temporal tools if relevant
    if temporal_strategy == "continuous_time_series":
        steps.append({
            "tool": "temporal_trend_analysis",
            "reason": "Fit linear trend and compute slope/R-squared",
        })
    elif temporal_strategy == "anomaly_detection":
        steps.append({
            "tool": "temporal_anomaly_detect",
            "reason": "Compare current values against historical baseline",
        })

    # Correlation if comparing indices
    if len(requested_indices) >= 2 and "correlat" in query_lower:
        steps.append({
            "tool": "result_correlation",
            "reason": (
                f"Compute correlation between "
                f"{requested_indices[0].upper()} and {requested_indices[1].upper()}"
            ),
        })

    # Step 8: Visualization
    steps.append({
        "tool": "viz_show_map",
        "reason": "Display result on interactive map",
    })

    return steps


# --- Main planner function ---

def plan_analysis_strategy(query: str) -> Dict[str, Any]:
    """Plan analysis strategy from natural language query.

    Returns a structured JSON strategy with domain classification,
    recommended indices, temporal strategy, processing pattern,
    ordered tool sequence, confidence scores, detected intents,
    and clarification questions for low-confidence components.
    """
    # 1. Classify domain
    DOMAIN_CONTEXTS, _, classify_domain = _get_domain_knowledge()
    domain = classify_domain(query)
    domain_ctx = DOMAIN_CONTEXTS.get(domain, {}) if domain else {}

    # 2. Detect temporal strategy
    temporal_strategy = _detect_temporal_strategy(query)

    # 3. Detect explicitly requested indices
    requested_indices = _detect_requested_indices(query)

    # 4. Fill in defaults from domain if no explicit indices
    if not requested_indices and domain_ctx:
        default_idx = domain_ctx.get("default_index")
        if default_idx:
            requested_indices = [default_idx.lower()]

    # 5. Determine recommended indices (domain + explicit)
    recommended_indices = list(requested_indices)  # copy
    if domain_ctx and domain_ctx.get("indices"):
        for idx in domain_ctx["indices"]:
            idx_lower = idx.lower()
            if idx_lower not in recommended_indices:
                recommended_indices.append(idx_lower)

    # 6. Assess complexity
    complexity = _assess_complexity(domain, temporal_strategy, requested_indices, query)

    # 7. Build tool sequence
    tool_sequence = _build_tool_sequence(
        domain, temporal_strategy, requested_indices, complexity, query
    )

    # 8. Get temporal strategy metadata
    ts_meta = TEMPORAL_STRATEGIES.get(temporal_strategy, TEMPORAL_STRATEGIES["single_snapshot"])

    # 9. Build response
    strategy = {
        "domain": domain or "general",
        "confidence": "high" if domain else "low",
        "complexity": complexity,
        "indices": {
            "primary": requested_indices[:1] if requested_indices else [],
            "secondary": requested_indices[1:] if len(requested_indices) > 1 else [],
            "recommended": recommended_indices[:5],  # cap at 5
        },
        "collections": domain_ctx.get("collections", ["sentinel-2-l2a"]),
        "temporal_strategy": {
            "type": temporal_strategy,
            "description": ts_meta["description"],
            "hint": ts_meta["temporal_hint"],
            "processing_pattern": ts_meta["pattern"],
        },
        "colormap": domain_ctx.get("default_colormap", "viridis"),
        "thresholds": domain_ctx.get("thresholds", {}),
        "tool_sequence": tool_sequence,
        "tips": domain_ctx.get("tips", ""),
        "requirements": domain_ctx.get("requirements"),
    }

    # --- Confidence scoring ---
    confidence_scores = {
        "domain": _score_domain_confidence(query, domain),
        "spatial": _score_spatial_confidence(query),
        "temporal": _score_temporal_confidence(query, temporal_strategy),
        "index": _score_index_confidence(query, requested_indices),
        "output": _score_output_confidence(query),
    }

    # --- Multi-intent detection ---
    intents = _detect_multiple_intents(query)

    # --- Clarification check ---
    CLARIFICATION_THRESHOLD = 0.5
    low_confidence_components = [
        comp for comp, score in confidence_scores.items()
        if score < CLARIFICATION_THRESHOLD
    ]
    clarification_needed = len(low_confidence_components) > 0
    clarification_questions = []
    if clarification_needed:
        clarification_questions = _build_clarification_questions(
            low_confidence_components, query, domain
        )

    # Add enhanced fields to strategy dict
    strategy["confidence_scores"] = confidence_scores
    strategy["intents"] = intents
    strategy["clarification_needed"] = clarification_needed
    strategy["clarification_questions"] = clarification_questions

    return strategy


# --- Tool factory ---

STRATEGY_TOOL_DEFINITIONS = [
    {
        "name": "analysis_strategy_planner",
        "description": (
            "Plan the optimal analysis strategy for an Earth Observation query. "
            "Takes a natural language query and returns: domain classification, "
            "recommended indices, temporal strategy (single snapshot vs time series "
            "vs change detection vs anomaly detection), processing pattern, "
            "ordered tool sequence, interpretation thresholds, and tips. "
            "Also returns per-component confidence scores (0.0-1.0), detected "
            "intents, and clarification questions for low-confidence components. "
            "Use this FIRST for complex or ambiguous queries to determine the "
            "best approach before executing individual tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The user's natural language Earth Observation query. "
                        "Examples: 'assess drought impact in Rajasthan over the last year', "
                        "'compare NDVI before and after the 2024 wildfire near LA', "
                        "'monitor crop health trends for the past 6 months in Punjab'"
                    ),
                },
            },
            "required": ["query"],
        },
    },
]


def create_strategy_tools() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Create strategy planner tools.

    Returns:
        Tuple of (tool_handlers_dict, tool_definitions_list)
    """
    async def _plan_strategy(args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query", "")
        if not query:
            return {
                "content": [{"type": "text", "text": "Error: 'query' parameter is required."}]
            }
        strategy = plan_analysis_strategy(query)
        return {
            "content": [{"type": "text", "text": json.dumps(strategy, indent=2)}]
        }

    tools_dict = {
        "analysis_strategy_planner": _plan_strategy,
    }

    return tools_dict, STRATEGY_TOOL_DEFINITIONS
