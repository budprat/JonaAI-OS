"""Fixed reduce_dimension implementation.

The upstream openeo-processes-dask has a bug where the @process decorator
passes both positional args AND named parameters with the same data when
executing reducers, causing "got multiple values for argument" errors.

This module provides a fixed implementation that properly handles reducer
execution without the double-passing bug.
"""

import logging
from typing import Callable, Optional

import numpy as np
import xarray as xr

from ..core.job_notes import add_job_note

logger = logging.getLogger(__name__)

# OpenEO dimension name aliases — odc.stac uses 'time'/'latitude'/'longitude'
# while OpenEO process graphs may use 't'/'y'/'x'/'bands'
DIMENSION_ALIASES = {
    "t": ["time", "temporal"],
    "time": ["t", "temporal"],
    "x": ["longitude", "lon"],
    "longitude": ["x", "lon"],
    "lon": ["x", "longitude"],
    "y": ["latitude", "lat"],
    "latitude": ["y", "lat"],
    "lat": ["y", "latitude"],
    "bands": ["band"],
    "band": ["bands"],
}


def resolve_dimension(dimension: str, data: xr.DataArray) -> str:
    """Resolve a dimension name using aliases if not found in data.

    Args:
        dimension: Requested dimension name (e.g., 't', 'x', 'bands')
        data: DataArray to check available dimensions

    Returns:
        Resolved dimension name that exists in data.dims

    Raises:
        ValueError: If dimension and none of its aliases exist in data
    """
    if dimension in data.dims:
        return dimension

    for alias in DIMENSION_ALIASES.get(dimension, []):
        if alias in data.dims:
            logger.info(
                f"Dimension alias: '{dimension}' → '{alias}' "
                f"(available: {list(data.dims)})"
            )
            return alias

    available = list(data.dims)
    raise ValueError(
        f"Dimension '{dimension}' not found in data. "
        f"Available dimensions: {available}"
    )


def reduce_dimension(
    data: xr.DataArray,
    reducer: Callable,
    dimension: str,
    context: Optional[dict] = None,
    **kwargs,
) -> xr.DataArray:
    """
    Reduce a dimension of a datacube using a reducer function.

    This is a fixed implementation that properly handles reducer callbacks
    without the double argument passing bug in upstream openeo-processes-dask.

    Args:
        data: Input datacube (xarray DataArray)
        reducer: Reducer function or callable (e.g., mean, min, max)
        dimension: Name of dimension to reduce
        context: Optional context dict
        **kwargs: Additional arguments (ignored, accepts named_parameters etc.)

    Returns:
        Reduced datacube with the specified dimension removed
    """
    # Remove extra kwargs that we don't need
    kwargs.pop('named_parameters', None)

    logger.debug(f"reduce_dimension: reducing '{dimension}' with {reducer}")

    dimension = resolve_dimension(dimension, data)

    # Strategy 1: Fast path - try to resolve as simple aggregation
    reduce_func = _get_reduce_function(reducer)

    if reduce_func is not None:
        logger.debug(f"Using fast path reduce function: {reduce_func.__name__ if hasattr(reduce_func, '__name__') else reduce_func}")
        try:
            result = data.reduce(
                _safe_reduce_wrapper(reduce_func),
                dim=dimension,
                keep_attrs=True,
            )
            logger.debug(f"Reduction complete, result shape: {result.shape}")
            return result
        except Exception as e:
            logger.error(f"Fast path reduce_dimension failed: {e}")
            raise

    # Strategy 2: Callback path - try executing the reducer as a callback
    if callable(reducer):
        logger.info(f"Trying callback execution path for reducer: {reducer}")
        try:
            result = _execute_callback_reducer(data, reducer, dimension)
            logger.debug(f"Callback reduction complete, result shape: {result.shape}")
            return result
        except ValueError:
            raise  # Re-raise ValueError with informative message
        except Exception as e:
            logger.error(f"Callback reducer execution failed: {e}")
            raise ValueError(
                f"Could not execute reducer callback: {e}. "
                f"Use a simple aggregation (mean, median, min, max) or check your reducer definition."
            )

    # Strategy 3: Error - no valid reducer
    raise ValueError(
        f"Could not resolve reducer: {reducer}. "
        f"Expected a callable, numpy function, or string name (mean, median, min, max, sum, std, var, count, first, last)."
    )


