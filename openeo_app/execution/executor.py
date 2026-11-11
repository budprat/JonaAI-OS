"""Process Graph Executor for OpenEO using Dask."""

import logging
import sys
import pkgutil
import importlib
import importlib.util
import inspect
from functools import wraps
from typing import Any, Callable, Optional

import numpy as np
import xarray as xr

from openeo_pg_parser_networkx import OpenEOProcessGraph
from openeo_pg_parser_networkx.process_registry import Process

logger = logging.getLogger(__name__)

# Phase 16: Langfuse @observe for custom spans around process graph execution.
try:
    from langfuse import observe as _lf_observe  # type: ignore
except ImportError:  # pragma: no cover
    def _lf_observe(*args, **kwargs):  # type: ignore
        def _decorator(fn):
            return fn
        if args and callable(args[0]):
            return args[0]
        return _decorator

# ── NumPy 2.0 compatibility shim ──────────────────────────────────────────
# openeo-processes-dask uses np.obj2sctype and np.issubsctype which were
# removed in NumPy 2.0.  Patch them back so comparison processes (eq, gt,
# lt, …) work until the upstream library is updated.
if not hasattr(np, "obj2sctype"):
    def _obj2sctype(rep, default=None):
        """Replacement for removed np.obj2sctype."""
        try:
            return np.dtype(rep).type
        except Exception:
            return default
    np.obj2sctype = _obj2sctype
    logger.info("Patched np.obj2sctype for NumPy 2.0 compatibility")

if not hasattr(np, "issubsctype"):
    def _issubsctype(arg1, arg2):
        """Replacement for removed np.issubsctype."""
        return np.issubdtype(np.dtype(arg1), arg2)
    np.issubsctype = _issubsctype
    logger.info("Patched np.issubsctype for NumPy 2.0 compatibility")


def _strip_parser_kwargs(kwargs: dict) -> None:
    """Remove kwargs injected by openeo-pg-parser-networkx / xarray.reduce
    that process implementations do not expect."""
    kwargs.pop('named_parameters', None)
    kwargs.pop('positional_parameters', None)
    kwargs.pop('axis', None)


def _drop_positional_if_overlap(args: tuple, kwargs: dict, param_names: list) -> tuple:
    """Overlap-resolver fix: xarray's reduce() passes data as the first
    positional arg while the parser also supplies it as a kwarg, causing
    "got multiple values for argument". Drop the positional in that case."""
    if args and param_names and param_names[0] in kwargs:
        return ()
    return args


def _wrap_process(func: Callable) -> Callable:
    """
    Wrap a process function to accept and ignore extra keyword arguments
    like 'named_parameters' that openeo-pg-parser-networkx passes.

    Also handles the overlap_resolver case where xarray's reduce() passes
    data as the first positional arg AND the parser passes x/y as kwargs,
    causing 'got multiple values for argument x'.
    """
    # Cache the function's parameter names for the overlap resolver check
    try:
        _param_names = list(inspect.signature(func).parameters.keys())
    except (ValueError, TypeError):
        _param_names = []

    @wraps(func)
    def wrapper(*args, **kwargs):
        _strip_parser_kwargs(kwargs)
        # A reducer's data reaches every node in its callback, including arithmetic on
        # earlier results, which takes no data argument.
        if 'data' in kwargs and _param_names and 'data' not in _param_names and 'kwargs' not in _param_names:
            kwargs.pop('data')
        args = _drop_positional_if_overlap(args, kwargs, _param_names)

        # Debug: log process execution
        arg_types = [type(a).__name__ for a in args]
        kwarg_types = {k: type(v).__name__ for k, v in kwargs.items()}
        logger.debug(f"EXEC {func.__name__}(args={arg_types}, kwargs={kwarg_types})")
        result = func(*args, **kwargs)
        if hasattr(result, 'shape'):
            logger.debug(f"  -> {type(result).__name__} shape={result.shape}")
        else:
            logger.debug(f"  -> {type(result).__name__}")
        return result
    return wrapper


