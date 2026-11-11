"""Deterministic lowering from SCF to openEO Process Graph."""
import re
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from openeo_fastapi.api.types import Error
from openeo_fastapi.api.models import ValidationIssue
from openeo_ai.validation.scf.hash import hash_json


DIMENSION_MAP = {
    "time": "t",
    "temporal": "t",
    "bands": "bands",
    "x": "x",
    "y": "y",
}


class LoweringError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class NodeBuilder:
    def __init__(self):
        self.nodes: Dict[str, Dict] = {}
        self._counts: Dict[str, int] = {}

    def add(self, process_id: str, arguments: Dict, result: bool = False, node_id: Optional[str] = None) -> str:
        if not node_id:
            base = process_id.replace("-", "_")
            self._counts[base] = self._counts.get(base, 0) + 1
            node_id = f"{base}_{self._counts[base]}"
        if node_id in self.nodes:
            # LoweringError, not ValueError: every other failure in this module lands in
            # the returned errors list through the `except LoweringError` handlers, and a
            # collision is reachable from ordinary input — two spectral_index
            # transformations both want the fixed id "ndvi". As a ValueError it escaped
            # lower_scf_to_pg entirely: graph_builder_tools.py:1067 does not guard the
            # call at all, and validation_tools.py:862 caught it so broadly that the
            # structural validation already computed above was discarded and the caller
            # saw only "SCF validation failed: Node id collision: ndvi".
            raise LoweringError(
                "NodeIdCollision",
                f"Cannot build two '{process_id}' steps in one graph (node id "
                f"'{node_id}' is already used). Ask for one at a time.",
            )
        node = {"process_id": process_id, "arguments": arguments}
        if result:
            node["result"] = True
        self.nodes[node_id] = node
        return node_id


_EXPR_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _formula_tokens(formula: str) -> Set[str]:
    """Identifier tokens in a formula, for classifying which index it expresses.

    Substring tests misclassify: "red" is inside "rededge" and "nir" is inside "nir08".
    `swir` is matched by prefix on purpose, so swir16 and swir22 both count.
    """
    return set(_EXPR_IDENTIFIER.findall(formula or ""))


def _apply_band_aliases(expression: str, band_alias_map: Dict[str, str]) -> str:
    """Rename whole band names in an expression, never substrings.

    band_math used `expr.replace(common, actual)` once per alias, which corrupts any
    name that contains another as a substring, and does it differently depending on dict
    order: with {"red": "B04", "rededge": "B05"}, "rededge - red" became
    "B04edge - B04", while the same map built the other way round gave "B05 - B04".
    One pass over identifier tokens is order-independent and cannot splice a name.

    Latent today — neither caller of lower_scf_to_pg passes band_alias_map, so it is
    always empty — but it is a trap for whoever wires aliases up.
    """
    if not band_alias_map:
        return expression
    return _EXPR_IDENTIFIER.sub(
        lambda m: band_alias_map.get(m.group(0), m.group(0)), expression
    )


def _tokenize_expression(expr: str) -> List[str]:
    tokens: List[str] = []
    current = ""
    for ch in expr:
        if ch.isalnum() or ch == "_" or ch == ".":
            current += ch
        elif ch in "+-*/()":
            if current:
                tokens.append(current)
                current = ""
            tokens.append(ch)
        elif ch.isspace():
            if current:
                tokens.append(current)
                current = ""
        else:
            if current:
                tokens.append(current)
                current = ""
    if current:
        tokens.append(current)
    return tokens


def _shunting_yard(tokens: List[str]) -> List[str]:
    prec = {"+": 1, "-": 1, "*": 2, "/": 2}
    output: List[str] = []
    ops: List[str] = []
    for token in tokens:
        if token in prec:
            while ops and ops[-1] in prec and prec[ops[-1]] >= prec[token]:
                output.append(ops.pop())
            ops.append(token)
        elif token == "(":
            ops.append(token)
        elif token == ")":
            while ops and ops[-1] != "(":
                output.append(ops.pop())
            if ops and ops[-1] == "(":
                ops.pop()
        else:
            output.append(token)
    while ops:
        output.append(ops.pop())
    return output


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except Exception:
        return False