def _get_reduce_function(reducer) -> Optional[Callable]:
    """
    Extract the actual reduce function from a reducer specification.

    The reducer can be:
    1. A direct function (e.g., np.nanmean)
    2. A callable from openeo-processes-dask
    3. A string name like 'mean', 'max', 'min'

    Returns:
        The actual numpy/xarray reduce function to use
    """
    # If it's already a known numpy function, use it
    if reducer is np.nanmean or reducer is np.mean:
        return np.nanmean
    if reducer is np.nanmin or reducer is np.min:
        return np.nanmin
    if reducer is np.nanmax or reducer is np.max:
        return np.nanmax
    if reducer is np.nansum or reducer is np.sum:
        return np.nansum
    if reducer is np.nanmedian or (hasattr(np, 'median') and reducer is np.median):
        return np.nanmedian
    if reducer is np.nanstd or (hasattr(np, 'std') and reducer is np.std):
        return np.nanstd

    # If it's a string, map to numpy function
    if isinstance(reducer, str):
        reduce_map = {
            'mean': np.nanmean,
            'min': np.nanmin,
            'max': np.nanmax,
            'sum': np.nansum,
            'median': np.nanmedian,
            'std': np.nanstd,
            'var': np.nanvar,
            'count': _count_valid,
            'first': _first,
            'last': _last,
        }
        return reduce_map.get(reducer.lower())

    # If it's a callable, check if it's a wrapped process function
    if callable(reducer):
        import functools

        # Log what we're dealing with
        func_name = getattr(reducer, '__name__', str(reducer)).lower()
        func_repr = repr(reducer)
        logger.info(f"Reducer callable: name={func_name}, repr={func_repr[:200]}")

        # Map exact function names to numpy aggregators (avoid substring false positives)
        # e.g., "summarize_minimum" won't accidentally match "min"
        import re
        _name_map = {
            'mean': np.nanmean, 'nanmean': np.nanmean,
            'min': np.nanmin, 'nanmin': np.nanmin,
            'max': np.nanmax, 'nanmax': np.nanmax,
            'sum': np.nansum, 'nansum': np.nansum,
            'median': np.nanmedian, 'nanmedian': np.nanmedian,
            'std': np.nanstd, 'nanstd': np.nanstd,
            'first': _first, 'last': _last,
        }

        # Exact name match first
        if func_name in _name_map:
            logger.info(f"Exact name match '{func_name}', using {_name_map[func_name].__name__}")
            return _name_map[func_name]

        # Token-based match: split on non-alphanumeric chars (handles _ and .)
        # e.g., "reduce_mean" → ["reduce", "mean"] — matches "mean"
        # e.g., "summarize_minimum_values" → ["summarize", "minimum", "values"] — no match for "min"
        func_tokens = set(re.split(r'[^a-z0-9]+', func_name))
        for keyword, func in _name_map.items():
            if keyword in func_tokens:
                logger.info(f"Token match '{keyword}' in '{func_name}'")
                return func

        # Token-based match in repr (for process graph callables)
        repr_lower = func_repr.lower()
        repr_tokens = set(re.split(r'[^a-z0-9]+', repr_lower))
        for keyword, func in _name_map.items():
            if keyword in repr_tokens:
                logger.info(f"Token match '{keyword}' in repr")
                return func

        # If it's a functools.partial (from process graph callback), try to test it
        # with a small array to detect the operation
        if isinstance(reducer, functools.partial):
            try:
                # Skewed probe so mean != median and all targets are distinct:
                # mean=6, median=3, min=1, max=20, sum=30. The prior [1,2,3,4,5]
                # probe collided mean==median==3.0, misreading a median reducer
                # as nanmean.
                test_arr = np.array([1.0, 2.0, 3.0, 4.0, 20.0])
                # Try calling with data= kwarg (OpenEO style)
                test_result = reducer(data=test_arr)
                # Check what operation was performed
                if np.isclose(test_result, 6.0):  # mean of [1,2,3,4,20]
                    logger.info("Callback produces mean, using np.nanmean")
                    return np.nanmean
                elif np.isclose(test_result, 3.0):  # median of [1,2,3,4,20]
                    logger.info("Callback produces median, using np.nanmedian")
                    return np.nanmedian
                elif np.isclose(test_result, 1.0):  # min
                    logger.info("Callback produces min, using np.nanmin")
                    return np.nanmin
                elif np.isclose(test_result, 20.0):  # max
                    logger.info("Callback produces max, using np.nanmax")
                    return np.nanmax
                elif np.isclose(test_result, 30.0):  # sum
                    logger.info("Callback produces sum, using np.nansum")
                    return np.nansum
                else:
                    logger.warning(
                        f"Callback probe result={test_result} does not match any known reducer. "
                        f"Falling back to callback execution path (no silent assumptions)."
                    )
                    return None
            except Exception as e:
                logger.debug(f"Callback test failed: {e}")

        # No silent fallback - return None so caller can try callback execution
        logger.info(f"Could not identify reducer as simple aggregation, returning None for callback execution")
        return None

    return None


