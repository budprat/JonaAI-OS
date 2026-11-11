# ABOUTME: Direct tests for the process-graph executor's NumPy/xarray shims:
# ABOUTME: wrapper kwarg handling, type preservation, and registry mapping.

import numpy as np
import xarray as xr

from openeo_app.execution.executor import _wrap_process, _wrap_type_preserving
from openeo_app.execution.process_registry_singleton import ProcessRegistrySingleton


def _sample_da() -> xr.DataArray:
    return xr.DataArray(
        np.arange(6.0).reshape(2, 3),
        dims=("y", "x"),
        coords={"y": [0, 1], "x": [10, 20, 30]},
    )


class TestWrapProcess:
    def test_strips_parser_kwargs(self):
        seen = {}

        def proc(x, y):
            seen.update(x=x, y=y)
            return x + y

        wrapped = _wrap_process(proc)
        out = wrapped(
            x=2, y=3,
            named_parameters={"a": 1},
            positional_parameters=["p"],
            axis=0,
        )
        assert out == 5
        assert seen == {"x": 2, "y": 3}

    def test_overlap_resolver_drops_positional_arg(self):
        def resolver(x, y):
            return (x, y)

        wrapped = _wrap_process(resolver)
        # xarray.reduce passes data positionally AND the parser passes x/y as
        # kwargs from named_parameters — the positional must be dropped.
        out = wrapped(np.array([9.0]), x=1, y=2)
        assert out == (1, 2)

    def test_positional_kept_without_kwarg_collision(self):
        def proc(x, y=10):
            return (x, y)

        wrapped = _wrap_process(proc)
        assert wrapped(5) == (5, 10)


class TestWrapTypePreserving:
    def test_numpy_result_rewrapped_exact_shape(self):
        da = _sample_da()

        def eq_like(x, y):
            return np.asarray(x) == y  # plain ndarray, metadata stripped

        wrapped = _wrap_type_preserving(eq_like)
        out = wrapped(da, y=2.0)
        assert isinstance(out, xr.DataArray)
        assert out.dims == da.dims
        assert list(out.coords["x"].values) == [10, 20, 30]
        assert bool(out.sel(y=0, x=30)) is True  # value 2.0 == 2.0

    def test_numpy_result_rewrapped_reduced_shape(self):
        da = _sample_da()

        def collapse(x):
            return np.asarray(x).sum(axis=0)  # (2,3) -> (3,)

        wrapped = _wrap_type_preserving(collapse)
        out = wrapped(da)
        assert isinstance(out, xr.DataArray)
        assert out.dims == ("x",)
        assert list(out.coords["x"].values) == [10, 20, 30]
        assert list(out.values) == [3.0, 5.0, 7.0]

    def test_parser_kwargs_stripped_and_overlap_dropped(self):
        da = _sample_da()

        def ident(x):
            return np.asarray(x)

        wrapped = _wrap_type_preserving(ident)
        out = wrapped(np.array([1.0]), x=da, named_parameters={"n": 1}, axis=0)
        assert isinstance(out, xr.DataArray)
        assert out.shape == da.shape

    def test_plain_input_stays_plain(self):
        def add1(x):
            return np.asarray(x) + 1

        wrapped = _wrap_type_preserving(add1)
        out = wrapped(np.array([1.0, 2.0]))
        assert isinstance(out, np.ndarray)


class TestRegistryMapping:
    def test_reserved_word_processes_register_unprefixed(self):
        reg = ProcessRegistrySingleton.get_registry()
        for public in ("min", "max", "and", "or", "not"):
            assert public in reg, f"'{public}' missing from registry"
        for private in ("_min", "_and", "_or"):
            assert private not in reg, f"'{private}' leaked into registry"

    def test_registry_size_baseline(self):
        # /health reports processes_count from this registry; pin the floor so
        # registry-construction regressions surface here, not in production.
        reg = ProcessRegistrySingleton.get_registry()
        assert len(reg) >= 112

    def test_array_element_selects_by_label_and_index(self):
        reg = ProcessRegistrySingleton.get_registry()
        impl = reg["array_element"].implementation
        da = xr.DataArray(
            np.arange(6.0).reshape(3, 2),
            dims=("bands", "x"),
            coords={"bands": ["red", "green", "nir"], "x": [0, 1]},
        )
        by_label = impl(da, label="nir")
        assert list(np.asarray(by_label)) == [4.0, 5.0]
        by_index = impl(da, index=1)
        assert list(np.asarray(by_index)) == [2.0, 3.0]