# ── xr.DataArray type preservation shim ───────────────────────────────────
# openeo-processes-dask comparison/logic functions (eq, or, and, …) use
# raw numpy ops (np.where, np.nan_to_num, np.logical_or) that strip
# xr.DataArray metadata, returning plain numpy.ndarray.  Downstream
# processes like mask() then fail with:
#   AttributeError: 'numpy.ndarray' object has no attribute 'openeo'
# This wrapper re-wraps the result as xr.DataArray when inputs were DataArrays.

# Processes whose numpy internals strip xr.DataArray metadata
TYPE_PRESERVING_PROCESSES = {
    # Comparison processes
    "eq", "neq", "gt", "gte", "lt", "lte", "between",
    "is_infinite", "is_valid", "is_nan", "is_nodata",
    # Logic processes
    "_and", "_or", "xor", "_not", "_if", "_any", "_all",
}


def _wrap_type_preserving(func: Callable) -> Callable:
    """
    Wrap a process function to preserve xr.DataArray type through numpy ops.

    When openeo-processes-dask comparison/logic functions receive xr.DataArray
    inputs, their internal numpy operations (np.where, np.nan_to_num, etc.)
    can strip xarray metadata, returning plain numpy.ndarray.  This wrapper
    detects that case and re-wraps the result as an xr.DataArray with the
    original dimensions and coordinates preserved.
    """
    # Cache parameter names for overlap resolver check
    try:
        _param_names = list(inspect.signature(func).parameters.keys())
    except (ValueError, TypeError):
        _param_names = []

    @wraps(func)
    def wrapper(*args, **kwargs):
        _strip_parser_kwargs(kwargs)
        args = _drop_positional_if_overlap(args, kwargs, _param_names)

        # Debug: log process execution
        arg_types = [type(a).__name__ for a in args]
        kwarg_types = {k: type(v).__name__ for k, v in kwargs.items()}
        logger.debug(f"EXEC-TP {func.__name__}(args={arg_types}, kwargs={kwarg_types})")

        # Find reference DataArray from args/kwargs
        reference_da = None
        for arg in args:
            if isinstance(arg, xr.DataArray):
                reference_da = arg
                break
        if reference_da is None:
            for v in kwargs.values():
                if isinstance(v, xr.DataArray):
                    reference_da = v
                    break

        result = func(*args, **kwargs)

        # Re-wrap numpy result back to DataArray if input was DataArray
        if reference_da is not None and isinstance(result, np.ndarray):
            if result.shape == reference_da.shape:
                # Exact match — reuse dims and coords from reference
                result = xr.DataArray(
                    data=result,
                    dims=reference_da.dims,
                    coords=reference_da.coords,
                )
                logger.info(
                    f"Type-preserved {func.__name__}: numpy.ndarray → xr.DataArray "
                    f"shape={result.shape}"
                )
            elif result.ndim <= reference_da.ndim:
                # Shape changed (e.g. aggregation reduced a dim) — reconstruct
                # with matching trailing dims from reference
                ref_dims = reference_da.dims
                ref_coords = reference_da.coords
                # Use the last N dims from reference that match result shape
                new_dims = ref_dims[-result.ndim:] if result.ndim > 0 else ()
                new_coords = {
                    d: ref_coords[d] for d in new_dims
                    if d in ref_coords and ref_coords[d].shape == (result.shape[new_dims.index(d)],)
                } if result.ndim > 0 else {}
                result = xr.DataArray(data=result, dims=new_dims, coords=new_coords)
                logger.info(
                    f"Type-preserved {func.__name__} (shape changed): "
                    f"numpy.ndarray → xr.DataArray shape={result.shape} dims={new_dims}"
                )
            else:
                logger.warning(
                    f"Type-preserving SKIPPED for {func.__name__}: "
                    f"result.ndim={result.ndim} > ref.ndim={reference_da.ndim}"
                )

        if hasattr(result, 'shape'):
            logger.debug(f"  -> {type(result).__name__} shape={result.shape}")
        else:
            logger.debug(f"  -> {type(result).__name__}")

        return result
    return wrapper


# Processes that need special handling or custom implementations
CUSTOM_PROCESS_OVERRIDES = {
    "load_collection",  # Our custom implementation with STAC mapping
}

# Modules to skip when discovering processes (problematic dependencies)
SKIP_MODULES = {
    "experimental",
    "ml",
}