def _execute_callback_reducer(
    data: xr.DataArray,
    callback: Callable,
    dimension: str,
) -> xr.DataArray:
    """
    Execute a complex callback reducer by iterating over dimension slices.

    This handles functools.partial and other callable reducers that can't
    be mapped to simple numpy aggregations.

    Args:
        data: Input datacube
        callback: Callable reducer (e.g., functools.partial from process graph)
        dimension: Dimension to reduce over

    Returns:
        Reduced DataArray with the dimension removed

    Raises:
        ValueError: If callback execution fails
    """
    logger.info(f"Attempting callback reducer execution along '{dimension}'")

    try:
        # Collect results for each slice along the non-reduced dimensions
        # We reduce by calling the callback with data slices along the target dimension
        coords = data.coords[dimension].values

        # Try calling the callback with the full data along the dimension
        # OpenEO convention: callback receives data= parameter
        try:
            result = callback(data=data)
            if isinstance(result, xr.DataArray):
                # If the callback already reduced the dimension, return it
                if dimension not in result.dims:
                    logger.info("Callback successfully reduced dimension")
                    return result
        except Exception as e:
            logger.debug(f"Direct callback call failed: {e}, trying slice-based approach")

        # Slice-based approach: apply callback to each pixel's time series
        # Stack all non-target dimensions, iterate, unstack
        other_dims = [d for d in data.dims if d != dimension]

        if not other_dims:
            # Only one dimension - just call callback directly
            try:
                result_val = callback(data=data.values)
                return xr.DataArray(result_val, attrs=data.attrs)
            except Exception:
                result_val = callback(data)
                return xr.DataArray(result_val, attrs=data.attrs)

        # Use xarray's apply_ufunc for vectorized callback execution
        def _apply_along_dim(arr):
            """Apply callback along the reduction axis."""
            try:
                return callback(data=arr)
            except TypeError:
                return callback(arr)

        result = xr.apply_ufunc(
            _apply_along_dim,
            data,
            input_core_dims=[[dimension]],
            vectorize=True,
            dask='parallelized',
            output_dtypes=[data.dtype],
        )

        logger.info(f"Callback reducer execution successful, result shape: {result.shape}")
        return result

    except Exception as e:
        raise ValueError(
            f"Callback reducer execution failed: {e}. "
            f"The reducer callback could not be executed. "
            f"Consider using a simple aggregation (mean, median, min, max, sum) instead."
        )


