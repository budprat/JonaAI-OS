# ABOUTME: Structured goal extraction tools for Earth Observation queries.
# Decomposes user queries into 5 components: what, where, when, which data, what output.
# Returns confidence scores per component and flags missing information.

"""
Structured goal extraction for Earth Observation queries.

Provides tools to decompose user queries into structured AnalysisGoal objects
with confidence scoring and completeness validation. Pure logic — no HTTP
calls, no file I/O, no job execution.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Lazy imports to avoid circular dependency:
#   goal_tools → sdk.domain_knowledge → sdk.__init__ → sdk.client → goal_tools


def _get_domain_knowledge():
    """Lazy import of domain_knowledge to avoid circular import."""
    from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS, classify_domain
    return DOMAIN_CONTEXTS, classify_domain


def _get_strategy_tools():
    """Lazy import of strategy_tools functions."""
    from openeo_ai.tools.strategy_tools import (
        _detect_requested_indices,
        _detect_temporal_strategy,
        _assess_complexity,
        _score_domain_confidence,
        _score_spatial_confidence,
        _score_temporal_confidence,
        _score_index_confidence,
        _score_output_confidence,
    )
    return (
        _detect_requested_indices,
        _detect_temporal_strategy,
        _assess_complexity,
        _score_domain_confidence,
        _score_spatial_confidence,
        _score_temporal_confidence,
        _score_index_confidence,
        _score_output_confidence,
    )


@dataclass
class AnalysisGoal:
    """Structured decomposition of a user's EO analysis request."""
    # Component 1: What?
    analysis_type: str = ""                    # "vegetation_index"|"water_mapping"|"change_detection"|...
    index: Optional[str] = None                # "ndvi"|"evi"|"ndwi"|... (None if not index-based)

    # Component 2: Where?
    spatial_extent: Optional[Dict] = None      # {west, south, east, north} or None
    location_name: Optional[str] = None        # "Berlin", "Kerala" — for resolve_location

    # Component 3: When?
    temporal_extent: Optional[List[str]] = None # [start, end] ISO dates
    temporal_strategy: str = "single_snapshot"  # From TEMPORAL_STRATEGIES

    # Component 4: Which data?
    collection: Optional[str] = None           # "sentinel-2-l2a"|"landsat-c2-l2"|...
    bands: List[str] = field(default_factory=list)

    # Component 5: What output?
    output_type: str = "map"                   # "map"|"statistics"|"time_series"|"comparison"|"download"

    # Metadata
    domain: str = ""                           # From classify_domain
    complexity: str = "simple"                 # "simple"|"moderate"|"complex"
    confidence: Dict[str, float] = field(default_factory=dict)  # Per-component confidence
    missing_components: List[str] = field(default_factory=list)  # Components needing resolution

    def to_dict(self) -> dict:
        return {
            "analysis_type": self.analysis_type,
            "index": self.index,
            "spatial_extent": self.spatial_extent,
            "location_name": self.location_name,
            "temporal_extent": self.temporal_extent,
            "temporal_strategy": self.temporal_strategy,
            "collection": self.collection,
            "bands": self.bands,
            "output_type": self.output_type,
            "domain": self.domain,
            "complexity": self.complexity,
            "confidence": self.confidence,
            "missing_components": self.missing_components,
        }


# --- Analysis type classification ---

_ANALYSIS_TYPE_KEYWORDS = {
    "vegetation_index": ["ndvi", "evi", "savi", "vegetation index", "plant health", "greenness", "vegetation health"],
    "water_mapping": ["ndwi", "mndwi", "water detection", "water body", "water extent", "flood map"],
    "change_detection": ["change detection", "before and after", "pre and post", "difference", "change over time"],
    "burn_severity": ["burn", "fire severity", "dnbr", "burn severity", "wildfire damage"],
    "urban_mapping": ["ndbi", "built-up", "urban extent", "urban sprawl", "impervious surface"],
    "snow_mapping": ["ndsi", "snow cover", "snow extent", "snowpack"],
    "soil_analysis": ["bsi", "bare soil", "soil index", "erosion"],
    "moisture_analysis": ["ndmi", "moisture", "water stress", "drought"],
    "time_series": ["trend", "time series", "over time", "temporal profile", "phenology"],
    "classification": ["classify", "land cover", "land use", "segment"],
}