# Reserved word functions that start with _ but should be included
RESERVED_WORD_FUNCTIONS = {
    "_and", "_or", "_not", "_if", "_int", "_round", "_min", "_max", "_sum", "_all", "_any"
}


def _discover_processes_from_package():
    """
    Dynamically discover all process implementations from openeo_processes_dask.

    Returns a dict mapping process_id to implementation function.
    """
    process_map = {}

    try:
        import openeo_processes_dask
        pkg_path = openeo_processes_dask.__path__
        impl_spec = importlib.util.find_spec("openeo_processes_dask.process_implementations")
        if impl_spec is None or impl_spec.submodule_search_locations is None:
            logger.error("Could not find openeo_processes_dask.process_implementations package")
            return process_map
        impl_path = list(impl_spec.submodule_search_locations)
    except ImportError as e:
        logger.error(f"Could not import openeo_processes_dask: {e}")
        return process_map

    for importer, modname, ispkg in pkgutil.walk_packages(
        impl_path,
        prefix='openeo_processes_dask.process_implementations.'
    ):
        if any(skip in modname for skip in SKIP_MODULES):
            continue

        try:
            module = importlib.import_module(modname)

            for name in dir(module):
                if name.startswith('_') and name not in RESERVED_WORD_FUNCTIONS:
                    continue

                obj = getattr(module, name)

                if not callable(obj) or inspect.isclass(obj):
                    continue

                if not hasattr(obj, '__module__'):
                    continue
                if 'process_implementations' not in str(obj.__module__):
                    continue

                if name.startswith('get_') or name.startswith('update_'):
                    continue

                if name not in process_map:
                    if name in TYPE_PRESERVING_PROCESSES:
                        process_map[name] = _wrap_type_preserving(obj)
                    else:
                        process_map[name] = _wrap_process(obj)

        except Exception as e:
            logger.debug(f"Could not import module {modname}: {e}")

    return process_map


_UNSAFE_PROCESSES = ("load_url", "load_stac", "run_udf", "run_udf_externally", "load_uploaded_files")


