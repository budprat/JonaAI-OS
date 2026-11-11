# ABOUTME: Claude SDK client for conversational Earth Observation analysis.
# Implements agentic chat loop with custom OpenEO, GeoAI, and visualization tools.

"""
Claude SDK client for OpenEO AI Assistant.

Uses the Anthropic SDK with custom tools for Earth Observation
data processing and analysis.
"""

import os
import json
import asyncio
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Any, Optional, Dict, List

import anthropic

from openeo_ai import budget

from .sessions import SessionManager
from .permissions import openeo_permission_callback
from .user_context import get_current_user_id

logger = logging.getLogger(__name__)


# Module-level query function for mockability in tests
async def query(
    client: "OpenEOAIClient",
    prompt: str,
    user_id: str,
    session_id: Optional[str] = None,
    messages: Optional[List[dict]] = None
) -> AsyncIterator[Any]:
    """
    Query the Claude API and yield responses.

    This is a module-level function to enable easy mocking in tests.

    Args:
        client: The OpenEOAIClient instance
        prompt: User's message
        user_id: Authenticated user ID
        session_id: Optional session ID
        messages: Message history

    Yields:
        Response objects from Claude API
    """
    if messages is None:
        messages = []

    messages.append({"role": "user", "content": prompt})

    try:
        budget.record_llm_call(user_id, "direct", client.config.model, session_id=session_id)
    except Exception:  # noqa: BLE001
        logger.debug("[budget] direct tag failed", exc_info=True)

    # Call Claude API (with prompt caching for system prompt)
    response = await client.client.messages.create(
        model=client.config.model,
        max_tokens=client.config.max_tokens,
        system=[{
            "type": "text",
            "text": client.SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }],
        tools=TOOL_DEFINITIONS,
        messages=messages
    )

    # Yield response blocks as response objects
    for block in response.content:
        result = type('Response', (), {
            'text': block.text if hasattr(block, 'text') and block.type == 'text' else None,
            'tool_result': None,
            'tool_name': block.name if hasattr(block, 'name') and block.type == 'tool_use' else None,
            'tool_input': block.input if hasattr(block, 'input') and block.type == 'tool_use' else None,
            'tool_use_id': block.id if hasattr(block, 'id') and block.type == 'tool_use' else None,
            'visualization': None,
            'session_id': session_id,
            'stop_reason': response.stop_reason
        })()
        yield result


@dataclass
class OpenEOAIConfig:
    """Configuration for OpenEO AI Assistant."""

    model: str = "claude-opus-5-5"
    max_turns: int = 15
    max_tokens: int = 16000
    openeo_url: str = field(
        default_factory=lambda: os.environ.get(
            "OPENEO_URL", "http://localhost:8080/openeo/1.1.0"
        )
    )
    sqlite_path: str = field(
        default_factory=lambda: os.environ.get(
            "OPENEO_AI_DB", "data/openeo_ai.db"
        )
    )
    geoai_models_path: str = field(
        default_factory=lambda: os.environ.get(
            "GEOAI_MODELS_PATH", "openeo_ai/geoai/models/"
        )
    )
    stac_api_url: str = field(
        default_factory=lambda: os.environ.get(
            "STAC_API_URL", "https://earth-search.aws.element84.com/v1/"
        )
    )
    api_key: str = field(
        default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", "")
    )