def _apply_scale_offset(
    builder: NodeBuilder,
    node_id: str,
    scale_offset: Optional[Dict[str, float]],
    capabilities: Set[str],
) -> str:
    current = node_id
    if not scale_offset:
        return current
    scale = scale_offset.get("scale")
    offset = scale_offset.get("offset")
    if scale is not None and scale != 1:
        _require(capabilities, "multiply")
        current = builder.add("multiply", {"x": {"from_node": current}, "y": scale})
    if offset is not None and offset != 0:
        _require(capabilities, "add")
        current = builder.add("add", {"x": {"from_node": current}, "y": offset})
    return current


def _get_band_node(
    label: str,
    builder: NodeBuilder,
    current_node: str,
    capabilities: Set[str],
    band_cache: Dict[str, str],
    band_scale_offset: Optional[Dict[str, Dict[str, float]]],
) -> str:
    if label in band_cache:
        return band_cache[label]
    _require(capabilities, "array_element")
    node_id = builder.add(
        "array_element",
        {"data": {"from_node": current_node}, "label": label},
        node_id=f"band_{label}",
    )
    scale_offset = (band_scale_offset or {}).get(label)
    node_id = _apply_scale_offset(builder, node_id, scale_offset, capabilities)
    band_cache[label] = node_id
    return node_id


def _build_expression(
    expression: str,
    builder: NodeBuilder,
    current_node: str,
    capabilities: Set[str],
    band_cache: Dict[str, str],
    band_scale_offset: Optional[Dict[str, Dict[str, float]]],
) -> Union[str, float]:
    tokens = _tokenize_expression(expression)
    if not tokens:
        raise LoweringError("InvalidExpression", "Expression is empty.")
    rpn = _shunting_yard(tokens)
    stack: List[Union[str, float]] = []

    for token in rpn:
        if token in {"+", "-", "*", "/"}:
            if len(stack) < 2:
                raise LoweringError("InvalidExpression", "Expression has insufficient operands.")
            rhs = stack.pop()
            lhs = stack.pop()
            op_map = {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}
            process_id = op_map[token]
            _require(capabilities, process_id)
            args: Dict[str, Union[dict, float]] = {}
            args["x"] = {"from_node": lhs} if isinstance(lhs, str) else lhs
            args["y"] = {"from_node": rhs} if isinstance(rhs, str) else rhs
            node_id = builder.add(process_id, args)
            stack.append(node_id)
        else:
            if _is_number(token):
                stack.append(float(token))
            else:
                band_node = _get_band_node(
                    token,
                    builder,
                    current_node,
                    capabilities,
                    band_cache,
                    band_scale_offset,
                )
                stack.append(band_node)

    if len(stack) != 1:
        raise LoweringError("InvalidExpression", "Expression could not be reduced.")
    return stack[0]


def build_capability_profile(process_specs: Dict[str, Dict]) -> Set[str]:
    return set(process_specs.keys())


def _require(capabilities: Set[str], process_id: str):
    if process_id not in capabilities:
        raise LoweringError("MissingCapability", f"Required process '{process_id}' not available.")


def _map_dimension(dim: str) -> str:
    return DIMENSION_MAP.get(dim, dim)


