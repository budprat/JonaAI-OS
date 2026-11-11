# ABOUTME: Spectral index catalog (search) + a truth-debt fence on calculate, which
# ABOUTME: refuses raw-DN computation and redirects to the verified reflectance paths.

"""
Spectral index tools for Claude SDK.

spectral_index_search: a built-in catalog of common remote sensing indices.
spectral_index_calculate: fenced — refuses computation (raw-DN math was
physically invalid) and returns the verified alternative instead.
"""

import json
from typing import Any, Dict


SPECTRAL_INDICES = {
    "NDVI": {"name": "Normalized Difference Vegetation Index", "formula": "(nir - red) / (nir + red)", "bands": ["nir", "red"], "category": "vegetation", "description": "Measures vegetation health and density", "range": [-1, 1]},
    "EVI": {"name": "Enhanced Vegetation Index", "formula": "2.5 * ((nir - red) / (nir + 6*red - 7.5*blue + 1))", "bands": ["nir", "red", "blue"], "category": "vegetation", "description": "Improved vegetation index reducing atmospheric effects", "range": [-1, 1]},
    "SAVI": {"name": "Soil Adjusted Vegetation Index", "formula": "((nir - red) / (nir + red + 0.5)) * 1.5", "bands": ["nir", "red"], "category": "vegetation", "description": "Minimizes soil brightness influences", "range": [-1, 1]},
    "NDWI": {"name": "Normalized Difference Water Index", "formula": "(green - nir) / (green + nir)", "bands": ["green", "nir"], "category": "water", "description": "Detects surface water bodies", "range": [-1, 1]},
    "MNDWI": {"name": "Modified NDWI", "formula": "(green - swir16) / (green + swir16)", "bands": ["green", "swir16"], "category": "water", "description": "Better water detection in urban areas", "range": [-1, 1]},
    "NBR": {"name": "Normalized Burn Ratio", "formula": "(nir - swir22) / (nir + swir22)", "bands": ["nir", "swir22"], "category": "fire", "description": "Assesses burn severity", "range": [-1, 1]},
    "dNBR": {"name": "Differenced NBR", "formula": "NBR_pre - NBR_post", "bands": ["nir", "swir22"], "category": "fire", "description": "Change in burn ratio between two dates", "range": [-2, 2]},
    "NDBI": {"name": "Normalized Difference Built-up Index", "formula": "(swir16 - nir) / (swir16 + nir)", "bands": ["swir16", "nir"], "category": "urban", "description": "Identifies built-up/urban areas", "range": [-1, 1]},
    "BSI": {"name": "Bare Soil Index", "formula": "((swir16 + red) - (nir + blue)) / ((swir16 + red) + (nir + blue))", "bands": ["swir16", "red", "nir", "blue"], "category": "soil", "description": "Detects bare soil areas", "range": [-1, 1]},
    "NDSI": {"name": "Normalized Difference Snow Index", "formula": "(green - swir16) / (green + swir16)", "bands": ["green", "swir16"], "category": "snow", "description": "Identifies snow and ice cover", "range": [-1, 1]},
    "MSAVI2": {"name": "Modified SAVI 2", "formula": "(2*nir + 1 - sqrt((2*nir+1)**2 - 8*(nir-red))) / 2", "bands": ["nir", "red"], "category": "vegetation", "description": "Soil-adjusted vegetation for sparse vegetation areas", "range": [-1, 1]},
    "GNDVI": {"name": "Green NDVI", "formula": "(nir - green) / (nir + green)", "bands": ["nir", "green"], "category": "vegetation", "description": "Sensitive to chlorophyll concentration", "range": [-1, 1]},
    "NDMI": {"name": "Normalized Difference Moisture Index", "formula": "(nir - swir16) / (nir + swir16)", "bands": ["nir", "swir16"], "category": "moisture", "description": "Assesses vegetation water content", "range": [-1, 1]},
    "LAI": {"name": "Leaf Area Index (approx)", "formula": "3.618 * EVI - 0.118", "bands": ["nir", "red", "blue"], "category": "vegetation", "description": "Approximates leaf area from EVI", "range": [0, 8]},
    "ARVI": {"name": "Atmospherically Resistant Vegetation Index", "formula": "(nir - (2*red - blue)) / (nir + (2*red - blue))", "bands": ["nir", "red", "blue"], "category": "vegetation", "description": "Resistant to atmospheric effects", "range": [-1, 1]},
    "CHL_RE": {"name": "Chlorophyll Red Edge", "formula": "(nir08 / red) - 1", "bands": ["nir08", "red"], "category": "vegetation", "description": "Chlorophyll content using red edge band", "range": [0, 20]},
    "NDRE": {"name": "Normalized Difference Red Edge", "formula": "(nir - rededge1) / (nir + rededge1)", "bands": ["nir", "rededge1"], "category": "vegetation", "description": "Sensitive to chlorophyll in dense canopy using red edge band (705nm)", "range": [-1, 1]},
}