def _safe_reduce_wrapper(func: Callable) -> Callable:
    """
    Wrap a reduce function to handle extra kwargs from xarray.reduce().

    xarray.reduce() passes extra kwargs like keepdims, axis, etc.
    This wrapper ensures only the expected arguments are passed.
    """
    def wrapper(values, axis=None, keepdims=False, **kwargs):
        # Filter out extra kwargs that numpy functions don't expect
        # These come from xarray's reduce internals
        kwargs.pop('positional_parameters', None)
        kwargs.pop('named_parameters', None)
        kwargs.pop('context', None)
        kwargs.pop('dim_labels', None)
        kwargs.pop('keep_attrs', None)

        try:
            # Try calling with axis and keepdims (numpy style)
            return func(values, axis=axis, keepdims=keepdims)
        except TypeError:
            try:
                # Try with just axis
                return func(values, axis=axis)
            except TypeError:
                # Fall back to just values
                return func(values)

    return wrapper


def _safe_nan_mask(values):
    """Get NaN mask that works for both float and integer dtypes.

    np.isnan() raises TypeError on integer arrays. Integer arrays have no NaN
    values, so return an all-False mask for them.
    """
    try:
        return np.isnan(values)
    except (TypeError, ValueError):
        return np.zeros(values.shape, dtype=bool)


def _count_valid(values, axis=None, keepdims=False):
    """Count non-NaN values along axis."""
    return np.sum(~_safe_nan_mask(values), axis=axis, keepdims=keepdims)


def _first(values, axis=None, keepdims=False):
    """Get first non-NaN value along axis."""
    if axis is None:
        flat = values.flatten()
        valid = flat[~_safe_nan_mask(flat)]
        result = valid[0] if len(valid) > 0 else np.nan
        return np.array(result).reshape((1,) * values.ndim) if keepdims else result

    # NaN-aware first along axis: find the first non-NaN value
    # by iterating slices along the axis
    result = np.take(values, 0, axis=axis).copy()
    result[:] = np.nan
    n = values.shape[axis]
    found = np.zeros(result.shape, dtype=bool)
    for i in range(n):
        sl = np.take(values, i, axis=axis)
        valid = ~_safe_nan_mask(sl) & ~found
        result[valid] = sl[valid]
        found |= valid
        if found.all():
            break
    if keepdims:
        result = np.expand_dims(result, axis=axis)
    return result


def _last(values, axis=None, keepdims=False):
    """Get last non-NaN value along axis."""
    if axis is None:
        flat = values.flatten()
        valid = flat[~_safe_nan_mask(flat)]
        result = valid[-1] if len(valid) > 0 else np.nan
        return np.array(result).reshape((1,) * values.ndim) if keepdims else result

    # NaN-aware last along axis: iterate in reverse
    result = np.take(values, 0, axis=axis).copy()
    result[:] = np.nan
    n = values.shape[axis]
    found = np.zeros(result.shape, dtype=bool)
    for i in range(n - 1, -1, -1):
        sl = np.take(values, i, axis=axis)
        valid = ~_safe_nan_mask(sl) & ~found
        result[valid] = sl[valid]
        found |= valid
        if found.all():
            break
    if keepdims:
        result = np.expand_dims(result, axis=axis)
    return result


