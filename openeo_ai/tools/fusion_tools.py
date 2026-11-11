# ABOUTME: Multi-sensor data fusion tools for combining data from different satellite sources.
# Generates OpenEO process graphs that use merge_cubes + resample_cube_spatial for sensor fusion.

"""
Multi-Sensor Data Fusion tools for Claude SDK.

Provides tools for generating process graphs that combine data from
multiple satellite collections (e.g., Sentinel-2 + Sentinel-1,
Sentinel-2 + Landsat, optical + DEM overlay).

Uses existing OpenEO backend processes:
- load_collection (x2 for two sources)
- resample_cube_spatial (align resolutions)
- filter_temporal (align date ranges)
- merge_cubes (combine datacubes)
- save_result (output fused product)
"""

import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION_METADATA = {
    "sentinel-2-l2a": {
        "name": "Sentinel-2 L2A",
        "type": "optical",
        "resolution_m": 10,
        "revisit_days": 5,
        "default_bands": ["red", "green", "blue", "nir"],
        "all_bands": ["blue", "green", "red", "nir", "nir08", "swir16", "swir22", "scl"],
        "provider": "ESA",
    },
    "sentinel-2-l1c": {
        "name": "Sentinel-2 L1C",
        "type": "optical",
        "resolution_m": 10,
        "revisit_days": 5,
        "default_bands": ["red", "green", "blue", "nir"],
        "all_bands": ["blue", "green", "red", "nir", "nir08", "swir16", "swir22"],
        "provider": "ESA",
    },
    "landsat-c2-l2": {
        "name": "Landsat C2 L2",
        "type": "optical",
        "resolution_m": 30,
        "revisit_days": 16,
        "default_bands": ["red", "green", "blue", "nir08"],
        "all_bands": ["blue", "green", "red", "nir08", "swir16", "swir22", "lwir11"],
        "provider": "USGS",
    },
    "cop-dem-glo-30": {
        "name": "Copernicus DEM 30m",
        "type": "dem",
        "resolution_m": 30,
        "revisit_days": None,
        "default_bands": ["data"],
        "all_bands": ["data"],
        "provider": "ESA",
    },
    "cop-dem-glo-90": {
        "name": "Copernicus DEM 90m",
        "type": "dem",
        "resolution_m": 90,
        "revisit_days": None,
        "default_bands": ["data"],
        "all_bands": ["data"],
        "provider": "ESA",
    },
}

FUSION_PRESETS = {
    "optical_multires": {
        "name": "Multi-Resolution Optical Fusion",
        "description": "Combine high-res Sentinel-2 (10m) with long-record Landsat (30m) for extended temporal coverage at Sentinel-2 resolution.",
        "primary": "sentinel-2-l2a",
        "secondary": "landsat-c2-l2",
        "use_cases": ["long-term change detection", "gap filling", "historical baselines"],
        "notes": "Sentinel-2 used as spatial reference (10m). Landsat resampled to match.",
    },
    "optical_dem": {
        "name": "Optical + DEM Overlay",
        "description": "Combine optical imagery with elevation data for terrain-aware analysis (slope correction, altitude stratification).",
        "primary": "sentinel-2-l2a",
        "secondary": "cop-dem-glo-30",
        "use_cases": ["terrain correction", "altitude-based vegetation zones", "flood modeling", "slope analysis"],
        "notes": "DEM is static (no temporal dimension). Merged as additional band layer.",
    },
    "cross_sensor": {
        "name": "Cross-Sensor Harmonization",
        "description": "Combine two optical sensors for denser temporal sampling. Useful when one sensor has cloud cover gaps.",
        "primary": "sentinel-2-l2a",
        "secondary": "sentinel-2-l1c",
        "use_cases": ["cloud gap filling", "frequent monitoring", "rapid response"],
        "notes": "L2A (surface reflectance) is primary. L1C (TOA) used as fallback for gaps.",
    },
}


