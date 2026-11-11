# ABOUTME: Process graph validation with educational feedback and suggestions.
# Validates structure, processes, data flow, band names, and spatial extents.

"""
Process graph validation tools with educational feedback.

Validates OpenEO process graphs and provides helpful suggestions
for optimization and best practices.
"""

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
from dataclasses import dataclass, field
from datetime import datetime

if TYPE_CHECKING:
    # These imports are only for type checking — not executed at runtime.
    # Avoids circular import: validation_tools ↔ openeo_tools ↔ validation_tools
    from openeo_ai.validation.graph_validator import GraphValidator
    from openeo_ai.validation.llm_validator import ClaudeProcessGraphValidator

logger = logging.getLogger(__name__)


# ── Lazy-loaded singletons (break circular imports) ──
_graph_validator_instance = None
_graph_validator_specs = None


def _get_graph_validator():
    """Get or create the GraphValidator singleton (lazy loaded)."""
    global _graph_validator_instance, _graph_validator_specs
    if _graph_validator_instance is None:
        from openeo_ai.validation.graph_validator import (
            GraphValidator,
            build_default_process_specs,
            get_custom_process_specs,
        )
        _graph_validator_specs = build_default_process_specs()
        _graph_validator_specs.update(get_custom_process_specs())
        _graph_validator_instance = GraphValidator(process_specs=_graph_validator_specs)
    return _graph_validator_instance


# Known processes with their required arguments
PROCESS_SPECS = {
    "load_collection": {
        "required": ["id"],
        "optional": ["spatial_extent", "temporal_extent", "bands", "properties"]
    },
    "ndvi": {
        "required": ["data"],
        "optional": ["nir", "red", "target_band"]
    },
    "normalized_difference": {
        "required": ["x", "y"],
        "optional": []
    },
    "reduce_dimension": {
        "required": ["data", "dimension", "reducer"],
        "optional": ["context"]
    },
    "filter_bands": {
        "required": ["data", "bands"],
        "optional": []
    },
    "filter_bbox": {
        "required": ["data", "extent"],
        "optional": []
    },
    "filter_temporal": {
        "required": ["data", "extent"],
        "optional": []
    },
    "save_result": {
        "required": ["data", "format"],
        "optional": ["options"]
    },
    "mask": {
        "required": ["data", "mask"],
        "optional": ["replacement"]
    },
    "add": {"required": ["x", "y"], "optional": []},
    "subtract": {"required": ["x", "y"], "optional": []},
    "multiply": {"required": ["x", "y"], "optional": []},
    "divide": {"required": ["x", "y"], "optional": []},
    # Array operations
    "array_element": {
        "required": ["data"],
        "optional": ["index", "label", "return_nodata"]
    },
    # Apply operations
    "apply": {
        "required": ["data", "process"],
        "optional": ["context"]
    },
    "apply_dimension": {
        "required": ["data", "process", "dimension"],
        "optional": ["target_dimension", "context"]
    },
    # Merge
    "merge_cubes": {
        "required": ["cube1", "cube2"],
        "optional": ["overlap_resolver", "context"]
    },
    # Filter operations
    "filter_spatial": {
        "required": ["data", "geometries"],
        "optional": []
    },
    # Aggregate operations
    "aggregate_temporal": {
        "required": ["data", "intervals", "reducer"],
        "optional": ["labels", "dimension", "context"]
    },
    "aggregate_temporal_period": {
        "required": ["data", "reducer", "period"],
        "optional": ["dimension"]
    },
    "aggregate_spatial": {
        "required": ["data", "geometries", "reducer"],
        "optional": ["chunk_size"]
    },
    # Resample operations
    "resample_spatial": {
        "required": ["data"],
        "optional": ["projection", "resolution", "method", "align"]
    },
    "resample_cube_spatial": {
        "required": ["data", "target"],
        "optional": ["method", "options"]
    },
    # Mask operations
    "mask_polygon": {
        "required": ["data", "mask"],
        "optional": ["replacement", "inside"]
    },
    # Comparison operations
    "eq": {"required": ["x", "y"], "optional": ["delta", "case_sensitive"]},
    "neq": {"required": ["x", "y"], "optional": ["delta", "case_sensitive"]},
    "gt": {"required": ["x", "y"], "optional": []},
    "gte": {"required": ["x", "y"], "optional": []},
    "lt": {"required": ["x", "y"], "optional": []},
    "lte": {"required": ["x", "y"], "optional": []},
    "between": {"required": ["x", "min", "max"], "optional": ["exclude_max"]},
    # Logic operations
    "and": {"required": ["x", "y"], "optional": []},
    "or": {"required": ["x", "y"], "optional": []},
    "not": {"required": ["x"], "optional": []},
    "if": {"required": ["value", "accept"], "optional": ["reject"]},
    # Math operations
    "clip": {"required": ["x", "min", "max"], "optional": []},
    "mean": {"required": ["data"], "optional": ["ignore_nodata"]},
    "median": {"required": ["data"], "optional": ["ignore_nodata"]},
    "min": {"required": ["data"], "optional": ["ignore_nodata"]},
    "max": {"required": ["data"], "optional": ["ignore_nodata"]},
    "sum": {"required": ["data"], "optional": ["ignore_nodata"]},
    "count": {"required": ["data"], "optional": ["condition", "context"]},
    # Geometry operations
    "load_geojson": {"required": ["data"], "optional": ["properties"]},
    # Datacube operations
    "rename_labels": {
        "required": ["data", "dimension", "target"],
        "optional": ["source"]
    },
    "dimension_labels": {
        "required": ["data", "dimension"],
        "optional": []
    },
    "add_dimension": {
        "required": ["data", "name", "label"],
        "optional": ["type"]
    },
    "drop_dimension": {
        "required": ["data", "name"],
        "optional": []
    },
}