def _classify_analysis_type(query: str, domain: Optional[str], indices: List[str]) -> str:
    """Classify the analysis type from query, domain, and detected indices."""
    query_lower = query.lower()

    # Check direct keyword matches (longer first to avoid substring collision)
    scores: Dict[str, int] = {}
    for atype, keywords in _ANALYSIS_TYPE_KEYWORDS.items():
        score = sum(1 for kw in sorted(keywords, key=len, reverse=True) if kw in query_lower)
        if score > 0:
            scores[atype] = score

    if scores:
        return max(scores, key=lambda k: scores[k])

    # Infer from detected indices
    if indices:
        idx = indices[0].lower()
        index_to_type = {
            "ndvi": "vegetation_index", "evi": "vegetation_index", "savi": "vegetation_index",
            "gndvi": "vegetation_index", "ndre": "vegetation_index", "lai": "vegetation_index",
            "ndwi": "water_mapping", "mndwi": "water_mapping",
            "nbr": "burn_severity", "dnbr": "burn_severity",
            "ndbi": "urban_mapping", "ndsi": "snow_mapping",
            "bsi": "soil_analysis", "ndmi": "moisture_analysis",
            "ndci": "water_mapping",
        }
        if idx in index_to_type:
            return index_to_type[idx]

    # Infer from domain
    domain_to_type = {
        "vegetation": "vegetation_index", "agriculture": "vegetation_index",
        "water": "water_mapping", "coastal": "water_mapping",
        "fire": "burn_severity", "urban": "urban_mapping",
        "snow": "snow_mapping", "soil": "soil_analysis",
        "drought": "moisture_analysis", "flood": "water_mapping",
        "deforestation": "change_detection", "climate": "time_series",
        "wetland": "water_mapping", "algal_bloom": "water_mapping",
        "mining": "change_detection",
    }
    if domain and domain in domain_to_type:
        return domain_to_type[domain]

    return ""


# --- Spatial extraction ---

def _extract_location_name(query: str) -> Optional[str]:
    """Extract a location name from the query using pattern matching."""
    # Pattern: "in/near/around/for/of [Capitalized Place Name]"
    pattern = r'\b(?:in|near|around|for|of|at)\s+([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+)*)'
    match = re.search(pattern, query)
    if match:
        name = match.group(1)
        # Filter out common false positives (analysis terms that start sentences)
        false_positives = {"NDVI", "EVI", "NDWI", "MNDWI", "NBR", "NDBI", "NDSI",
                          "Sentinel", "Landsat", "January", "February", "March",
                          "April", "May", "June", "July", "August", "September",
                          "October", "November", "December"}
        if name not in false_positives:
            return name
    return None

def _extract_coordinates(query: str) -> Optional[Dict[str, float]]:
    """Extract bbox coordinates from query."""
    # Pattern: numbers that look like coordinates
    bbox_pattern = r'(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)'
    match = re.search(bbox_pattern, query)
    if match:
        try:
            west, south, east, north = [float(x) for x in match.groups()]
            if -180 <= west <= 180 and -90 <= south <= 90:
                return {"west": west, "south": south, "east": east, "north": north}
        except ValueError:
            pass
    return None


# --- Output type detection ---