def lower_scf_to_pg(
    scf: dict,
    process_specs: Dict[str, Dict],
    capabilities: Optional[Set[str]] = None,
    band_alias_map: Optional[Dict[str, str]] = None,
    band_scale_offset: Optional[Dict[str, Dict[str, float]]] = None,
) -> Tuple[Optional[dict], List[ValidationIssue], List[Error], Optional[str]]:
    warnings: List[ValidationIssue] = []
    errors: List[Error] = []

    data = scf.get("data", {}) or {}
    policy = scf.get("policy_profile", {}) or {}
    quality = scf.get("quality_constraints", {}) or {}
    transformations = scf.get("transformations", []) or []
    output = scf.get("output", {}) or {}

    capabilities = capabilities or build_capability_profile(process_specs)

    builder = NodeBuilder()

    band_alias_map = band_alias_map or {}
    band_scale_offset = band_scale_offset or {}
    bands = list(data.get("bands") or [])
    resolved_bands = [band_alias_map.get(b, b) for b in bands]
    cloud_mask = bool(quality.get("cloud_mask"))
    cloud_algo = policy.get("cloud_mask_algorithm")

    extra_bands = []

    if cloud_mask:
        if cloud_algo != "scl":
            errors.append(Error(code="UnsupportedCloudMask", message=f"cloud_mask_algorithm '{cloud_algo}' not supported."))
        scl_band = band_alias_map.get("scl", "scl")
        if scl_band not in resolved_bands:
            extra_bands.append(scl_band)

    load_bands = resolved_bands + [b for b in extra_bands if b not in resolved_bands]

    try:
        _require(capabilities, "load_collection")
        load_id = builder.add(
            "load_collection",
            {
                "id": data.get("collection"),
                "spatial_extent": data.get("spatial_extent"),
                "temporal_extent": [
                    data.get("temporal_extent", {}).get("start"),
                    data.get("temporal_extent", {}).get("end"),
                ],
                "bands": load_bands,
            },
            node_id="load",
        )
    except LoweringError as e:
        errors.append(Error(code=e.code, message=e.message))
        return None, warnings, errors, None

    current = load_id

    if cloud_mask and not errors:
        try:
            _require(capabilities, "array_element")
            _require(capabilities, "eq")
            _require(capabilities, "mask")
            _require(capabilities, "or")

            scl_label = band_alias_map.get("scl", "scl")
            scl_id = builder.add(
                "array_element",
                {"data": {"from_node": current}, "label": scl_label},
                node_id="scl",
            )

            classes = policy.get("cloud_mask_classes") or []
            if not isinstance(classes, list) or not classes:
                raise LoweringError("MissingCloudMaskClasses", "policy_profile.cloud_mask_classes is required.")

            eq_nodes: List[str] = []
            for idx, cls in enumerate(classes):
                eq_id = builder.add(
                    "eq",
                    {"x": {"from_node": scl_id}, "y": cls},
                    node_id=f"cloud_eq_{idx+1}",
                )
                eq_nodes.append(eq_id)

            mask_id = eq_nodes[0]
            for idx in range(1, len(eq_nodes)):
                mask_id = builder.add(
                    "or",
                    {"x": {"from_node": mask_id}, "y": {"from_node": eq_nodes[idx]}},
                    node_id=f"cloud_or_{idx+1}",
                )

            masked = builder.add(
                "mask",
                {"data": {"from_node": current}, "mask": {"from_node": mask_id}},
                node_id="cloud_mask",
            )
            current = masked

            if extra_bands:
                _require(capabilities, "filter_bands")
                current = builder.add(
                    "filter_bands",
                    {"data": {"from_node": current}, "bands": resolved_bands},
                    node_id="filter_bands",
                )

        except LoweringError as e:
            errors.append(Error(code=e.code, message=e.message))

    # Optional CRS/resolution resampling
    target_crs = data.get("crs")
    target_resolution = data.get("resolution")
    if (target_crs or target_resolution) and not errors:
        try:
            process_id = None
            if "resample_spatial" in capabilities:
                process_id = "resample_spatial"
            elif "resample_cube_spatial" in capabilities:
                process_id = "resample_cube_spatial"
            else:
                raise LoweringError("MissingCapability", "resample_spatial is required for CRS/resolution changes.")

            args: Dict[str, Any] = {"data": {"from_node": current}}
            if target_resolution is not None:
                if not isinstance(target_resolution, dict) or "x" not in target_resolution or "y" not in target_resolution:
                    raise LoweringError("InvalidResolution", "data.resolution must include x and y fields.")
                args["resolution"] = target_resolution
            if target_crs:
                args["projection"] = target_crs
            method = policy.get("resampling_method")
            if method:
                args["method"] = method

            current = builder.add(process_id, args, node_id="resample_spatial")
        except LoweringError as e:
            errors.append(Error(code=e.code, message=e.message))

    band_cache: Dict[str, str] = {}
    # Set once a spectral index has consumed the cube. A second index cannot be lowered
    # correctly here: _get_band_node binds array_element to `current`, which is by then
    # the first index's output, so the second index reads its bands out of a
    # single-band index raster. Measured on ndvi followed by ndsi: band_green and
    # band_swir16 read from_node "ndvi", the first index was left dangling and unused,
    # and errors came back empty — a meaningless graph reported as a success. Refusing
    # is the conservative answer; merging two indices into one output would be inventing
    # SCF semantics and would change what the result contains.
    index_applied: Optional[str] = None

    for t in transformations:
        t_type = t.get("type")
        if t_type == "spectral_index":
            name = t.get("name")
            if index_applied is not None:
                errors.append(Error(
                    code="UnsupportedIndexChain",
                    message=(
                        f"Cannot apply spectral_index '{name}' after "
                        f"'{index_applied}': the second index would read its bands from "
                        "the first index's output. Ask for one index per request."
                    ),
                ))
                continue
            formula = t.get("formula", "") or ""
            # Whole tokens, not substrings. "red" is a substring of "rededge", so a
            # red-edge formula such as "(nir - rededge) / (nir + rededge)" satisfied
            # both tests and was built as NDVI: it extracted a `red` band the request
            # never listed, named the node "ndvi", and reported no error at all.
            tokens = _formula_tokens(formula)
            is_ndvi = name == "ndvi" or {"nir", "red"} <= tokens
            is_ndsi = name in {"ndsi", "snow_index"} or (
                "green" in tokens and any(t.startswith("swir") for t in tokens)
            )
            if not is_ndvi and not is_ndsi:
                errors.append(Error(code="UnsupportedIndex", message=f"Unsupported spectral_index '{name}'."))
                continue
            try:
                _require(capabilities, "array_element")
                if "normalized_difference" in capabilities:
                    index_process = "normalized_difference"
                elif "ndvi" in capabilities:
                    index_process = "ndvi"
                else:
                    raise LoweringError("MissingCapability", "No normalized_difference or ndvi process available.")

                if is_ndvi:
                    nir_label = band_alias_map.get("nir", "nir")
                    red_label = band_alias_map.get("red", "red")
                    nir = _get_band_node(
                        nir_label,
                        builder,
                        current,
                        capabilities,
                        band_cache,
                        band_scale_offset,
                    )
                    red = _get_band_node(
                        red_label,
                        builder,
                        current,
                        capabilities,
                        band_cache,
                        band_scale_offset,
                    )
                    current = builder.add(
                        index_process,
                        {"x": {"from_node": nir}, "y": {"from_node": red}},
                        node_id="ndvi",
                    )
                    index_applied = "ndvi"
                else:
                    green_label = band_alias_map.get("green", "green")
                    swir_label = band_alias_map.get("swir16", band_alias_map.get("swir", "swir16"))
                    green = _get_band_node(
                        green_label,
                        builder,
                        current,
                        capabilities,
                        band_cache,
                        band_scale_offset,
                    )
                    swir = _get_band_node(
                        swir_label,
                        builder,
                        current,
                        capabilities,
                        band_cache,
                        band_scale_offset,
                    )
                    current = builder.add(
                        index_process,
                        {"x": {"from_node": green}, "y": {"from_node": swir}},
                        node_id="ndsi",
                    )
                    index_applied = "ndsi"
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "temporal_reduction":
            reducer = t.get("reducer")
            dimension = _map_dimension(t.get("dimension", "time"))
            if not reducer:
                errors.append(Error(code="MissingReducer", message="temporal_reduction requires reducer."))
                continue
            try:
                _require(capabilities, "reduce_dimension")
                _require(capabilities, reducer)
                reducer_graph = {
                    "process_graph": {
                        "reducer": {
                            "process_id": reducer,
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                }
                current = builder.add(
                    "reduce_dimension",
                    {"data": {"from_node": current}, "dimension": dimension, "reducer": reducer_graph},
                    node_id="reduce",
                )
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "temporal_resample":
            reducer = t.get("reducer")
            period = t.get("period") or t.get("temporal_step")
            if not reducer:
                errors.append(Error(code="MissingReducer", message="temporal_resample requires reducer."))
                continue
            if not period:
                errors.append(Error(code="MissingPeriod", message="temporal_resample requires period/temporal_step."))
                continue
            try:
                _require(capabilities, "aggregate_temporal_period")
                _require(capabilities, reducer)
                reducer_graph = {
                    "process_graph": {
                        "reducer": {
                            "process_id": reducer,
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                }
                current = builder.add(
                    "aggregate_temporal_period",
                    {"data": {"from_node": current}, "period": period, "reducer": reducer_graph},
                    node_id="temporal_resample",
                )
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "climate_anomaly":
            try:
                _require(capabilities, "reduce_dimension")
                _require(capabilities, "mean")
                _require(capabilities, "subtract")
                reducer_graph = {
                    "process_graph": {
                        "reducer": {
                            "process_id": "mean",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                }
                baseline = builder.add(
                    "reduce_dimension",
                    {"data": {"from_node": current}, "dimension": "t", "reducer": reducer_graph},
                    node_id="baseline_mean",
                )
                current = builder.add(
                    "subtract",
                    {"x": {"from_node": current}, "y": {"from_node": baseline}},
                    node_id="climate_anomaly",
                )
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "degree_days":
            base_temperature = t.get("base_temperature", 0)
            dimension = t.get("dimension") or "t"
            band_name = t.get("band") or t.get("variable")
            try:
                _require(capabilities, "subtract")
                _require(capabilities, "gt")
                _require(capabilities, "if")
                _require(capabilities, "reduce_dimension")
                _require(capabilities, "sum")

                if not band_name:
                    bands = data.get("bands") or []
                    band_name = bands[0] if bands else None
                if not band_name:
                    raise LoweringError("MissingBand", "degree_days requires a band or variable name.")

                band_label = band_alias_map.get(band_name, band_name)
                band_node = _get_band_node(
                    band_label,
                    builder,
                    current,
                    capabilities,
                    band_cache,
                    band_scale_offset,
                )

                sub_id = builder.add(
                    "subtract",
                    {"x": {"from_node": band_node}, "y": base_temperature},
                    node_id="degree_sub",
                )
                gt_id = builder.add(
                    "gt",
                    {"x": {"from_node": sub_id}, "y": 0},
                    node_id="degree_gt",
                )
                clamp_id = builder.add(
                    "if",
                    {"value": {"from_node": gt_id}, "accept": {"from_node": sub_id}, "reject": 0},
                    node_id="degree_clamp",
                )
                reducer_graph = {
                    "process_graph": {
                        "reducer": {
                            "process_id": "sum",
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                }
                current = builder.add(
                    "reduce_dimension",
                    {"data": {"from_node": clamp_id}, "dimension": dimension, "reducer": reducer_graph},
                    node_id="degree_days",
                )
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "snow_cover_classification":
            threshold = t.get("threshold", 0.4)
            try:
                _require(capabilities, "array_element")
                _require(capabilities, "normalized_difference")
                _require(capabilities, "gt")
                _require(capabilities, "if")

                green_label = band_alias_map.get("green", "green")
                swir_label = band_alias_map.get("swir16", band_alias_map.get("swir", "swir16"))
                green = _get_band_node(
                    green_label,
                    builder,
                    current,
                    capabilities,
                    band_cache,
                    band_scale_offset,
                )
                swir = _get_band_node(
                    swir_label,
                    builder,
                    current,
                    capabilities,
                    band_cache,
                    band_scale_offset,
                )
                ndsi = builder.add(
                    "normalized_difference",
                    {"x": {"from_node": green}, "y": {"from_node": swir}},
                    node_id="ndsi",
                )
                mask = builder.add(
                    "gt",
                    {"x": {"from_node": ndsi}, "y": threshold},
                    node_id="snow_gt",
                )
                current = builder.add(
                    "if",
                    {"value": {"from_node": mask}, "accept": 1, "reject": 0},
                    node_id="snow_class",
                )
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "spatial_reduction":
            reducer = t.get("reducer")
            if not reducer:
                errors.append(Error(code="MissingReducer", message="spatial_reduction requires reducer."))
                continue
            try:
                _require(capabilities, "reduce_dimension")
                _require(capabilities, reducer)
                reducer_graph = {
                    "process_graph": {
                        "reducer": {
                            "process_id": reducer,
                            "arguments": {"data": {"from_parameter": "data"}},
                            "result": True,
                        }
                    }
                }
                # Reduce Y then X for spatial aggregation
                reduce_y = builder.add(
                    "reduce_dimension",
                    {"data": {"from_node": current}, "dimension": "y", "reducer": reducer_graph},
                    node_id="reduce_y",
                )
                current = builder.add(
                    "reduce_dimension",
                    {"data": {"from_node": reduce_y}, "dimension": "x", "reducer": reducer_graph},
                    node_id="reduce_x",
                )
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "band_math":
            expression = t.get("expression") or t.get("formula")
            if not expression:
                errors.append(Error(code="MissingExpression", message="band_math requires expression."))
                continue
            try:
                # Rename whole band names only; see _apply_band_aliases.
                expr = _apply_band_aliases(expression, band_alias_map)
                node_or_value = _build_expression(
                    expression=expr,
                    builder=builder,
                    current_node=current,
                    capabilities=capabilities,
                    band_cache=band_cache,
                    band_scale_offset=band_scale_offset,
                )
                if isinstance(node_or_value, str):
                    current = node_or_value
                else:
                    # Literal result, wrap in add with 0 to form a node
                    _require(capabilities, "add")
                    current = builder.add("add", {"x": 0, "y": node_or_value}, node_id="band_math")
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        elif t_type == "sar_backscatter":
            polarization = t.get("polarization")
            scale = t.get("scale", "linear")
            if not polarization:
                errors.append(Error(code="SARPolarizationMissing", message="sar_backscatter requires polarization."))
                continue
            try:
                sar_label = band_alias_map.get(polarization, polarization)
                current = _get_band_node(
                    sar_label,
                    builder,
                    current,
                    capabilities,
                    band_cache,
                    band_scale_offset,
                )
                if scale == "db":
                    _require(capabilities, "log10")
                    _require(capabilities, "multiply")
                    log_node = builder.add("log10", {"x": {"from_node": current}}, node_id="sar_log10")
                    current = builder.add("multiply", {"x": 10, "y": {"from_node": log_node}}, node_id="sar_db")
            except LoweringError as e:
                errors.append(Error(code=e.code, message=e.message))

        else:
            errors.append(Error(code="UnsupportedTransformation", message=f"Unsupported transformation type '{t_type}'."))

    if errors:
        return None, warnings, errors, None

    output_format = output.get("format") or policy.get("default_output_format")
    if not output_format:
        errors.append(Error(code="MissingOutputFormat", message="Missing output.format and policy default."))
        return None, warnings, errors, None

    try:
        _require(capabilities, "save_result")
        builder.add(
            "save_result",
            {"data": {"from_node": current}, "format": output_format},
            result=True,
            node_id="save",
        )
    except LoweringError as e:
        errors.append(Error(code=e.code, message=e.message))
        return None, warnings, errors, None

    pg = {"process_graph": builder.nodes}
    pg_hash = hash_json(pg)

    return pg, warnings, errors, pg_hash