# Band mappings for validation
COLLECTION_BANDS = {
    "sentinel-2-l2a": [
        "blue", "green", "red", "rededge1", "rededge2", "rededge3",
        "nir", "nir08", "nir09", "swir16", "swir22", "scl"
    ],
    "sentinel-2-l1c": [
        "blue", "green", "red", "rededge1", "rededge2", "rededge3",
        "nir", "nir08", "nir09", "swir16", "swir22"
    ],
    "landsat-c2-l2": [
        "blue", "green", "red", "nir08", "swir16", "swir22"
    ],
    "cop-dem-glo-30": ["data"],
    "cop-dem-glo-90": ["data"],
}

# Common band name corrections
BAND_CORRECTIONS = {
    "B02": "blue",
    "B03": "green",
    "B04": "red",
    "B05": "rededge1",
    "B06": "rededge2",
    "B07": "rededge3",
    "B08": "nir",
    "B8A": "nir08",
    "B09": "nir09",
    "B11": "swir16",
    "B12": "swir22",
}


@lru_cache(maxsize=32)
def _get_available_bands_cached(collection_id: str) -> Optional[tuple]:
    """Cached wrapper that returns bands as a tuple (hashable for lru_cache).

    Returns None if the collection is not in the static map and dynamic
    lookup fails or is unavailable.
    """
    # Fast path: known collection
    if collection_id in COLLECTION_BANDS:
        return tuple(COLLECTION_BANDS[collection_id])

    # Slow path: dynamic lookup via DatasetIntelligenceService (STAC catalog)
    # This catches QA/classification bands (e.g. SCL) from item_assets
    # that simpler band parsers miss.
    try:
        import os
        from openeo_ai.validation.dataset_intelligence import DatasetIntelligenceService
        stac_url = os.environ.get(
            "STAC_API_URL", "https://earth-search.aws.element84.com/v1/"
        )
        svc = DatasetIntelligenceService(stac_url)
        raw = svc.fetch_collection_sync(collection_id)
        normalized = svc.normalize_collection(raw)
        bands = normalized.get("bands", [])
        if bands:
            band_names = []
            for b in bands:
                name = b.get("common_name") or b.get("name")
                if name and name not in band_names:
                    band_names.append(name)
            if band_names:
                return tuple(band_names)
    except Exception as e:
        logger.debug(f"DatasetIntelligence lookup failed for {collection_id}: {e}")

    # Fallback: try OpenEOTools.get_collection_info
    try:
        from openeo_ai.tools.openeo_tools import OpenEOTools
        tools = OpenEOTools()
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                info = pool.submit(
                    asyncio.run, tools.get_collection_info(collection_id)
                ).result(timeout=15)
        else:
            info = loop.run_until_complete(tools.get_collection_info(collection_id))

        if info and "bands" in info:
            band_names = []
            for name, meta in info["bands"].items():
                common = meta.get("common_name")
                band_names.append(common if common else name)
            return tuple(band_names) if band_names else None
    except Exception as e:
        logger.debug(f"OpenEOTools band lookup failed for {collection_id}: {e}")

    return None


