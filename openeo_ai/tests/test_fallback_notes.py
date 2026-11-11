# ABOUTME: When a process falls back to a different method (unknown period or reducer, a failed
# ABOUTME: interval, a failed merge), the job says so in its notes.
import contextvars
import sys

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from openeo_app.core.job_notes import start_job_notes
from openeo_app.processes import reduce_dimension_fix as rdf


def _cube(days=59):  # 1 January to 28 February: two calendar months
    times = pd.date_range("2026-01-01", periods=days, freq="D")
    return xr.DataArray(np.arange(days * 4, dtype="float32").reshape(days, 2, 2),
                        dims=("t", "y", "x"), coords={"t": times})


def _notes_from(fn):
    """Run fn as a job would, and return the notes it left."""
    def job():
        notes = start_job_notes()
        result = fn()
        return result, notes
    return contextvars.copy_context().run(job)


def _mean(data=None, **kwargs):
    return np.nanmean(data, axis=kwargs.get("axis"))


def test_an_unsupported_period_is_aggregated_by_month_and_says_so():
    result, notes = _notes_from(lambda: rdf.aggregate_temporal_period(_cube(), period="fortnight", reducer="mean"))
    assert result.sizes["t"] == 2  # January and February
    assert any("'fortnight'" in n and "by month" in n for n in notes)


def test_a_missing_reducer_uses_the_median_and_says_so():
    _, notes = _notes_from(lambda: rdf.aggregate_temporal_period(_cube(), period="month", reducer=None))
    assert any("median" in n and "No reducer" in n for n in notes)


def test_an_unrecognised_reducer_uses_the_median_and_says_so():
    _, notes = _notes_from(lambda: rdf.aggregate_temporal_period(_cube(), period="month", reducer="quantile_90"))
    assert any("wasn't recognised" in n and "median" in n for n in notes)


@pytest.mark.parametrize("reducer", ["mean", "median", "max"])
def test_a_recognised_reducer_and_period_leave_no_note(reducer):
    _, notes = _notes_from(lambda: rdf.aggregate_temporal_period(_cube(), period="month", reducer=reducer))
    assert notes == []


def test_an_interval_with_no_data_is_named_as_missing():
    intervals = [["2026-01-01", "2026-01-15"], ["2027-01-01", "2027-02-01"]]
    result, notes = _notes_from(lambda: rdf.aggregate_temporal(_cube(), intervals=intervals, reducer=_mean,
                                                               dimension="t"))
    assert result.sizes["t"] == 1
    assert any("2027-01-01" in n and "missing from the result" in n for n in notes)


def test_a_failed_merge_says_overlaps_were_not_resolved(monkeypatch):
    # Make the upstream merge unavailable, the same path as an upstream failure.
    monkeypatch.setitem(sys.modules, "openeo_processes_dask.process_implementations.cubes.merge", None)
    a = _cube(3).expand_dims(bands=["B04"])
    b = _cube(3).expand_dims(bands=["B08"])
    _, notes = _notes_from(lambda: rdf.merge_cubes(a, b))
    assert any("Merging the two cubes failed" in n and "not resolved" in n for n in notes)


def test_outside_a_job_the_fallbacks_still_compute():
    result = rdf.aggregate_temporal_period(_cube(), period="fortnight", reducer="mean")
    assert result.sizes["t"] == 2