# Also provide fixed aggregate_temporal that uses our reduce
def aggregate_temporal(
    data: xr.DataArray,
    intervals: list,
    reducer: Callable,
    labels: Optional[list] = None,
    dimension: str = "time",
    context: Optional[dict] = None,
    **kwargs,
) -> xr.DataArray:
    """
    Aggregate data over temporal intervals.

    Args:
        data: Input datacube
        intervals: List of [start, end] datetime pairs
        reducer: Reducer function
        labels: Optional labels for output intervals
        dimension: Time dimension name
        context: Optional context

    Returns:
        Aggregated datacube
    """
    kwargs.pop('named_parameters', None)

    logger.debug(f"aggregate_temporal: {len(intervals)} intervals")

    # Resolve dimension aliases (e.g., 't' → 'time')
    dimension = resolve_dimension(dimension, data)

    if not intervals or len(intervals) == 0:
        # No intervals specified - reduce entire time dimension
        return reduce_dimension(
            data=data,
            reducer=reducer,
            dimension=dimension,
            context=context,
        )

    import pandas as pd

    # Process each interval
    results = []
    result_labels = []

    for i, interval in enumerate(intervals):
        if not isinstance(interval, (list, tuple)) or len(interval) < 2:
            logger.warning(f"Skipping invalid interval at index {i}: {interval}")
            add_job_note(f"Interval {i + 1} ({interval!r}) isn't a [start, end] pair, so it was left out.")
            continue

        start, end = interval[0], interval[1]

        # Select data within the interval
        try:
            # Convert to pandas timestamps for robust comparison
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)

            # Select time slice
            time_mask = (data.coords[dimension] >= start_ts) & (data.coords[dimension] < end_ts)
            interval_data = data.sel({dimension: time_mask})

            if interval_data.sizes.get(dimension, 0) == 0:
                logger.warning(f"No data found for interval [{start}, {end})")
                add_job_note(f"No data fell in the interval {start} to {end}, so it's missing from the result.")
                continue

            # Reduce this interval
            reduced = reduce_dimension(
                data=interval_data,
                reducer=reducer,
                dimension=dimension,
                context=context,
            )

            # Assign label
            label = labels[i] if labels and i < len(labels) else str(start)
            reduced = reduced.expand_dims({dimension: [pd.Timestamp(label)]})
            results.append(reduced)
            result_labels.append(label)

        except Exception as e:
            logger.error(f"Failed to process interval [{start}, {end}): {e}")
            add_job_note(f"The interval {start} to {end} couldn't be computed, so it's missing from the result.")
            continue

    if not results:
        logger.warning("No intervals produced results, falling back to full reduction")
        add_job_note("None of the requested intervals produced a result, so the whole period "
                     "was reduced to a single value instead.")
        return reduce_dimension(
            data=data,
            reducer=reducer,
            dimension=dimension,
            context=context,
        )

    # Concatenate results along time dimension
    result = xr.concat(results, dim=dimension)
    result.attrs = data.attrs

    logger.debug(f"aggregate_temporal complete: {len(results)} intervals aggregated")
    return result


def aggregate_temporal_period(
    data: xr.DataArray,
    period: str = "month",
    reducer: Optional[Callable] = None,
    dimension: Optional[str] = None,
    context: Optional[dict] = None,
    **kwargs,
) -> xr.DataArray:
    """
    Aggregate data over regular time periods (month, year, season, etc.).

    Replaces the upstream openeo_processes_dask version which has a
    ParameterReference resolution bug (TypeError: 'ParameterReference' / 'int').

    This implementation uses xarray's resample() which correctly handles
    the time grouping without needing callback resolution.

    Args:
        data: Input datacube with time dimension
        period: One of: hour, day, week, month, season, year
        reducer: Reducer function (ignored — we use nanmedian by default)
        dimension: Time dimension name
        context: Optional context

    Returns:
        Aggregated datacube with one timestep per period
    """
    kwargs.pop('named_parameters', None)

    # Resolve time dimension
    if dimension is None:
        for candidate in ["time", "t", "temporal"]:
            if candidate in data.dims:
                dimension = candidate
                break
        if dimension is None:
            dimension = resolve_dimension("time", data)
    else:
        dimension = resolve_dimension(dimension, data)

    logger.info(f"aggregate_temporal_period: period={period}, dim={dimension}")

    # Map OpenEO period names to pandas resample frequencies
    PERIOD_MAP = {
        "hour": "h",
        "day": "D",
        "week": "W",
        "dekad": "10D",
        "month": "ME",
        "season": "QS-DEC",
        "tropical-season": "6ME",
        "year": "YE",
    }

    freq = PERIOD_MAP.get(period, "ME")  # Default to monthly
    if period not in PERIOD_MAP:
        add_job_note(f"The period {period!r} isn't supported, so the data was aggregated by month instead.")

    try:
        # Use xarray resample — avoids the callback resolution bug entirely
        resampled = data.resample({dimension: freq})

        # Detect reducer type from callback if possible
        reducer_name = "median"  # safe default
        if reducer is None:
            add_job_note(f"No reducer was given for aggregating by {period}, so the median was used.")
        else:
            reducer_str = str(reducer).lower()
            if "mean" in reducer_str:
                reducer_name = "mean"
            elif "min" in reducer_str:
                reducer_name = "min"
            elif "max" in reducer_str:
                reducer_name = "max"
            elif "sum" in reducer_str:
                reducer_name = "sum"
            elif "median" not in reducer_str:
                add_job_note(f"The reducer for aggregating by {period} wasn't recognised, so the median "
                             "was used instead.")

        # Apply the detected reducer
        if reducer_name == "mean":
            result = resampled.mean(dim=dimension, skipna=True)
        elif reducer_name == "min":
            result = resampled.min(dim=dimension, skipna=True)
        elif reducer_name == "max":
            result = resampled.max(dim=dimension, skipna=True)
        elif reducer_name == "sum":
            result = resampled.sum(dim=dimension, skipna=True)
        else:
            result = resampled.median(dim=dimension, skipna=True)

        logger.info(f"aggregate_temporal_period complete: {result.sizes}")
        return result

    except Exception as e:
        logger.warning(f"aggregate_temporal_period resample failed: {e}, falling back to reduce_dimension")
        add_job_note(f"Aggregating by {period} failed, so the whole period was reduced to a single value "
                     "instead of one per period.")
        # Fallback: just reduce the entire time dimension
        return reduce_dimension(
            data=data,
            reducer=reducer,
            dimension=dimension,
            context=context,
        )