def _detect_output_type(query: str) -> str:
    """Detect desired output type from query."""
    query_lower = query.lower()
    output_keywords = {
        "statistics": ["statistics", "stats", "numbers", "values", "calculate", "compute"],
        "time_series": ["time series", "chart", "graph", "plot", "trend line", "over time"],
        "comparison": ["compare", "side by side", "versus", "vs"],
        "download": ["download", "export", "save as", "geotiff", "tiff"],
    }
    for otype, keywords in output_keywords.items():
        if any(kw in query_lower for kw in keywords):
            return otype
    return "map"


# --- Collection inference ---

def _infer_collection(domain: Optional[str], indices: List[str]) -> Optional[str]:
    """Infer the best collection from domain and indices."""
    DOMAIN_CONTEXTS, _ = _get_domain_knowledge()
    if domain:
        ctx = DOMAIN_CONTEXTS.get(domain, {})
        collections = ctx.get("collections", [])
        if collections:
            return collections[0]
    # Default for most indices
    if indices:
        return "sentinel-2-l2a"
    return None


# --- Main tool functions ---

async def extract_analysis_goal(args: Dict[str, Any]) -> Dict[str, Any]:
    """Extract structured goal from user query.

    Uses domain classification, index detection, temporal strategy detection,
    and spatial extraction to decompose query into AnalysisGoal.
    Returns confidence scores per component.
    """
    query = args.get("query", "")
    if not query:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "'query' parameter is required."})}]
        }

    # Lazy imports
    DOMAIN_CONTEXTS, classify_domain = _get_domain_knowledge()
    (
        _detect_requested_indices,
        _detect_temporal_strategy,
        _assess_complexity,
        _score_domain_confidence,
        _score_spatial_confidence,
        _score_temporal_confidence,
        _score_index_confidence,
        _score_output_confidence,
    ) = _get_strategy_tools()

    # 1. Classify domain
    domain = classify_domain(query)

    # 2. Detect indices
    indices = _detect_requested_indices(query)
    domain_ctx = DOMAIN_CONTEXTS.get(domain, {}) if domain else {}
    if not indices and domain_ctx:
        default_idx = domain_ctx.get("default_index")
        if default_idx:
            indices = [default_idx.lower()]

    # 3. Detect temporal strategy
    temporal_strategy = _detect_temporal_strategy(query)

    # 4. Classify analysis type
    analysis_type = _classify_analysis_type(query, domain, indices)

    # 5. Extract spatial info
    location_name = _extract_location_name(query)
    spatial_extent = _extract_coordinates(query)

    # 6. Detect output type
    output_type = _detect_output_type(query)

    # 7. Infer collection
    collection = _infer_collection(domain, indices)

    # 8. Assess complexity
    complexity = _assess_complexity(domain, temporal_strategy, indices, query)

    # 9. Score confidence per component
    confidence = {
        "domain": _score_domain_confidence(query, domain),
        "spatial": _score_spatial_confidence(query),
        "temporal": _score_temporal_confidence(query, temporal_strategy),
        "index": _score_index_confidence(query, indices),
        "output": _score_output_confidence(query),
    }

    # 10. Identify missing components
    THRESHOLD = 0.5
    missing = [comp for comp, score in confidence.items() if score < THRESHOLD]

    # Build goal
    goal = AnalysisGoal(
        analysis_type=analysis_type,
        index=indices[0] if indices else None,
        spatial_extent=spatial_extent,
        location_name=location_name,
        temporal_strategy=temporal_strategy,
        collection=collection,
        output_type=output_type,
        domain=domain or "",
        complexity=complexity,
        confidence=confidence,
        missing_components=missing,
    )

    return {
        "content": [{"type": "text", "text": json.dumps(goal.to_dict(), indent=2)}]
    }