SENTINEL2_BAND_MAP = {
    "blue": 1, "green": 2, "red": 3, "nir": 4,
    "nir08": 5, "swir16": 6, "swir22": 7,
    # Reason: Red Edge 1 (B05, 705nm) needed for NDRE computation
    "rededge1": 5,  # B05 (same physical band as nir08 in 10-band products)
}

# Truth-debt fence (NU decision 2026-07-23, priority-roadmap §6.3): computing these
# formulas on raw digital numbers — no BOA reflectance scaling, no SCL cloud mask —
# yields physically invalid values (EVI/SAVI-family constants assume reflectance in
# [0,1]; BOA offsets do not cancel in ratios). calculate refuses and redirects to the
# verified reflectance paths; only the 5 kernel indices have one today.
_KERNEL_INDICES = {"NDVI", "NDWI", "NDMI", "NBR", "EVI"}

_FENCE_CORE = (
    "spectral_index_calculate is disabled: it computed indices on raw digital numbers "
    "with no BOA reflectance scaling and no SCL cloud masking, so its values were not "
    "physically valid."
)


def _resolve_band_mapping(ds) -> Dict[str, int]:
    """Resolve band name to 1-indexed band number from file metadata or fallback."""
    descriptions = ds.descriptions
    if descriptions and any(d is not None for d in descriptions):
        mapping = {}
        for i, desc in enumerate(descriptions):
            if desc is not None:
                mapping[desc.lower().strip()] = i + 1
        if mapping:
            return mapping
    return dict(SENTINEL2_BAND_MAP)


def _compute_index(index_key: str, bands: Dict[str, Any]) -> Any:
    """Compute a spectral index given band arrays. Returns numpy array."""
    import numpy as np

    nir = bands.get("nir")
    red = bands.get("red")
    blue = bands.get("blue")
    green = bands.get("green")
    swir16 = bands.get("swir16")
    swir22 = bands.get("swir22")
    nir08 = bands.get("nir08")

    if index_key == "NDVI":
        return (nir - red) / (nir + red + 1e-10)
    elif index_key == "EVI":
        return 2.5 * ((nir - red) / (nir + 6.0 * red - 7.5 * blue + 1.0 + 1e-10))
    elif index_key == "SAVI":
        return ((nir - red) / (nir + red + 0.5 + 1e-10)) * 1.5
    elif index_key == "NDWI":
        return (green - nir) / (green + nir + 1e-10)
    elif index_key == "MNDWI":
        return (green - swir16) / (green + swir16 + 1e-10)
    elif index_key == "NBR":
        return (nir - swir22) / (nir + swir22 + 1e-10)
    elif index_key == "dNBR":
        return (nir - swir22) / (nir + swir22 + 1e-10)
    elif index_key == "NDBI":
        return (swir16 - nir) / (swir16 + nir + 1e-10)
    elif index_key == "BSI":
        return ((swir16 + red) - (nir + blue)) / ((swir16 + red) + (nir + blue) + 1e-10)
    elif index_key == "NDSI":
        return (green - swir16) / (green + swir16 + 1e-10)
    elif index_key == "MSAVI2":
        return (2.0 * nir + 1.0 - np.sqrt((2.0 * nir + 1.0) ** 2 - 8.0 * (nir - red))) / 2.0
    elif index_key == "GNDVI":
        return (nir - green) / (nir + green + 1e-10)
    elif index_key == "NDMI":
        return (nir - swir16) / (nir + swir16 + 1e-10)
    elif index_key == "LAI":
        evi = 2.5 * ((nir - red) / (nir + 6.0 * red - 7.5 * blue + 1.0 + 1e-10))
        return 3.618 * evi - 0.118
    elif index_key == "ARVI":
        rb = 2.0 * red - blue
        return (nir - rb) / (nir + rb + 1e-10)
    elif index_key == "CHL_RE":
        return (nir08 / (red + 1e-10)) - 1.0
    elif index_key == "NDRE":
        # Reason: NDRE = (NIR - Red Edge 1) / (NIR + Red Edge 1)
        rededge1 = bands.get("rededge1")
        return (nir - rededge1) / (nir + rededge1 + 1e-10)
    else:
        raise ValueError(f"Unknown index: {index_key}")