def _import_processes():
    """
    Import all process implementations from openeo_processes_dask.

    Uses dynamic discovery to load all available processes, then applies
    custom overrides for processes that need special handling.

    Returns a dict mapping process_id to implementation function.
    """
    # Start with dynamically discovered processes
    process_map = _discover_processes_from_package()

    logger.info(f"Discovered {len(process_map)} processes from openeo_processes_dask")

    # Apply custom overrides

    # 1. Our custom load_collection with STAC collection mapping
    try:
        from openeo_app.processes.load_collection import load_collection
        process_map["load_collection"] = load_collection
        logger.debug("Using custom load_collection implementation")
    except ImportError as e:
        logger.warning(f"Could not import custom load_collection: {e}")

    # 1b. Our fixed reduce_dimension that doesn't have the double-argument bug
    try:
        from openeo_app.processes.reduce_dimension_fix import (
            reduce_dimension,
            aggregate_temporal,
        )
        process_map["reduce_dimension"] = reduce_dimension
        process_map["aggregate_temporal"] = aggregate_temporal
        logger.debug("Using fixed reduce_dimension implementation")
    except ImportError as e:
        logger.warning(f"Could not import fixed reduce_dimension: {e}")

    # 1c. Override aggregate_temporal_period if the upstream version has
    # ParameterReference resolution bugs (TypeError: 'ParameterReference' / 'int').
    # Use our reduce_dimension_fix version which handles callbacks correctly.
    try:
        from openeo_app.processes.reduce_dimension_fix import aggregate_temporal_period
        process_map["aggregate_temporal_period"] = aggregate_temporal_period
        logger.debug("Using fixed aggregate_temporal_period implementation")
    except ImportError:
        # Our fix module may not have it yet — check if upstream has it
        if "aggregate_temporal_period" not in process_map:
            logger.warning("aggregate_temporal_period not available in any source")

    # 1d. Override merge_cubes to fix ParameterReference bug in overlap_resolver.
    # Upstream merge_cubes passes unresolved ParameterReference objects to the
    # overlap_resolver callback (e.g. subtract), causing TypeError.
    try:
        from openeo_app.processes.reduce_dimension_fix import merge_cubes
        process_map["merge_cubes"] = merge_cubes
        logger.debug("Using fixed merge_cubes implementation")
    except ImportError as e:
        logger.warning(f"Could not import fixed merge_cubes: {e}")

    # 1e. Override spatial resampling to handle latitude/longitude versus y/x names.
    if "resample_cube_spatial" in process_map and "resample_spatial" in process_map:
        from openeo_app.processes.resample_fix import fixed_resampling
        process_map["resample_cube_spatial"], process_map["resample_spatial"] = fixed_resampling(
            process_map["resample_cube_spatial"], process_map["resample_spatial"]
        )

    # 1f. Override aggregate_spatial: upstream's xvec call fails on graph reducers.
    # load_geojson is overridden too: upstream's fails on a bare Polygon.
    from openeo_app.processes.aggregate_fix import aggregate_spatial, load_geojson
    process_map["aggregate_spatial"] = aggregate_spatial
    process_map["load_geojson"] = load_geojson

    # 2. Ensure save_result has a fallback
    if "save_result" not in process_map:
        def save_result(data, format="GTiff", options=None, **kwargs):
            """Basic save_result that just returns the data."""
            kwargs.pop('named_parameters', None)
            return data
        process_map["save_result"] = save_result
        logger.debug("Using basic save_result fallback")

    # 3. Ensure basic math operations have fallbacks (with **kwargs to accept named_parameters)
    # Import stable math operations from our math_fixes module
    try:
        from openeo_app.processes.math_fixes import (
            divide_safe,
            normalized_difference_stable,
        )
        # Use stable implementations with type guards for unresolved references
        def _guard(x, y, op_name):
            """Guard against dict/ParameterReference operands from unresolved callbacks."""
            if isinstance(x, dict) or isinstance(y, dict):
                msg = (
                    f"[MATH TYPE ERROR] Cannot apply '{op_name}' to unresolved operands "
                    f"(x={type(x).__name__}, y={type(y).__name__}). "
                    f"This usually means a process graph callback was not properly resolved."
                )
                logger.error(msg)
                raise TypeError(msg)
        def _add(x, y, **kwargs): _guard(x, y, "add"); return x + y
        def _subtract(x, y, **kwargs): _guard(x, y, "subtract"); return x - y
        def _multiply(x, y, **kwargs): _guard(x, y, "multiply"); return x * y
        def _divide(x, y, **kwargs): return divide_safe(x, y)  # Safe division!

        basic_math = {
            "add": _add,
            "subtract": _subtract,
            "multiply": _multiply,
            "divide": _divide,
            "normalized_difference": normalized_difference_stable,  # Stable NDVI
        }
    except ImportError:
        logger.warning("Could not import math_fixes, using basic fallbacks")
        def _add(x, y, **kwargs): return x + y
        def _subtract(x, y, **kwargs): return x - y
        def _multiply(x, y, **kwargs): return x * y
        def _divide(x, y, **kwargs):
            # Basic safe division fallback
            import numpy as np
            if hasattr(y, "where"):
                return x / y.where(y != 0, 1e-10)
            else:
                return x / np.where(y != 0, y, 1e-10)

        basic_math = {
            "add": _add,
            "subtract": _subtract,
            "multiply": _multiply,
            "divide": _divide,
        }

    # Always override normalized_difference with the stable version (epsilon + clip).
    # The upstream openeo_processes_dask version lacks div-by-zero protection,
    # causing NaN/inf in dark pixels (water, shadows).
    for name, impl in basic_math.items():
        if name == "normalized_difference":
            process_map[name] = impl
            logger.info(f"Force-overriding {name} with stable version")
        elif name not in process_map:
            process_map[name] = impl
            logger.debug(f"Using basic {name} fallback")

    # 4. Force-override array_element. When openeo_processes_dask's import
    # graph is already warm, process discovery can surface an upstream
    # array_element that requires openeo's internal `labels` coordinate
    # convention and raises KeyError on plain band dimensions. Registering
    # ours conditionally made registry contents depend on import order;
    # our graphs rely on dim/label/common_name selection, so always override
    # (same policy as normalized_difference above).
    def array_element(data, index=None, label=None, dimension=None, **kwargs):
        """Select an element from a dimension by index or label.

        Supports xarray DataArray: selects along the given dimension
        by label (band name) or positional index.
        """
        import xarray as xr
        from openeo_app.core.exceptions import UserFacingError
        kwargs.pop('named_parameters', None)

        if isinstance(data, xr.DataArray):
            dim = dimension or "bands"
            if label is not None:
                # Select by label (e.g., band name "nir")
                if dim in data.dims:
                    return data.sel({dim: label})
                # Try common_name coordinate
                if "common_name" in data.coords:
                    match = data.where(data.common_name == label, drop=True)
                    if match.sizes.get(dim, 0) > 0:
                        return match.isel({dim: 0})
                raise UserFacingError(f"Band '{label}' isn't in this data (dimension '{dim}').")
            elif index is not None:
                if dim in data.dims:
                    return data.isel({dim: index})
                return data
            return data
        elif isinstance(data, (list, tuple)):
            if index is not None:
                return data[index]
            return data

        # A reducer over a dimension passes a raw array, with its axis and labels.
        axis = kwargs.get('axis')
        if label is not None:
            labels = list(kwargs.get('dim_labels') if kwargs.get('dim_labels') is not None else [])
            if label not in labels:
                raise UserFacingError(f"Band '{label}' isn't in this data: {labels}")
            index = labels.index(label)
        if axis is None:
            # The reducer's probe calls land here. Failing keeps a probe's result out of
            # the graph's cache, where the real call would find it instead of a band.
            raise ValueError("array_element needs an axis to select from a raw array")
        return data if index is None else np.take(data, index, axis=axis)

    process_map["array_element"] = array_element
    logger.debug("Added array_element implementation")

    # 4b. A graph is untrusted input. These fetch arbitrary URLs or run arbitrary code, and
    # nothing here needs them: data comes through load_collection and its catalog list.
    for unsafe in _UNSAFE_PROCESSES:
        process_map.pop(unsafe, None)

    # 5. Handle Python reserved word conflicts (and, or, not, if, etc.)
    # These are exported with LEADING underscores in openeo_processes_dask (_min, _max, etc.)
    reserved_word_map = {
        "and": "_and",
        "or": "_or",
        "not": "_not",
        "if": "_if",
        "int": "_int",
        "round": "_round",
        "min": "_min",
        "max": "_max",
        "sum": "_sum",
        "all": "_all",
        "any": "_any",
    }

    for openeo_name, python_name in reserved_word_map.items():
        if python_name in process_map:
            # Add the user-facing name
            process_map[openeo_name] = process_map[python_name]
            # Remove the internal name (users should use 'min' not '_min')
            del process_map[python_name]
            logger.debug(f"Mapped {python_name} -> {openeo_name}")

    return process_map


