# ABOUTME: OpenEO data discovery and process graph generation tools.
# List collections, get metadata, and generate graphs from natural language.

"""
OpenEO data discovery and process graph tools.

Provides tools for listing collections, getting collection info,
and generating process graphs.
"""

import json
import logging
import os
import time
import threading
import httpx
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# TTL cache for STAC queries (5 minutes)
_CACHE_TTL_SECONDS = 300


class _TTLCache:
    """Simple thread-safe TTL cache using a dict with timestamps."""

    def __init__(self, ttl: float = _CACHE_TTL_SECONDS):
        self._store: Dict[str, Any] = {}  # key -> (value, expiry_time)
        self._lock = threading.Lock()
        self._ttl = ttl

    def get(self, key: str) -> Any:
        """Get a cached value. Returns None if not found or expired."""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            value, expiry = entry
            if time.monotonic() > expiry:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        """Store a value with TTL."""
        with self._lock:
            self._store[key] = (value, time.monotonic() + self._ttl)

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()


class OpenEOTools:
    """OpenEO data discovery tools."""

    def __init__(self, openeo_url: str = "http://localhost:8080/openeo/1.1.0"):
        """
        Initialize OpenEO tools.

        Args:
            openeo_url: Base URL of OpenEO API
        """
        self.openeo_url = openeo_url.rstrip("/")
        self._client = None
        self._cache = _TTLCache(ttl=_CACHE_TTL_SECONDS)

    @property
    def client(self) -> httpx.AsyncClient:
        """Lazy-initialize HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def _fetch_collections(self) -> List[Dict[str, Any]]:
        """Fetch collections from OpenEO API, with TTL caching."""
        cache_key = "collections_list"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        response = await self.client.get(f"{self.openeo_url}/collections")
        response.raise_for_status()
        data = response.json()
        result = data.get("collections", [])
        self._cache.set(cache_key, result)
        return result

    async def list_collections(self) -> List[Dict[str, Any]]:
        """
        List available Earth Observation collections.

        Returns:
            List of collection summaries
        """
        collections = await self._fetch_collections()
        return [
            {
                "id": c.get("id"),
                "title": c.get("title", c.get("id")),
                "description": c.get("description", ""),
            }
            for c in collections
        ]

    async def _fetch_collection_info(self, collection_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed collection info, with TTL caching."""
        cache_key = f"collection_info:{collection_id}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        response = await self.client.get(
            f"{self.openeo_url}/collections/{collection_id}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        result = response.json()
        self._cache.set(cache_key, result)
        return result

    async def get_collection_info(self, collection_id: str) -> Optional[Dict[str, Any]]:
        """
        Get detailed information about a collection.

        Args:
            collection_id: Collection identifier

        Returns:
            Collection details including bands, or None if not found
        """
        if not collection_id:
            raise ValueError("collection_id is required")

        info = await self._fetch_collection_info(collection_id)
        if info is None:
            return None

        # Extract band information
        bands = {}
        for asset_name, asset in info.get("item_assets", {}).items():
            if "eo:bands" in asset:
                for band in asset["eo:bands"]:
                    bands[band.get("name", asset_name)] = {
                        "common_name": band.get("common_name"),
                        "description": band.get("description"),
                    }
            else:
                bands[asset_name] = {
                    "description": asset.get("description", "")
                }

        # Build citation source URLs for provenance
        collection_id = info.get("id", collection_id)
        sources = []
        # STAC catalog link
        for link in info.get("links", []):
            if link.get("rel") in ("self", "root", "catalog"):
                sources.append(link.get("href"))
        # License info
        license_val = info.get("license")
        if license_val:
            sources.append(f"License: {license_val}")

        result = {
            "id": collection_id,
            "title": info.get("title"),
            "description": info.get("description"),
            "bands": bands,
            "extent": info.get("extent"),
        }
        if sources:
            result["sources"] = sources
        return result

    # ── Index definitions: band pairs + whether to use built-in ndvi process ──
    # Reason: Single source of truth for all normalized-difference indices.
    # cloud_mask_all and the composable builder use this to auto-select bands.
    INDEX_SPECS = {
        "ndvi":  {"band_x": "nir",    "band_y": "red",    "use_builtin": True},
        "ndwi":  {"band_x": "green",  "band_y": "nir",    "use_builtin": False},
        "mndwi": {"band_x": "green",  "band_y": "swir16", "use_builtin": False},
        "nbr":   {"band_x": "nir",    "band_y": "swir22", "use_builtin": False},
        "ndbi":  {"band_x": "swir16", "band_y": "nir",    "use_builtin": False},
        "ndsi":  {"band_x": "green",  "band_y": "swir16", "use_builtin": False},
        "ndmi":  {"band_x": "nir",    "band_y": "swir16", "use_builtin": False},
        "gndvi": {"band_x": "nir",    "band_y": "green",  "use_builtin": False},
        # Reason: NDRE uses NIR (842nm) and Red Edge 1 (705nm), NOT nir08/red
        "ndre":  {"band_x": "nir",    "band_y": "rededge1", "use_builtin": False},
        # SAVI REMOVED from INDEX_SPECS: its formula ((NIR-RED)/(NIR+RED+L))*(1+L)
        # cannot be expressed as normalized_difference. Route through
        # spectral_index_calculate post-hoc which has the correct formula.
        # Reason: NDCI uses Red Edge 1 (705nm) and Red (665nm) for chlorophyll-a in water
        "ndci":  {"band_x": "rededge1", "band_y": "red",  "use_builtin": False},
        # Reason: WIW (Water In Wetlands) = (blue - nir) / (blue + nir)
        "wiw":   {"band_x": "blue",   "band_y": "nir",    "use_builtin": False},
        # Reason: UI (Urban Index) = (swir22 - nir) / (swir22 + nir)
        "ui":    {"band_x": "swir22", "band_y": "nir",    "use_builtin": False},
        # Reason: NDTI (Normalized Difference Tillage Index) = (swir16 - swir22) / (swir16 + swir22)
        "ndti":  {"band_x": "swir16", "band_y": "swir22", "use_builtin": False},
    }

    def _extract_indices_from_description(self, desc: str) -> List[str]:
        """Extract spectral index names mentioned in a description string.

        Checks for longer index names first to avoid substring collision
        (e.g. "gndvi" before "ndvi").

        Returns list of found indices, or ["ndvi", "ndwi"] as sensible default
        if fewer than 2 are found.
        """
        found = []
        # Reason: Check longer names first to avoid substring collision
        sorted_indices = sorted(self.INDEX_SPECS.keys(), key=len, reverse=True)
        for idx in sorted_indices:
            if idx in desc and idx not in found:
                found.append(idx)
        return found if len(found) >= 2 else ["ndvi", "ndwi"]

    def _detect_kernel_type(self, desc: str) -> str:
        """Detect spatial filter type from a description string."""
        if any(w in desc for w in ["edge", "sobel"]):
            return "sobel_x"
        if "laplacian" in desc:
            return "laplacian"
        if "sharpen" in desc:
            return "sharpen"
        return "gaussian"  # default: smoothing

    def _detect_analysis_type(self, desc_lower: str) -> str:
        """
        Detect the analysis type from a lowercased description string.

        Args:
            desc_lower: Lowercased description of desired analysis

        Returns:
            Analysis type string used to select graph builder pattern
        """
        # Reason: More specific multi-word keywords must be checked before
        # generic single-word ones (e.g. "cloud-masked ndvi" before "ndvi").
        # CRITICAL: Indices whose names contain other index names as substrings
        # (gndvi contains "ndvi", ndmi contains "ndm") MUST come BEFORE the
        # shorter index to prevent false substring matches.
        keyword_map = [
            # Cloud-masked variants (checked first)
            (["cloud-masked", "cloud masked", "cloud-free", "cloud free"], "_cloud_prefix"),
            # Multi-index (checked before individual indices)
            # Reason: Must be checked BEFORE individual index keywords since
            # "ndvi and ndwi" contains "ndvi" which would otherwise match first.
            (["multi-index", "multiple indices", "combined indices",
              "compare indices", "ndvi and ndwi", "ndvi ndwi",
              "ndwi and ndvi", "ndvi and ndbi", "ndwi and ndbi"], "multi_index"),
            # Zonal statistics
            (["zonal statistics", "aggregate spatial", "mean per polygon",
              "average in region", "statistics per zone", "watershed average",
              "zonal stats"], "zonal_stats"),
            # Spatial filtering
            (["smooth", "blur", "sharpen", "edge detect", "gaussian filter",
              "spatial filter", "convolution", "apply kernel"], "spatial_filter"),
            # Monthly/periodic composites
            (["monthly composite", "monthly mosaic", "monthly aggregat"], "monthly_composite"),
            # EVI (special formula, not normalized_difference)
            (["evi", "enhanced vegetation"], "evi"),
            # Reason: GNDVI/NDRE checked BEFORE NDVI because "gndvi" contains
            # substring "ndvi" — checking NDVI first would always match.
            (["gndvi", "green ndvi", "chlorophyll index"], "gndvi"),
            (["ndre", "red edge"], "ndre"),
            (["ndmi", "moisture index", "drought index", "moisture stress"], "ndmi"),
            # Reason: MNDWI checked BEFORE NDWI — "mndwi" contains "ndwi" substring
            (["mndwi", "modified water"], "mndwi"),
            (["ndci", "chlorophyll index", "algal bloom", "eutrophic"], "ndci"),
            (["wiw", "water in wetland", "wetland index"], "wiw"),
            (["ndti", "tillage index"], "ndti"),
            (["ui", "urban index"], "ui"),
            # Standard indices (generic patterns AFTER specific ones)
            (["ndvi", "vegetation", "plant health", "greenness", "forest health"], "ndvi"),
            (["ndwi", "water body", "water detection", "flood extent"], "ndwi"),
            (["nbr", "burn severity", "burn", "fire"], "nbr"),
            (["ndbi", "built-up"], "ndbi"),
            (["ndsi", "snow cover", "snow"], "ndsi"),
            # Composite/aggregate
            (["composite", "mosaic"], "temporal_composite"),
            # Change detection
            (["change", "difference", "before and after"], "change_detection"),
        ]

        detected = None
        cloud_masked = False

        for keywords, analysis_type in keyword_map:
            for kw in keywords:
                if kw in desc_lower:
                    if analysis_type == "_cloud_prefix":
                        cloud_masked = True
                    elif detected is None:
                        detected = analysis_type
                    break

        if detected is None:
            return "generic"

        # Reason: If user asked for "cloud-masked NDWI" etc., prefix the type
        if cloud_masked and detected in self.INDEX_SPECS:
            return f"cloud_masked_{detected}"

        return detected

    # ── Composable graph node builders ──

    def _build_load_node(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        bands: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Build a load_collection process node."""
        args: Dict[str, Any] = {
            "id": collection,
            "spatial_extent": spatial_extent,
            "temporal_extent": temporal_extent,
        }
        if bands:
            args["bands"] = bands
        return {
            "process_id": "load_collection",
            "arguments": args,
        }

    def _build_reduce_time_node(
        self, from_node: str, reducer: str = "median"
    ) -> Dict[str, Any]:
        """Build a reduce_dimension node over the temporal dimension."""
        return {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": from_node},
                "dimension": "time",
                "reducer": {
                    "process_graph": {
                        reducer: {
                            "process_id": reducer,
                            "arguments": {
                                "data": {"from_parameter": "data"},
                            },
                            "result": True,
                        }
                    }
                },
            },
        }

    def _build_temporal_aggregate_node(
        self, from_node: str, reducer: str, period: str
    ) -> Dict[str, Any]:
        """Build an aggregate_temporal_period node (calendar-period reduction)."""
        return {
            "process_id": "aggregate_temporal_period",
            "arguments": {
                "data": {"from_node": from_node},
                "reducer": {
                    "process_graph": {
                        reducer: {
                            "process_id": reducer,
                            "arguments": {
                                "data": {"from_parameter": "data"},
                            },
                            "result": True,
                        }
                    }
                },
                "period": period,
            },
        }

    def _build_save_node(
        self, from_node: str, output_format: str
    ) -> Dict[str, Any]:
        """Build a save_result node (always marked as result=True)."""
        return {
            "process_id": "save_result",
            "arguments": {
                "data": {"from_node": from_node},
                "format": output_format,
            },
            "result": True,
        }

    def _build_cloud_mask_nodes(self, load_node: str = "load", suffix: str = "") -> Dict[str, Any]:
        """Build SCL-based cloud masking nodes: extract SCL -> between(8,11) -> mask.

        Args:
            load_node: Node ID to apply cloud masking to.
            suffix: Optional suffix for node IDs to avoid collision in multi-load graphs
                    (e.g., suffix="1" produces get_scl1, cloud_mask1, apply_mask1).

        Returns dict of nodes to merge into graph. Last node is 'apply_mask{suffix}'.
        """
        return {
            f"get_scl{suffix}": {
                "process_id": "array_element",
                "arguments": {
                    "data": {"from_node": load_node},
                    "label": "scl",
                },
            },
            f"cloud_mask{suffix}": {
                "process_id": "between",
                "arguments": {
                    "x": {"from_node": f"get_scl{suffix}"},
                    "min": 8,
                    "max": 11,
                },
            },
            f"apply_mask{suffix}": {
                "process_id": "mask",
                "arguments": {
                    "data": {"from_node": load_node},
                    "mask": {"from_node": f"cloud_mask{suffix}"},
                },
            },
        }

    def _build_index_nodes(
        self, index_name: str, data_node: str
    ) -> Dict[str, Any]:
        """Build nodes for a spectral index computation.

        For NDVI, uses the built-in ndvi process.
        For all others, uses normalized_difference with array_element band selection.

        Returns dict of nodes. Last node key is the index_name.
        """
        spec = self.INDEX_SPECS.get(index_name)
        if not spec:
            raise ValueError(f"Unknown index: {index_name}. Available: {list(self.INDEX_SPECS.keys())}")

        if spec.get("use_builtin"):
            # Reason: Built-in ndvi process is simpler and more reliable
            return {
                index_name: {
                    "process_id": "ndvi",
                    "arguments": {
                        "data": {"from_node": data_node},
                        "nir": spec["band_x"],
                        "red": spec["band_y"],
                    },
                }
            }
        else:
            return {
                f"{index_name}_x": {
                    "process_id": "array_element",
                    "arguments": {
                        "data": {"from_node": data_node},
                        "label": spec["band_x"],
                    },
                },
                f"{index_name}_y": {
                    "process_id": "array_element",
                    "arguments": {
                        "data": {"from_node": data_node},
                        "label": spec["band_y"],
                    },
                },
                index_name: {
                    "process_id": "normalized_difference",
                    "arguments": {
                        "x": {"from_node": f"{index_name}_x"},
                        "y": {"from_node": f"{index_name}_y"},
                    },
                },
            }

    def _get_bands_for_index(self, index_name: str, cloud_mask: bool = False) -> List[str]:
        """Get the required bands for an index, optionally adding SCL for cloud masking."""
        spec = self.INDEX_SPECS.get(index_name)
        if not spec:
            return []
        bands = [spec["band_x"], spec["band_y"]]
        # Deduplicate while preserving order
        seen = set()
        unique_bands = []
        for b in bands:
            if b not in seen:
                seen.add(b)
                unique_bands.append(b)
        if cloud_mask:
            unique_bands.append("scl")
        return unique_bands

    # ── Composable graph builder ──

    def _build_index_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        index_name: str,
        cloud_mask: bool = False,
        time_reducer: str = "median",
        period: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Composable graph builder for any spectral index.

        Builds: load → [cloud_mask] → index → reduce_time/aggregate_period → save

        Args:
            collection: STAC collection ID
            spatial_extent: Bounding box
            temporal_extent: [start, end] ISO dates
            output_format: GTiff, netCDF, etc.
            index_name: Key from INDEX_SPECS (ndvi, ndwi, nbr, etc.)
            cloud_mask: If True, adds SCL-based cloud masking before index
            time_reducer: Aggregation function (median, mean, max, min)
            period: If set, uses aggregate_temporal_period instead of reduce_dimension
        """
        bands = self._get_bands_for_index(index_name, cloud_mask=cloud_mask)
        graph = {}

        # Step 1: Load
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        # Step 2: Cloud masking (optional)
        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        # Step 3: Compute index
        graph.update(self._build_index_nodes(index_name, data_node))

        # Step 4: Temporal reduction or aggregation
        if time_reducer == "none":
            last_node = index_name
        elif period:
            graph["aggregate_time"] = self._build_temporal_aggregate_node(
                index_name, time_reducer, period
            )
            last_node = "aggregate_time"
        else:
            graph["reduce_time"] = self._build_reduce_time_node(index_name, time_reducer)
            last_node = "reduce_time"

        # Step 5: Save
        graph["save"] = self._build_save_node(last_node, output_format)

        return graph

    # ── EVI (special formula, not normalized_difference) ──

    def _build_evi_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        cloud_mask: bool = False,
        time_reducer: str = "median",
    ) -> Dict[str, Any]:
        """
        Build EVI: 2.5 * (NIR - RED) / (NIR + 6*RED - 7.5*BLUE + 1)

        load(blue,red,nir[,scl]) → [cloud_mask] → reduce_time → apply(EVI) → save
        """
        bands = ["blue", "red", "nir"]
        if cloud_mask:
            bands.append("scl")

        graph = {}
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        if time_reducer != "none":
            graph["reduce_time"] = self._build_reduce_time_node(data_node, time_reducer)
            evi_input = "reduce_time"
        else:
            evi_input = data_node

        graph["evi"] = {
            "process_id": "reduce_dimension",
            "arguments": {
                "data": {"from_node": evi_input},
                "dimension": "bands",
                "reducer": {
                    "process_graph": {
                        "nir": {
                            "process_id": "array_element",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "label": "nir",
                            },
                        },
                        "red": {
                            "process_id": "array_element",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "label": "red",
                            },
                        },
                        "blue": {
                            "process_id": "array_element",
                            "arguments": {
                                "data": {"from_parameter": "data"},
                                "label": "blue",
                            },
                        },
                        "nir_minus_red": {
                            "process_id": "subtract",
                            "arguments": {
                                "x": {"from_node": "nir"},
                                "y": {"from_node": "red"},
                            },
                        },
                        "red_times_6": {
                            "process_id": "multiply",
                            "arguments": {
                                "x": {"from_node": "red"},
                                "y": 6.0,
                            },
                        },
                        "blue_times_7_5": {
                            "process_id": "multiply",
                            "arguments": {
                                "x": {"from_node": "blue"},
                                "y": 7.5,
                            },
                        },
                        "nir_plus_6red": {
                            "process_id": "add",
                            "arguments": {
                                "x": {"from_node": "nir"},
                                "y": {"from_node": "red_times_6"},
                            },
                        },
                        "denom_partial": {
                            "process_id": "subtract",
                            "arguments": {
                                "x": {"from_node": "nir_plus_6red"},
                                "y": {"from_node": "blue_times_7_5"},
                            },
                        },
                        "denominator": {
                            "process_id": "add",
                            "arguments": {
                                "x": {"from_node": "denom_partial"},
                                "y": 1.0,
                            },
                        },
                        "ratio": {
                            "process_id": "divide",
                            "arguments": {
                                "x": {"from_node": "nir_minus_red"},
                                "y": {"from_node": "denominator"},
                            },
                        },
                        "evi_result": {
                            "process_id": "multiply",
                            "arguments": {
                                "x": 2.5,
                                "y": {"from_node": "ratio"},
                            },
                            "result": True,
                        },
                    }
                },
            },
        }

        graph["save"] = self._build_save_node("evi", output_format)
        return graph

    # ── Temporal composite ──

    def _build_temporal_composite(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        cloud_mask: bool = False,
        time_reducer: str = "median",
        period: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build temporal composite: load → [cloud_mask] → reduce/aggregate → save."""
        bands = None
        graph = {}

        if cloud_mask:
            bands = ["blue", "green", "red", "nir", "swir16", "swir22", "scl"]

        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        if time_reducer == "none":
            last_node = data_node
        elif period:
            graph["aggregate_time"] = self._build_temporal_aggregate_node(
                data_node, time_reducer, period
            )
            last_node = "aggregate_time"
        else:
            graph["reduce_time"] = self._build_reduce_time_node(data_node, time_reducer)
            last_node = "reduce_time"

        graph["save"] = self._build_save_node(last_node, output_format)
        return graph

    # ── Change detection ──

    def _build_change_detection(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        index_name: str = "ndvi",
        cloud_mask: bool = False,
    ) -> Dict[str, Any]:
        """
        Build change detection: split temporal range in half,
        compute index on each period, subtract to get change.
        """
        from datetime import datetime

        start = datetime.fromisoformat(temporal_extent[0])
        end = datetime.fromisoformat(temporal_extent[1])
        mid = start + (end - start) / 2
        mid_str = mid.strftime("%Y-%m-%d")

        period1 = [temporal_extent[0], mid_str]
        period2 = [mid_str, temporal_extent[1]]

        spec = self.INDEX_SPECS.get(index_name, self.INDEX_SPECS["ndvi"])
        bands = self._get_bands_for_index(index_name, cloud_mask=cloud_mask)

        graph = {}

        # Period 1
        graph["load1"] = self._build_load_node(
            collection, spatial_extent, period1, bands=bands
        )
        data_node_1 = "load1"
        if cloud_mask:
            cm1 = self._build_cloud_mask_nodes("load1")
            # Rename nodes to avoid collision with period 2
            graph["get_scl1"] = cm1["get_scl"]
            graph["cloud_mask1"] = cm1["cloud_mask"]
            graph["cloud_mask1"]["arguments"]["x"] = {"from_node": "get_scl1"}
            graph["apply_mask1"] = cm1["apply_mask"]
            graph["apply_mask1"]["arguments"]["data"] = {"from_node": "load1"}
            graph["apply_mask1"]["arguments"]["mask"] = {"from_node": "cloud_mask1"}
            data_node_1 = "apply_mask1"

        idx1_nodes = self._build_index_nodes(index_name, data_node_1)
        # Rename index nodes for period 1
        for k, v in idx1_nodes.items():
            graph[f"{k}1"] = v
            # Fix from_node references to include "1" suffix
            if "arguments" in v:
                for arg_key, arg_val in v["arguments"].items():
                    if isinstance(arg_val, dict) and "from_node" in arg_val:
                        orig = arg_val["from_node"]
                        if orig in idx1_nodes or orig == data_node_1:
                            if orig != data_node_1:
                                v["arguments"][arg_key] = {"from_node": f"{orig}1"}

        graph["reduce_time1"] = self._build_reduce_time_node(f"{index_name}1", "median")

        # Period 2
        graph["load2"] = self._build_load_node(
            collection, spatial_extent, period2, bands=bands
        )
        data_node_2 = "load2"
        if cloud_mask:
            cm2 = self._build_cloud_mask_nodes("load2")
            graph["get_scl2"] = cm2["get_scl"]
            graph["get_scl2"]["arguments"]["data"] = {"from_node": "load2"}
            graph["cloud_mask2"] = cm2["cloud_mask"]
            graph["cloud_mask2"]["arguments"]["x"] = {"from_node": "get_scl2"}
            graph["apply_mask2"] = cm2["apply_mask"]
            graph["apply_mask2"]["arguments"]["data"] = {"from_node": "load2"}
            graph["apply_mask2"]["arguments"]["mask"] = {"from_node": "cloud_mask2"}
            data_node_2 = "apply_mask2"

        idx2_nodes = self._build_index_nodes(index_name, data_node_2)
        for k, v in idx2_nodes.items():
            graph[f"{k}2"] = v
            if "arguments" in v:
                for arg_key, arg_val in v["arguments"].items():
                    if isinstance(arg_val, dict) and "from_node" in arg_val:
                        orig = arg_val["from_node"]
                        if orig in idx2_nodes or orig == data_node_2:
                            if orig != data_node_2:
                                v["arguments"][arg_key] = {"from_node": f"{orig}2"}

        graph["reduce_time2"] = self._build_reduce_time_node(f"{index_name}2", "median")

        # Change = period2 - period1
        graph["change"] = {
            "process_id": "merge_cubes",
            "arguments": {
                "cube1": {"from_node": "reduce_time2"},
                "cube2": {"from_node": "reduce_time1"},
                "overlap_resolver": {
                    "process_graph": {
                        "subtract": {
                            "process_id": "subtract",
                            "arguments": {
                                "x": {"from_parameter": "x"},
                                "y": {"from_parameter": "y"},
                            },
                            "result": True,
                        }
                    }
                },
            },
        }

        graph["save"] = self._build_save_node("change", output_format)
        return graph

    # ── Multi-index composite ──

    # Reason: Preset kernel matrices for spatial filtering.
    # Gaussian kernels are normalized (factor = 1/sum), others use factor=1.
    SPATIAL_KERNELS = {
        "gaussian_3": {"kernel": [[1, 2, 1], [2, 4, 2], [1, 2, 1]], "factor": 1.0 / 16},
        "gaussian_5": {
            "kernel": [
                [1, 4, 6, 4, 1],
                [4, 16, 24, 16, 4],
                [6, 24, 36, 24, 6],
                [4, 16, 24, 16, 4],
                [1, 4, 6, 4, 1],
            ],
            "factor": 1.0 / 256,
        },
        "sobel_x": {"kernel": [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], "factor": 1.0},
        "sobel_y": {"kernel": [[-1, -2, -1], [0, 0, 0], [1, 2, 1]], "factor": 1.0},
        "laplacian": {"kernel": [[0, 1, 0], [1, -4, 1], [0, 1, 0]], "factor": 1.0},
        "sharpen": {"kernel": [[0, -1, 0], [-1, 5, -1], [0, -1, 0]], "factor": 1.0},
    }

    def _build_multi_index_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        indices: List[str],
        cloud_mask: bool = False,
        time_reducer: str = "median",
    ) -> Dict[str, Any]:
        """
        Build a multi-index composite: compute N spectral indices in one graph.

        Loads bands once, reduces time, computes each index, then merges
        results via merge_cubes into a single multi-band output.

        Args:
            collection: STAC collection ID
            spatial_extent: Bounding box
            temporal_extent: [start, end] ISO dates
            output_format: GTiff, netCDF, etc.
            indices: List of index names from INDEX_SPECS (e.g. ["ndvi", "ndwi"])
            cloud_mask: If True, adds SCL-based cloud masking
            time_reducer: Aggregation function (median, mean, max, min)
        """
        # Reason: Cap at 5 indices to keep graph size manageable
        if len(indices) > 5:
            indices = indices[:5]
        if len(indices) < 2:
            raise ValueError("multi_index requires at least 2 indices")

        # Collect union of all bands needed across all indices
        all_bands = set()
        for idx in indices:
            spec = self.INDEX_SPECS.get(idx)
            if not spec:
                raise ValueError(f"Unknown index: {idx}. Available: {list(self.INDEX_SPECS.keys())}")
            all_bands.add(spec["band_x"])
            all_bands.add(spec["band_y"])
        if cloud_mask:
            all_bands.add("scl")
        bands_list = sorted(all_bands)

        graph = {}

        # Step 1: Single load with all needed bands
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands_list
        )

        # Step 2: Cloud masking (optional)
        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        # Step 3: Reduce time FIRST (smaller datacube = faster index compute)
        if time_reducer != "none":
            graph["reduce_time"] = self._build_reduce_time_node(data_node, time_reducer)
            index_input = "reduce_time"
        else:
            index_input = data_node

        # Step 4: Compute each index and rename bands
        merge_sources = []
        for idx_name in indices:
            # Compute index from the (optionally time-reduced) cube
            idx_nodes = self._build_index_nodes(idx_name, index_input)
            # Prefix node names with index name to avoid collision
            for k, v in idx_nodes.items():
                prefixed_key = f"filter_{idx_name}" if k == f"{idx_name}_x" else \
                               f"filter2_{idx_name}" if k == f"{idx_name}_y" else \
                               f"nd_{idx_name}" if k == idx_name else k
                # Fix from_node references
                node_copy = json.loads(json.dumps(v))
                if "arguments" in node_copy:
                    for arg_key, arg_val in node_copy["arguments"].items():
                        if isinstance(arg_val, dict) and "from_node" in arg_val:
                            orig = arg_val["from_node"]
                            if orig == f"{idx_name}_x":
                                node_copy["arguments"][arg_key] = {"from_node": f"filter_{idx_name}"}
                            elif orig == f"{idx_name}_y":
                                node_copy["arguments"][arg_key] = {"from_node": f"filter2_{idx_name}"}
                graph[prefixed_key] = node_copy

            # Rename the result band to the index name
            nd_node = f"nd_{idx_name}"
            # An index result has no bands dimension left, so give it one to merge along.
            graph[f"rename_{idx_name}"] = {
                "process_id": "add_dimension",
                "arguments": {
                    "data": {"from_node": nd_node},
                    "name": "bands",
                    "label": idx_name,
                    "type": "bands",
                },
            }
            merge_sources.append(f"rename_{idx_name}")

        # Step 5: Merge index results with merge_cubes
        # Reason: Band labels differ, so no overlap_resolver needed
        if len(merge_sources) == 1:
            last_node = merge_sources[0]
        else:
            # Chain merge_cubes: merge(idx0, idx1) → merge(result, idx2) → ...
            graph["merge_01"] = {
                "process_id": "merge_cubes",
                "arguments": {
                    "cube1": {"from_node": merge_sources[0]},
                    "cube2": {"from_node": merge_sources[1]},
                },
            }
            last_node = "merge_01"
            for i in range(2, len(merge_sources)):
                merge_key = f"merge_{i - 1:02d}{i:02d}"
                graph[merge_key] = {
                    "process_id": "merge_cubes",
                    "arguments": {
                        "cube1": {"from_node": last_node},
                        "cube2": {"from_node": merge_sources[i]},
                    },
                }
                last_node = merge_key

        # Step 6: Save
        graph["save"] = self._build_save_node(last_node, output_format)
        return graph

    # ── Zonal statistics ──

    def _build_zonal_stats_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        geometries: Dict[str, Any],
        reducer: str = "mean",
        index_name: Optional[str] = None,
        cloud_mask: bool = False,
        time_reducer: str = "median",
    ) -> Dict[str, Any]:
        """
        Build a zonal statistics graph using aggregate_spatial.

        Computes aggregate statistics within GeoJSON polygon(s).

        Args:
            collection: STAC collection ID
            spatial_extent: Bounding box encompassing all geometries
            temporal_extent: [start, end] ISO dates
            output_format: Output format (forced to JSON for aggregate_spatial)
            geometries: GeoJSON FeatureCollection or Geometry object
            reducer: Aggregation function (mean, median, min, max, sum, sd)
            index_name: Optional spectral index to compute first
            cloud_mask: If True, adds SCL-based cloud masking
        """
        bands = None
        if index_name:
            bands = self._get_bands_for_index(index_name, cloud_mask=cloud_mask)
        elif cloud_mask:
            bands = ["scl"]

        graph = {}

        # Step 1: Load
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        # Step 2: Cloud masking (optional)
        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        # Step 3: Compute index (optional)
        if index_name:
            graph.update(self._build_index_nodes(index_name, data_node))
            data_node = index_name

        # Step 4: Reduce time (skip if time_reducer="none")
        if time_reducer != "none":
            graph["reduce_time"] = self._build_reduce_time_node(data_node, time_reducer)
            agg_input = "reduce_time"
        else:
            agg_input = data_node

        # Step 5: Load geometries
        graph["geometries"] = {
            "process_id": "load_geojson",
            "arguments": {
                "data": geometries,
            },
        }

        # Step 6: Aggregate spatial
        graph["aggregate_spatial"] = {
            "process_id": "aggregate_spatial",
            "arguments": {
                "data": {"from_node": agg_input},
                "geometries": {"from_node": "geometries"},
                "reducer": {
                    "process_graph": {
                        reducer: {
                            "process_id": reducer,
                            "arguments": {
                                "data": {"from_parameter": "data"},
                            },
                            "result": True,
                        }
                    }
                },
            },
        }

        # Step 7: Save as JSON (aggregate_spatial returns tabular data)
        graph["save"] = self._build_save_node("aggregate_spatial", "JSON")
        return graph

    # ── Spatial filtering (convolution kernels) ──

    def _build_spatial_filter_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        kernel_type: str = "gaussian",
        kernel_size: int = 3,
        cloud_mask: bool = False,
        index_name: Optional[str] = None,
        time_reducer: str = "median",
    ) -> Dict[str, Any]:
        """
        Build a spatial filter graph using apply_kernel.

        Applies a convolution kernel for smoothing, edge detection, or sharpening.

        Args:
            collection: STAC collection ID
            spatial_extent: Bounding box
            temporal_extent: [start, end] ISO dates
            output_format: GTiff, netCDF, etc.
            kernel_type: gaussian, sobel_x, sobel_y, laplacian, sharpen
            kernel_size: 3 or 5 (only affects gaussian)
            cloud_mask: If True, adds SCL-based cloud masking
            index_name: Optional spectral index to compute first
        """
        # Resolve kernel key
        if kernel_type == "gaussian":
            kernel_key = f"gaussian_{kernel_size}"
        else:
            kernel_key = kernel_type

        kernel_spec = self.SPATIAL_KERNELS.get(kernel_key)
        if not kernel_spec:
            raise ValueError(f"Unknown kernel: {kernel_key}. Available: {list(self.SPATIAL_KERNELS.keys())}")

        bands = None
        if index_name:
            bands = self._get_bands_for_index(index_name, cloud_mask=cloud_mask)
        elif cloud_mask:
            bands = ["scl"]

        graph = {}

        # Step 1: Load
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        # Step 2: Cloud masking (optional)
        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        # Step 3: Compute index (optional)
        if index_name:
            graph.update(self._build_index_nodes(index_name, data_node))
            data_node = index_name

        # Step 4: Reduce time (skip if time_reducer="none")
        if time_reducer != "none":
            graph["reduce_time"] = self._build_reduce_time_node(data_node, time_reducer)
            kernel_input = "reduce_time"
        else:
            kernel_input = data_node

        # Step 5: Apply kernel
        graph["apply_kernel"] = {
            "process_id": "apply_kernel",
            "arguments": {
                "data": {"from_node": kernel_input},
                "kernel": kernel_spec["kernel"],
                "factor": kernel_spec["factor"],
                "border": 0,
            },
        }

        # Step 6: Save
        graph["save"] = self._build_save_node("apply_kernel", output_format)
        return graph

    # ── Composable pipeline builder ──

    def compose_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        steps: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Compose a custom process graph from a declarative step list.

        Steps auto-wire sequentially via last_node tracking. Bands are
        auto-resolved from index operations.

        Args:
            collection: STAC collection ID
            spatial_extent: Bounding box dict
            temporal_extent: [start, end] ISO dates
            steps: List of step dicts with 'op' and op-specific params

        Returns:
            Complete OpenEO process graph dict

        Raises:
            ValueError: On invalid step configuration
        """
        if not steps:
            raise ValueError("Steps array cannot be empty")
        if steps[0].get("op") != "load":
            raise ValueError("First step must be 'load'")
        if steps[-1].get("op") != "save":
            raise ValueError("Last step must be 'save'")

        valid_ops = {"load", "cloud_mask", "index", "reduce_time",
                     "aggregate_time", "spatial_filter", "save",
                     "filter_bands", "filter_temporal", "resample",
                     "mask_geometry", "apply_math", "rename_bands"}
        for i, step in enumerate(steps):
            op = step.get("op")
            if op not in valid_ops:
                raise ValueError(f"Step {i}: unknown op '{op}'. Valid: {sorted(valid_ops)}")

        # ── Band auto-resolution ──
        # Scan steps for index ops and collect required bands
        auto_bands: List[str] = []
        has_cloud_mask = any(s.get("op") == "cloud_mask" for s in steps)

        for step in steps:
            if step.get("op") == "index":
                index_name = step.get("index_name", "").lower()
                if not index_name:
                    raise ValueError("'index' step requires 'index_name'")
                spec = self.INDEX_SPECS.get(index_name)
                if not spec:
                    raise ValueError(
                        f"Unknown index: {index_name}. "
                        f"Available: {sorted(self.INDEX_SPECS.keys())}"
                    )
                auto_bands.extend([spec["band_x"], spec["band_y"]])

        if has_cloud_mask:
            auto_bands.append("scl")

        # Deduplicate while preserving order
        seen = set()
        unique_bands = []
        for b in auto_bands:
            if b not in seen:
                seen.add(b)
                unique_bands.append(b)

        # ── Build graph step by step ──
        graph: Dict[str, Any] = {}
        last_node: Optional[str] = None

        for i, step in enumerate(steps):
            op = step.get("op")

            if op == "load":
                # Use explicit bands if provided, otherwise auto-resolved
                bands = step.get("bands", unique_bands if unique_bands else None)
                graph["load"] = self._build_load_node(
                    collection, spatial_extent, temporal_extent, bands=bands
                )
                last_node = "load"

            elif op == "cloud_mask":
                graph.update(self._build_cloud_mask_nodes(last_node))
                last_node = "apply_mask"

            elif op == "index":
                index_name = step["index_name"].lower()
                graph.update(self._build_index_nodes(index_name, last_node))
                last_node = index_name

            elif op == "reduce_time":
                reducer = step.get("reducer", "median")
                graph["reduce_time"] = self._build_reduce_time_node(last_node, reducer)
                last_node = "reduce_time"

            elif op == "aggregate_time":
                period = step.get("period")
                if not period:
                    raise ValueError("'aggregate_time' step requires 'period'")
                reducer = step.get("reducer", "median")
                graph["aggregate_time"] = self._build_temporal_aggregate_node(
                    last_node, reducer, period
                )
                last_node = "aggregate_time"

            elif op == "spatial_filter":
                kernel_type = step.get("kernel_type", "gaussian_3")
                kernel_spec = self.SPATIAL_KERNELS.get(kernel_type)
                if not kernel_spec:
                    raise ValueError(
                        f"Unknown kernel: {kernel_type}. "
                        f"Available: {sorted(self.SPATIAL_KERNELS.keys())}"
                    )
                graph["apply_kernel"] = {
                    "process_id": "apply_kernel",
                    "arguments": {
                        "data": {"from_node": last_node},
                        "kernel": kernel_spec["kernel"],
                        "factor": kernel_spec["factor"],
                        "border": 0,
                    },
                }
                last_node = "apply_kernel"

            elif op == "filter_bands":
                bands = step.get("bands", [])
                if not bands:
                    raise ValueError("'filter_bands' step requires 'bands' list")
                nid = f"filter_bands_{i}"
                graph[nid] = {
                    "process_id": "filter_bands",
                    "arguments": {
                        "data": {"from_node": last_node},
                        "bands": bands,
                    },
                }
                last_node = nid

            elif op == "filter_temporal":
                extent = step.get("extent", [])
                if not extent or len(extent) < 2:
                    raise ValueError("'filter_temporal' step requires 'extent' [start, end]")
                nid = f"filter_temporal_{i}"
                graph[nid] = {
                    "process_id": "filter_temporal",
                    "arguments": {
                        "data": {"from_node": last_node},
                        "extent": extent,
                    },
                }
                last_node = nid

            elif op == "resample":
                resolution = step.get("resolution")
                method = step.get("method", "bilinear")
                projection = step.get("projection")
                nid = f"resample_{i}"
                args = {"data": {"from_node": last_node}}
                if resolution:
                    args["resolution"] = resolution
                if projection:
                    args["projection"] = projection
                args["method"] = method
                graph[nid] = {
                    "process_id": "resample_spatial",
                    "arguments": args,
                }
                last_node = nid

            elif op == "mask_geometry":
                geojson = step.get("geometries")
                if not geojson:
                    raise ValueError("'mask_geometry' step requires 'geometries' GeoJSON")
                nid = f"mask_polygon_{i}"
                graph[nid] = {
                    "process_id": "mask_polygon",
                    "arguments": {
                        "data": {"from_node": last_node},
                        "mask": geojson,
                    },
                }
                last_node = nid

            elif op == "apply_math":
                # Apply scalar math: multiply, add, linear_scale_range
                operator = step.get("operator", "multiply")
                value = step.get("value")
                if value is None:
                    raise ValueError("'apply_math' step requires 'value'")
                nid = f"math_{i}"
                graph[nid] = {
                    "process_id": operator,
                    "arguments": {
                        "x": {"from_node": last_node},
                        "y": value,
                    },
                }
                last_node = nid

            elif op == "rename_bands":
                target = step.get("target", [])
                if not target:
                    raise ValueError("'rename_bands' step requires 'target' list")
                nid = f"rename_bands_{i}"
                graph[nid] = {
                    "process_id": "rename_labels",
                    "arguments": {
                        "data": {"from_node": last_node},
                        "dimension": "bands",
                        "target": target,
                    },
                }
                last_node = nid

            elif op == "save":
                output_format = step.get("output_format", "GTiff")
                graph["save"] = self._build_save_node(last_node, output_format)
                last_node = "save"

        # Validate graph structure before returning
        errors = self._validate_graph_schema(graph)
        if errors:
            return {
                "error": f"Composed graph has {len(errors)} structural error(s)",
                "errors": errors,
                "graph": graph,
            }

        return graph

    # ── Algorithm templates (complex multi-process workflows) ──

    ALGORITHM_SPECS = {
        "snow_cover": {
            "description": "Snow cover mapping using NDSI > threshold AND NDVI < threshold",
            "bands": ["green", "red", "nir", "swir16"],
        },
        "burn_severity": {
            "description": "Burn severity via dNBR (pre-fire NBR minus post-fire NBR)",
            "bands": ["nir", "swir22"],
            "dual_temporal": True,
        },
        "flood_extent": {
            "description": "Flood extent mapping using MNDWI > threshold",
            "bands": ["green", "swir16"],
        },
        "urban_growth": {
            "description": "Urban growth detection via NDBI change between two periods",
            "bands": ["swir16", "nir"],
            "dual_temporal": True,
        },
    }

    def _build_snow_cover_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        ndsi_threshold: float = 0.4,
        ndvi_threshold: float = 0.2,
        cloud_mask: bool = True,
        time_reducer: str = "mean",
    ) -> Dict[str, Any]:
        """
        Snow cover: NDSI > threshold AND NDVI < threshold.

        Uses mean reducer by default to produce snow frequency map.
        """
        bands = ["green", "red", "nir", "swir16"]
        if cloud_mask:
            bands.append("scl")

        graph = {}
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        # NDSI = (green - swir16) / (green + swir16)
        graph["ndsi_green"] = {
            "process_id": "array_element",
            "arguments": {"data": {"from_node": data_node}, "label": "green"},
        }
        graph["ndsi_swir"] = {
            "process_id": "array_element",
            "arguments": {"data": {"from_node": data_node}, "label": "swir16"},
        }
        graph["ndsi"] = {
            "process_id": "normalized_difference",
            "arguments": {
                "x": {"from_node": "ndsi_green"},
                "y": {"from_node": "ndsi_swir"},
            },
        }

        # NDSI > threshold → snow candidate
        graph["snow_mask"] = {
            "process_id": "gt",
            "arguments": {"x": {"from_node": "ndsi"}, "y": ndsi_threshold},
        }

        # NDVI for vegetation exclusion
        graph["ndvi"] = {
            "process_id": "ndvi",
            "arguments": {
                "data": {"from_node": data_node},
                "nir": "nir",
                "red": "red",
            },
        }

        # NDVI < threshold → non-vegetation
        graph["veg_mask"] = {
            "process_id": "lt",
            "arguments": {"x": {"from_node": "ndvi"}, "y": ndvi_threshold},
        }

        # Combine: snow AND non-vegetation
        graph["snow_combined"] = {
            "process_id": "and",
            "arguments": {
                "x": {"from_node": "snow_mask"},
                "y": {"from_node": "veg_mask"},
            },
        }

        # Reduce time (mean on boolean gives snow frequency 0-1)
        if time_reducer != "none":
            graph["reduce_time"] = self._build_reduce_time_node(
                "snow_combined", time_reducer
            )
            last_node = "reduce_time"
        else:
            last_node = "snow_combined"

        graph["save"] = self._build_save_node(last_node, output_format)
        return graph

    def _build_burn_severity_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        pre_event: List[str],
        post_event: List[str],
        output_format: str,
        cloud_mask: bool = True,
    ) -> Dict[str, Any]:
        """
        Burn severity via dNBR: pre-fire NBR minus post-fire NBR.

        Reuses _build_change_detection pattern with NBR index.
        Higher dNBR = more severe burn.
        """
        # Reason: Delegate to change_detection with NBR — same algorithm
        combined = [pre_event[0], post_event[1]]
        graph = self._build_change_detection(
            collection, spatial_extent, combined, output_format,
            index_name="nbr", cloud_mask=cloud_mask,
        )
        # Reason: Override the mid-point split with actual pre/post boundaries
        # by rebuilding load nodes with correct temporal extents
        bands = self._get_bands_for_index("nbr", cloud_mask=cloud_mask)
        graph["load1"]["arguments"]["temporal_extent"] = pre_event
        graph["load2"]["arguments"]["temporal_extent"] = post_event
        # Reason: change_detection computes later minus earlier; dNBR is pre minus
        # post, so burned land is positive and matches the severity classes.
        graph["change"]["arguments"]["cube1"] = {"from_node": "reduce_time1"}
        graph["change"]["arguments"]["cube2"] = {"from_node": "reduce_time2"}
        return graph

    def _build_flood_extent_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str,
        water_index: str = "mndwi",
        water_threshold: float = 0.3,
        cloud_mask: bool = True,
        time_reducer: str = "max",
    ) -> Dict[str, Any]:
        """
        Flood extent: water index > threshold.

        Uses max reducer by default to capture maximum flood extent.
        """
        bands = self._get_bands_for_index(water_index, cloud_mask=cloud_mask)

        graph = {}
        graph["load"] = self._build_load_node(
            collection, spatial_extent, temporal_extent, bands=bands
        )

        data_node = "load"
        if cloud_mask:
            graph.update(self._build_cloud_mask_nodes("load"))
            data_node = "apply_mask"

        # Compute water index
        graph.update(self._build_index_nodes(water_index, data_node))

        # Threshold: water_index > threshold → flooded
        graph["flood_mask"] = {
            "process_id": "gt",
            "arguments": {
                "x": {"from_node": water_index},
                "y": water_threshold,
            },
        }

        # Reduce time
        if time_reducer != "none":
            graph["reduce_time"] = self._build_reduce_time_node(
                "flood_mask", time_reducer
            )
            last_node = "reduce_time"
        else:
            last_node = "flood_mask"

        graph["save"] = self._build_save_node(last_node, output_format)
        return graph

    def _build_urban_growth_graph(
        self,
        collection: str,
        spatial_extent: Dict[str, float],
        pre_event: List[str],
        post_event: List[str],
        output_format: str,
        cloud_mask: bool = True,
    ) -> Dict[str, Any]:
        """
        Urban growth: NDBI change between two periods.

        Positive values = new built-up areas.
        """
        # Reason: Delegate to change_detection with NDBI
        combined = [pre_event[0], post_event[1]]
        graph = self._build_change_detection(
            collection, spatial_extent, combined, output_format,
            index_name="ndbi", cloud_mask=cloud_mask,
        )
        graph["load1"]["arguments"]["temporal_extent"] = pre_event
        graph["load2"]["arguments"]["temporal_extent"] = post_event
        return graph

    def build_algorithm_graph(
        self,
        algorithm: str,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: Optional[List[str]] = None,
        pre_event: Optional[List[str]] = None,
        post_event: Optional[List[str]] = None,
        output_format: str = "GTiff",
        cloud_mask: bool = True,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Public dispatch for algorithm templates.

        Args:
            algorithm: One of snow_cover, burn_severity, flood_extent, urban_growth
            collection: STAC collection ID
            spatial_extent: Bounding box
            temporal_extent: [start, end] for single-period algorithms
            pre_event: [start, end] for pre-event period (dual-temporal)
            post_event: [start, end] for post-event period (dual-temporal)
            output_format: GTiff, netCDF, etc.
            cloud_mask: Enable SCL-based cloud masking
            **kwargs: Algorithm-specific parameters
        """
        if algorithm not in self.ALGORITHM_SPECS:
            raise ValueError(
                f"Unknown algorithm: {algorithm}. "
                f"Available: {sorted(self.ALGORITHM_SPECS.keys())}"
            )

        spec = self.ALGORITHM_SPECS[algorithm]
        is_dual = spec.get("dual_temporal", False)

        if is_dual:
            if not pre_event or not post_event:
                raise ValueError(
                    f"Algorithm '{algorithm}' requires pre_event and post_event "
                    f"temporal extents (two time periods)"
                )
        else:
            if not temporal_extent:
                raise ValueError(
                    f"Algorithm '{algorithm}' requires temporal_extent"
                )

        if algorithm == "snow_cover":
            return self._build_snow_cover_graph(
                collection, spatial_extent, temporal_extent, output_format,
                ndsi_threshold=kwargs.get("ndsi_threshold", 0.4),
                ndvi_threshold=kwargs.get("ndvi_threshold", 0.2),
                cloud_mask=cloud_mask,
                time_reducer=kwargs.get("time_reducer", "mean"),
            )
        elif algorithm == "burn_severity":
            return self._build_burn_severity_graph(
                collection, spatial_extent, pre_event, post_event,
                output_format, cloud_mask=cloud_mask,
            )
        elif algorithm == "flood_extent":
            return self._build_flood_extent_graph(
                collection, spatial_extent, temporal_extent, output_format,
                water_index=kwargs.get("water_index", "mndwi"),
                water_threshold=kwargs.get("water_threshold", 0.3),
                cloud_mask=cloud_mask,
                time_reducer=kwargs.get("time_reducer", "max"),
            )
        elif algorithm == "urban_growth":
            return self._build_urban_growth_graph(
                collection, spatial_extent, pre_event, post_event,
                output_format, cloud_mask=cloud_mask,
            )

    # ── Graph validation ──

    @staticmethod
    def _validate_graph_schema(graph: dict, check_process_names: bool = True) -> list:
        """Validate process graph structure. Returns list of errors (empty = valid).

        Args:
            graph: The process graph dict to validate.
            check_process_names: If True, also verify process IDs exist in the
                executor's registry and suggest alternatives for unknown ones.
        """
        errors = []
        if not isinstance(graph, dict) or not graph:
            errors.append("Graph must be a non-empty dict")
            return errors

        has_result = False
        for node_id, node in graph.items():
            if not isinstance(node, dict):
                errors.append(f"Node '{node_id}' must be a dict")
                continue
            if "process_id" not in node:
                errors.append(f"Node '{node_id}' missing 'process_id'")
            if "arguments" not in node:
                errors.append(f"Node '{node_id}' missing 'arguments'")
            if node.get("result"):
                has_result = True
            # Validate from_node references point to existing nodes
            for arg_name, arg_val in node.get("arguments", {}).items():
                if isinstance(arg_val, dict) and "from_node" in arg_val:
                    ref = arg_val["from_node"]
                    if ref not in graph:
                        errors.append(
                            f"Node '{node_id}' arg '{arg_name}' references unknown node '{ref}'"
                        )
                # Check nested process_graph reducers for from_parameter refs
                if isinstance(arg_val, dict) and "process_graph" in arg_val:
                    sub_graph = arg_val["process_graph"]
                    if isinstance(sub_graph, dict):
                        for sub_id, sub_node in sub_graph.items():
                            if isinstance(sub_node, dict):
                                if "process_id" not in sub_node:
                                    errors.append(
                                        f"Sub-node '{sub_id}' in '{node_id}' missing 'process_id'"
                                    )

        if not has_result:
            errors.append("No result node found (one node must have 'result': true)")

        # Layer 1: Validate process names against actual registry
        if not check_process_names:
            return errors
        try:
            from openeo_app.execution.executor import _discover_processes_from_package
            available = set(_discover_processes_from_package().keys())
            # Add builtins that are always handled specially
            available |= {"load_collection", "load_stac", "save_result"}
            # Python keyword/builtin aliases (executor registers with _ prefix)
            available |= {"max", "min", "sum", "all", "any", "and", "or", "not", "if", "array_element"}
            used = set()
            for node_id, node in graph.items():
                if isinstance(node, dict) and "process_id" in node:
                    used.add(node.get("process_id"))
                    # Skip sub-graph process IDs — reducer callbacks are
                    # resolved separately by the executor with alias mapping
            unknown = used - available
            if unknown:
                from difflib import get_close_matches
                for proc in unknown:
                    suggestions = get_close_matches(proc, list(available), n=3, cutoff=0.5)
                    hint = f" Did you mean: {suggestions}?" if suggestions else ""
                    errors.append(
                        f"Unknown process '{proc}' — not available in executor.{hint}"
                    )
        except ImportError:
            pass  # Graceful degradation if executor not importable

        return errors

    # ── Public API ──

    async def generate_process_graph(
        self,
        description: str,
        collection: str,
        spatial_extent: Dict[str, float],
        temporal_extent: List[str],
        output_format: str = "GTiff",
        time_reducer: str = "median",
    ) -> Dict[str, Any]:
        """
        Generate a process graph from a description.

        Args:
            description: Natural language description of desired analysis
            collection: Collection ID to use
            spatial_extent: Bounding box dict
            temporal_extent: [start_date, end_date]
            output_format: Output format (GTiff, netCDF, etc.)
            time_reducer: Temporal reduction strategy (median, mean, min, max, none)

        Returns:
            OpenEO process graph dict
        """
        desc_lower = description.lower()
        analysis_type = self._detect_analysis_type(desc_lower)
        graph = None

        # Reason: Check for cloud-masked variants of any index
        if analysis_type.startswith("cloud_masked_"):
            index = analysis_type[len("cloud_masked_"):]
            if index == "evi":
                graph = self._build_evi_graph(
                    collection, spatial_extent, temporal_extent, output_format,
                    cloud_mask=True, time_reducer=time_reducer,
                )
            elif index in self.INDEX_SPECS:
                graph = self._build_index_graph(
                    collection, spatial_extent, temporal_extent, output_format,
                    index_name=index, cloud_mask=True, time_reducer=time_reducer,
                )

        # Standard index graphs (all use composable builder now)
        # Reason: Always enable cloud masking for optical satellite data.
        # Without it, the graph omits 'scl' from loaded bands, causing
        # array_element(label='scl') to fail at execution time.
        if graph is None and analysis_type in self.INDEX_SPECS:
            graph = self._build_index_graph(
                collection, spatial_extent, temporal_extent, output_format,
                index_name=analysis_type, cloud_mask=True, time_reducer=time_reducer,
            )

        if graph is None:
            if analysis_type == "evi":
                graph = self._build_evi_graph(
                    collection, spatial_extent, temporal_extent, output_format,
                    time_reducer=time_reducer,
                )
            elif analysis_type == "monthly_composite":
                graph = self._build_temporal_composite(
                    collection, spatial_extent, temporal_extent, output_format,
                    cloud_mask=True, period="month", time_reducer=time_reducer,
                )
            elif analysis_type == "temporal_composite":
                graph = self._build_temporal_composite(
                    collection, spatial_extent, temporal_extent, output_format,
                    time_reducer=time_reducer,
                )
            elif analysis_type == "change_detection":
                graph = self._build_change_detection(
                    collection, spatial_extent, temporal_extent, output_format,
                )
            elif analysis_type == "multi_index":
                indices = self._extract_indices_from_description(desc_lower)
                if len(indices) >= 2:
                    graph = self._build_multi_index_graph(
                        collection, spatial_extent, temporal_extent, output_format,
                        indices=indices, cloud_mask=True, time_reducer=time_reducer,
                    )
            elif analysis_type == "zonal_stats":
                # Reason: Zonal stats requires geometries from tool args.
                # Handled by the dedicated openeo_aggregate_spatial tool.
                pass
            elif analysis_type == "spatial_filter":
                kernel_type = self._detect_kernel_type(desc_lower)
                graph = self._build_spatial_filter_graph(
                    collection, spatial_extent, temporal_extent, output_format,
                    kernel_type=kernel_type, time_reducer=time_reducer,
                )
            if graph is None:
                # Generic fallback
                graph = {
                    "load": self._build_load_node(
                        collection, spatial_extent, temporal_extent
                    ),
                }
                if time_reducer and time_reducer != "none":
                    graph["reduce_time"] = self._build_reduce_time_node("load", time_reducer)
                    graph["save"] = self._build_save_node("reduce_time", output_format)
                else:
                    graph["save"] = self._build_save_node("load", output_format)

        # Validate graph structure before returning
        errors = self._validate_graph_schema(graph)
        if errors:
            return {
                "error": f"Generated graph has {len(errors)} structural error(s)",
                "errors": errors,
                "graph": graph,
            }

        return graph


def create_openeo_tools(config) -> Dict[str, Any]:
    """Create OpenEO tools dict for Claude SDK."""
    from .validation_tools import ValidationTools

    tools = OpenEOTools(openeo_url=config.openeo_url)
    validation_tools = ValidationTools()

    async def _list_collections(args: Dict[str, Any]) -> Dict[str, Any]:
        """List available Earth Observation data collections."""
        collections = await tools.list_collections()
        return {
            "content": [{"type": "text", "text": json.dumps(collections)}]
        }

    async def _get_collection_info(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed information about a specific collection."""
        info = await tools.get_collection_info(args["collection_id"])
        return {
            "content": [{"type": "text", "text": json.dumps(info)}]
        }

    async def _generate_graph(args: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a process graph from natural language description."""
        graph = await tools.generate_process_graph(
            description=args["description"],
            collection=args["collection"],
            spatial_extent=args["spatial_extent"],
            temporal_extent=args["temporal_extent"],
            output_format=args.get("output_format", "GTiff"),
            time_reducer=args.get("time_reducer", "median"),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(graph)}]
        }

    async def _validate_graph(args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate a process graph for errors and provide suggestions."""
        result = await validation_tools.validate(args.get("process_graph", {}))
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _resolve_location(args: Dict[str, Any]) -> Dict[str, Any]:
        """Resolve a location name to a bounding box for spatial queries."""
        from ..utils.geocoding import resolve_location, constrain_bbox_to_target_size

        location = args.get("location", "")
        buffer_deg = args.get("buffer_degrees", 0.1)
        target_mb = args.get("target_file_size_mb", 400)
        num_bands = args.get("num_bands", 1)
        num_timesteps = args.get("num_timesteps", 1)

        result = resolve_location(location, buffer_deg)

        # Constrain bbox to target file size
        if result.success and result.bbox:
            constrained_bbox, size_info = constrain_bbox_to_target_size(
                bbox=result.bbox,
                target_mb=target_mb,
                num_bands=num_bands,
                num_timesteps=num_timesteps,
            )
            result.bbox = constrained_bbox
            result_dict = result.to_dict()
            result_dict["file_size_estimate"] = size_info
            return {
                "content": [{"type": "text", "text": json.dumps(result_dict)}]
            }

        return {
            "content": [{"type": "text", "text": json.dumps(result.to_dict())}]
        }

    async def _parse_temporal(args: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a natural language temporal expression to date range."""
        from ..utils.temporal import parse_temporal_expression

        expression = args.get("expression", "")
        result = parse_temporal_expression(expression)
        return {
            "content": [{"type": "text", "text": json.dumps(result.to_dict())}]
        }

    async def _estimate_extent(args: Dict[str, Any]) -> Dict[str, Any]:
        """Estimate data size and validate extent for a query."""
        from ..utils.extent_validator import validate_extent

        spatial_extent = args.get("spatial_extent", {})
        temporal_extent = args.get("temporal_extent")
        collection = args.get("collection", "sentinel-2-l2a")
        bands = args.get("bands")

        result = validate_extent(
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            collection=collection,
            bands=bands,
        )
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _get_quality_metrics(args: Dict[str, Any]) -> Dict[str, Any]:
        """Get comprehensive data quality metrics including cloud coverage and temporal coverage."""
        from ..utils.quality_metrics import get_quality_metrics

        spatial_extent = args.get("spatial_extent", {})
        temporal_extent = args.get("temporal_extent")
        collection = args.get("collection", "sentinel-2-l2a")
        bands = args.get("bands")

        result = get_quality_metrics(
            collection=collection,
            spatial_extent=spatial_extent,
            temporal_extent=temporal_extent,
            bands=bands,
        )
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _validate_geospatial(args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate geospatial extent including CRS, antimeridian handling, and coordinate bounds."""
        from ..utils.geospatial import validate_extent as validate_geo

        spatial_extent = args.get("spatial_extent", {})
        crs = args.get("crs", "EPSG:4326")

        result = validate_geo(
            spatial_extent=spatial_extent,
            crs=crs,
        )
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _aggregate_spatial(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compute zonal statistics for GeoJSON polygon regions."""
        graph = tools._build_zonal_stats_graph(
            collection=args["collection"],
            spatial_extent=args["spatial_extent"],
            temporal_extent=args["temporal_extent"],
            output_format="JSON",
            geometries=args["geometries"],
            reducer=args.get("reducer", "mean"),
            index_name=args.get("index_name"),
            cloud_mask=args.get("cloud_mask", True),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(graph)}]
        }

    async def _apply_kernel(args: Dict[str, Any]) -> Dict[str, Any]:
        """Apply a spatial convolution kernel to satellite imagery."""
        graph = tools._build_spatial_filter_graph(
            collection=args["collection"],
            spatial_extent=args["spatial_extent"],
            temporal_extent=args["temporal_extent"],
            output_format=args.get("output_format", "GTiff"),
            kernel_type=args["kernel_type"],
            kernel_size=args.get("kernel_size", 3),
            cloud_mask=args.get("cloud_mask", True),
            index_name=args.get("index_name"),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(graph)}]
        }

    async def _compose_graph(args: Dict[str, Any]) -> Dict[str, Any]:
        """Compose a custom process graph from atomic steps."""
        graph = tools.compose_graph(
            collection=args["collection"],
            spatial_extent=args["spatial_extent"],
            temporal_extent=args["temporal_extent"],
            steps=args["steps"],
        )
        return {
            "content": [{"type": "text", "text": json.dumps(graph)}]
        }

    async def _raw_graph(args: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and pass through a raw process graph.

        Runs both structural validation AND type-inference validation
        (dimension/band tracking) to catch errors before job submission.
        """
        graph = args.get("process_graph", {})

        # Phase 1: Structural validation (fast)
        structural_errors = tools._validate_graph_schema(graph)

        # Phase 2: Type-inference validation (GraphValidator)
        type_errors = []
        type_warnings = []
        try:
            from openeo_ai.validation.graph_validator import (
                GraphValidator,
                build_default_process_specs,
                get_custom_process_specs,
            )
            specs = build_default_process_specs()
            specs.update(get_custom_process_specs())
            gv = GraphValidator(process_specs=specs)
            gv_result = gv.validate(graph)
            type_errors = [f"[{e.code}] {e.message}" for e in gv_result.errors]
            type_warnings = [f"[{w.code}] {w.message}" for w in gv_result.warnings]
        except Exception as e:
            logger.warning(f"Type-inference validation skipped: {e}")

        all_errors = structural_errors + type_errors
        if all_errors:
            return {
                "content": [{"type": "text", "text": json.dumps({
                    "error": f"Graph has {len(all_errors)} error(s). Fix before submitting.",
                    "errors": all_errors,
                    "warnings": type_warnings,
                    "graph": graph,
                })}]
            }

        result = {"graph": graph}
        if type_warnings:
            result["warnings"] = type_warnings
        return {
            "content": [{"type": "text", "text": json.dumps(result)}]
        }

    async def _algorithm(args: Dict[str, Any]) -> Dict[str, Any]:
        """Build a graph from a named algorithm template."""
        def _decoded(value):
            # Models sometimes send structured arguments as JSON strings.
            return json.loads(value) if isinstance(value, str) else value

        graph = tools.build_algorithm_graph(
            algorithm=args["algorithm"],
            collection=args["collection"],
            spatial_extent=_decoded(args["spatial_extent"]),
            temporal_extent=_decoded(args.get("temporal_extent")),
            pre_event=_decoded(args.get("pre_event")),
            post_event=_decoded(args.get("post_event")),
            output_format=args.get("output_format", "GTiff"),
            cloud_mask=args.get("cloud_mask", True),
            ndsi_threshold=args.get("ndsi_threshold", 0.4),
            ndvi_threshold=args.get("ndvi_threshold", 0.2),
            water_index=args.get("water_index", "mndwi"),
            water_threshold=args.get("water_threshold", 0.3),
            time_reducer=args.get("time_reducer"),
        )
        return {
            "content": [{"type": "text", "text": json.dumps(graph)}]
        }

    return {
        "openeo_list_collections": _list_collections,
        "openeo_get_collection_info": _get_collection_info,
        # openeo_validate_graph handler removed: canonical version in validation_tools.py
        "openeo_generate_graph": _generate_graph,
        "openeo_resolve_location": _resolve_location,
        "openeo_parse_temporal": _parse_temporal,
        "openeo_estimate_extent": _estimate_extent,
        "openeo_quality_metrics": _get_quality_metrics,
        "openeo_validate_geospatial": _validate_geospatial,
        "openeo_aggregate_spatial": _aggregate_spatial,
        "openeo_apply_kernel": _apply_kernel,
        "openeo_compose_graph": _compose_graph,
        "openeo_raw_graph": _raw_graph,
        "openeo_algorithm": _algorithm,
    }


async def list_collections_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone tool function for listing collections."""
    tools = OpenEOTools(openeo_url=os.environ.get("OPENEO_URL", "http://localhost:8080/openeo/1.1.0"))
    collections = await tools.list_collections()
    return {
        "content": [{"type": "text", "text": json.dumps(collections)}]
    }