# Tool definitions for Claude API
TOOL_DEFINITIONS = [
    {
        "name": "openeo_list_collections",
        "description": "List available Earth Observation data collections from the STAC catalog. Returns collection IDs, titles, and descriptions.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "openeo_get_collection_info",
        "description": "Get detailed information about a specific collection including available bands, spatial/temporal extent, and metadata.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection_id": {
                    "type": "string",
                    "description": "The collection identifier (e.g., 'sentinel-2-l2a', 'landsat-c2-l2')"
                }
            },
            "required": ["collection_id"]
        }
    },
    {
        "name": "openeo_validate_graph",
        "description": "Validate an OpenEO process graph for errors, warnings, and suggestions. Checks structure, process IDs, arguments, data flow, band names, and extent sizes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "process_graph": {
                    "type": "object",
                    "description": "The OpenEO process graph to validate"
                }
            },
            "required": ["process_graph"]
        }
    },
    {
        "name": "openeo_generate_graph",
        "description": "Generate an OpenEO process graph from a natural language description.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Natural language description of the desired analysis"
                },
                "collection": {
                    "type": "string",
                    "description": "Collection ID to use"
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
                    "description": "Start and end dates [start, end] in ISO format"
                },
                "output_format": {
                    "type": "string",
                    "description": "Output format (GTiff, netCDF, etc.)",
                    "default": "GTiff"
                },
                "time_reducer": {
                    "type": "string",
                    "enum": ["median", "mean", "min", "max", "none"],
                    "description": "Temporal reduction strategy. Use 'none' to preserve the time dimension (raw data, time series). Default: 'median'.",
                    "default": "median"
                }
            },
            "required": ["description", "collection", "spatial_extent", "temporal_extent"]
        }
    },
    {
        "name": "openeo_compose_graph",
        "description": "Compose a custom OpenEO process graph from atomic steps. Use for multi-stage workflows not supported by openeo_generate_graph (e.g. NDVI + smoothing, index + edge detection, custom temporal aggregations). Steps auto-wire sequentially.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "description": "Collection ID to use (e.g., 'sentinel-2-l2a')"
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
                    "description": "Start and end dates [start, end] in ISO format"
                },
                "steps": {
                    "type": "array",
                    "description": "Ordered list of processing steps. Must start with 'load' and end with 'save'.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "op": {
                                "type": "string",
                                "enum": ["load", "cloud_mask", "index", "reduce_time", "aggregate_time", "spatial_filter", "save"],
                                "description": "The operation to perform"
                            },
                            "bands": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Explicit bands for load step (auto-detected from index ops if omitted)"
                            },
                            "index_name": {
                                "type": "string",
                                "description": "Spectral index name for 'index' step (ndvi, ndwi, nbr, etc.)"
                            },
                            "reducer": {
                                "type": "string",
                                "enum": ["median", "mean", "min", "max"],
                                "description": "Reducer for reduce_time or aggregate_time (default: median)"
                            },
                            "period": {
                                "type": "string",
                                "enum": ["day", "week", "month", "season", "year"],
                                "description": "Period for aggregate_time step"
                            },
                            "kernel_type": {
                                "type": "string",
                                "enum": ["gaussian_3", "gaussian_5", "sobel_x", "sobel_y", "laplacian", "sharpen"],
                                "description": "Kernel type for spatial_filter step"
                            },
                            "output_format": {
                                "type": "string",
                                "description": "Output format for save step (default: GTiff)"
                            }
                        },
                        "required": ["op"]
                    }
                }
            },
            "required": ["collection", "spatial_extent", "temporal_extent", "steps"]
        }
    },
    {
        "name": "openeo_raw_graph",
        "description": "Submit a raw OpenEO process graph constructed by the assistant. Use for complex algorithms requiring arbitrary process combinations (thresholding, boolean logic, arithmetic, merge_cubes). Graph is validated before return.",
        "input_schema": {
            "type": "object",
            "properties": {
                "process_graph": {
                    "type": "object",
                    "description": "Complete OpenEO process graph dict. Must have at least one node with result=true."
                }
            },
            "required": ["process_graph"]
        }
    },
    {
        "name": "openeo_algorithm",
        "description": "Run a pre-built algorithm template for complex EO workflows. Available: snow_cover (NDSI+NDVI thresholding), burn_severity (dNBR from pre/post fire), flood_extent (water index thresholding), urban_growth (NDBI change detection).",
        "input_schema": {
            "type": "object",
            "properties": {
                "algorithm": {
                    "type": "string",
                    "enum": ["snow_cover", "burn_severity", "flood_extent", "urban_growth"],
                    "description": "Algorithm to run"
                },
                "collection": {
                    "type": "string",
                    "description": "Collection ID (e.g., 'sentinel-2-l2a')"
                },
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box with west, south, east, north",
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
                    "description": "Date range [start, end] for single-period algorithms (snow_cover, flood_extent)"
                },
                "pre_event": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Pre-event date range [start, end] for dual-temporal algorithms (burn_severity, urban_growth)"
                },
                "post_event": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Post-event date range [start, end] for dual-temporal algorithms (burn_severity, urban_growth)"
                },
                "output_format": {
                    "type": "string",
                    "description": "Output format (default: GTiff)",
                    "default": "GTiff"
                },
                "cloud_mask": {
                    "type": "boolean",
                    "description": "Enable SCL-based cloud masking (default: true)",
                    "default": True
                },
                "ndsi_threshold": {
                    "type": "number",
                    "description": "NDSI threshold for snow_cover (default: 0.4)"
                },
                "ndvi_threshold": {
                    "type": "number",
                    "description": "NDVI threshold for snow_cover vegetation exclusion (default: 0.2)"
                },
                "water_index": {
                    "type": "string",
                    "enum": ["mndwi", "ndwi"],
                    "description": "Water index for flood_extent (default: mndwi)"
                },
                "water_threshold": {
                    "type": "number",
                    "description": "Water index threshold for flood_extent (default: 0.3)"
                }
            },
            "required": ["algorithm", "collection", "spatial_extent"]
        }
    },
    {
        "name": "openeo_list_jobs",
        "description": "ALWAYS use this tool when the user asks to 'list jobs', 'list all jobs', 'show jobs', 'show all jobs', 'what jobs exist', or 'check job status'. Lists ALL batch jobs (running, queued, finished, error, created) with their ID, title, status, and creation date. Can filter by status.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of jobs to return (default 50)"
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status: finished, error, running, queued, created. Omit to show all.",
                    "enum": ["finished", "error", "running", "queued", "created"]
                }
            },
            "required": []
        }
    },
    {
        "name": "openeo_create_job",
        "description": "Create, start, and monitor a batch processing job. Automatically starts the job after creation and polls status every 10 seconds until complete (up to 120s). Returns final status with job ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Job title"
                },
                "process_graph": {
                    "type": "object",
                    "description": "The OpenEO process graph to execute"
                },
                "description": {
                    "type": "string",
                    "description": "Optional job description"
                }
            },
            "required": ["title", "process_graph"]
        }
    },
    {
        "name": "openeo_start_job",
        "description": "Start a queued batch job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job ID to start"
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "openeo_get_job_status",
        "description": "Get the current status of a batch job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job ID to check"
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "openeo_get_results",
        "description": "Get the results of a completed batch job.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job ID to get results for"
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "result_load",
        "description": "Load a completed job's result file metadata for chaining into post-processing tools. Returns file_path, bands, CRS, bounds, and resolution. Use the returned file_path as input to result_correlation, result_difference, raster_statistics, threshold_classify, etc. This enables multi-step analysis pipelines without re-running jobs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job ID whose result to load for chaining"
                }
            },
            "required": ["job_id"]
        }
    },
    {
        "name": "geoai_segment",
        "description": "Run semantic segmentation on satellite imagery to classify land cover types.",
        "input_schema": {
            "type": "object",
            "properties": {
                "input_path": {
                    "type": "string",
                    "description": "Path to input GeoTIFF file"
                },
                "model": {
                    "type": "string",
                    "description": "Model name (default: segmentation_default)",
                    "default": "segmentation_default"
                },
                "output_path": {
                    "type": "string",
                    "description": "Optional output path for results"
                }
            },
            "required": ["input_path"]
        }
    },
    {
        "name": "geoai_detect_change",
        "description": "Detect changes between two satellite images from different times.",
        "input_schema": {
            "type": "object",
            "properties": {
                "before_path": {
                    "type": "string",
                    "description": "Path to before image GeoTIFF"
                },
                "after_path": {
                    "type": "string",
                    "description": "Path to after image GeoTIFF"
                },
                "model": {
                    "type": "string",
                    "description": "Model name (default: change_default)",
                    "default": "change_default"
                }
            },
            "required": ["before_path", "after_path"]
        }
    },
    {
        "name": "geoai_estimate_canopy_height",
        "description": "Estimate tree canopy height from RGB satellite imagery.",
        "input_schema": {
            "type": "object",
            "properties": {
                "input_path": {
                    "type": "string",
                    "description": "Path to input RGB GeoTIFF file"
                }
            },
            "required": ["input_path"]
        }
    },
    {
        "name": "viz_show_map",
        "description": "Create an interactive map visualization of raster data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "geotiff_path": {
                    "type": "string",
                    "description": "Path to GeoTIFF file to display"
                },
                "title": {
                    "type": "string",
                    "description": "Map title"
                },
                "colormap": {
                    "type": "string",
                    "description": "Colormap name (viridis, plasma, ndvi, terrain)",
                    "default": "viridis"
                }
            },
            "required": ["geotiff_path"]
        }
    },
    {
        "name": "viz_show_time_series",
        "description": "Create a time series chart from temporal data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "dates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Array of date strings"
                },
                "values": {
                    "type": "array",
                    "items": {"type": "number"},
                    "description": "Array of values corresponding to dates"
                },
                "title": {
                    "type": "string",
                    "description": "Chart title"
                },
                "y_label": {
                    "type": "string",
                    "description": "Y-axis label"
                }
            },
            "required": ["dates", "values"]
        }
    },
    {
        "name": "viz_show_ndvi_map",
        "description": "Display NDVI results with a vegetation-specific color scale (red-yellow-green). Use this instead of viz_show_map when displaying NDVI data for appropriate coloring.",
        "input_schema": {
            "type": "object",
            "properties": {
                "geotiff_path": {
                    "type": "string",
                    "description": "Path to NDVI GeoTIFF file"
                },
                "title": {
                    "type": "string",
                    "description": "Map title (default: 'NDVI')",
                    "default": "NDVI"
                }
            },
            "required": ["geotiff_path"]
        }
    },
    {
        "name": "viz_compare_images",
        "description": "Create a side-by-side before/after comparison slider for two raster images. Useful for change detection, before/after analysis, and visual comparison.",
        "input_schema": {
            "type": "object",
            "properties": {
                "before_path": {
                    "type": "string",
                    "description": "Path to the 'before' image file"
                },
                "after_path": {
                    "type": "string",
                    "description": "Path to the 'after' image file"
                },
                "title": {
                    "type": "string",
                    "description": "Comparison title",
                    "default": "Before / After"
                }
            },
            "required": ["before_path", "after_path"]
        }
    },
    {
        "name": "viz_show_dashboard",
        "description": "Generate a composite dashboard view from a single result file. Automatically creates map, statistics, and histogram panels in a grid layout. Use this for comprehensive at-a-glance analysis of a result.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to GeoTIFF result file"
                },
                "title": {
                    "type": "string",
                    "description": "Dashboard title",
                    "default": "Analysis Dashboard"
                },
                "include": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["map", "stats", "histogram"]},
                    "description": "Panel types to include (default: all three)",
                    "default": ["map", "stats", "histogram"]
                },
                "colormap": {
                    "type": "string",
                    "description": "Colormap for the map panel (default: viridis)",
                    "default": "viridis"
                }
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "openeo_resolve_location",
        "description": "Resolve a place name, city, region, or coordinates to a geographic bounding box for spatial queries. Automatically constrains the bbox so output GeoTIFFs are ~400MB. For larger or smaller areas, specify target_file_size_mb.",
        "input_schema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "Location name (e.g., 'Mumbai', 'Kerala', 'Amazon rainforest') or coordinates ('28.6139, 77.2090')"
                },
                "buffer_degrees": {
                    "type": "number",
                    "description": "Buffer in degrees to add around point locations (default: 0.1, ~11km)",
                    "default": 0.1
                },
                "target_file_size_mb": {
                    "type": "number",
                    "description": "Target output file size in MB (default: 400). The bbox will be constrained so the estimated GeoTIFF output is approximately this size.",
                    "default": 400
                },
                "num_bands": {
                    "type": "integer",
                    "description": "Expected number of output bands (default: 1). Used for file size estimation. E.g., NDVI=1, RGB=3, multi-band composite=6.",
                    "default": 1
                },
                "num_timesteps": {
                    "type": "integer",
                    "description": "Expected number of temporal steps (default: 1). Used for file size estimation. E.g., single date=1, monthly over a year=12.",
                    "default": 1
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "openeo_parse_temporal",
        "description": "Parse natural language temporal expressions to ISO date ranges. Use this when the user mentions time periods like 'last summer', 'monsoon 2024', 'past 3 months'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Temporal expression (e.g., 'last summer', '2020-2023', 'monsoon', 'past 6 months')"
                }
            },
            "required": ["expression"]
        }
    },
    {
        "name": "openeo_estimate_extent",
        "description": "Estimate data size and validate extent before processing. Use this to check if a query will produce too much data and warn the user about large requests.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box {west, south, east, north}",
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
                    "description": "Date range [start, end] in ISO format"
                },
                "collection": {
                    "type": "string",
                    "description": "Collection ID (default: sentinel-2-l2a)"
                },
                "bands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of bands to use"
                }
            },
            "required": ["spatial_extent"]
        }
    },
    {
        "name": "openeo_quality_metrics",
        "description": "Get comprehensive data quality metrics including estimated cloud coverage, temporal coverage, and data completeness. Use this to inform users about expected data quality before processing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box {west, south, east, north}",
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
                    "description": "Date range [start, end] in ISO format"
                },
                "collection": {
                    "type": "string",
                    "description": "Collection ID (default: sentinel-2-l2a)"
                },
                "bands": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of bands to use"
                }
            },
            "required": ["spatial_extent"]
        }
    },
    {
        "name": "openeo_validate_geospatial",
        "description": "Validate geospatial extent including CRS validation, antimeridian handling, and coordinate bounds. Use this to check if coordinates are valid before processing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box {west, south, east, north}",
                    "properties": {
                        "west": {"type": "number"},
                        "south": {"type": "number"},
                        "east": {"type": "number"},
                        "north": {"type": "number"}
                    },
                    "required": ["west", "south", "east", "north"]
                },
                "crs": {
                    "type": "string",
                    "description": "Coordinate Reference System (default: EPSG:4326)",
                    "default": "EPSG:4326"
                }
            },
            "required": ["spatial_extent"]
        }
    },
    {
        "name": "openeo_aggregate_spatial",
        "description": "Compute zonal statistics (mean, median, min, max, sum, std) for regions defined by GeoJSON polygons. Use this to answer 'What is the average NDVI in this area?' type questions. Returns a process graph that produces per-polygon statistics as JSON output.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "description": "STAC collection ID (e.g., 'sentinel-2-l2a')"
                },
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box encompassing all geometries",
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
                    "description": "Start and end dates [start, end] in ISO format"
                },
                "geometries": {
                    "type": "object",
                    "description": "GeoJSON FeatureCollection or Geometry object defining zones"
                },
                "reducer": {
                    "type": "string",
                    "enum": ["mean", "median", "min", "max", "sum", "sd"],
                    "description": "Aggregation function (default: mean)"
                },
                "index_name": {
                    "type": "string",
                    "description": "Optional: compute this spectral index first, then aggregate (e.g., 'ndvi', 'ndwi')"
                },
                "cloud_mask": {
                    "type": "boolean",
                    "description": "Apply SCL cloud masking (default: true)"
                }
            },
            "required": ["collection", "spatial_extent", "temporal_extent", "geometries"]
        }
    },
    {
        "name": "openeo_apply_kernel",
        "description": "Apply a spatial convolution kernel to satellite imagery. Supports smoothing (gaussian), edge detection (sobel, laplacian), and sharpening. Use for noise reduction, feature enhancement, or spatial analysis. Returns a process graph.",
        "input_schema": {
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "description": "STAC collection ID (e.g., 'sentinel-2-l2a')"
                },
                "spatial_extent": {
                    "type": "object",
                    "description": "Bounding box",
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
                    "description": "Start and end dates [start, end] in ISO format"
                },
                "kernel_type": {
                    "type": "string",
                    "enum": ["gaussian", "sobel_x", "sobel_y", "laplacian", "sharpen"],
                    "description": "Type of convolution kernel"
                },
                "kernel_size": {
                    "type": "integer",
                    "enum": [3, 5],
                    "description": "Kernel size (3x3 or 5x5, only for gaussian). Default: 3"
                },
                "index_name": {
                    "type": "string",
                    "description": "Optional: compute spectral index first, then apply kernel (e.g., 'ndvi')"
                },
                "cloud_mask": {
                    "type": "boolean",
                    "description": "Apply SCL cloud masking (default: true)"
                },
                "output_format": {
                    "type": "string",
                    "description": "Output format (default: GTiff)"
                }
            },
            "required": ["collection", "spatial_extent", "temporal_extent", "kernel_type"]
        }
    },
    {
        "name": "saved_jobs_list",
        "description": "List permanently saved result files (GeoTIFFs archived from completed jobs). Use ONLY when the user asks about 'saved results', 'saved files', 'archived results', or 'downloaded results'. Do NOT use for general 'list jobs' queries — use openeo_list_jobs instead.",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of jobs to return (default 20)"
                }
            },
            "required": []
        }
    },
    {
        "name": "saved_jobs_load",
        "description": "Load a saved/finished job result onto the map for visualization. Takes a save_id from saved_jobs_list. Use when the user asks to show, load, or display a previous result on the map.",
        "input_schema": {
            "type": "object",
            "properties": {
                "save_id": {
                    "type": "string",
                    "description": "The save_id of the job to load (from saved_jobs_list)"
                }
            },
            "required": ["save_id"]
        }
    },
    {
        "name": "saved_jobs_delete",
        "description": "Permanently delete a saved job result. Removes both the file and manifest entry.",
        "input_schema": {
            "type": "object",
            "properties": {
                "save_id": {
                    "type": "string",
                    "description": "The save_id of the job to delete"
                }
            },
            "required": ["save_id"]
        }
    },
    {
        "name": "save_workflow_template",
        "description": "Save the current analysis workflow as a reusable template. Users can say 'save this as a template' after completing an analysis. The template can then be run across multiple locations via batch jobs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Template name (e.g., 'Monthly NDVI for Farmland')"
                },
                "description": {
                    "type": "string",
                    "description": "What this template does"
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for discovery (e.g., ['vegetation', 'ndvi', 'monthly'])"
                },
                "process_graph": {
                    "type": "object",
                    "description": "OpenEO process graph JSON. If omitted, uses the most recent graph generated in this session."
                }
            },
            "required": ["name"]
        }
    }
]