def get_available_bands(collection_id: str) -> Optional[List[str]]:
    """Get available bands for a collection with caching.

    Checks the static COLLECTION_BANDS map first, then falls back to a
    dynamic STAC catalog lookup via get_collection_info(). Results are
    cached (up to 32 collections) for the lifetime of the process.

    Args:
        collection_id: The STAC collection identifier.

    Returns:
        List of band names, or None if the collection is unknown.
    """
    result = _get_available_bands_cached(collection_id)
    return list(result) if result is not None else None


@dataclass
class ValidationResult:
    """Result of process graph validation."""
    valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)
    resource_estimate: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
            "suggestions": self.suggestions,
            "resource_estimate": self.resource_estimate
        }


class ValidationTools:
    """Validate process graphs before execution."""

    async def validate(self, process_graph: Any) -> Dict[str, Any]:
        """
        Run all validation checks on a process graph.

        Args:
            process_graph: OpenEO process graph dict

        Returns:
            ValidationResult as dict
        """
        errors = []
        warnings = []
        suggestions = []

        # Handle non-dict input
        if not isinstance(process_graph, dict):
            return ValidationResult(
                valid=False,
                errors=["Process graph must be a dictionary"]
            ).to_dict()

        # 1. Structural validation
        errors.extend(self._validate_structure(process_graph))

        # 2. Process validation
        errors.extend(self._validate_processes(process_graph))

        # Add warnings for unknown processes (could be UDPs)
        for node_id, process_id in getattr(self, '_unknown_processes', []):
            warnings.append(
                f"Unknown process '{process_id}' in node '{node_id}'. "
                "This may be a user-defined process (UDP)."
            )

        # 3. Data flow validation
        errors.extend(self._validate_data_flow(process_graph))

        # 4. Band validation
        band_issues = self._validate_bands(process_graph)
        # Band issues can be errors or warnings
        for issue in band_issues:
            if "not available" in issue.lower():
                errors.append(issue)
            else:
                warnings.append(issue)

        # 5. Extent validation
        warnings.extend(self._validate_extents(process_graph))

        # 6. Check for temporal formats
        errors.extend(self._validate_temporal_format(process_graph))

        # 7. Generate suggestions
        suggestions.extend(self._generate_suggestions(process_graph))

        # 8. Estimate resources
        estimate = self._estimate_resources(process_graph)

        # 9. Upstream type-inference validation (dimension/band tracking)
        type_errors, type_warnings = self._run_type_inference_validation(process_graph)
        errors.extend(type_errors)
        warnings.extend(type_warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            resource_estimate=estimate
        ).to_dict()

    def _run_type_inference_validation(
        self, process_graph: Dict[str, Any]
    ) -> Tuple[List[str], List[str]]:
        """Run the upstream GraphValidator for type/dimension/band inference.

        Returns:
            (errors, warnings) as plain string lists matching existing contract.
        """
        type_errors: List[str] = []
        type_warnings: List[str] = []

        try:
            validator = _get_graph_validator()

            # Optional LLM-assisted validation (disabled by default)
            llm_validator = None
            try:
                from openeo_ai.validation.llm_validator import (
                    ClaudeProcessGraphValidator,
                    LLMValidationConfig,
                )
                llm_config = LLMValidationConfig.from_env()
                if llm_config.enabled and llm_config.api_key:
                    llm_validator = ClaudeProcessGraphValidator(llm_config)
                    logger.info("LLM validation enabled (Claude)")
            except Exception as llm_err:
                logger.debug(f"LLM validator not available: {llm_err}")

            result = validator.validate(process_graph, llm_validator=llm_validator)

            # Convert typed Error objects to plain strings, deduplicating
            # against errors already caught by JonaAI's existing checks.
            seen_messages = set()
            for err in result.errors:
                msg = f"[{err.code}] {err.message}"
                if msg not in seen_messages:
                    seen_messages.add(msg)
                    type_errors.append(msg)

            for warn in result.warnings:
                msg = f"[{warn.code}] {warn.message}"
                if msg not in seen_messages:
                    seen_messages.add(msg)
                    type_warnings.append(msg)

        except Exception as e:
            logger.warning(f"Type-inference validation failed (non-fatal): {e}")

        return type_errors, type_warnings

    def _validate_structure(self, pg: Dict[str, Any]) -> List[str]:
        """Validate basic structure."""
        errors = []

        if len(pg) == 0:
            errors.append("Process graph is empty")
            return errors

        # Check for result node
        result_nodes = [
            node_id for node_id, node in pg.items()
            if isinstance(node, dict) and node.get("result", False)
        ]

        if len(result_nodes) == 0:
            errors.append("No result node found. Mark one node with 'result': true")
        elif len(result_nodes) > 1:
            errors.append(f"Multiple result nodes found: {result_nodes}. Only one is allowed.")

        return errors

    def _validate_processes(self, pg: Dict[str, Any]) -> List[str]:
        """Validate process IDs and arguments."""
        errors = []
        self._unknown_processes = []  # Track unknown processes for warnings

        for node_id, node in pg.items():
            if not isinstance(node, dict):
                continue

            process_id = node.get("process_id")
            if not process_id:
                errors.append(f"Node '{node_id}' missing process_id")
                continue

            # Check required arguments for known processes
            if process_id in PROCESS_SPECS:
                spec = PROCESS_SPECS[process_id]
                args = node.get("arguments", {})

                for req_arg in spec["required"]:
                    if req_arg not in args:
                        errors.append(
                            f"Node '{node_id}' ({process_id}): "
                            f"missing required argument '{req_arg}'"
                        )
            else:
                # Track unknown processes (could be UDPs)
                self._unknown_processes.append((node_id, process_id))

        return errors

    def _validate_data_flow(self, pg: Dict[str, Any]) -> List[str]:
        """Validate node references and detect cycles."""
        errors = []
        node_ids = set(pg.keys())

        # Check all from_node references
        for node_id, node in pg.items():
            if not isinstance(node, dict):
                continue

            args = node.get("arguments", {})
            self._check_references(args, node_ids, node_id, errors)

        # Check for cycles
        cycle_errors = self._detect_cycles(pg)
        errors.extend(cycle_errors)

        return errors

    def _check_references(
        self,
        obj: Any,
        valid_nodes: set,
        current_node: str,
        errors: List[str]
    ):
        """Recursively check from_node references."""
        if isinstance(obj, dict):
            if "from_node" in obj:
                ref = obj["from_node"]
                if ref not in valid_nodes:
                    errors.append(
                        f"Node '{current_node}' references unknown node '{ref}'"
                    )
            # Don't recurse into from_parameter or process_graph sub-graphs.
            # Sub-graphs (reducers, apply callbacks) have internal from_node
            # references that are NOT top-level node IDs.
            for key, value in obj.items():
                if key not in ("from_parameter", "process_graph"):
                    self._check_references(value, valid_nodes, current_node, errors)
        elif isinstance(obj, list):
            for item in obj:
                self._check_references(item, valid_nodes, current_node, errors)

    def _detect_cycles(self, pg: Dict[str, Any]) -> List[str]:
        """Detect circular dependencies in process graph."""
        errors = []

        # Build dependency graph
        deps = {}
        for node_id, node in pg.items():
            if not isinstance(node, dict):
                continue
            deps[node_id] = self._get_dependencies(node)

        # DFS to detect cycles
        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> bool:
            visited.add(node)
            rec_stack.add(node)

            for dep in deps.get(node, []):
                if dep not in visited:
                    if dfs(dep, path + [dep]):
                        return True
                elif dep in rec_stack:
                    cycle = path[path.index(dep):] + [dep]
                    errors.append(f"Circular reference detected: {' -> '.join(cycle)}")
                    return True

            rec_stack.remove(node)
            return False

        for node in pg.keys():
            if node not in visited:
                dfs(node, [node])

        return errors

    def _get_dependencies(self, node: Dict[str, Any]) -> List[str]:
        """Extract from_node dependencies from a node."""
        deps = []

        def extract(obj):
            if isinstance(obj, dict):
                if "from_node" in obj:
                    deps.append(obj["from_node"])
                # Skip process_graph sub-graphs — their internal from_node
                # references are self-contained and not top-level dependencies.
                for key, value in obj.items():
                    if key != "process_graph":
                        extract(value)
            elif isinstance(obj, list):
                for item in obj:
                    extract(item)

        extract(node.get("arguments", {}))
        return deps

    def _validate_bands(self, pg: Dict[str, Any]) -> List[str]:
        """Validate band names against collection capabilities.

        Uses get_available_bands() which checks the static map first,
        then falls back to a dynamic STAC catalog lookup.
        """
        issues = []

        for node_id, node in pg.items():
            if not isinstance(node, dict):
                continue

            if node.get("process_id") != "load_collection":
                continue

            args = node.get("arguments", {})
            collection_id = args.get("id", "")
            bands = args.get("bands", [])

            if not bands or not collection_id:
                continue

            available_list = get_available_bands(collection_id)
            if available_list is None:
                # Unknown collection — can't validate
                issues.append(
                    f"Node '{node_id}': Unknown collection '{collection_id}', "
                    "cannot validate band names."
                )
                continue

            available = set(available_list)
            requested = set(bands)
            invalid = requested - available

            if invalid:
                # Check if they're using old-style band names
                corrections = []
                for band in invalid:
                    if band in BAND_CORRECTIONS:
                        corrections.append(
                            f"'{band}' should be '{BAND_CORRECTIONS[band]}'"
                        )

                if corrections:
                    issues.append(
                        f"Node '{node_id}': Band name corrections needed: "
                        f"{', '.join(corrections)}. "
                        f"Use AWS Earth Search band names (e.g., 'red', 'nir')."
                    )
                else:
                    issues.append(
                        f"Node '{node_id}': bands {invalid} not available "
                        f"in {collection_id}. Available: {sorted(available)}"
                    )

        return issues

    def _validate_extents(self, pg: Dict[str, Any]) -> List[str]:
        """Check spatial/temporal extents for warnings."""
        warnings = []

        for node_id, node in pg.items():
            if not isinstance(node, dict):
                continue

            if node.get("process_id") != "load_collection":
                continue

            args = node.get("arguments", {})

            # Check spatial extent
            spatial = args.get("spatial_extent", {})
            if spatial:
                width = abs(spatial.get("east", 0) - spatial.get("west", 0))
                height = abs(spatial.get("north", 0) - spatial.get("south", 0))

                if width > 1 or height > 1:
                    warnings.append(
                        f"Large spatial extent ({width:.2f}° x {height:.2f}°). "
                        "Consider reducing for faster processing."
                    )

                if width > 5 or height > 5:
                    warnings.append(
                        f"Very large extent may timeout or fail. "
                        "Recommend < 1° x 1° for testing."
                    )

            # Check temporal extent
            temporal = args.get("temporal_extent", [])
            if len(temporal) >= 2 and temporal[0] and temporal[1]:
                try:
                    start = datetime.fromisoformat(temporal[0].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(temporal[1].replace("Z", "+00:00"))
                    days = (end - start).days

                    if days > 365:
                        warnings.append(
                            f"Long temporal range ({days} days). "
                            "Consider reducing for faster processing."
                        )
                except ValueError:
                    pass  # Will be caught by temporal format validation

        return warnings

    def _validate_temporal_format(self, pg: Dict[str, Any]) -> List[str]:
        """Validate temporal extent date formats."""
        errors = []

        for node_id, node in pg.items():
            if not isinstance(node, dict):
                continue

            if node.get("process_id") != "load_collection":
                continue

            temporal = node.get("arguments", {}).get("temporal_extent", [])
            if temporal:
                for i, date_str in enumerate(temporal):
                    if date_str is None:
                        continue
                    try:
                        # Try parsing the date
                        if isinstance(date_str, str):
                            datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    except ValueError:
                        errors.append(
                            f"Node '{node_id}': Invalid date format at position {i}: "
                            f"'{date_str}'. Use ISO format (YYYY-MM-DD)."
                        )

        return errors

    def _generate_suggestions(self, pg: Dict[str, Any]) -> List[str]:
        """Generate optimization suggestions."""
        suggestions = []

        # Check for cloud masking with Sentinel
        has_cloud_mask = any(
            node.get("process_id") in ["mask", "mask_polygon"]
            for node in pg.values() if isinstance(node, dict)
        )

        has_sentinel = any(
            "sentinel" in str(node.get("arguments", {}).get("id", "")).lower()
            for node in pg.values() if isinstance(node, dict)
        )

        if has_sentinel and not has_cloud_mask:
            suggestions.append(
                "Consider adding cloud masking for Sentinel data. "
                "Use the SCL band to filter cloudy pixels."
            )

        # Check for time reduction
        has_temporal = any(
            node.get("arguments", {}).get("temporal_extent")
            for node in pg.values()
            if isinstance(node, dict) and node.get("process_id") == "load_collection"
        )

        has_reduce_time = any(
            node.get("process_id") == "reduce_dimension" and
            node.get("arguments", {}).get("dimension") in ["time", "t"]
            for node in pg.values() if isinstance(node, dict)
        )

        if has_temporal and not has_reduce_time:
            suggestions.append(
                "Consider adding reduce_dimension over time if you need "
                "a single composite image (e.g., mean, median)."
            )

        # Check for NDVI without NIR band
        has_ndvi = any(
            node.get("process_id") in ["ndvi", "normalized_difference"]
            for node in pg.values() if isinstance(node, dict)
        )

        if has_ndvi:
            # Check if NIR band is loaded
            for node in pg.values():
                if isinstance(node, dict) and node.get("process_id") == "load_collection":
                    bands = node.get("arguments", {}).get("bands", [])
                    if bands and "nir" not in bands:
                        suggestions.append(
                            "NDVI calculation requires NIR band. "
                            "Make sure to include 'nir' in the bands argument."
                        )

        return suggestions

    def _estimate_resources(self, pg: Dict[str, Any]) -> Dict[str, Any]:
        """Estimate processing resources needed."""
        # Calculate based on extent, temporal range, bands
        total_pixels = 0
        complexity = "low"

        for node in pg.values():
            if isinstance(node, dict) and node.get("process_id") == "load_collection":
                args = node.get("arguments", {})

                spatial = args.get("spatial_extent", {})
                if spatial:
                    width = abs(spatial.get("east", 0) - spatial.get("west", 0))
                    height = abs(spatial.get("north", 0) - spatial.get("south", 0))
                    # Rough estimate: 10m resolution for Sentinel-2
                    pixels = (width * 111000 / 10) * (height * 111000 / 10)
                    total_pixels += pixels

                    if width > 0.5 or height > 0.5:
                        complexity = "medium"
                    if width > 2 or height > 2:
                        complexity = "high"
                    if width > 5 or height > 5:
                        complexity = "very_high"

        return {
            "estimated_size_mb": "unknown",
            "estimated_time_seconds": "unknown",
            "complexity": complexity,
            "estimated_pixels": int(total_pixels) if total_pixels else "unknown"
        }


async def validate_scf_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and optionally lower an SCF document to a process graph.

    Args:
        args: {"scf": dict, "lower": bool (default False)}
    Returns:
        Validation results + optional process graph
    """
    scf_doc = args.get("scf", {})
    should_lower = args.get("lower", False)

    try:
        from openeo_ai.validation.scf import validate_scf, lower_scf_to_pg, hash_json
        from openeo_ai.validation.graph_validator import build_default_process_specs, get_custom_process_specs

        errors, warnings, required = validate_scf(scf_doc)

        result = {
            "valid": len(errors) == 0,
            "errors": [f"[{e.code}] {e.message}" for e in errors],
            "warnings": [f"[{w.code}] {w.message}" for w in warnings],
            "required_decisions": [f"[{r.code}] {r.message}" for r in required],
            "scf_hash": hash_json(scf_doc),
        }

        # Cost estimation
        try:
            from openeo_ai.validation.scf.tools.cost_estimation import estimate
            cost = estimate(scf_doc)
            result["cost_estimate"] = cost
        except Exception:
            pass

        # Optionally lower to process graph
        if should_lower and len(errors) == 0:
            specs = build_default_process_specs()
            specs.update(get_custom_process_specs())
            pg, pg_warnings, pg_errors, pg_hash = lower_scf_to_pg(scf_doc, process_specs=specs)
            result["process_graph"] = pg
            result["pg_hash"] = pg_hash
            result["lowering_errors"] = [f"[{e.code}] {e.message}" for e in pg_errors]
            result["lowering_warnings"] = [f"[{w.code}] {w.message}" for w in pg_warnings]

        return {"content": [{"type": "text", "text": json.dumps(result)}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": json.dumps({
            "valid": False, "errors": [f"SCF validation failed: {e}"]
        })}]}


async def validate_graph_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """Standalone tool function for validating process graphs."""
    tools = ValidationTools()
    result = await tools.validate(args.get("process_graph", {}))
    return {
        "content": [{"type": "text", "text": json.dumps(result)}]
    }


# ── Optical indices that require cloud masking consideration ──
_OPTICAL_INDICES = {
    "ndvi", "ndwi", "mndwi", "nbr", "ndbi", "ndsi", "ndmi", "gndvi",
    "ndre", "savi", "evi", "ndci", "wiw", "ui", "ndti", "bsi", "lai",
    "arvi", "msavi", "chl_re", "awei", "vci", "tci", "bai",
}


async def validate_pre_execution(args: Dict[str, Any]) -> Dict[str, Any]:
    """Semantic pre-flight validation before graph generation.

    Checks band availability, temporal sanity, cloud masking hints,
    extent size, and domain-index compatibility against the structured
    AnalysisGoal produced by extract_analysis_goal.

    Args:
        args: {"goal": dict, "collection": str (optional override)}

    Returns:
        MCP-style result with {valid, checks, errors, warnings} payload.
    """
    # Lazy imports to avoid circular dependencies
    from openeo_ai.tools.openeo_tools import OpenEOTools
    from openeo_ai.sdk.domain_knowledge import DOMAIN_CONTEXTS

    goal = args.get("goal", {})
    collection = args.get("collection") or goal.get("collection", "")
    checks: List[Dict[str, str]] = []

    index_specs = OpenEOTools.INDEX_SPECS

    # ── 1. band_availability ──
    index_name = (goal.get("index") or "").lower()
    if index_name and collection:
        spec = index_specs.get(index_name)
        available_list = get_available_bands(collection)
        if spec and available_list is not None:
            required_bands = {spec["band_x"], spec["band_y"]}
            available_bands = set(available_list)
            missing = required_bands - available_bands
            if missing:
                checks.append({
                    "level": "error",
                    "check": "band_availability",
                    "message": (
                        f"Index '{index_name}' requires bands {sorted(required_bands)} "
                        f"but {sorted(missing)} not available in {collection}. "
                        f"Available: {sorted(available_bands)}"
                    ),
                })
            else:
                checks.append({
                    "level": "info",
                    "check": "band_availability",
                    "message": f"All bands for '{index_name}' available in {collection}.",
                })
        elif spec and available_list is None:
            checks.append({
                "level": "warning",
                "check": "band_availability",
                "message": (
                    f"Unknown collection '{collection}', cannot verify band "
                    f"availability for index '{index_name}'."
                ),
            })

    # ── 2. temporal_sanity ──
    temporal_strategy = (goal.get("temporal_strategy") or "").lower()
    temporal_extent = goal.get("temporal_extent") or []

    if temporal_strategy == "change_detection":
        # Change detection needs two dates / date ranges
        if len(temporal_extent) < 2 or not temporal_extent[0] or not temporal_extent[1]:
            checks.append({
                "level": "warning",
                "check": "temporal_sanity",
                "message": (
                    "Change detection analysis typically requires two temporal windows "
                    "(pre and post). Only a single date or no temporal extent was provided."
                ),
            })
    if temporal_strategy == "time_series" and len(temporal_extent) >= 2:
        try:
            start = datetime.fromisoformat(str(temporal_extent[0]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(temporal_extent[1]).replace("Z", "+00:00"))
            days = (end - start).days
            if days < 180:
                checks.append({
                    "level": "warning",
                    "check": "temporal_sanity",
                    "message": (
                        f"Time-series analysis with only {days} days of data. "
                        "Consider extending to >= 6 months for meaningful trends."
                    ),
                })
        except (ValueError, TypeError):
            pass  # Date parsing handled elsewhere

    # ── 3. cloud_masking ──
    analysis_type = (goal.get("analysis_type") or "").lower()
    if collection and index_name:
        is_optical_collection = any(
            kw in collection.lower() for kw in ("sentinel", "landsat")
        )
        is_optical_index = index_name in _OPTICAL_INDICES
        # Heuristic: if no mention of cloud masking in the goal
        bands = goal.get("bands") or []
        has_scl = "scl" in [b.lower() for b in bands]
        if is_optical_collection and is_optical_index and not has_scl:
            checks.append({
                "level": "warning",
                "check": "cloud_masking",
                "message": (
                    f"Optical index '{index_name}' on {collection} without SCL band. "
                    "Consider enabling cloud masking (SCL classes 8-11) for cleaner results."
                ),
            })

    # ── 4. extent_size ──
    spatial_extent = goal.get("spatial_extent") or {}
    if spatial_extent:
        width = abs(spatial_extent.get("east", 0) - spatial_extent.get("west", 0))
        height = abs(spatial_extent.get("north", 0) - spatial_extent.get("south", 0))
        if width > 5 or height > 5:
            checks.append({
                "level": "error",
                "check": "extent_size",
                "message": (
                    f"Spatial extent is very large ({width:.2f}° x {height:.2f}°). "
                    "This will likely timeout or exceed memory. Reduce to < 5° in each dimension."
                ),
            })

    # ── 5. domain_index_match ──
    domain = (goal.get("domain") or "").lower()
    if domain and index_name and domain in DOMAIN_CONTEXTS:
        domain_indices = [i.lower() for i in DOMAIN_CONTEXTS[domain].get("indices", [])]
        if index_name not in domain_indices:
            checks.append({
                "level": "warning",
                "check": "domain_index_match",
                "message": (
                    f"Index '{index_name}' is not among the recommended indices "
                    f"for the '{domain}' domain ({', '.join(DOMAIN_CONTEXTS[domain]['indices'])}). "
                    "It may still work, but consider using a domain-recommended index."
                ),
            })

    # ── 6. resolution_info ──
    if collection:
        if "sentinel-2" in collection.lower():
            checks.append({
                "level": "info",
                "check": "resolution_info",
                "message": "Sentinel-2 provides 10m resolution for visible/NIR bands, 20m for SWIR/red-edge.",
            })
        elif "landsat" in collection.lower():
            checks.append({
                "level": "info",
                "check": "resolution_info",
                "message": "Landsat provides 30m resolution. Consider Sentinel-2 if higher resolution is needed.",
            })

    # ── Summarise ──
    error_count = sum(1 for c in checks if c["level"] == "error")
    warning_count = sum(1 for c in checks if c["level"] == "warning")

    result = {
        "valid": error_count == 0,
        "checks": checks,
        "errors": error_count,
        "warnings": warning_count,
    }

    return {
        "content": [{"type": "text", "text": json.dumps(result)}]
    }


# ── Tool definitions ──────────────────────────────────────────────────────────

VALIDATION_TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "openeo_validate_graph",
        "description": (
            "Validate an OpenEO process graph for errors, warnings, and suggestions. "
            "Checks structure, process IDs, arguments, data flow, band names, and extent sizes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "process_graph": {
                    "type": "object",
                    "description": "The OpenEO process graph to validate",
                },
            },
            "required": ["process_graph"],
        },
    },
    {
        "name": "validate_pre_execution",
        "description": (
            "Semantic pre-flight validation before graph generation. "
            "Checks band availability, temporal sanity, cloud masking, extent size, "
            "and domain-index compatibility. Call AFTER extract_analysis_goal and "
            "BEFORE generating process graphs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "goal": {
                    "type": "object",
                    "description": "The AnalysisGoal dict from extract_analysis_goal",
                },
                "collection": {
                    "type": "string",
                    "description": "Override collection (optional, uses goal.collection if not provided)",
                },
            },
            "required": ["goal"],
        },
    },
]


# ── Factory ───────────────────────────────────────────────────────────────────

def create_validation_tools() -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Create validation tools.

    Returns:
        Tuple of (tool_handlers_dict, tool_definitions_list)
    """
    handlers = {
        "openeo_validate_graph": validate_graph_tool,
        "validate_pre_execution": validate_pre_execution,
        "validate_scf": validate_scf_tool,
    }
    return handlers, VALIDATION_TOOL_DEFINITIONS