def create_spectral_index_tools(config=None) -> Dict[str, Any]:
    """
    Create spectral index tools dict for Claude SDK.

    Args:
        config: Optional configuration object.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _spectral_index_calculate(args: Dict[str, Any]) -> Dict[str, Any]:
        """Refuse raw-DN index computation and point at the verified paths."""
        canonical = {k.upper(): k for k in SPECTRAL_INDICES}
        raw = str((args or {}).get("index", "")).upper()
        index_key = canonical.get(raw, raw)

        if raw in _KERNEL_INDICES:
            redirect = (
                f" For {index_key}, use agentic_decision_query (decision-grade, "
                "reflectance-corrected, SCL cloud-masked) or an openEO process graph "
                "(openeo_generate_graph / openeo_algorithm), which computes on "
                "corrected reflectance in the backend."
            )
        elif index_key in SPECTRAL_INDICES:
            redirect = (
                f" {index_key} is not available on a verified reflectance path yet. "
                "Verified indices: NDVI, NDWI, NDMI, NBR, EVI — via "
                "agentic_decision_query or an openEO process graph."
            )
        else:
            available = ", ".join(sorted(SPECTRAL_INDICES.keys()))
            redirect = (
                f" Unknown index '{index_key}'. Catalog (see spectral_index_search): "
                f"{available}."
            )
        return {"content": [{"type": "text", "text": _FENCE_CORE + redirect}]}

    async def _spectral_index_search(args: Dict[str, Any]) -> Dict[str, Any]:
        """Search the spectral index database by keyword and/or category."""
        try:
            query = args.get("query", "").lower()
            category = args.get("category", "").lower() if args.get("category") else None

            candidates = dict(SPECTRAL_INDICES)

            if category:
                candidates = {
                    k: v for k, v in candidates.items()
                    if v["category"].lower() == category
                }

            if query:
                scored = []
                for key, info in candidates.items():
                    searchable = " ".join([
                        key.lower(),
                        info["name"].lower(),
                        info["description"].lower(),
                        info["category"].lower(),
                        info["formula"].lower(),
                    ])
                    tokens = query.split()
                    score = sum(1 for token in tokens if token in searchable)
                    if score > 0:
                        scored.append((key, info, score))
                scored.sort(key=lambda x: x[2], reverse=True)
                results = [(k, v) for k, v, _ in scored]
            else:
                results = list(candidates.items())

            if not results:
                msg = f"No spectral indices found matching query='{query}'"
                if category:
                    msg += f", category='{category}'"
                msg += f". Available categories: {', '.join(sorted(set(v['category'] for v in SPECTRAL_INDICES.values())))}"
                return {
                    "content": [{"type": "text", "text": msg}]
                }

            lines = [f"Found {len(results)} spectral index(es):", ""]
            result_list = []
            for key, info in results:
                lines.append(f"  {key}: {info['name']}")
                lines.append(f"    Formula: {info['formula']}")
                lines.append(f"    Bands: {', '.join(info['bands'])}")
                lines.append(f"    Category: {info['category']}")
                lines.append(f"    Description: {info['description']}")
                lines.append(f"    Value range: {info['range']}")
                lines.append("")
                result_list.append({
                    "key": key,
                    "name": info["name"],
                    "formula": info["formula"],
                    "bands": info["bands"],
                    "category": info["category"],
                    "description": info["description"],
                    "range": info["range"],
                })

            text = "\n".join(lines)
            return {
                "content": [
                    {"type": "text", "text": text},
                    {"type": "text", "text": json.dumps(result_list)},
                ]
            }

        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error searching spectral indices: {str(e)}"}]
            }

    return {
        "spectral_index_calculate": _spectral_index_calculate,
        "spectral_index_search": _spectral_index_search,
    }


SPECTRAL_INDEX_TOOL_DEFINITIONS = [
    {
        "name": "spectral_index_calculate",
        "description": "REFUSES computation (truth-debt fence): this tool applied index formulas to raw digital numbers — no BOA reflectance scaling, no SCL cloud masking — which produces physically invalid values, so it now refuses and returns the verified alternative instead. For decision-grade NDVI, NDWI, NDMI, NBR, or EVI use agentic_decision_query; for other index computation build an openEO process graph (openeo_generate_graph / openeo_algorithm), which computes on corrected reflectance in the backend. spectral_index_search remains available as the index catalog.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the input multi-band GeoTIFF file"
                },
                "index": {
                    "type": "string",
                    "description": "Spectral index name (e.g., 'NDVI', 'EVI', 'NDWI', 'NBR', 'SAVI', 'NDBI', 'BSI', 'NDSI', 'MSAVI2', 'GNDVI', 'NDMI', 'LAI', 'ARVI', 'CHL_RE', 'NDRE')"
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional output file path for the result GeoTIFF. If not provided, saves to the user's output folder"
                }
            },
            "required": ["file_path", "index"]
        }
    },
    {
        "name": "spectral_index_search",
        "description": "Search the built-in spectral index database by keyword and/or category. Returns matching indices with their names, formulas, required bands, and descriptions. Use this to find the right index for a given use case.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query describing the use case (e.g., 'vegetation health', 'water detection', 'burn severity', 'urban areas')"
                },
                "category": {
                    "type": "string",
                    "description": "Optional category filter: vegetation, water, fire, urban, soil, snow, or moisture",
                    "enum": ["vegetation", "water", "fire", "urban", "soil", "snow", "moisture"]
                }
            },
            "required": ["query"]
        }
    },
]