async def validate_goal_completeness(args: Dict[str, Any]) -> Dict[str, Any]:
    """Check if all required components are resolved before proceeding.

    Returns pass/fail per component with reasons.
    Must be called AFTER goal extraction and BEFORE graph generation.
    """
    goal = args.get("goal", {})
    if not goal:
        return {
            "content": [{"type": "text", "text": json.dumps({"error": "'goal' parameter is required."})}]
        }

    checks = {}
    all_pass = True

    # Check analysis_type
    if goal.get("analysis_type"):
        checks["analysis_type"] = {"status": "pass", "value": goal["analysis_type"]}
    else:
        checks["analysis_type"] = {"status": "fail", "reason": "Analysis type could not be determined. Please specify what you want to analyze."}
        all_pass = False

    # Check spatial
    has_spatial = goal.get("spatial_extent") or goal.get("location_name")
    if has_spatial:
        loc = goal.get("location_name") or str(goal.get("spatial_extent"))
        checks["spatial"] = {"status": "pass", "value": loc}
    else:
        checks["spatial"] = {"status": "fail", "reason": "No location or spatial extent specified. Please provide an area to analyze."}
        all_pass = False

    # Check temporal
    if goal.get("temporal_extent"):
        checks["temporal"] = {"status": "pass", "value": goal["temporal_extent"]}
    elif goal.get("temporal_strategy") != "single_snapshot":
        checks["temporal"] = {"status": "warning", "reason": f"Strategy '{goal.get('temporal_strategy')}' selected but no dates provided. Will use defaults."}
    else:
        checks["temporal"] = {"status": "warning", "reason": "No temporal extent specified. Will use recent 3-month window."}

    # Check collection
    if goal.get("collection"):
        from openeo_ai.tools.validation_tools import COLLECTION_BANDS
        if goal["collection"] in COLLECTION_BANDS:
            checks["collection"] = {"status": "pass", "value": goal["collection"]}
        else:
            checks["collection"] = {"status": "warning", "reason": f"Collection '{goal['collection']}' not in known collections. May still work."}
    else:
        checks["collection"] = {"status": "warning", "reason": "No collection specified. Will default to sentinel-2-l2a."}

    # Check for conflicts
    conflicts = []
    index = (goal.get("index") or "").upper()
    domain = goal.get("domain", "")

    # Water index + fire domain conflict
    water_indices = {"NDWI", "MNDWI"}
    fire_domains = {"fire"}
    if index in water_indices and domain in fire_domains:
        conflicts.append(f"Water index ({index}) used with fire domain. Consider NBR or dNBR instead.")

    # Built-up index + vegetation domain
    urban_indices = {"NDBI"}
    veg_domains = {"vegetation", "agriculture"}
    if index in urban_indices and domain in veg_domains:
        conflicts.append(f"Urban index ({index}) used with {domain} domain. Consider NDVI or EVI instead.")

    if conflicts:
        checks["conflicts"] = {"status": "warning", "issues": conflicts}

    result = {
        "complete": all_pass,
        "checks": checks,
        "goal": goal,
    }

    return {
        "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
    }


# --- Tool definitions ---

GOAL_TOOL_DEFINITIONS = [
    {
        "name": "extract_analysis_goal",
        "description": (
            "Extract a structured analysis goal from the user's natural language query. "
            "Decomposes into 5 components: analysis type, spatial extent, temporal extent, "
            "data collection, and output format. Returns confidence scores per component "
            "and flags missing components that need user clarification. "
            "Call this FIRST before any other analysis tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The user's natural language Earth Observation query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "validate_goal_completeness",
        "description": (
            "Validate that all required components of an analysis goal are resolved "
            "before proceeding to graph generation. Returns pass/fail per component "
            "with reasons. Call AFTER extract_analysis_goal and BEFORE graph generation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "object",
                    "description": "The AnalysisGoal dict from extract_analysis_goal",
                },
            },
            "required": ["goal"],
        },
    },
]


def create_goal_tools() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Create goal extraction tools.

    Returns:
        Tuple of (tool_handlers_dict, tool_definitions_list)
    """
    tools_dict = {
        "extract_analysis_goal": extract_analysis_goal,
        "validate_goal_completeness": validate_goal_completeness,
    }
    return tools_dict, GOAL_TOOL_DEFINITIONS