class ProcessGraphExecutor:
    """
    Execute OpenEO process graphs using Dask.

    This executor:
    1. Parses process graphs into NetworkX DAGs
    2. Maps process IDs to implementation functions
    3. Executes processes using the graph's to_callable() method
    4. Handles Dask lazy computation

    The execution leverages openeo-pg-parser-networkx for graph parsing
    and openeo-processes-dask for process implementations.
    """

    def __init__(
        self,
        stac_api_url: str,
    ):
        """
        Initialize the ProcessGraphExecutor.

        Args:
            stac_api_url: URL of the STAC API for data loading
        """
        self.stac_api_url = stac_api_url

        # Use singleton process registry for performance
        # (saves 400-500ms per job by avoiding repeated process discovery)
        from openeo_app.execution.process_registry_singleton import ProcessRegistrySingleton
        self._process_registry = ProcessRegistrySingleton.get_registry()

        logger.info(
            f"ProcessGraphExecutor initialized with {len(self._process_registry)} processes"
        )

    @_lf_observe(name="openeo.execute_process_graph")
    def execute(
        self,
        process_graph: dict,
        parameters: Optional[dict] = None,
        timeout_seconds: int = 300,
    ) -> Any:
        """
        Execute a process graph and return results.

        Phase 16: wrapped in @observe so Langfuse captures the full graph
        execution as a child span under the Claude SDK tool call that
        triggered it. Exposes process node count and execution duration.

        Args:
            process_graph: OpenEO process graph dict
            parameters: Optional parameters for the graph
            timeout_seconds: Maximum execution time in seconds (default 300s / 5min)

        Returns:
            Computation result (xarray.DataArray or other type depending on output)

        Raises:
            TimeoutError: If execution exceeds timeout_seconds
        """
        import signal

        def _timeout_handler(signum, frame):
            raise TimeoutError(
                f"Process graph execution timed out after {timeout_seconds}s. "
                f"Try reducing spatial extent or temporal range."
            )

        # Set alarm-based timeout (Unix only; no-op on Windows)
        prev_handler = None
        try:
            prev_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(timeout_seconds)
        except (ValueError, OSError, AttributeError):
            pass  # Not on main thread or Windows — skip timeout

        logger.info("Starting process graph execution")

        if parameters is None:
            parameters = {}

        # Handle wrapped process graph format
        if "process_graph" in process_graph:
            pg_data = process_graph["process_graph"]
        else:
            pg_data = process_graph

        logger.info(f"Process graph nodes: {list(pg_data.keys())}")
        # Dump each node's process_id and arguments for debugging
        for node_id, node_data in pg_data.items():
            proc_id = node_data.get("process_id", "unknown")
            args_summary = {}
            for k, v in node_data.get("arguments", {}).items():
                if isinstance(v, dict) and "from_node" in v:
                    args_summary[k] = f"<from_node:{v['from_node']}>"
                elif isinstance(v, dict) and "process_graph" in v:
                    args_summary[k] = "<callback>"
                else:
                    args_summary[k] = v
            logger.info(f"  Node '{node_id}': {proc_id}({args_summary})")

        try:
            # Parse process graph into NetworkX DAG
            pg = OpenEOProcessGraph(pg_data=pg_data)

            logger.info(f"Parsed graph with {len(pg.nodes)} nodes")
            logger.info(f"Required processes: {pg.required_processes}")

            # Verify all required processes are available
            missing = pg.required_processes - set(self._process_registry.keys())
            if missing:
                from difflib import get_close_matches
                avail = list(self._process_registry.keys())
                hints = {p: get_close_matches(p, avail, n=3, cutoff=0.5) for p in missing}
                hint_str = "; ".join(f"'{p}' (did you mean {s}?)" if s else f"'{p}'" for p, s in hints.items())
                raise ValueError(f"Missing process implementations: {hint_str}")

            # Use the graph's to_callable method for execution
            # This handles all the dependency resolution and callback handling
            callable_graph = pg.to_callable(
                process_registry=self._process_registry,
                parameters=parameters,
            )

            # Execute the callable to get the result
            result = callable_graph()

            logger.info("Process graph execution completed")

            # Trigger Dask computation if result is lazy
            if hasattr(result, "compute"):
                logger.info("Triggering Dask computation...")
                result = result.compute()
                logger.info("Dask computation completed")

            return result

        except Exception as e:
            # Log at ERROR with full traceback for grep-ability
            logger.error(
                f"[EXECUTION FAILED] Process graph execution error: "
                f"{type(e).__name__}: {e}",
                exc_info=True,
            )
            # Also print to stderr for immediate visibility in log tail
            import traceback as _tb
            import sys as _sys
            print(
                f"[EXECUTION FAILED] {type(e).__name__}: {e}\n"
                f"{''.join(_tb.format_exception(e))}",
                file=_sys.stderr,
            )
            raise
        finally:
            # Cancel alarm timeout
            try:
                signal.alarm(0)
                if prev_handler is not None:
                    signal.signal(signal.SIGALRM, prev_handler)
            except (ValueError, OSError, AttributeError):
                pass

    def get_available_processes(self) -> list:
        """Get list of available process IDs."""
        return list(self._process_registry.keys())

    def has_process(self, process_id: str) -> bool:
        """Check if a process is available."""
        return process_id in self._process_registry

    def register_process(self, process_id: str, implementation: Callable):
        """
        Register a custom process implementation.

        Args:
            process_id: The process identifier
            implementation: The implementation function
        """
        self._process_registry[process_id] = Process(
            spec={"id": process_id},
            implementation=implementation,
        )
        logger.info(f"Registered custom process: {process_id}")
