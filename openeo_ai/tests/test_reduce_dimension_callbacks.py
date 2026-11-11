# ABOUTME: Regression tests locking in reduce_dimension's loud-failure contract —
# ABOUTME: complex callbacks execute or raise ValueError, never silently become nanmean.

import functools

import numpy as np
import pytest
import xarray as xr

from openeo_app.processes.reduce_dimension_fix import (
    _get_reduce_function,
    reduce_dimension,
)


# Keyword-free names so the probe (not name/repr matching) is what resolves them.
def _middle_value(data=None, **kwargs):
    arr = np.sort(np.asarray(data).ravel())
    return float(arr[len(arr) // 2])


def _average_value(data=None, **kwargs):
    return float(np.mean(np.asarray(data)))


# Reducers are defined at module level on purpose: _get_reduce_function inspects
# the callable's qualified name, so an enclosing scope whose name contains a
# reducer keyword (e.g. "mean") would be mis-detected. Module-level names keep
# the qualname keyword-free.
def spread_reducer(data=None, **kwargs):
    """Range (max - min) over a 1-D slice; refuses the whole-cube probe call."""
    arr = np.asarray(data)
    if arr.ndim != 1:
        raise ValueError("spread_reducer expects a 1-D time series")
    return float(np.nanmax(arr) - np.nanmin(arr))


def unrunnable_reducer(data=None, **kwargs):
    raise RuntimeError("callback cannot execute")


def _cube() -> xr.DataArray:
    # Per-pixel time series chosen so range (max-min) differs from the average,
    # making a silent nanmean fallback detectable.
    return xr.DataArray(
        np.array(
            [
                [[0.0, 10.0], [0.0, 0.0]],   # t=0
                [[5.0, 20.0], [1.0, 2.0]],   # t=1
                [[10.0, 60.0], [2.0, 4.0]],  # t=2
            ]
        ),
        dims=("t", "y", "x"),
        coords={"t": [0, 1, 2]},
    )


def test_complex_callback_executes_and_is_not_collapsed():
    data = _cube()

    result = reduce_dimension(data, reducer=spread_reducer, dimension="t")

    expected_range = (data.max("t") - data.min("t")).values  # [[10, 50], [2, 4]]
    average = data.mean("t").values                          # [[5, 30], [1, 2]]

    assert "t" not in result.dims
    np.testing.assert_allclose(result.values, expected_range)
    # The core assertion: the reducer actually ran; it did NOT collapse to nanmean.
    assert not np.allclose(result.values, average)


def test_unexecutable_callback_raises_valueerror_loudly():
    data = _cube()

    # Loud failure is the contract — a silent nanmean fallback would return a
    # cube instead of raising.
    with pytest.raises(ValueError):
        reduce_dimension(data, reducer=unrunnable_reducer, dimension="t")


def test_probe_distinguishes_median_from_mean():
    # A median partial that reaches the value-probe must resolve to nanmedian,
    # not nanmean. The prior [1,2,3,4,5] probe collided median==mean==3.0.
    median_partial = functools.partial(_middle_value)
    mean_partial = functools.partial(_average_value)

    assert _get_reduce_function(median_partial) is np.nanmedian
    assert _get_reduce_function(mean_partial) is np.nanmean