def merge_cubes(
    cube1: xr.DataArray,
    cube2: xr.DataArray,
    overlap_resolver: Callable = None,
    context: Optional[dict] = None,
    **kwargs,
) -> xr.DataArray:
    """Fixed merge_cubes that resolves ParameterReference in overlap_resolver.

    The upstream openeo_processes_dask merge_cubes passes ParameterReference
    objects (from 'from_parameter': 'x'/'y') to xr.reduce() as named_parameters,
    but the overlap_resolver callback (e.g. subtract) receives unresolved
    ParameterReference objects instead of actual data, causing:
        TypeError: unsupported operand type(s) for -: 'ParameterReference' and 'ParameterReference'

    This fix detects the overlap_resolver type (subtract, add, multiply, etc.)
    and applies the operation directly using xarray, bypassing the broken
    callback resolution path entirely.
    """
    kwargs.pop("named_parameters", None)

    if context is None:
        context = {}

    logger.info(
        f"merge_cubes: cube1={getattr(cube1, 'shape', '?')}, "
        f"cube2={getattr(cube2, 'shape', '?')}, "
        f"overlap_resolver={'yes' if overlap_resolver else 'no'}"
    )

    # --- Detect overlap resolver operation ---
    op_name = _detect_overlap_operation(overlap_resolver)

    if op_name is not None:
        logger.info(f"merge_cubes: detected overlap operation '{op_name}', using direct path")
        return _merge_cubes_direct(cube1, cube2, op_name)

    # --- Fallback to upstream merge_cubes for non-overlap cases ---
    # (e.g. when cubes have different band labels and can be concatenated)
    logger.info("merge_cubes: no overlap resolver or unrecognized, using upstream")
    try:
        from openeo_processes_dask.process_implementations.cubes.merge import (
            merge_cubes as upstream_merge_cubes,
        )
        return upstream_merge_cubes(cube1, cube2, overlap_resolver=overlap_resolver, context=context)
    except Exception as e:
        logger.warning(f"Upstream merge_cubes failed: {e}, using xr.concat fallback")
        # Simple fallback: concat along bands if possible
        try:
            merged = xr.concat([cube1, cube2], dim="bands")
            add_job_note("Merging the two cubes failed, so they were stacked along the band dimension "
                         "instead; overlapping values were not resolved.")
            return merged
        except Exception:
            add_job_note("Merging the two cubes failed, so they were combined as two separate "
                         "variables; overlapping values were not resolved.")
            return xr.merge([cube1.to_dataset(name="cube1"), cube2.to_dataset(name="cube2")]).to_array()


