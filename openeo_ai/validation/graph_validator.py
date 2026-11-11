"""Graph validation utilities for OpenEO process graphs.

Provides deterministic validation (structure, dependencies, argument presence, and
basic dimension/type inference) and optional LLM-assisted diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Protocol
import logging

from openeo_fastapi.api.types import Error
from openeo_fastapi.api.models import ValidationIssue

logger = logging.getLogger(__name__)


@dataclass
class ValueType:
    """Coarse-grained value type for process graph inference."""
    kind: str
    dims: Set[str] = field(default_factory=set)
    bands: Optional[List[str]] = None

    def is_cube(self) -> bool:
        return self.kind == "cube"

    def to_summary(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "dims": sorted(self.dims) if self.dims else [],
            "bands": self.bands or [],
        }


@dataclass
class LLMIssue:
    code: str
    message: str
    node_id: Optional[str] = None
    suggestion: Optional[str] = None


@dataclass
class LLMValidationResult:
    errors: List[LLMIssue] = field(default_factory=list)
    warnings: List[LLMIssue] = field(default_factory=list)
    suggestions: List[LLMIssue] = field(default_factory=list)
    patches: List[Dict[str, Any]] = field(default_factory=list)
    raw: Optional[str] = None
    reason: Optional[str] = None


class LLMValidator(Protocol):
    def validate(self, graph_summary: Dict[str, Any], process_specs: Dict[str, Any]) -> LLMValidationResult:
        ...


@dataclass
class GraphValidationResult:
    errors: List[Error] = field(default_factory=list)
    warnings: List[ValidationIssue] = field(default_factory=list)
    suggestions: List[ValidationIssue] = field(default_factory=list)
    patches: List[Dict[str, Any]] = field(default_factory=list)
    graph_summary: Dict[str, Any] = field(default_factory=dict)

    def has_errors(self) -> bool:
        return len(self.errors) > 0


PROCESS_DIMENSION_REDUCERS = {
    "reduce_dimension",
    "aggregate_temporal",
    "reduce_temporal",
    "aggregate_spatial",
    "reduce_spatial",
}

PROCESS_DIMENSION_APPLIERS = {
    "apply_dimension",
}

NUMERIC_BINARY_OPS = {
    "add",
    "subtract",
    "multiply",
    "divide",
    "normalized_difference",
    "ndvi",
    "evi",
    "atan2",
    "power",
}

NUMERIC_UNARY_OPS = {
    "abs",
    "sqrt",
    "log",
    "ln",
    "log10",
    "exp",
    "round",
    "floor",
    "ceil",
}


def _infer_literal_type(value: Any) -> ValueType:
    if isinstance(value, bool):
        return ValueType(kind="boolean")
    if isinstance(value, (int, float)):
        return ValueType(kind="number")
    if isinstance(value, str):
        return ValueType(kind="string")
    if isinstance(value, list):
        return ValueType(kind="array")
    if isinstance(value, dict):
        return ValueType(kind="object")
    return ValueType(kind="unknown")


def _extract_dependencies(value: Any, deps: Set[str]) -> None:
    if isinstance(value, dict):
        if "from_node" in value:
            deps.add(value["from_node"])
        else:
            for v in value.values():
                _extract_dependencies(v, deps)
    elif isinstance(value, list):
        for v in value:
            _extract_dependencies(v, deps)


def _normalize_graph(process_graph: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(process_graph, dict):
        return {}
    if "process_graph" in process_graph and len(process_graph) == 1:
        inner = process_graph.get("process_graph")
        if isinstance(inner, dict):
            return inner
    return process_graph


def _summarize_process_specs(process_specs: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    for pid, spec in process_specs.items():
        params = []
        for param in spec.get("parameters", []) or []:
            name = param.get("name")
            if name:
                params.append(name)
        returns = spec.get("returns", {})
        schema = returns.get("schema", {}) if isinstance(returns, dict) else {}
        return_type = schema.get("type") if isinstance(schema, dict) else None
        summary[pid] = {
            "parameters": params,
            "return_type": return_type,
        }
    return summary


def get_custom_process_specs() -> Dict[str, Dict[str, Any]]:
    """Return custom process specs not included in openeo_processes_dask."""
    specs: Dict[str, Dict[str, Any]] = {}

    # Ice/Snow classification spec
    try:
        from openeo_fastapi.client.processes.ice_snow_classification import IceSnowClassificationProcess
        specs[IceSnowClassificationProcess.PROCESS_ID] = IceSnowClassificationProcess.PROCESS_SPEC
    except Exception as e:
        logger.debug(f"Skipping ice_snow_classification spec: {e}")

    # Provide minimal specs for UDF-related processes if missing upstream
    minimal_udf_specs = {
        "run_udf": {
            "id": "run_udf",
            "summary": "Run user-defined function",
            "parameters": [
                {"name": "data"},
                {"name": "udf"},
                {"name": "runtime", "optional": True},
                {"name": "context", "optional": True},
            ],
            "returns": {"schema": {"type": "raster-cube"}},
        },
        "apply_neighborhood": {
            "id": "apply_neighborhood",
            "summary": "Apply a process to a neighborhood",
            "parameters": [
                {"name": "data"},
                {"name": "process"},
                {"name": "size"},
                {"name": "overlap", "optional": True},
                {"name": "context", "optional": True},
            ],
            "returns": {"schema": {"type": "raster-cube"}},
        },
        "apply_polygon": {
            "id": "apply_polygon",
            "summary": "Apply a process to each polygon",
            "parameters": [
                {"name": "data"},
                {"name": "polygons"},
                {"name": "process"},
                {"name": "context", "optional": True},
            ],
            "returns": {"schema": {"type": "raster-cube"}},
        },
        "otsu_threshold": {
            "id": "otsu_threshold",
            "summary": "Otsu thresholding",
            "parameters": [
                {"name": "data"},
                {"name": "context", "optional": True},
            ],
            "returns": {"schema": {"type": "raster-cube"}},
        },
        "hillshade": {
            "id": "hillshade",
            "summary": "Hillshade computation",
            "parameters": [
                {"name": "data"},
                {"name": "altitude", "optional": True},
                {"name": "azimuth", "optional": True},
            ],
            "returns": {"schema": {"type": "raster-cube"}},
        },
    }

    specs.update(minimal_udf_specs)

    # Python reserved-keyword processes not exported by openeo_processes_dask.specs.__all__
    keyword_processes = {
        "or": {
            "id": "or",
            "summary": "Logical OR",
            "parameters": [
                {"name": "x"},
                {"name": "y"},
            ],
            "returns": {"schema": {"type": "boolean"}},
        },
        "and": {
            "id": "and",
            "summary": "Logical AND",
            "parameters": [
                {"name": "x"},
                {"name": "y"},
            ],
            "returns": {"schema": {"type": "boolean"}},
        },
        "not": {
            "id": "not",
            "summary": "Logical NOT",
            "parameters": [
                {"name": "x"},
            ],
            "returns": {"schema": {"type": "boolean"}},
        },
        "if": {
            "id": "if",
            "summary": "Conditional (if-then-else)",
            "parameters": [
                {"name": "value"},
                {"name": "accept"},
                {"name": "reject", "optional": True},
            ],
            "returns": {"schema": {}},
        },
    }
    for pid, spec in keyword_processes.items():
        if pid not in specs:
            specs[pid] = spec

    return specs


def build_default_process_specs() -> Dict[str, Dict[str, Any]]:
    """Build process specs from openeo_processes_dask if available."""
    specs: Dict[str, Dict[str, Any]] = {}
    try:
        import openeo_processes_dask.specs
        for process_id in openeo_processes_dask.specs.__all__:
            specs[process_id] = getattr(openeo_processes_dask.specs, process_id)
    except Exception as e:
        logger.warning(f"Failed to load openeo_processes_dask specs: {e}")
    return specs


class GraphValidator:
    """Deterministic validation and inference for OpenEO process graphs."""

    def __init__(
        self,
        process_specs: Dict[str, Dict[str, Any]],
        get_udp_spec: Optional[Any] = None,
        namespace: Optional[str] = None,
    ) -> None:
        self.process_specs = process_specs
        self.get_udp_spec = get_udp_spec
        self.namespace = namespace

    def validate(
        self,
        process_graph: Dict[str, Any],
        llm_validator: Optional[LLMValidator] = None,
    ) -> GraphValidationResult:
        errors: List[Error] = []
        warnings: List[ValidationIssue] = []
        suggestions: List[ValidationIssue] = []
        patches: List[Dict[str, Any]] = []

        graph = _normalize_graph(process_graph)
        if not isinstance(graph, dict) or not graph:
            errors.append(Error(code="InvalidGraph", message="Process graph must be a non-empty object."))
            return GraphValidationResult(errors=errors, warnings=warnings, suggestions=suggestions)

        nodes: Dict[str, Dict[str, Any]] = {}
        deps_map: Dict[str, Set[str]] = {}

        for node_id, node in graph.items():
            if not isinstance(node, dict):
                errors.append(Error(code="InvalidNode", message=f"Node '{node_id}' must be an object."))
                continue
            process_id = node.get("process_id")
            if not process_id:
                errors.append(Error(code="MissingProcessId", message=f"Node '{node_id}' is missing process_id."))
                continue
            arguments = node.get("arguments", {})
            if arguments is None:
                arguments = {}
            deps: Set[str] = set()
            _extract_dependencies(arguments, deps)
            nodes[node_id] = {
                "process_id": process_id,
                "arguments": arguments,
                "result": bool(node.get("result")),
            }
            deps_map[node_id] = deps

        # Validate dependency references
        for node_id, deps in deps_map.items():
            for dep in deps:
                if dep not in nodes:
                    errors.append(
                        Error(
                            code="InvalidReference",
                            message=f"Node '{node_id}' references missing node '{dep}'.",
                        )
                    )

        # Detect cycles (simple Kahn's algorithm)
        topo_order: List[str] = []
        in_degree = {nid: 0 for nid in nodes}
        for nid, deps in deps_map.items():
            for dep in deps:
                if dep in in_degree:
                    in_degree[nid] += 1
        reverse_deps: Dict[str, Set[str]] = {nid: set() for nid in nodes}
        for nid, deps in deps_map.items():
            for dep in deps:
                if dep in reverse_deps:
                    reverse_deps[dep].add(nid)
        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        while queue:
            current = queue.pop(0)
            topo_order.append(current)
            for nid, deps in deps_map.items():
                if current in deps:
                    in_degree[nid] -= 1
                    if in_degree[nid] == 0:
                        queue.append(nid)
        if len(topo_order) != len(nodes):
            errors.append(Error(code="CyclicGraph", message="Process graph contains a cycle."))

        # Check result nodes
        result_nodes = [nid for nid, node in nodes.items() if node.get("result")]
        if not result_nodes:
            warnings.append(
                ValidationIssue(
                    code="MissingResult",
                    message="No node is marked as result. Consider adding a result node or save_result.",
                    severity="warning",
                )
            )

        # Warn about save_result nodes that are not terminal
        for nid, node in nodes.items():
            if node.get("process_id") == "save_result" and reverse_deps.get(nid):
                warnings.append(
                    ValidationIssue(
                        code="SaveResultNotTerminal",
                        message=f"Node '{nid}' save_result is used by other nodes; it should be terminal.",
                        node_id=nid,
                        severity="warning",
                    )
                )

        # Warn about dangling nodes (never used and not a result)
        for nid in nodes:
            if not reverse_deps.get(nid) and nid not in result_nodes:
                warnings.append(
                    ValidationIssue(
                        code="DanglingNode",
                        message=f"Node '{nid}' is not referenced by any other node and is not a result.",
                        node_id=nid,
                        severity="warning",
                    )
                )

        node_outputs: Dict[str, ValueType] = {}

        for node_id in topo_order:
            node = nodes[node_id]
            process_id = node["process_id"]
            arguments = node["arguments"]

            spec = self.process_specs.get(process_id)
            if spec is None and self.get_udp_spec:
                try:
                    udp = self.get_udp_spec(process_id, self.namespace)
                    if udp:
                        spec = {
                            "id": process_id,
                            "parameters": udp.get("parameters") or [],
                            "returns": udp.get("returns") or {},
                        }
                except Exception:
                    spec = None

            if spec is None:
                errors.append(
                    Error(
                        code="UnknownProcess",
                        message=f"Unknown process '{process_id}' in node '{node_id}'.",
                    )
                )
                node_outputs[node_id] = ValueType(kind="unknown")
                continue

            # Required parameter checks
            for param in spec.get("parameters", []) or []:
                param_name = param.get("name")
                if not param_name:
                    continue
                if not param.get("optional", False) and param_name not in arguments:
                    errors.append(
                        Error(
                            code="MissingParameter",
                            message=f"Node '{node_id}' missing required parameter '{param_name}'.",
                        )
                    )

            # Validate nested process graphs for apply/reducer style arguments
            self._validate_process_graph_arguments(
                node_id=node_id,
                process_id=process_id,
                arguments=arguments,
                errors=errors,
                warnings=warnings,
            )

            # Infer output type
            output_type = self._infer_output_type(
                process_id=process_id,
                arguments=arguments,
                node_outputs=node_outputs,
                node_id=node_id,
                warnings=warnings,
                errors=errors,
            )
            node_outputs[node_id] = output_type

        graph_summary = {
            "node_count": len(nodes),
            "nodes": [
                {
                    "id": nid,
                    "process_id": nodes[nid]["process_id"],
                    "dependencies": sorted(list(deps_map.get(nid, set()))),
                    "arguments": list((nodes[nid].get("arguments") or {}).keys()),
                    "result": bool(nodes[nid].get("result")),
                    "inferred_output": node_outputs.get(nid, ValueType(kind="unknown")).to_summary(),
                }
                for nid in topo_order
            ],
            "result_nodes": result_nodes,
        }

        # Optional LLM diagnostics
        if llm_validator is not None:
            try:
                llm_result = llm_validator.validate(
                    graph_summary=graph_summary,
                    process_specs=_summarize_process_specs(self.process_specs),
                )
                warnings.extend(
                    [
                        ValidationIssue(
                            code=f"LLM.{issue.code}",
                            message=issue.message,
                            node_id=issue.node_id,
                            suggestion=issue.suggestion,
                            severity="warning",
                        )
                        for issue in llm_result.warnings
                    ]
                )
                suggestions.extend(
                    [
                        ValidationIssue(
                            code=f"LLM.{issue.code}",
                            message=issue.message,
                            node_id=issue.node_id,
                            suggestion=issue.suggestion,
                            severity="info",
                        )
                        for issue in llm_result.suggestions
                    ]
                )
                # LLM-reported errors are downgraded to warnings to avoid false positives
                warnings.extend(
                    [
                        ValidationIssue(
                            code=f"LLM.{issue.code}",
                            message=issue.message,
                            node_id=issue.node_id,
                            suggestion=issue.suggestion,
                            severity="warning",
                        )
                        for issue in llm_result.errors
                    ]
                )
                if isinstance(llm_result.patches, list):
                    patches.extend(llm_result.patches)
            except Exception as e:
                warnings.append(
                    ValidationIssue(
                        code="LLMValidationFailed",
                        message=f"LLM validation failed: {e}",
                        severity="warning",
                    )
                )

        return GraphValidationResult(
            errors=errors,
            warnings=warnings,
            suggestions=suggestions,
            patches=patches,
            graph_summary=graph_summary,
        )

    def _validate_process_graph_arguments(
        self,
        node_id: str,
        process_id: str,
        arguments: Dict[str, Any],
        errors: List[Error],
        warnings: List[ValidationIssue],
    ) -> None:
        """Validate nested process graphs in arguments (apply/reducer patterns)."""
        # Known argument names that contain process graphs
        graph_arg_names = {"process", "reducer", "process_graph"}

        def _validate_subgraph(
            graph_payload: Dict[str, Any],
            label: str,
            allowed_params: Optional[Set[str]] = None,
        ) -> None:
            if not isinstance(graph_payload, dict):
                errors.append(
                    Error(
                        code="InvalidSubgraph",
                        message=f"Node '{node_id}' {label} must be an object with 'process_graph'.",
                    )
                )
                return
            subgraph = graph_payload.get("process_graph") if "process_graph" in graph_payload else graph_payload
            if not isinstance(subgraph, dict) or not subgraph:
                errors.append(
                    Error(
                        code="InvalidSubgraph",
                        message=f"Node '{node_id}' {label} process_graph is empty or invalid.",
                    )
                )
                return

            # Validate subgraph deterministically (no LLM)
            sub_validator = GraphValidator(
                process_specs=self.process_specs,
                get_udp_spec=self.get_udp_spec,
                namespace=self.namespace,
            )
            sub_result = sub_validator.validate(subgraph)
            if sub_result.errors:
                for err in sub_result.errors:
                    errors.append(
                        Error(
                            code=f"Subgraph.{err.code}",
                            message=f"Node '{node_id}' {label} subgraph error: {err.message}",
                        )
                    )

            # Promote missing result from warning to error for reducers/apply
            if not sub_result.graph_summary.get("result_nodes"):
                errors.append(
                    Error(
                        code="SubgraphMissingResult",
                        message=f"Node '{node_id}' {label} subgraph has no result node.",
                    )
                )

            # Warn about load_collection or save_result inside subgraph
            for sub_node in sub_result.graph_summary.get("nodes", []):
                sub_pid = sub_node.get("process_id")
                if sub_pid in {"load_collection", "save_result"}:
                    warnings.append(
                        ValidationIssue(
                            code="SubgraphContainsDataIO",
                            message=(
                                f"Node '{node_id}' {label} subgraph contains '{sub_pid}', "
                                "which is unusual for apply/reducer graphs."
                            ),
                            node_id=node_id,
                            severity="warning",
                        )
                    )

            # Check from_parameter usage
            if allowed_params:
                used_params: Set[str] = set()

                def _collect_from_parameter(value: Any) -> None:
                    if isinstance(value, dict):
                        if "from_parameter" in value:
                            used_params.add(value["from_parameter"])
                        else:
                            for v in value.values():
                                _collect_from_parameter(v)
                    elif isinstance(value, list):
                        for v in value:
                            _collect_from_parameter(v)

                _collect_from_parameter(subgraph)
                for param in used_params:
                    if param not in allowed_params:
                        warnings.append(
                            ValidationIssue(
                                code="SubgraphUnknownParameter",
                                message=(
                                    f"Node '{node_id}' {label} subgraph uses from_parameter '{param}' "
                                    f"which is not in expected parameters {sorted(allowed_params)}."
                                ),
                                node_id=node_id,
                                severity="warning",
                            )
                        )

            # For reducers, warn if output appears to be a cube
            result_nodes = sub_result.graph_summary.get("result_nodes", [])
            if result_nodes:
                result_id = result_nodes[0]
                node_lookup = {n.get("id"): n for n in sub_result.graph_summary.get("nodes", [])}
                result_summary = node_lookup.get(result_id, {})
                inferred = result_summary.get("inferred_output", {}) or {}
                if inferred.get("kind") == "cube":
                    warnings.append(
                        ValidationIssue(
                            code="ReducerReturnsCube",
                            message=(
                                f"Node '{node_id}' {label} subgraph returns a cube. "
                                "Reducers typically return a scalar per pixel."
                            ),
                            node_id=node_id,
                            severity="warning",
                        )
                    )

        for arg_name, arg_value in (arguments or {}).items():
            if arg_name in graph_arg_names and isinstance(arg_value, dict):
                label = f"argument '{arg_name}'"
                allowed_params = {"data"}
                _validate_subgraph(arg_value, label=label, allowed_params=allowed_params)
            elif isinstance(arg_value, dict) and "process_graph" in arg_value:
                label = f"argument '{arg_name}'"
                _validate_subgraph(arg_value, label=label, allowed_params={"data"})

    def _infer_output_type(
        self,
        process_id: str,
        arguments: Dict[str, Any],
        node_outputs: Dict[str, ValueType],
        node_id: str,
        warnings: List[ValidationIssue],
        errors: List[Error],
    ) -> ValueType:
        def resolve_arg(arg: Any) -> ValueType:
            if isinstance(arg, dict):
                if "from_node" in arg:
                    return node_outputs.get(arg["from_node"], ValueType(kind="unknown"))
                if "from_parameter" in arg:
                    return ValueType(kind="unknown")
                return ValueType(kind="object")
            return _infer_literal_type(arg)

        if process_id == "load_collection":
            bands = arguments.get("bands")
            band_list = None
            if isinstance(bands, list) and all(isinstance(b, str) for b in bands):
                band_list = bands
            return ValueType(kind="cube", dims={"x", "y", "bands", "time"}, bands=band_list)

        if process_id in {"filter_bands"}:
            data = resolve_arg(arguments.get("data"))
            bands = arguments.get("bands")
            if data.is_cube() and isinstance(bands, list):
                if data.bands is not None:
                    missing = [b for b in bands if b not in data.bands]
                    if missing:
                        errors.append(
                            Error(
                                code="UnknownBand",
                                message=f"Node '{node_id}' references unknown bands: {missing}",
                            )
                        )
                return ValueType(kind="cube", dims=data.dims, bands=bands)
            warnings.append(
                ValidationIssue(
                    code="FilterBandsUnknown",
                    message=f"Node '{node_id}' filter_bands could not validate band list.",
                    node_id=node_id,
                    severity="warning",
                )
            )
            return data

        if process_id in {"array_element"}:
            data = resolve_arg(arguments.get("data"))
            label = arguments.get("label")
            if data.is_cube() and isinstance(label, str):
                if data.bands is not None and label not in data.bands:
                    errors.append(
                        Error(
                            code="UnknownBand",
                            message=f"Node '{node_id}' array_element label '{label}' not in bands.",
                        )
                    )
                return ValueType(kind="cube", dims=set(data.dims) - {"bands"}, bands=None)
            return data

        if process_id in PROCESS_DIMENSION_REDUCERS:
            data = resolve_arg(arguments.get("data"))
            dimension = arguments.get("dimension")
            if data.is_cube() and isinstance(dimension, str):
                if dimension not in data.dims:
                    errors.append(
                        Error(
                            code="InvalidDimension",
                            message=f"Node '{node_id}' reduces missing dimension '{dimension}'.",
                        )
                    )
                return ValueType(kind="cube", dims=set(data.dims) - {dimension}, bands=data.bands)
            return data

        if process_id in PROCESS_DIMENSION_APPLIERS:
            data = resolve_arg(arguments.get("data"))
            dimension = arguments.get("dimension")
            if data.is_cube() and isinstance(dimension, str):
                if dimension not in data.dims:
                    errors.append(
                        Error(
                            code="InvalidDimension",
                            message=f"Node '{node_id}' applies missing dimension '{dimension}'.",
                        )
                    )
            return data

        if process_id in NUMERIC_BINARY_OPS:
            x = resolve_arg(arguments.get("x"))
            y = resolve_arg(arguments.get("y"))
            if x.is_cube() and y.is_cube():
                if x.dims != y.dims:
                    warnings.append(
                        ValidationIssue(
                            code="MismatchedDimensions",
                            message=f"Node '{node_id}' combines cubes with different dimensions.",
                            node_id=node_id,
                            severity="warning",
                        )
                    )
                return x
            if x.is_cube():
                return x
            if y.is_cube():
                return y
            return ValueType(kind="number")

        if process_id in NUMERIC_UNARY_OPS:
            data = resolve_arg(arguments.get("x") or arguments.get("data"))
            if data.is_cube():
                return data
            return ValueType(kind="number")

        if process_id == "merge_cubes":
            x = resolve_arg(arguments.get("cube1") or arguments.get("x") or arguments.get("data"))
            y = resolve_arg(arguments.get("cube2") or arguments.get("y"))
            if x.is_cube() and y.is_cube():
                if x.dims != y.dims:
                    warnings.append(
                        ValidationIssue(
                            code="MismatchedDimensions",
                            message=f"Node '{node_id}' merges cubes with different dimensions.",
                            node_id=node_id,
                            severity="warning",
                        )
                    )
                merged_bands = None
                if x.bands and y.bands:
                    merged_bands = sorted(list(dict.fromkeys(x.bands + y.bands)))
                return ValueType(kind="cube", dims=x.dims, bands=merged_bands)
            return x if x.is_cube() else y

        # Comparison processes: output is a boolean cube with same dims as input
        if process_id in {"between", "gt", "gte", "lt", "lte", "eq", "neq", "is_nan", "is_nodata", "is_valid"}:
            x = resolve_arg(arguments.get("x") or arguments.get("data"))
            if x.is_cube():
                return ValueType(kind="cube", dims=x.dims, bands=x.bands)
            return ValueType(kind="boolean")

        if process_id == "mask":
            data = resolve_arg(arguments.get("data"))
            mask = resolve_arg(arguments.get("mask"))
            # Only warn if mask has dimensions NOT present in data (true mismatch).
            # A mask from between(array_element(...)) drops 'bands' dim, which is
            # expected — the mask is broadcastable to the data cube.
            if data.is_cube() and mask.is_cube():
                extra_dims = mask.dims - data.dims
                if extra_dims:
                    warnings.append(
                        ValidationIssue(
                            code="MaskDimensionMismatch",
                            message=f"Node '{node_id}' mask has extra dimensions {sorted(extra_dims)} not in data.",
                            node_id=node_id,
                            severity="warning",
                        )
                    )
            return data

        if process_id in {"resample_spatial", "resample_cube_spatial", "resample_cube_temporal"}:
            data = resolve_arg(arguments.get("data"))
            return data

        # apply / apply_kernel / apply_neighborhood — preserve cube shape
        if process_id in {"apply", "apply_kernel", "apply_neighborhood"}:
            data = resolve_arg(arguments.get("data"))
            return data

        # aggregate_temporal_period — preserves time dim (resampled, not removed)
        if process_id == "aggregate_temporal_period":
            data = resolve_arg(arguments.get("data"))
            return data  # time dim stays but with fewer labels

        # filter_temporal / filter_bbox / filter_spatial — subset, same type
        if process_id in {"filter_temporal", "filter_bbox", "filter_spatial"}:
            data = resolve_arg(arguments.get("data"))
            return data

        # rename_labels — changes band names but preserves structure
        if process_id == "rename_labels":
            data = resolve_arg(arguments.get("data"))
            target = arguments.get("target")
            if data.is_cube() and isinstance(target, list):
                dim = arguments.get("dimension", "bands")
                if dim == "bands":
                    return ValueType(kind="cube", dims=data.dims, bands=target)
            return data

        # add_dimension — adds a new dimension
        if process_id == "add_dimension":
            data = resolve_arg(arguments.get("data"))
            dim_name = arguments.get("name")
            if data.is_cube() and isinstance(dim_name, str):
                new_dims = set(data.dims) | {dim_name}
                return ValueType(kind="cube", dims=new_dims, bands=data.bands)
            return data

        # drop_dimension — removes a dimension
        if process_id == "drop_dimension":
            data = resolve_arg(arguments.get("data"))
            dim_name = arguments.get("name")
            if data.is_cube() and isinstance(dim_name, str):
                if dim_name not in data.dims:
                    errors.append(
                        Error(
                            code="InvalidDimension",
                            message=f"Node '{node_id}' drops missing dimension '{dim_name}'.",
                        )
                    )
                return ValueType(kind="cube", dims=set(data.dims) - {dim_name}, bands=data.bands)
            return data

        # trim_cube — removes empty slices, same type
        if process_id == "trim_cube":
            data = resolve_arg(arguments.get("data"))
            return data

        # dimension_labels — returns array of labels, not a cube
        if process_id == "dimension_labels":
            return ValueType(kind="array")

        # load_stac / load_url — like load_collection
        if process_id in {"load_stac", "load_url"}:
            bands = arguments.get("bands")
            band_list = None
            if isinstance(bands, list) and all(isinstance(b, str) for b in bands):
                band_list = bands
            return ValueType(kind="cube", dims={"x", "y", "bands", "time"}, bands=band_list)

        # load_geojson — returns vector data
        if process_id == "load_geojson":
            return ValueType(kind="vector")

        # if — returns whichever branch type
        if process_id == "if":
            accept = resolve_arg(arguments.get("accept"))
            reject = resolve_arg(arguments.get("reject"))
            if accept.is_cube():
                return accept
            if reject and reject.is_cube():
                return reject
            return accept

        # Cumulative ops — preserve cube
        if process_id in {"cumsum", "cummin", "cummax", "cumproduct"}:
            data = resolve_arg(arguments.get("data"))
            return data

        # clip / linear_scale_range — preserve cube
        if process_id in {"clip", "linear_scale_range"}:
            x = resolve_arg(arguments.get("x"))
            if x.is_cube():
                return x
            return ValueType(kind="number")

        # Logic operators — preserve cube shape
        if process_id in {"and", "or", "not", "xor"}:
            x = resolve_arg(arguments.get("x"))
            if x.is_cube():
                return ValueType(kind="cube", dims=x.dims, bands=x.bands)
            return ValueType(kind="boolean")

        # mask_polygon — preserve data cube
        if process_id == "mask_polygon":
            data = resolve_arg(arguments.get("data"))
            return data

        if process_id == "save_result":
            data = resolve_arg(arguments.get("data"))
            return data

        # Default propagation: return first cube argument if present
        for arg in arguments.values():
            resolved = resolve_arg(arg)
            if resolved.is_cube():
                return resolved
        return ValueType(kind="unknown")