# Extend with new analysis tool definitions
def _extend_tool_definitions():
    """Import and add tool definitions from new tool modules."""
    # Load each tool module individually so one failure doesn't disable all others.
    _ext_modules = [
        ("monitor_tools", "MONITOR_TOOL_DEFINITIONS"),
        ("result_analysis_tools", "RESULT_ANALYSIS_TOOL_DEFINITIONS"),
        ("spectral_index_tools", "SPECTRAL_INDEX_TOOL_DEFINITIONS"),
        ("temporal_analysis_tools", "TEMPORAL_ANALYSIS_TOOL_DEFINITIONS"),
        ("export_tools", "EXPORT_TOOL_DEFINITIONS"),
        ("comparison_tools", "COMPARISON_TOOL_DEFINITIONS"),
        ("discovery_tools", "DISCOVERY_TOOL_DEFINITIONS"),
        ("fusion_tools", "FUSION_TOOL_DEFINITIONS"),
        ("documentation_tools", "DOCUMENTATION_TOOL_DEFINITIONS"),
    ]
    for _mod_name, _defs_name in _ext_modules:
        try:
            _mod = __import__(f"openeo_ai.tools.{_mod_name}", fromlist=[_defs_name])
            TOOL_DEFINITIONS.extend(getattr(_mod, _defs_name))
        except Exception as e:
            logger.error(f"Failed to load {_mod_name} definitions: {e}", exc_info=True)

    # Separate try/except: strategy_tools imports from openeo_ai.sdk.domain_knowledge
    # which can cause circular import if strategy_tools is imported before client.py
    try:
        from ..tools.strategy_tools import STRATEGY_TOOL_DEFINITIONS
        TOOL_DEFINITIONS.extend(STRATEGY_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load strategy tool definitions: {e}")

    # Interpretation engine tools (imports domain_knowledge)
    try:
        from ..tools.interpretation import INTERPRETATION_TOOL_DEFINITIONS
        TOOL_DEFINITIONS.extend(INTERPRETATION_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load interpretation tool definitions: {e}")

    # Graph builder tools (atomic graph construction)
    try:
        if not os.environ.get("DISABLE_GRAPH_BUILDER_TOOLS"):
            from ..tools.graph_builder_tools import GRAPH_BUILDER_TOOL_DEFINITIONS
            TOOL_DEFINITIONS.extend(GRAPH_BUILDER_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load graph builder tool definitions: {e}")

    # Workflow executor (step-by-step plan execution)
    try:
        if not os.environ.get("DISABLE_WORKFLOW_EXECUTOR"):
            from ..tools.workflow_executor import WORKFLOW_EXECUTOR_TOOL_DEFINITIONS
            TOOL_DEFINITIONS.extend(WORKFLOW_EXECUTOR_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load workflow executor definitions: {e}")

    # Goal extraction tools (imports domain_knowledge + strategy_tools)
    try:
        from ..tools.goal_tools import GOAL_TOOL_DEFINITIONS
        TOOL_DEFINITIONS.extend(GOAL_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load goal tool definitions: {e}")

    # Quality assessment tools (imports domain_knowledge + interpretation)
    try:
        from ..tools.quality_tools import QUALITY_TOOL_DEFINITIONS
        TOOL_DEFINITIONS.extend(QUALITY_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load quality tool definitions: {e}")

    # Pre-execution validation tools (imports openeo_tools + domain_knowledge)
    try:
        from ..tools.validation_tools import VALIDATION_TOOL_DEFINITIONS
        # Filter out openeo_validate_graph (already in inline TOOL_DEFINITIONS above)
        new_defs = [d for d in VALIDATION_TOOL_DEFINITIONS if d["name"] != "openeo_validate_graph"]
        TOOL_DEFINITIONS.extend(new_defs)
    except Exception as e:
        logger.warning(f"Failed to load validation tool definitions: {e}")

    # Earth-Agent integration (meta-tool + dynamically discovered ea_* tools)
    try:
        from ..tools.earth_agent_tools import get_earth_agent_tool_definitions
        TOOL_DEFINITIONS.extend(get_earth_agent_tool_definitions())
    except Exception as e:
        logger.warning(f"Failed to load Earth-Agent tool definitions: {e}")

    # Agentic-EO decision-grade query (natural language -> real Sentinel-2 -> decision report)
    try:
        from ..agentic_eo.sdk_tool import DECISION_QUERY_TOOL_DEFINITIONS
        TOOL_DEFINITIONS.extend(DECISION_QUERY_TOOL_DEFINITIONS)
    except Exception as e:
        logger.warning(f"Failed to load agentic-EO decision-query tool definitions: {e}")

_extend_tool_definitions()


# --- Planner/Worker architecture constants ---

# Read-only discovery tools the Planner can use to understand context before planning
PLANNER_TOOL_NAMES = frozenset([
    "openeo_list_collections",
    "openeo_get_collection_info",
    "recommend_collection",
    "openeo_resolve_location",
    "openeo_parse_temporal",
    "openeo_estimate_extent",
    "openeo_quality_metrics",
    "openeo_validate_geospatial",
    "spectral_index_search",
    "explain_collection_bands",
    # Query intelligence tools
    "extract_analysis_goal",
    "validate_goal_completeness",
    "validate_pre_execution",
    "get_analysis_strategies",
])

# The curated main-session toolset. The CLI defers ALL MCP
# tools behind ToolSearch when definitions exceed ~10% of model context
# (~40K chars); Haiku loops on the load->invoke handoff. Keeping the core
# surface under budget (test_core_toolset.py pins < 36K chars) makes every
# session load its tools eagerly again. The long tail stays available to
# specialists via SPECIALIST_CONFIGS.
CORE_TOOL_NAMES = frozenset([
    # Goal + strategy
    "extract_analysis_goal",
    "analysis_strategy_planner",
    # Discovery + grounding
    "openeo_resolve_location",
    "openeo_parse_temporal",
    "recommend_collection",
    "openeo_estimate_extent",
    "openeo_quality_metrics",
    "openeo_list_collections",
    "explain_collection_bands",
    "spectral_index_search",
    # Graph + jobs
    "openeo_compose_graph",
    "openeo_generate_graph",
    "openeo_algorithm",
    "openeo_validate_graph",
    "openeo_create_job",
    "openeo_get_job_status",
    "openeo_get_results",
    "openeo_start_job",
    # Results + interpretation
    "result_inspect",
    "result_zonal_stats",
    "result_histogram",
    "viz_show_map",
    "assess_result_quality",
    "threshold_classify",
    "interpret_result",
    # Exports
    "export_styled_geotiff",
    "export_csv",
    "export_report",
    # Monitoring spine (flagship: "watch this farm and alert me")
    "monitor_create_asset",
    "monitor_create_watch",
    "monitor_list_assets",
    "monitor_list_signals",
    "monitor_asset_briefing",
    "signal_acknowledge",
    "signal_resolve",
    "evidence_get_chain",
    # Decision-grade pipeline
    "agentic_decision_query",
])

# Prompt text extracted to system_prompts.py (TECH-DEBT-HOTSPOTS.md #4).
# Rebinding keeps `from openeo_ai.sdk.client import PLANNER_SYSTEM_PROMPT`
# (tests, prompts.py fallback_attr) working unchanged.
from .system_prompts import (  # noqa: F401
    DATA_AGENT_SYSTEM_PROMPT,
    DATA_SCOUT_AGENT_SYSTEM_PROMPT,
    METHODOLOGIST_AGENT_SYSTEM_PROMPT,
    ML_AGENT_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
    PUBLISHER_AGENT_SYSTEM_PROMPT,
    QA_AGENT_SYSTEM_PROMPT,
    SENTINEL_AGENT_SYSTEM_PROMPT,
    TIMESERIES_AGENT_SYSTEM_PROMPT,
    VIZ_AGENT_SYSTEM_PROMPT,
)
from . import system_prompts as _system_prompts


# --- Phase 3: Specialist Multi-Agent Architecture ---

# Orchestrator gets same tools as Planner (discovery only)
ORCHESTRATOR_TOOL_NAMES = PLANNER_TOOL_NAMES

# Data Agent: data acquisition, loading, processing, job management
DATA_AGENT_TOOL_NAMES = frozenset([
    "openeo_list_collections", "openeo_get_collection_info", "recommend_collection",
    "openeo_resolve_location", "openeo_parse_temporal", "openeo_estimate_extent",
    "openeo_quality_metrics", "openeo_validate_geospatial",
    "openeo_generate_graph", "openeo_compose_graph", "openeo_raw_graph",
    "openeo_algorithm", "openeo_validate_graph",
    "openeo_aggregate_spatial", "openeo_apply_kernel",
    "openeo_create_job", "openeo_start_job", "openeo_get_job_status",
    "openeo_list_jobs", "openeo_get_results",
    "saved_jobs_list", "saved_jobs_load",
])  # 22 tools

# Viz Agent: rendering, mapping, comparison, export
VIZ_AGENT_TOOL_NAMES = frozenset([
    "viz_show_map", "viz_show_time_series", "viz_show_ndvi_map", "viz_compare_images", "viz_show_dashboard",
    "compare_collections", "result_difference", "result_correlation",
    "export_styled_geotiff", "export_report", "export_csv",
    "result_inspect", "result_histogram", "raster_statistics",
    "explain_collection_bands",
])  # 14 tools

# ML Agent: spectral indices, GeoAI models, temporal analysis, Earth-Agent
ML_AGENT_TOOL_NAMES = frozenset([
    "geoai_segment", "geoai_detect_change", "geoai_estimate_canopy_height",
    "spectral_index_calculate", "spectral_index_search",
    "temporal_trend_analysis", "temporal_anomaly_detect", "temporal_phenology",
    "threshold_classify",
    # Earth-Agent tools for post-processing analysis
    "earth_agent_analyze", "get_analysis_strategies",
])  # 11 tools

# QA Agent: result analysis, zonal stats, fusion, documentation
QA_AGENT_TOOL_NAMES = frozenset([
    "analyze_result_image",
    "result_inspect", "result_histogram", "raster_statistics",
    "result_zonal_stats", "result_extract_point",
    "fusion_multi_sensor", "fusion_list_presets",
    "export_workflow_doc", "generate_validation_checklist",
    "interpret_result", "generate_report",
])  # 12 tools





# Sentinel Agent: durable monitoring — set up/manage watches + triage signals.
# Reuses the (already user-scoped) spine tools plus resolve/generate so it can
# stand up a watch end to end. Overlaps intentionally with data's resolve/graph
# tools; the planner disambiguates one-off analysis (data) vs monitoring (sentinel).
SENTINEL_AGENT_TOOL_NAMES = frozenset([
    "monitor_create_asset", "monitor_list_assets",
    "monitor_create_watch", "monitor_update_watch",
    "monitor_pause_watch", "monitor_resume_watch",
    "monitor_list_signals", "signal_acknowledge", "signal_resolve",
    "monitor_asset_briefing", "monitor_portfolio_briefing",
    "monitor_list_deliveries", "evidence_get_chain",
    "openeo_resolve_location", "openeo_generate_graph",
])  # 15 tools


# Research personas (reuse existing tools; overlap with data/viz/ml/qa is
# intentional — the planner prompt disambiguates by intent, tools are shared).
DATA_SCOUT_AGENT_TOOL_NAMES = frozenset([
    "openeo_list_collections", "openeo_get_collection_info", "recommend_collection",
    "openeo_resolve_location", "openeo_parse_temporal", "openeo_estimate_extent",
    "explain_collection_bands",
])  # 7 tools

METHODOLOGIST_AGENT_TOOL_NAMES = frozenset([
    "openeo_validate_graph", "graph_validate_full", "validate_pre_execution",
    "validate_goal_completeness", "openeo_quality_metrics", "assess_result_quality",
    "generate_validation_checklist",
])  # 7 tools

PUBLISHER_AGENT_TOOL_NAMES = frozenset([
    "export_report", "export_csv", "export_styled_geotiff",
    "export_workflow_doc", "generate_report", "interpret_result",
])  # 6 tools

TIMESERIES_AGENT_TOOL_NAMES = frozenset([
    "temporal_trend_analysis", "temporal_anomaly_detect", "temporal_phenology",
    "result_extract_temporal_layers", "result_difference", "result_correlation",
    "compare_collections",
])  # 7 tools


SPECIALIST_CONFIGS = {
    "data": {"tool_names": DATA_AGENT_TOOL_NAMES, "system_prompt": DATA_AGENT_SYSTEM_PROMPT, "mcp_name": "openeo_data"},
    "viz":  {"tool_names": VIZ_AGENT_TOOL_NAMES,  "system_prompt": VIZ_AGENT_SYSTEM_PROMPT,  "mcp_name": "openeo_viz"},
    "ml":   {"tool_names": ML_AGENT_TOOL_NAMES,   "system_prompt": ML_AGENT_SYSTEM_PROMPT,   "mcp_name": "openeo_ml"},
    "qa":   {"tool_names": QA_AGENT_TOOL_NAMES,   "system_prompt": QA_AGENT_SYSTEM_PROMPT,   "mcp_name": "openeo_qa"},
    "sentinel": {"tool_names": SENTINEL_AGENT_TOOL_NAMES, "system_prompt": SENTINEL_AGENT_SYSTEM_PROMPT, "mcp_name": "openeo_sentinel"},
    "data_scout": {"tool_names": DATA_SCOUT_AGENT_TOOL_NAMES, "system_prompt": DATA_SCOUT_AGENT_SYSTEM_PROMPT, "mcp_name": "openeo_data_scout"},
    "methodologist": {"tool_names": METHODOLOGIST_AGENT_TOOL_NAMES, "system_prompt": METHODOLOGIST_AGENT_SYSTEM_PROMPT, "mcp_name": "openeo_methodologist"},
    "publisher": {"tool_names": PUBLISHER_AGENT_TOOL_NAMES, "system_prompt": PUBLISHER_AGENT_SYSTEM_PROMPT, "mcp_name": "openeo_publisher"},
    "timeseries": {"tool_names": TIMESERIES_AGENT_TOOL_NAMES, "system_prompt": TIMESERIES_AGENT_SYSTEM_PROMPT, "mcp_name": "openeo_timeseries"},
}


class OpenEOAIClient:
    """
    OpenEO AI Assistant using Claude SDK.

    Provides conversational interface for:
    - Natural language → Process graph generation
    - Process graph validation
    - Batch job management
    - Result visualization
    - GeoAI model inference
    """

    # Class attribute preserved: prompts.py falls back to
    # OpenEOAIClient.SYSTEM_PROMPT and web_interface reads client.SYSTEM_PROMPT.
    SYSTEM_PROMPT = _system_prompts.SYSTEM_PROMPT

    def __init__(self, config: Optional[OpenEOAIConfig] = None):
        """Initialize the OpenEO AI Client."""
        self.config = config or OpenEOAIConfig()
        self.session_manager = SessionManager(db_path=self.config.sqlite_path)
        self.tools = self._create_tools()
        self._client = None
        self._last_process_graph = None  # Track last generated process graph for save_workflow_template

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            if not self.config.api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY environment variable is required. "
                    "Set it with: export ANTHROPIC_API_KEY=your-key"
                )
            self._client = anthropic.AsyncAnthropic(api_key=self.config.api_key)
        return self._client

    def _create_tools(self) -> Dict[str, Any]:
        """Create and register all custom tools."""
        from ..tools.openeo_tools import create_openeo_tools
        from ..tools.job_tools import create_job_tools
        from ..tools.geoai_tools import create_geoai_tools
        from ..tools.viz_tools import create_viz_tools

        tools = {}
        tools.update(create_openeo_tools(self.config))
        tools.update(create_job_tools(self.config))
        tools.update(create_geoai_tools(self.config))
        tools.update(create_viz_tools(self.config))

        from ..tools.saved_jobs_tools import create_saved_jobs_tools
        tools.update(create_saved_jobs_tools(self.config))

        # Load each tool handler module individually so one failure doesn't disable all.
        _handler_modules = [
            ("result_analysis_tools", "create_result_analysis_tools", True),
            ("spectral_index_tools", "create_spectral_index_tools", True),
            ("temporal_analysis_tools", "create_temporal_analysis_tools", True),
            ("export_tools", "create_export_tools", True),
            ("comparison_tools", "create_comparison_tools", True),
            ("discovery_tools", "create_discovery_tools", True),
            ("fusion_tools", "create_fusion_tools", True),
            ("documentation_tools", "create_documentation_tools", True),
        ]
        for _mod_name, _factory_name, _takes_config in _handler_modules:
            try:
                _mod = __import__(f"openeo_ai.tools.{_mod_name}", fromlist=[_factory_name])
                _factory = getattr(_mod, _factory_name)
                tools.update(_factory(self.config) if _takes_config else _factory())
            except Exception as e:
                logger.error(f"Failed to load {_mod_name} handlers: {e}", exc_info=True)

        try:
            from ..tools.strategy_tools import create_strategy_tools
            strategy_tools, _ = create_strategy_tools()
            tools.update(strategy_tools)
        except Exception as e:
            logger.error(f"Failed to load strategy tools: {e}", exc_info=True)

        # Interpretation engine tools
        try:
            from ..tools.interpretation import create_interpretation_tools
            tools.update(create_interpretation_tools(self.config))
        except Exception as e:
            logger.warning(f"Failed to load interpretation tools: {e}")

        # Query intelligence tools (goal extraction, quality assessment, pre-execution validation)
        try:
            from ..tools.goal_tools import create_goal_tools
            goal_handlers, _ = create_goal_tools()
            tools.update(goal_handlers)
        except Exception as e:
            logger.warning(f"Failed to load goal tools: {e}")

        try:
            from ..tools.quality_tools import create_quality_tools
            quality_handlers, _ = create_quality_tools()
            tools.update(quality_handlers)
        except Exception as e:
            logger.warning(f"Failed to load quality tools: {e}")

        try:
            from ..tools.validation_tools import create_validation_tools
            validation_handlers, _ = create_validation_tools()
            tools.update(validation_handlers)
        except Exception as e:
            logger.warning(f"Failed to load validation tools: {e}")

        try:
            from ..tools.earth_agent_tools import create_earth_agent_tools
            tools.update(create_earth_agent_tools(self.config))
        except Exception as e:
            logger.warning(f"Failed to load Earth-Agent tools: {e}")

        # Graph builder tools (atomic graph construction)
        try:
            if not os.environ.get("DISABLE_GRAPH_BUILDER_TOOLS"):
                from ..tools.graph_builder_tools import create_graph_builder_tools
                tools.update(create_graph_builder_tools(self.config))
        except Exception as e:
            logger.warning(f"Failed to load graph builder tools: {e}")

        # Workflow executor (LAST — needs access to all other tool handlers)
        try:
            if not os.environ.get("DISABLE_WORKFLOW_EXECUTOR"):
                from ..tools.workflow_executor import create_workflow_executor_tools
                tools.update(create_workflow_executor_tools(self.config, tool_handlers=tools))
        except Exception as e:
            logger.warning(f"Failed to load workflow executor tools: {e}")


        # save_workflow_template handler (inline — needs access to self._last_process_graph)
        _self = self  # capture for closure

        async def _handle_save_workflow_template(args: Dict[str, Any]) -> Dict[str, Any]:
            """Save the current workflow as a reusable template."""
            from ..batch.template_store import TemplateStore
            from ..batch.models import WorkflowTemplateCreate

            db_path = _self.config.sqlite_path
            store = TemplateStore(db_path=db_path)

            # Get process graph: explicit arg > last generated in session
            process_graph = args.get("process_graph") or _self._last_process_graph
            if not process_graph:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "No process graph available. Either provide one explicitly or run a graph-generating tool first (openeo_generate_graph, openeo_compose_graph, openeo_raw_graph)."
                    })}]
                }

            name = args["name"]
            description = args.get("description", "")
            tags = args.get("tags", [])

            # Auto-detect parameters from the graph
            detected_params = TemplateStore.auto_detect_parameters(process_graph)

            # Extract metadata for collection_id and index_type
            meta = TemplateStore._extract_graph_metadata(process_graph)

            data = WorkflowTemplateCreate(
                name=name,
                description=description,
                process_graph=process_graph,
                parameters=detected_params,
                tags=tags,
                source_query=description,
                collection_id=meta.get("collection_id", ""),
                index_type=meta.get("index_type", ""),
            )

            template = store.create_template(data, user_id="admin")
            if template is None:
                return {
                    "content": [{"type": "text", "text": json.dumps({
                        "error": "Failed to save template"
                    })}]
                }

            param_names = [p.name for p in detected_params]

            return {
                "content": [{"type": "text", "text": json.dumps({
                    "status": "saved",
                    "template_id": template.id,
                    "name": template.name,
                    "description": template.description,
                    "tags": tags,
                    "version": template.version,
                    "collection_id": meta.get("collection_id", ""),
                    "index_type": meta.get("index_type", ""),
                    "detected_parameters": param_names,
                    "message": f"Template '{name}' saved successfully. It can be used for batch jobs across multiple locations."
                })}]
            }

        tools["save_workflow_template"] = _handle_save_workflow_template

        return tools

    def _build_system_messages(self, user_prompt: str) -> list:
        """Build system prompt with optional learned pattern injection.

        The base SYSTEM_PROMPT retains cache_control for prompt caching.
        Learned patterns are appended as a second block that varies per-query.
        """
        blocks = [{
            "type": "text",
            "text": self.SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"}
        }]
        if os.environ.get("DISABLE_LEARNED_PATTERNS"):
            return blocks
        try:
            from ..batch.template_store import TemplateStore
            store = TemplateStore(self.config.sqlite_path)
            text = store.get_injection_text(user_prompt)
            if text:
                blocks.append({"type": "text", "text": text})
        except Exception:
            pass  # Never break chat for pattern retrieval
        return blocks

    async def chat(
        self,
        prompt: str,
        user_id: str,
        session_id: Optional[str] = None
    ) -> AsyncIterator[dict]:
        """
        Chat with the OpenEO AI Assistant.

        Args:
            prompt: User's message
            user_id: Authenticated user ID (from OIDC)
            session_id: Optional session to resume

        Yields:
            Response messages with text, tool results, or visualizations
        """
        # Get or create session
        if session_id:
            session = self.session_manager.get_session(session_id)
            if session:
                # Messages are stored in context.messages
                context = session.get("context", {})
                messages = context.get("messages", [])
                # Limit conversation history to prevent token overflow
                # Keep last 20 messages (10 turns) to stay under token limits
                if len(messages) > 20:
                    messages = messages[-20:]
            else:
                session_id = self.session_manager.create_session(user_id)
                messages = []
        else:
            session_id = self.session_manager.create_session(user_id)
            messages = []

        # Agentic loop - continues until Claude stops using tools
        turn = 0
        current_prompt = prompt

        while turn < self.config.max_turns:
            turn += 1
            tool_calls_this_turn = []
            should_continue = False

            try:
                # Add user message on first turn
                if turn == 1:
                    messages.append({"role": "user", "content": current_prompt})

                # Build tools list with cache_control on last item
                # Reason: 47 tool definitions = ~4K tokens. Caching avoids
                # re-processing them on every turn (up to 40% input token savings).
                cached_tools = list(TOOL_DEFINITIONS)
                if cached_tools:
                    cached_tools[-1] = {
                        **cached_tools[-1],
                        "cache_control": {"type": "ephemeral"},
                    }

                try:
                    budget.record_llm_call(get_current_user_id(), "direct", self.config.model)
                except Exception:  # noqa: BLE001
                    logger.debug("[budget] direct tag failed", exc_info=True)

                # Call Claude API (with prompt caching for system prompt + tools)
                response = await self.client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    system=self._build_system_messages(prompt),
                    tools=cached_tools,
                    messages=messages
                )

                # Build assistant message content
                assistant_content = []

                # Process each content block
                for block in response.content:
                    if block.type == "text":
                        yield {"type": "text", "content": block.text}
                        assistant_content.append({"type": "text", "text": block.text})

                    elif block.type == "tool_use":
                        tool_name = block.name
                        tool_input = block.input
                        tool_use_id = block.id

                        assistant_content.append({
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": tool_name,
                            "input": tool_input
                        })

                        # Check permission
                        if not openeo_permission_callback(tool_name, tool_input):
                            tool_result = {"error": f"Permission denied for tool: {tool_name}"}
                            yield {
                                "type": "tool_blocked",
                                "tool": tool_name,
                                "reason": "Permission denied"
                            }
                        else:
                            # Execute tool
                            try:
                                tool_result = await self._execute_tool(tool_name, tool_input)

                                # Check if result is a visualization
                                is_viz = (
                                    isinstance(tool_result, dict) and (
                                        tool_result.get("type") == "visualization" or
                                        tool_result.get("type") in ("map", "comparison_slider", "chart") or
                                        (tool_result.get("component", {}).get("type") in ("map", "comparison_slider", "chart"))
                                    )
                                )
                                if is_viz:
                                    # Extract the actual visualization component
                                    viz_content = tool_result.get("component", tool_result)
                                    yield {
                                        "type": "visualization",
                                        "content": viz_content
                                    }
                                else:
                                    yield {
                                        "type": "tool_result",
                                        "tool": tool_name,
                                        "result": tool_result
                                    }
                            except Exception as e:
                                logger.error(f"Tool execution error: {e}")
                                tool_result = {"error": str(e)}
                                yield {
                                    "type": "tool_error",
                                    "tool": tool_name,
                                    "error": str(e)
                                }

                        tool_calls_this_turn.append({
                            "tool_use_id": tool_use_id,
                            "result": tool_result
                        })

                # Add assistant message
                messages.append({"role": "assistant", "content": assistant_content})

                # If there were tool calls, add tool results and continue
                if tool_calls_this_turn:
                    tool_results_content = []
                    for tc in tool_calls_this_turn:
                        # Truncate large tool results to prevent token overflow
                        result_str = json.dumps(tc["result"]) if isinstance(tc["result"], (dict, list)) else str(tc["result"])
                        if len(result_str) > 6000:
                            # Smart truncation: keep first 4000 chars (main data) +
                            # last 1000 chars (often contains errors/summary)
                            result_str = (
                                result_str[:4000]
                                + "\n... [truncated middle — "
                                + f"{len(result_str) - 5000} chars removed] ...\n"
                                + result_str[-1000:]
                            )
                        tool_results_content.append({
                            "type": "tool_result",
                            "tool_use_id": tc["tool_use_id"],
                            "content": result_str
                        })
                    messages.append({"role": "user", "content": tool_results_content})
                    should_continue = True

                # Check if we should stop
                if response.stop_reason == "end_turn" and not tool_calls_this_turn:
                    break

                if not should_continue:
                    break

            except anthropic.APIError as e:
                logger.error(f"Claude API error: {e}")
                yield {"type": "error", "content": f"API error: {str(e)}"}
                return
            except Exception as e:
                if "ConnectionError" in str(type(e).__name__):
                    raise
                logger.error(f"Query error: {e}")
                yield {"type": "error", "content": str(e)}
                return

        # Save session
        self.session_manager.update_context(session_id, {"messages": messages})

        # Yield session info
        yield {"type": "session", "session_id": session_id}

    async def _execute_tool(self, tool_name: str, tool_input: Dict[str, Any]) -> Any:
        """Execute a tool and return its result."""
        if tool_name not in self.tools:
            raise ValueError(f"Unknown tool: {tool_name}")

        tool_func = self.tools[tool_name]

        # Call the tool function
        result = await tool_func(tool_input)

        # Reason: Track last process graph for save_workflow_template tool.
        # Graph-producing tools return the graph as JSON in their content.
        _GRAPH_TOOLS = frozenset({
            'openeo_generate_graph', 'openeo_compose_graph',
            'openeo_raw_graph', 'openeo_algorithm',
        })
        if tool_name in _GRAPH_TOOLS:
            try:
                _raw = result
                if isinstance(_raw, dict) and 'content' in _raw:
                    _c = _raw['content']
                    if isinstance(_c, list) and _c and isinstance(_c[0], dict) and 'text' in _c[0]:
                        _parsed = json.loads(_c[0]['text'])
                        if isinstance(_parsed, dict) and not _parsed.get('error'):
                            self._last_process_graph = _parsed
            except Exception:
                pass  # Never break tool execution for graph tracking


        # Extract content from Claude SDK format
        if isinstance(result, dict) and "content" in result:
            content = result["content"]
            if isinstance(content, list) and len(content) > 0:
                first_item = content[0]
                if isinstance(first_item, dict) and "text" in first_item:
                    try:
                        return json.loads(first_item["text"])
                    except json.JSONDecodeError:
                        return first_item["text"]
                return first_item
            return content

        return result

    def _build_options(self, user_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Build options dict for reference."""
        return {
            "model": self.config.model,
            "system_prompt": self.SYSTEM_PROMPT,
            "allowed_tools": list(self.tools.keys()),
            "can_use_tool": openeo_permission_callback,
            "custom_tools": self.tools,
            "max_turns": self.config.max_turns,
            "resume": session_id,
        }

    async def chat_sync(
        self,
        prompt: str,
        user_id: str,
        session_id: Optional[str] = None
    ) -> List[dict]:
        """
        Synchronous chat that collects all responses.

        Args:
            prompt: User's message
            user_id: Authenticated user ID
            session_id: Optional session to resume

        Returns:
            List of all response messages
        """
        responses = []
        async for response in self.chat(prompt, user_id, session_id):
            responses.append(response)
        return responses
