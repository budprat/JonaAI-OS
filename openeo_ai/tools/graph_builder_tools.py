# ABOUTME: Atomic graph building tools for incremental OpenEO process graph construction.
# Provides 7 tools: threshold, logic, math, band_filter, mask, merge, validate_full.
# Fills the gap between compose_graph (standard pipelines) and raw_graph (full JSON).

"""
Atomic graph builder tools for incremental process graph construction.

These tools let Claude build complex graphs step-by-step by adding one
operation at a time. Each tool accepts a graph dict, adds node(s), and
returns the updated graph. Auto-wiring connects new nodes to the last
terminal node unless an explicit input is specified.

Workflow:
1. Start with openeo_compose_graph (omit save step) or a minimal graph
2. Chain graph_add_* tools, passing the graph dict between calls
3. Call graph_validate_full before submitting to openeo_create_job
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ── Auto-Wiring Helpers ──


def _collect_from_nodes(obj: Any, result_set: Set[str]) -> None:
    """Recursively collect all from_node references from nested arguments."""
    if isinstance(obj, dict):
        if "from_node" in obj:
            result_set.add(obj["from_node"])
        # Don't recurse into process_graph sub-graphs — those are
        # self-contained reducers, not top-level references
        if "process_graph" not in obj:
            for v in obj.values():
                _collect_from_nodes(v, result_set)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_from_nodes(item, result_set)


def _find_last_node(graph: dict) -> Optional[str]:
    """Find the terminal node (not referenced by any other node's from_node).

    Uses topological analysis — does NOT rely on dict insertion order.
    Prefers non-save terminals. Returns None for empty graphs.
    """
    if not graph:
        return None

    # Collect all referenced nodes
    referenced: Set[str] = set()
    for node_id, node in graph.items():
        if isinstance(node, dict):
            args = node.get("arguments", {})
            _collect_from_nodes(args, referenced)

    # Unreferenced nodes are terminals
    terminals = [nid for nid in graph if nid not in referenced]

    if not terminals:
        # Circular graph — shouldn't happen, fallback to last key
        return list(graph.keys())[-1]

    # Prefer non-save terminals
    non_save = [t for t in terminals if not _is_save_node(graph.get(t, {}))]
    if non_save:
        return non_save[-1]
    return terminals[-1]


def _is_save_node(node: dict) -> bool:
    """Check if a node is a save_result node."""
    return isinstance(node, dict) and node.get("process_id") == "save_result"


def _generate_node_id(graph: dict, prefix: str) -> str:
    """Generate a unique node ID like threshold_1, threshold_2."""
    counter = 1
    while f"{prefix}_{counter}" in graph:
        counter += 1
    return f"{prefix}_{counter}"


def _make_result(graph: dict, last_node: str) -> dict:
    """Standard return format for graph builder tools."""
    return {
        "graph": graph,
        "last_node": last_node,
        "node_count": len(graph),
    }


def _resolve_input(graph: dict, explicit_input: Optional[str]) -> str:
    """Resolve input node: use explicit if given, otherwise auto-wire."""
    if explicit_input:
        if explicit_input not in graph:
            raise ValueError(
                f"Input node '{explicit_input}' not found in graph. "
                f"Available: {list(graph.keys())}"
            )
        return explicit_input
    last = _find_last_node(graph)
    if last is None:
        raise ValueError("Cannot auto-wire: graph is empty")
    return last


def _build_graph_topology(graph: dict) -> str:
    """Build a human-readable topology string like 'load -> cloud_mask -> ndvi -> save'."""
    if not graph:
        return "(empty)"

    # Build adjacency: node_id -> list of nodes that depend on it
    children: Dict[str, List[str]] = {nid: [] for nid in graph}
    parents: Dict[str, List[str]] = {nid: [] for nid in graph}

    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        refs: Set[str] = set()
        _collect_from_nodes(node.get("arguments", {}), refs)
        for ref in refs:
            if ref in graph:
                children[ref].append(node_id)
                parents[node_id].append(ref)

    # Find root nodes (no parents in graph)
    roots = [nid for nid in graph if not parents[nid]]
    if not roots:
        roots = [list(graph.keys())[0]]

    # Walk from roots building chains
    visited = set()
    chains = []

    def walk(node_id: str) -> List[str]:
        if node_id in visited:
            return []
        visited.add(node_id)
        chain = [node_id]
        kids = children.get(node_id, [])
        if len(kids) == 1:
            chain.extend(walk(kids[0]))
        elif len(kids) > 1:
            # Branch — show first path
            chain.extend(walk(kids[0]))
        return chain

    for root in roots:
        chain = walk(root)
        if chain:
            chains.append(chain)

    if chains:
        return " -> ".join(chains[0])
    return " -> ".join(graph.keys())


# ── Seven Atomic Tools ──


def _graph_add_threshold(
    graph: dict,
    operator: str,
    value: Optional[float] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    input_node: Optional[str] = None,
    node_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """Add a comparison/threshold operation to the graph.

    Operators: gt, lt, gte, lte, between
    """
    valid_ops = {"gt", "lt", "gte", "lte", "between"}
    if operator not in valid_ops:
        raise ValueError(f"Invalid operator '{operator}'. Valid: {sorted(valid_ops)}")

    source = _resolve_input(graph, input_node)
    nid = node_id or _generate_node_id(graph, operator)

    if operator == "between":
        if min_value is None or max_value is None:
            raise ValueError("'between' requires both min_value and max_value")
        graph[nid] = {
            "process_id": "between",
            "arguments": {
                "x": {"from_node": source},
                "min": min_value,
                "max": max_value,
            },
        }
    else:
        if value is None:
            raise ValueError(f"Operator '{operator}' requires 'value'")
        graph[nid] = {
            "process_id": operator,
            "arguments": {
                "x": {"from_node": source},
                "y": value,
            },
        }

    return graph, nid


def _graph_add_logic(
    graph: dict,
    operator: str,
    input_a: Optional[str] = None,
    input_b: Optional[str] = None,
    node_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """Add a boolean logic operation (and/or/not)."""
    valid_ops = {"and", "or", "not"}
    if operator not in valid_ops:
        raise ValueError(f"Invalid operator '{operator}'. Valid: {sorted(valid_ops)}")

    nid = node_id or _generate_node_id(graph, operator)

    if operator == "not":
        source = _resolve_input(graph, input_a)
        graph[nid] = {
            "process_id": "not",
            "arguments": {
                "x": {"from_node": source},
            },
        }
    else:
        if not input_a or not input_b:
            raise ValueError(f"'{operator}' requires both input_a and input_b")
        if input_a not in graph:
            raise ValueError(f"input_a '{input_a}' not found in graph")
        if input_b not in graph:
            raise ValueError(f"input_b '{input_b}' not found in graph")
        graph[nid] = {
            "process_id": operator,
            "arguments": {
                "x": {"from_node": input_a},
                "y": {"from_node": input_b},
            },
        }

    return graph, nid


def _graph_add_math(
    graph: dict,
    operator: str,
    input_a: str,
    input_b: Any,  # str (node ref) or number (scalar)
    node_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """Add an arithmetic operation (add/subtract/multiply/divide)."""
    valid_ops = {"add", "subtract", "multiply", "divide"}
    if operator not in valid_ops:
        raise ValueError(f"Invalid operator '{operator}'. Valid: {sorted(valid_ops)}")

    if input_a not in graph:
        raise ValueError(f"input_a '{input_a}' not found in graph")

    nid = node_id or _generate_node_id(graph, operator)

    # Determine if input_b is a node reference or scalar
    if isinstance(input_b, str):
        if input_b not in graph:
            raise ValueError(f"input_b '{input_b}' not found in graph")
        y_arg = {"from_node": input_b}
    elif isinstance(input_b, (int, float)):
        y_arg = input_b
    else:
        raise ValueError(f"input_b must be a node ID (str) or number, got {type(input_b).__name__}")

    graph[nid] = {
        "process_id": operator,
        "arguments": {
            "x": {"from_node": input_a},
            "y": y_arg,
        },
    }

    return graph, nid


def _graph_add_band_filter(
    graph: dict,
    band_name: str,
    input_node: Optional[str] = None,
    node_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """Extract a single band using array_element."""
    source = _resolve_input(graph, input_node)
    nid = node_id or _generate_node_id(graph, f"get_{band_name}")

    graph[nid] = {
        "process_id": "array_element",
        "arguments": {
            "data": {"from_node": source},
            "label": band_name,
        },
    }

    return graph, nid


def _graph_add_mask(
    graph: dict,
    data_node: str,
    mask_node: str,
    replacement: Any = None,
    node_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """Apply a computed boolean mask to data."""
    if data_node not in graph:
        raise ValueError(f"data_node '{data_node}' not found in graph")
    if mask_node not in graph:
        raise ValueError(f"mask_node '{mask_node}' not found in graph")

    nid = node_id or _generate_node_id(graph, "mask")

    node: Dict[str, Any] = {
        "process_id": "mask",
        "arguments": {
            "data": {"from_node": data_node},
            "mask": {"from_node": mask_node},
        },
    }
    if replacement is not None:
        node["arguments"]["replacement"] = replacement

    graph[nid] = node
    return graph, nid


def _graph_add_merge(
    graph: dict,
    cube_a: str,
    cube_b: str,
    overlap_resolver: Optional[str] = None,
    node_id: Optional[str] = None,
) -> Tuple[dict, str]:
    """Merge two datacubes with optional overlap resolver."""
    if cube_a not in graph:
        raise ValueError(f"cube_a '{cube_a}' not found in graph")
    if cube_b not in graph:
        raise ValueError(f"cube_b '{cube_b}' not found in graph")

    nid = node_id or _generate_node_id(graph, "merge")

    node: Dict[str, Any] = {
        "process_id": "merge_cubes",
        "arguments": {
            "cube1": {"from_node": cube_a},
            "cube2": {"from_node": cube_b},
        },
    }

    if overlap_resolver:
        valid_resolvers = {"subtract", "add", "multiply", "divide", "mean"}
        if overlap_resolver not in valid_resolvers:
            raise ValueError(
                f"Invalid resolver '{overlap_resolver}'. Valid: {sorted(valid_resolvers)}"
            )
        # Build sub-process graph for overlap resolver
        if overlap_resolver == "mean":
            # mean of two values = add then divide by 2
            node["arguments"]["overlap_resolver"] = {
                "process_graph": {
                    "add_overlap": {
                        "process_id": "add",
                        "arguments": {
                            "x": {"from_parameter": "x"},
                            "y": {"from_parameter": "y"},
                        },
                    },
                    "mean_overlap": {
                        "process_id": "divide",
                        "arguments": {
                            "x": {"from_node": "add_overlap"},
                            "y": 2,
                        },
                        "result": True,
                    },
                }
            }
        else:
            node["arguments"]["overlap_resolver"] = {
                "process_graph": {
                    f"{overlap_resolver}_overlap": {
                        "process_id": overlap_resolver,
                        "arguments": {
                            "x": {"from_parameter": "x"},
                            "y": {"from_parameter": "y"},
                        },
                        "result": True,
                    }
                }
            }

    graph[nid] = node
    return graph, nid


def _graph_validate_full(
    graph: dict,
    collection: Optional[str] = None,
    fix_suggestions: bool = True,
) -> dict:
    """Deep pre-submission validation with topology and fix suggestions.

    Reuses _validate_graph_schema from openeo_tools for structural checks,
    adds orphan detection, topology visualization, and fix suggestions.
    """
    errors: List[str] = []
    warnings: List[str] = []
    suggestions: List[str] = []
    fixes: List[dict] = []

    if not isinstance(graph, dict) or not graph:
        return {
            "ready_to_submit": False,
            "errors": ["Graph must be a non-empty dict"],
            "warnings": [],
            "suggestions": [],
            "fixes": [],
            "orphaned_nodes": [],
            "node_count": 0,
            "graph_topology": "(empty)",
        }

    # ── Structural validation (reuse existing) ──
    try:
        from .openeo_tools import OpenEOTools
        struct_errors = OpenEOTools._validate_graph_schema(graph)
        errors.extend(struct_errors)
    except ImportError:
        # Inline basic validation if import fails
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
        if not has_result:
            errors.append("No result node found (one node must have 'result': true)")

    # ── Orphaned node detection ──
    referenced: Set[str] = set()
    for node_id, node in graph.items():
        if isinstance(node, dict):
            _collect_from_nodes(node.get("arguments", {}), referenced)

    # A node is orphaned if it references nothing AND nothing references it
    # (and it's not a load_collection which is a source node)
    all_nodes = set(graph.keys())
    nodes_that_ref_others: Set[str] = set()
    for node_id, node in graph.items():
        if isinstance(node, dict):
            refs: Set[str] = set()
            _collect_from_nodes(node.get("arguments", {}), refs)
            if refs:
                nodes_that_ref_others.add(node_id)

    orphaned = []
    for node_id in all_nodes:
        node = graph[node_id]
        if not isinstance(node, dict):
            continue
        is_source = node.get("process_id") in ("load_collection",)
        is_referenced = node_id in referenced
        has_refs = node_id in nodes_that_ref_others
        if not is_source and not is_referenced and not has_refs:
            orphaned.append(node_id)

    if orphaned:
        warnings.append(f"Orphaned nodes (not connected): {orphaned}")

    # ── Missing save_result check ──
    has_save = any(
        isinstance(n, dict) and n.get("process_id") == "save_result"
        for n in graph.values()
    )
    if not has_save:
        last = _find_last_node(graph)
        msg = "No save_result node found — graph cannot be submitted as a job"
        errors.append(msg)
        if fix_suggestions and last:
            fixes.append({
                "description": "Add save_result node",
                "node_id": "save",
                "node": {
                    "process_id": "save_result",
                    "arguments": {
                        "data": {"from_node": last},
                        "format": "GTiff",
                    },
                    "result": True,
                },
            })
            suggestions.append(
                f"Add a save node: graph['save'] = {json.dumps(fixes[-1]['node'])}"
            )

    # ── Broken from_node references ──
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        refs: Set[str] = set()
        _collect_from_nodes(node.get("arguments", {}), refs)
        for ref in refs:
            if ref not in graph:
                errors.append(
                    f"Node '{node_id}' references unknown node '{ref}'"
                )

    # ── Band validation (optional) ──
    if collection:
        try:
            from ..utils.schema_validator import COLLECTION_BANDS
            valid_bands = COLLECTION_BANDS.get(collection, set())
            if valid_bands:
                for node_id, node in graph.items():
                    if not isinstance(node, dict):
                        continue
                    pid = node.get("process_id")
                    args = node.get("arguments", {})
                    # Check load_collection bands
                    if pid == "load_collection":
                        bands_arg = args.get("bands", [])
                        for b in bands_arg:
                            if b not in valid_bands:
                                warnings.append(
                                    f"Band '{b}' in node '{node_id}' not in "
                                    f"{collection} bands: {sorted(valid_bands)}"
                                )
                    # Check array_element labels
                    elif pid == "array_element":
                        label = args.get("label", "")
                        if label and label not in valid_bands:
                            warnings.append(
                                f"Band '{label}' in node '{node_id}' not in "
                                f"{collection} bands: {sorted(valid_bands)}"
                            )
        except ImportError:
            pass

    # ── Topology ──
    topology = _build_graph_topology(graph)

    # ── Result node count check ──
    result_count = sum(
        1 for n in graph.values()
        if isinstance(n, dict) and n.get("result")
    )
    if result_count > 1:
        warnings.append(f"Multiple result nodes found ({result_count}). Only one is expected.")

    return {
        "ready_to_submit": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "suggestions": suggestions,
        "fixes": fixes,
        "orphaned_nodes": orphaned,
        "node_count": len(graph),
        "graph_topology": topology,
    }


# ── Tool Definitions ──

GRAPH_BUILDER_TOOL_DEFINITIONS = [
    {
        "name": "graph_add_threshold",
        "description": (
            "Add a comparison/threshold operation to an existing process graph. "
            "Operators: gt (>), lt (<), gte (>=), lte (<=), between. "
            "Use for water detection (MNDWI > 0.2), vegetation thresholds (NDVI > 0.3), etc. "
            "Auto-wires to the last node unless input_node is specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to modify",
                },
                "operator": {
                    "type": "string",
                    "enum": ["gt", "lt", "gte", "lte", "between"],
                    "description": "Comparison operator",
                },
                "value": {
                    "type": "number",
                    "description": "Threshold value (required for gt/lt/gte/lte)",
                },
                "min_value": {
                    "type": "number",
                    "description": "Minimum value (required for 'between')",
                },
                "max_value": {
                    "type": "number",
                    "description": "Maximum value (required for 'between')",
                },
                "input_node": {
                    "type": "string",
                    "description": "Explicit input node ID (auto-wires to last node if omitted)",
                },
                "node_id": {
                    "type": "string",
                    "description": "Custom node ID (auto-generated if omitted)",
                },
            },
            "required": ["graph", "operator"],
        },
    },
    {
        "name": "graph_add_logic",
        "description": (
            "Add a boolean logic operation (AND/OR/NOT) to combine masks. "
            "AND/OR require input_a and input_b. NOT requires only input_a. "
            "Use for snow cover (NDSI > 0.4 AND NDVI < 0.2), compound masks, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to modify",
                },
                "operator": {
                    "type": "string",
                    "enum": ["and", "or", "not"],
                    "description": "Boolean operator",
                },
                "input_a": {
                    "type": "string",
                    "description": "First input node ID (for AND/OR: required; for NOT: auto-wires if omitted)",
                },
                "input_b": {
                    "type": "string",
                    "description": "Second input node ID (required for AND/OR, ignored for NOT)",
                },
                "node_id": {
                    "type": "string",
                    "description": "Custom node ID (auto-generated if omitted)",
                },
            },
            "required": ["graph", "operator"],
        },
    },
    {
        "name": "graph_add_math",
        "description": (
            "Add arithmetic (add/subtract/multiply/divide) between two nodes or a node and a scalar. "
            "If input_b is a string, it references another node. If a number, it's a scalar value. "
            "Use for dNBR (pre_NBR - post_NBR), scaling (value * 0.0001), band ratios, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to modify",
                },
                "operator": {
                    "type": "string",
                    "enum": ["add", "subtract", "multiply", "divide"],
                    "description": "Arithmetic operator",
                },
                "input_a": {
                    "type": "string",
                    "description": "First input node ID",
                },
                "input_b": {
                    "description": "Second input: node ID (string) or scalar value (number)",
                },
                "node_id": {
                    "type": "string",
                    "description": "Custom node ID (auto-generated if omitted)",
                },
            },
            "required": ["graph", "operator", "input_a", "input_b"],
        },
    },
    {
        "name": "graph_add_band_filter",
        "description": (
            "Extract a single band from a datacube using array_element. "
            "Use before custom band math to isolate specific bands (e.g., NIR, SWIR16). "
            "Auto-wires to the last node unless input_node is specified."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to modify",
                },
                "band_name": {
                    "type": "string",
                    "description": "Band label to extract (e.g., 'nir', 'swir16', 'green')",
                },
                "input_node": {
                    "type": "string",
                    "description": "Explicit input node ID (auto-wires to last node if omitted)",
                },
                "node_id": {
                    "type": "string",
                    "description": "Custom node ID (auto-generated if omitted)",
                },
            },
            "required": ["graph", "band_name"],
        },
    },
    {
        "name": "graph_add_mask",
        "description": (
            "Apply a computed boolean mask to data. Pixels where mask is true are replaced "
            "with null (or a specified replacement value). Different from cloud masking — "
            "this uses any computed mask node. Use for custom land/water masking, thresholding, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to modify",
                },
                "data_node": {
                    "type": "string",
                    "description": "Node ID containing the data to mask",
                },
                "mask_node": {
                    "type": "string",
                    "description": "Node ID containing the boolean mask",
                },
                "replacement": {
                    "description": "Replacement value for masked pixels (null if omitted)",
                },
                "node_id": {
                    "type": "string",
                    "description": "Custom node ID (auto-generated if omitted)",
                },
            },
            "required": ["graph", "data_node", "mask_node"],
        },
    },
    {
        "name": "graph_add_merge",
        "description": (
            "Merge two datacubes with optional overlap resolver. "
            "Overlap resolvers: subtract, add, multiply, divide, mean. "
            "Use for change detection (merge pre/post cubes), multi-sensor fusion, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to modify",
                },
                "cube_a": {
                    "type": "string",
                    "description": "First datacube node ID",
                },
                "cube_b": {
                    "type": "string",
                    "description": "Second datacube node ID",
                },
                "overlap_resolver": {
                    "type": "string",
                    "enum": ["subtract", "add", "multiply", "divide", "mean"],
                    "description": "How to resolve overlapping dimensions (optional)",
                },
                "node_id": {
                    "type": "string",
                    "description": "Custom node ID (auto-generated if omitted)",
                },
            },
            "required": ["graph", "cube_a", "cube_b"],
        },
    },
    {
        "name": "graph_validate_full",
        "description": (
            "Deep validation of a process graph before job submission. "
            "Checks structure, from_node references, save_result presence, orphaned nodes, "
            "and optionally validates band names against a collection. "
            "Returns ready_to_submit boolean, errors, warnings, fix suggestions, and graph topology."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {
                    "type": "object",
                    "description": "The process graph dict to validate",
                },
                "collection": {
                    "type": "string",
                    "description": "Collection ID for band validation (e.g., 'sentinel-2-l2a')",
                },
                "fix_suggestions": {
                    "type": "boolean",
                    "description": "Include actionable fix suggestions (default: true)",
                    "default": True,
                },
            },
            "required": ["graph"],
        },
    },
    {
        "name": "openeo_process_node",
        "description": (
            "Add ANY OpenEO process node to an existing graph. Universal tool — "
            "use openeo_get_process_spec first to learn the correct parameters. "
            "Supports auto-wiring: set data/x argument to null and it connects to the last terminal node."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {"type": "object", "description": "Existing process graph dict"},
                "node_id": {"type": "string", "description": "Unique node ID (e.g. 'resample_1')"},
                "process_id": {"type": "string", "description": "OpenEO process ID (e.g. 'resample_spatial', 'filter_temporal')"},
                "arguments": {"type": "object", "description": "Process arguments matching the spec"},
                "result": {"type": "boolean", "description": "Mark as result node", "default": False},
                "auto_wire": {"type": "boolean", "description": "Auto-connect null data/x args to last terminal", "default": True},
            },
            "required": ["graph", "node_id", "process_id", "arguments"],
        },
    },
    {
        "name": "openeo_graph_inspect",
        "description": (
            "Inspect a process graph for debugging: node topology, dimension tracking, "
            "band flow, and validation errors. Use BEFORE submitting to openeo_create_job "
            "to catch issues like missing bands, dimension mismatches, or disconnected nodes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "graph": {"type": "object", "description": "Process graph dict to inspect"},
                "check": {
                    "type": "string",
                    "enum": ["topology", "dimensions", "bands", "full"],
                    "description": "What to check (default: full)",
                    "default": "full",
                },
            },
            "required": ["graph"],
        },
    },
    {
        "name": "openeo_compile_scf",
        "description": (
            "Compile a Scientific Computation Framework (SCF) document to a validated OpenEO process graph. "
            "SCF is a declarative format: specify WHAT (collection, bands, indices, reductions) not HOW. "
            "Validates scientific invariants (cloud masking order, reflectance scaling), estimates cost, "
            "and generates a tested process graph. Use instead of openeo_raw_graph for complex workflows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "scf": {
                    "type": "object",
                    "description": "SCF document with keys: scf_version, domain_profile, policy_profile, data, quality_constraints, transformations, output",
                },
                "validate_only": {
                    "type": "boolean",
                    "description": "Only validate without lowering to process graph",
                    "default": False,
                },
            },
            "required": ["scf"],
        },
    },
]


# ── Factory Function ──


# ── Three Strategic Tools ──


def _openeo_process_node(
    graph: dict,
    node_id: str,
    process_id: str,
    arguments: dict,
    result: bool = False,
    auto_wire: bool = True,
) -> Tuple[dict, str]:
    """Add ANY OpenEO process node to an existing graph.

    Universal tool that replaces the need for 100+ process-specific tools.
    Supports auto-wiring: if arguments contain a null/missing "data" or "x"
    key, it's automatically connected to the last terminal node.

    Args:
        graph: Existing process graph dict
        node_id: Unique node ID (e.g. "resample_1")
        process_id: OpenEO process ID (e.g. "resample_spatial")
        arguments: Process arguments dict
        result: Whether this is the result node
        auto_wire: Auto-connect data/x args to last terminal if not specified

    Returns:
        (updated_graph, node_id)
    """
    if node_id in graph:
        raise ValueError(f"Node '{node_id}' already exists in graph")

    # Auto-wire: connect unspecified data/x inputs to last terminal
    if auto_wire and graph:
        last = _find_last_node(graph)
        if last:
            for key in ("data", "x", "cube1"):
                if key in arguments and arguments[key] is None:
                    arguments[key] = {"from_node": last}

    node = {"process_id": process_id, "arguments": arguments}
    if result:
        node["result"] = True
    graph[node_id] = node
    return graph, node_id


def _openeo_graph_inspect(
    graph: dict,
    check: str = "full",
) -> dict:
    """Inspect a process graph for debugging: topology, dimensions, bands, validation.

    Uses the upstream GraphValidator for type inference — tracks ValueType
    (kind, dims, bands) through every node, exposing the data flow.

    Args:
        graph: Process graph dict
        check: One of "topology", "dimensions", "bands", "full"

    Returns:
        Inspection report dict
    """
    report: Dict[str, Any] = {}

    # Topology (always)
    report["topology"] = _build_graph_topology(graph)
    report["node_count"] = len(graph)
    report["nodes"] = []
    for nid, node in graph.items():
        if isinstance(node, dict):
            report["nodes"].append({
                "id": nid,
                "process_id": node.get("process_id", "?"),
                "result": bool(node.get("result")),
            })

    if check in ("dimensions", "bands", "full"):
        # Run GraphValidator for type inference
        try:
            from openeo_ai.validation.graph_validator import (
                GraphValidator,
                build_default_process_specs,
                get_custom_process_specs,
            )
            specs = build_default_process_specs()
            specs.update(get_custom_process_specs())
            validator = GraphValidator(process_specs=specs)
            result = validator.validate(graph)

            # Extract per-node type info from graph_summary
            node_types = {}
            for node_info in result.graph_summary.get("nodes", []):
                nid = node_info.get("id")
                inferred = node_info.get("inferred_output", {})
                node_types[nid] = {
                    "kind": inferred.get("kind", "unknown"),
                    "dims": inferred.get("dims", []),
                    "bands": inferred.get("bands", []),
                }

            report["type_inference"] = node_types
            report["errors"] = [f"[{e.code}] {e.message}" for e in result.errors]
            report["warnings"] = [f"[{w.code}] {w.message}" for w in result.warnings]
            report["valid"] = not result.has_errors()
        except Exception as e:
            report["type_inference_error"] = str(e)

    if check in ("bands", "full"):
        # Extract band flow: which bands are loaded and where they're used
        band_flow = {}
        for nid, node in graph.items():
            if not isinstance(node, dict):
                continue
            pid = node.get("process_id")
            args = node.get("arguments", {})
            if pid == "load_collection":
                band_flow[nid] = {"loaded": args.get("bands", [])}
            elif pid == "array_element":
                band_flow[nid] = {"extracts": args.get("label", "?")}
            elif pid == "filter_bands":
                band_flow[nid] = {"filters_to": args.get("bands", [])}
        report["band_flow"] = band_flow

    return report


def _openeo_compile_scf(
    scf: dict,
    validate_only: bool = False,
) -> dict:
    """Compile an SCF document to a validated OpenEO process graph.

    The SCF (Scientific Computation Framework) is a declarative format that
    describes WHAT to compute without HOW. This tool:
    1. Validates structure + scientific invariants (optical, SAR, etc.)
    2. Checks cost estimation (warns on large jobs)
    3. Lowers (compiles) to a concrete OpenEO process graph
    4. Validates the generated graph with type inference

    Args:
        scf: SCF document dict
        validate_only: If True, only validate without lowering

    Returns:
        Dict with validation results, cost estimate, and optional process graph
    """
    from openeo_ai.validation.scf import validate_scf, lower_scf_to_pg, hash_json
    from openeo_ai.validation.graph_validator import (
        GraphValidator,
        build_default_process_specs,
        get_custom_process_specs,
    )

    # Step 1: Validate SCF
    errors, warnings, required = validate_scf(scf)
    result = {
        "valid": len(errors) == 0,
        "errors": [f"[{e.code}] {e.message}" for e in errors],
        "warnings": [f"[{w.code}] {w.message}" for w in warnings],
        "required_decisions": [f"[{r.code}] {r.message}" for r in required],
        "scf_hash": hash_json(scf),
    }

    # Step 2: Cost estimation
    try:
        from openeo_ai.validation.scf.tools.cost_estimation import estimate
        result["cost_estimate"] = estimate(scf)
    except Exception:
        pass

    if validate_only or errors:
        return result

    # Step 3: Lower to process graph
    specs = build_default_process_specs()
    specs.update(get_custom_process_specs())
    pg, pg_warnings, pg_errors, pg_hash = lower_scf_to_pg(scf, process_specs=specs)

    if pg_errors:
        result["lowering_errors"] = [f"[{e.code}] {e.message}" for e in pg_errors]
        result["valid"] = False
        return result

    result["process_graph"] = pg
    result["pg_hash"] = pg_hash
    result["lowering_warnings"] = [f"[{w.code}] {w.message}" for w in pg_warnings]

    # Step 4: Validate the generated graph
    try:
        inner = pg.get("process_graph", pg)
        validator = GraphValidator(process_specs=specs)
        gv_result = validator.validate(inner)
        if gv_result.has_errors():
            result["graph_validation_errors"] = [
                f"[{e.code}] {e.message}" for e in gv_result.errors
            ]
        result["graph_node_count"] = gv_result.graph_summary.get("node_count", 0)
    except Exception:
        pass

    return result


def create_graph_builder_tools(config=None) -> Dict[str, Any]:
    """Create graph builder tools dict for Claude SDK.

    Args:
        config: Optional configuration object.

    Returns:
        Dictionary mapping tool names to async handler functions.
    """

    async def _handle_add_threshold(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _graph_add_threshold(
                graph=args["graph"],
                operator=args["operator"],
                value=args.get("value"),
                min_value=args.get("min_value"),
                max_value=args.get("max_value"),
                input_node=args.get("input_node"),
                node_id=args.get("node_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_add_logic(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _graph_add_logic(
                graph=args["graph"],
                operator=args["operator"],
                input_a=args.get("input_a"),
                input_b=args.get("input_b"),
                node_id=args.get("node_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_add_math(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _graph_add_math(
                graph=args["graph"],
                operator=args["operator"],
                input_a=args["input_a"],
                input_b=args["input_b"],
                node_id=args.get("node_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_add_band_filter(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _graph_add_band_filter(
                graph=args["graph"],
                band_name=args["band_name"],
                input_node=args.get("input_node"),
                node_id=args.get("node_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_add_mask(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _graph_add_mask(
                graph=args["graph"],
                data_node=args["data_node"],
                mask_node=args["mask_node"],
                replacement=args.get("replacement"),
                node_id=args.get("node_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_add_merge(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _graph_add_merge(
                graph=args["graph"],
                cube_a=args["cube_a"],
                cube_b=args["cube_b"],
                overlap_resolver=args.get("overlap_resolver"),
                node_id=args.get("node_id"),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_validate_full(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = _graph_validate_full(
                graph=args["graph"],
                collection=args.get("collection"),
                fix_suggestions=args.get("fix_suggestions", True),
            )
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": f"Error: {e}"}]}

    async def _handle_process_node(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            graph, nid = _openeo_process_node(
                graph=args["graph"],
                node_id=args["node_id"],
                process_id=args["process_id"],
                arguments=args.get("arguments", {}),
                result=args.get("result", False),
                auto_wire=args.get("auto_wire", True),
            )
            return {"content": [{"type": "text", "text": json.dumps(_make_result(graph, nid))}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}]}

    async def _handle_graph_inspect(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = _openeo_graph_inspect(
                graph=args["graph"],
                check=args.get("check", "full"),
            )
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}]}

    async def _handle_compile_scf(args: Dict[str, Any]) -> Dict[str, Any]:
        try:
            result = _openeo_compile_scf(
                scf=args["scf"],
                validate_only=args.get("validate_only", False),
            )
            return {"content": [{"type": "text", "text": json.dumps(result)}]}
        except Exception as e:
            return {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}]}

    return {
        "graph_add_threshold": _handle_add_threshold,
        "graph_add_logic": _handle_add_logic,
        "graph_add_math": _handle_add_math,
        "graph_add_band_filter": _handle_add_band_filter,
        "graph_add_mask": _handle_add_mask,
        "graph_add_merge": _handle_add_merge,
        "graph_validate_full": _handle_validate_full,
        "openeo_process_node": _handle_process_node,
        "openeo_graph_inspect": _handle_graph_inspect,
        "openeo_compile_scf": _handle_compile_scf,
    }