def _detect_overlap_operation(overlap_resolver) -> Optional[str]:
    """Detect the arithmetic operation from an overlap_resolver callback.

    The overlap_resolver is a compiled callback from openeo-pg-parser-networkx.
    It's typically a functools.partial wrapping subtract/add/multiply/divide.

    Returns:
        Operation name ('subtract', 'add', 'multiply', 'divide', 'min', 'max')
        or None if unrecognizable.
    """
    if overlap_resolver is None:
        return None

    import functools

    # Unwrap partial layers to find the actual function name
    func = overlap_resolver
    seen_names = []
    for _ in range(10):  # max unwrap depth
        name = getattr(func, "__name__", "")
        if name:
            seen_names.append(name.lower())
        if isinstance(func, functools.partial):
            func = func.func
        elif hasattr(func, "__wrapped__"):
            func = func.__wrapped__
        else:
            break

    # Also check string representation for process_id clues
    repr_str = repr(overlap_resolver).lower()

    # Map known operation names
    operation_map = {
        "subtract": "subtract",
        "_subtract": "subtract",
        "add": "add",
        "_add": "add",
        "multiply": "multiply",
        "_multiply": "multiply",
        "divide": "divide",
        "_divide": "divide",
        "min": "min",
        "_min": "min",
        "max": "max",
        "_max": "max",
    }

    # Check unwrapped names
    for name in seen_names:
        if name in operation_map:
            return operation_map[name]

    # Check repr for process names
    for key, op in operation_map.items():
        if key in repr_str:
            return op

    # Try calling with test data to detect the operation
    try:
        import numpy as _np
        a = _np.array([10.0])
        b = _np.array([3.0])
        result = overlap_resolver(x=a, y=b)
        val = float(result.flat[0]) if hasattr(result, "flat") else float(result)

        if _np.isclose(val, 7.0):
            return "subtract"
        elif _np.isclose(val, 13.0):
            return "add"
        elif _np.isclose(val, 30.0):
            return "multiply"
        elif _np.isclose(val, 10.0 / 3.0):
            return "divide"
        elif _np.isclose(val, 3.0):
            return "min"
        elif _np.isclose(val, 10.0):
            return "max"
    except Exception as e:
        logger.debug(f"Overlap resolver probe failed: {e}")

    return None


def _merge_cubes_direct(
    cube1: xr.DataArray,
    cube2: xr.DataArray,
    operation: str,
) -> xr.DataArray:
    """Apply overlap resolver operation directly using xarray.

    Bypasses the broken ParameterReference callback path by applying the
    arithmetic operation directly on aligned cubes.

    Args:
        cube1: First cube (for change detection: period2 / post)
        cube2: Second cube (for change detection: period1 / pre)
        operation: One of 'subtract', 'add', 'multiply', 'divide', 'min', 'max'

    Returns:
        Merged cube with operation applied element-wise
    """
    # Align cubes — handle slight coordinate mismatches
    try:
        c1, c2 = xr.align(cube1, cube2, join="inner")
    except Exception:
        logger.warning("xr.align failed, trying direct operation")
        c1, c2 = cube1, cube2

    if operation == "subtract":
        result = c1 - c2
    elif operation == "add":
        result = c1 + c2
    elif operation == "multiply":
        result = c1 * c2
    elif operation == "divide":
        # Safe division
        result = c1 / c2.where(c2 != 0, np.float64(1e-10))
    elif operation == "min":
        result = xr.ufuncs.minimum(c1, c2) if hasattr(xr, "ufuncs") else np.minimum(c1, c2)
    elif operation == "max":
        result = xr.ufuncs.maximum(c1, c2) if hasattr(xr, "ufuncs") else np.maximum(c1, c2)
    else:
        raise ValueError(f"Unknown merge operation: {operation}")

    # Preserve attributes from cube1
    result.attrs = cube1.attrs.copy()

    logger.info(f"merge_cubes direct '{operation}' complete, shape={result.shape}")
    return result