FUSION_TOOL_DEFINITIONS = [
    {
        "name": "fusion_multi_sensor",
        "description": (
            "Generate an OpenEO process graph that fuses data from two satellite collections. "
            "Handles resolution alignment (resample_cube_spatial), temporal alignment (filter_temporal), "
            "and merging (merge_cubes). Supports presets: 'optical_multires' (Sentinel-2 + Landsat), "
            "'optical_dem' (optical + elevation), 'cross_sensor' (S2-L2A + S2-L1C). "
            "Can also accept any two custom collection IDs. Returns a complete process graph ready for openeo_create_job."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "primary_collection": {
                    "type": "string",
                    "description": "Primary collection ID (used as spatial reference for resampling). E.g., 'sentinel-2-l2a'"
                },
                "secondary_collection": {
                    "type": "string",
                    "description": "Secondary collection ID to fuse with the primary. E.g., 'landsat-c2-l2', 'cop-dem-glo-30'"
                },
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box with west, south, east, north coordinates",
                    "properties": {
                        "west": {"type": "number"},
                        "south": {"type": "number"},
                        "east": {"type": "number"},
                        "north": {"type": "number"}
                    },
                    "required": ["west", "south", "east", "north"]
                },
                "temporal_extent": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Start and end dates [start, end] in ISO format. E.g., ['2024-01-01', '2024-06-30']"
                },
                "primary_bands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Bands to load from primary collection. If omitted, uses default bands."
                },
                "secondary_bands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Bands to load from secondary collection. If omitted, uses default bands."
                },
                "fusion_preset": {
                    "type": "string",
                    "description": "Optional preset name: 'optical_multires', 'optical_dem', 'cross_sensor'. Overrides collection IDs if provided.",
                    "enum": ["optical_multires", "optical_dem", "cross_sensor"]
                },
                "output_format": {
                    "type": "string",
                    "description": "Output format (GTiff, netCDF). Default: GTiff",
                    "default": "GTiff"
                }
            },
            "required": ["spatial_extent", "temporal_extent"]
        }
    },
    {
        "name": "fusion_list_presets",
        "description": (
            "List available multi-sensor fusion presets with descriptions, default collection pairs, "
            "and recommended use cases. Helps users choose the right fusion strategy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
]


def _build_fusion_graph(
    primary_collection: str,
    secondary_collection: str,
    spatial_extent: Dict[str, float],
    temporal_extent: List[str],
    primary_bands: Optional[List[str]],
    secondary_bands: Optional[List[str]],
    output_format: str,
) -> Dict[str, Any]:
    """Build an OpenEO process graph for multi-sensor fusion.

    Strategy:
    1. Load both collections with shared spatial/temporal extent
    2. If resolutions differ, resample secondary to match primary
    3. Merge the two datacubes with merge_cubes
    4. Save the fused result
    """
    primary_meta = COLLECTION_METADATA.get(primary_collection, {})
    secondary_meta = COLLECTION_METADATA.get(secondary_collection, {})

    p_bands = primary_bands or primary_meta.get("default_bands")
    s_bands = secondary_bands or secondary_meta.get("default_bands")

    is_dem = secondary_meta.get("type") == "dem"

    p_type = primary_meta.get("type", "")
    s_type = secondary_meta.get("type", "")
    same_sensor_family = (p_type == s_type == "optical" and
                          primary_collection.split("-")[0] == secondary_collection.split("-")[0])

    if p_bands and s_bands and not is_dem and not same_sensor_family:
        p_set = set(p_bands)
        s_set = set(s_bands)
        overlap = p_set & s_set
        if overlap:
            s_all = secondary_meta.get("all_bands", [])
            s_complementary = [b for b in s_all if b not in p_set and b not in ("scl", "qa", "lwir", "lwir11")]
            if s_complementary:
                s_bands = s_complementary
            else:
                s_only = [b for b in s_bands if b not in overlap]
                if s_only:
                    s_bands = s_only

    graph = {}

    load_primary_args = {
        "id": primary_collection,
        "spatial_extent": spatial_extent,
        "temporal_extent": temporal_extent,
    }
    if p_bands:
        load_primary_args["bands"] = p_bands
    graph["load_primary"] = {
        "process_id": "load_collection",
        "arguments": load_primary_args,
    }

    load_secondary_args = {
        "id": secondary_collection,
        "spatial_extent": spatial_extent,
    }
    if not is_dem:
        load_secondary_args["temporal_extent"] = temporal_extent
    if s_bands:
        load_secondary_args["bands"] = s_bands
    graph["load_secondary"] = {
        "process_id": "load_collection",
        "arguments": load_secondary_args,
    }

    p_res = primary_meta.get("resolution_m", 10)
    s_res = secondary_meta.get("resolution_m", 30)
    needs_resample = p_res != s_res

    merge_secondary_ref = "load_secondary"

    if needs_resample:
        graph["resample_secondary"] = {
            "process_id": "resample_cube_spatial",
            "arguments": {
                "data": {"from_node": "load_secondary"},
                "target": {"from_node": "load_primary"},
                "method": "bilinear",
            }
        }
        merge_secondary_ref = "resample_secondary"

    graph["merge"] = {
        "process_id": "merge_cubes",
        "arguments": {
            "cube1": {"from_node": "load_primary"},
            "cube2": {"from_node": merge_secondary_ref},
        }
    }

    graph["save"] = {
        "process_id": "save_result",
        "arguments": {
            "data": {"from_node": "merge"},
            "format": output_format,
        },
        "result": True,
    }

    return graph


def _describe_fusion(
    primary_collection: str,
    secondary_collection: str,
    spatial_extent: Dict[str, float],
    temporal_extent: List[str],
    primary_bands: Optional[List[str]],
    secondary_bands: Optional[List[str]],
    needs_resample: bool,
) -> str:
    """Generate a human-readable description of the fusion workflow."""
    p_meta = COLLECTION_METADATA.get(primary_collection, {})
    s_meta = COLLECTION_METADATA.get(secondary_collection, {})

    p_name = p_meta.get("name", primary_collection)
    s_name = s_meta.get("name", secondary_collection)
    p_res = p_meta.get("resolution_m", "unknown")
    s_res = s_meta.get("resolution_m", "unknown")

    lines = [
        f"Multi-Sensor Fusion: {p_name} + {s_name}",
        f"",
        f"Primary source:   {p_name} ({p_res}m resolution)",
        f"Secondary source: {s_name} ({s_res}m resolution)",
        f"",
        f"Spatial extent: W={spatial_extent['west']:.4f}, S={spatial_extent['south']:.4f}, "
        f"E={spatial_extent['east']:.4f}, N={spatial_extent['north']:.4f}",
        f"Temporal range:  {temporal_extent[0]} to {temporal_extent[1]}",
        f"",
        f"Primary bands:   {', '.join(primary_bands) if primary_bands else 'default'}",
        f"Secondary bands: {', '.join(secondary_bands) if secondary_bands else 'default'}",
        f"",
        f"Processing steps:",
        f"  1. Load {p_name} datacube",
        f"  2. Load {s_name} datacube",
    ]

    step = 3
    if needs_resample:
        lines.append(f"  {step}. Resample {s_name} ({s_res}m -> {p_res}m) using bilinear interpolation")
        step += 1

    lines.append(f"  {step}. Merge datacubes (merge_cubes)")
    step += 1
    lines.append(f"  {step}. Save fused result")
    lines.append("")
    lines.append("The resulting process graph is ready for openeo_create_job.")

    return "\n".join(lines)


def create_fusion_tools(config=None) -> Dict[str, Any]:
    """Create fusion tools dict for Claude SDK.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _fusion_multi_sensor(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a multi-sensor fusion process graph."""
        try:
            preset_name = args.get("fusion_preset")

            if preset_name and preset_name in FUSION_PRESETS:
                preset = FUSION_PRESETS[preset_name]
                primary = args.get("primary_collection", preset["primary"])
                secondary = args.get("secondary_collection", preset["secondary"])
            else:
                primary = args.get("primary_collection")
                secondary = args.get("secondary_collection")

            if not primary or not secondary:
                available = ", ".join(COLLECTION_METADATA.keys())
                presets_list = ", ".join(FUSION_PRESETS.keys())
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"Error: Please provide both primary_collection and secondary_collection, "
                            f"or use a fusion_preset.\n\n"
                            f"Available collections: {available}\n"
                            f"Available presets: {presets_list}"
                        )
                    }]
                }

            spatial_extent = args["spatial_extent"]
            temporal_extent = args["temporal_extent"]
            primary_bands = args.get("primary_bands")
            secondary_bands = args.get("secondary_bands")
            output_format = args.get("output_format", "GTiff")

            p_meta = COLLECTION_METADATA.get(primary, {})
            s_meta = COLLECTION_METADATA.get(secondary, {})
            p_res = p_meta.get("resolution_m", 10)
            s_res = s_meta.get("resolution_m", 30)
            needs_resample = p_res != s_res

            process_graph = _build_fusion_graph(
                primary_collection=primary,
                secondary_collection=secondary,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
                primary_bands=primary_bands,
                secondary_bands=secondary_bands,
                output_format=output_format,
            )

            description = _describe_fusion(
                primary_collection=primary,
                secondary_collection=secondary,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
                primary_bands=primary_bands,
                secondary_bands=secondary_bands,
                needs_resample=needs_resample,
            )

            node_count = len(process_graph)
            processes_used = [n["process_id"] for n in process_graph.values() if "process_id" in n]

            result = {
                "description": description,
                "process_graph": process_graph,
                "metadata": {
                    "primary_collection": primary,
                    "secondary_collection": secondary,
                    "spatial_extent": spatial_extent,
                    "temporal_extent": temporal_extent,
                    "primary_bands": primary_bands or p_meta.get("default_bands", []),
                    "secondary_bands": secondary_bands or s_meta.get("default_bands", []),
                    "resampled": needs_resample,
                    "node_count": node_count,
                    "processes_used": processes_used,
                    "output_format": output_format,
                },
                "next_step": "Use openeo_create_job with this process_graph to execute the fusion workflow.",
            }

            viz_component = {
                "type": "process_graph",
                "data": {
                    "graph": process_graph,
                    "title": f"Fusion: {p_meta.get('name', primary)} + {s_meta.get('name', secondary)}",
                }
            }

            return {
                "content": [
                    {"type": "text", "text": json.dumps(result, indent=2)},
                    {"type": "visualization", "component": viz_component},
                ]
            }
        except Exception as e:
            logger.error(f"Fusion tool error: {e}", exc_info=True)
            return {
                "content": [{"type": "text", "text": f"Error generating fusion graph: {str(e)}"}]
            }

    async def _fusion_list_presets(args: Dict[str, Any]) -> Dict[str, Any]:
        """List available fusion presets."""
        try:
            lines = ["Available Multi-Sensor Fusion Presets:", ""]

            for key, preset in FUSION_PRESETS.items():
                p_meta = COLLECTION_METADATA.get(preset["primary"], {})
                s_meta = COLLECTION_METADATA.get(preset["secondary"], {})

                lines.append(f"  {key}:")
                lines.append(f"    Name: {preset['name']}")
                lines.append(f"    {preset['description']}")
                lines.append(f"    Primary:   {preset['primary']} ({p_meta.get('resolution_m', '?')}m)")
                lines.append(f"    Secondary: {preset['secondary']} ({s_meta.get('resolution_m', '?')}m)")
                lines.append(f"    Use cases: {', '.join(preset['use_cases'])}")
                lines.append(f"    Notes: {preset['notes']}")
                lines.append("")

            lines.append("Supported collections for custom fusion:")
            for cid, meta in COLLECTION_METADATA.items():
                lines.append(f"  - {cid} ({meta['name']}, {meta['resolution_m']}m, {meta['type']})")

            lines.append("")
            lines.append("Usage: Call fusion_multi_sensor with a preset name or two collection IDs.")

            return {
                "content": [{"type": "text", "text": "\n".join(lines)}]
            }
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error listing presets: {str(e)}"}]
            }

    return {
        "fusion_multi_sensor": _fusion_multi_sensor,
        "fusion_list_presets": _fusion_list_presets,
    }
