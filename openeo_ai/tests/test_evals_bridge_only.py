# ABOUTME: Bridge-only eval mode — CI runs no openeo backend, so
# ABOUTME: execution-dependent golden items are split out and one bad item can't kill the run.
import asyncio

import pytest

from openeo_ai.evals.golden_dataset import GOLDEN_QUERIES, GoldenQuery
from openeo_ai.evals.run_experiment import (
    _run_item_isolated,
    _run_one,
    requires_execution,
    split_execution_queries,
)


def _q(qid, expected_tools):
    return GoldenQuery(
        id=qid, category="ndvi", query="ndvi over paris june 2024",
        expected_tools=expected_tools, expected_output="visualization",
    )


def test_requires_execution_predicate():
    assert requires_execution(_q("a", ["openeo_raw_graph", "openeo_execute"])) is True
    assert requires_execution(_q("b", ["openeo_create_job"])) is True
    assert requires_execution(_q("c", ["openeo_quality_metrics"])) is False
    assert requires_execution(_q("d", ["openeo_resolve_location", "recommend_collection"])) is False


def test_split_keeps_bridge_only_items_and_reports_skipped():
    kept, skipped = split_execution_queries(GOLDEN_QUERIES)
    assert kept and skipped, "golden dataset should contain both kinds"
    assert len(kept) + len(skipped) == len(GOLDEN_QUERIES)
    assert all(not requires_execution(q) for q in kept)
    assert all(requires_execution(q) for q in skipped)


@pytest.mark.asyncio
async def test_run_one_contains_cancelled_error(monkeypatch):
    """Regression for the first real evals-CI run: a per-item wait_for
    cancellation leaked through the SDK's anyio scopes as CancelledError,
    which `except Exception` does not catch — one item killed the whole
    16-minute experiment (exit 1, gate skipped)."""

    class _CancelledBridge:
        async def query(self, **kwargs):
            raise asyncio.CancelledError("cancel scope leak")

    result = await _run_one(_CancelledBridge(), _q("x", ["openeo_quality_metrics"]), "test-exp")
    assert result["error"], "cancellation must be recorded as an item error"
    assert result["query_id"] == "x"


@pytest.mark.asyncio
async def test_runner_survives_task_level_cancellation():
    """The second CI failure mode: the SDK's anyio cancel scopes mis-unwind
    and cancel the RUNNING TASK itself — a task marked cancelling re-raises
    CancelledError at every later checkpoint, so per-item catching alone
    cannot save the runner. Items must run in their own task and the runner
    must absorb stray cancellations."""

    class _ScopeLeakBridge:
        async def query(self, **kwargs):
            asyncio.current_task().cancel()  # emulate the scope mis-unwind
            await asyncio.sleep(3600)

    res = await _run_item_isolated(
        _ScopeLeakBridge(), _q("y", ["openeo_quality_metrics"]), "test-exp"
    )
    assert res["query_id"] == "y"
    assert res["error"], "task-level cancellation must be recorded as an item error"
    # The runner task must not be left in a cancelling state...
    assert asyncio.current_task().cancelling() == 0
    # ...and later awaits must work (a poisoned task would re-raise here).
    await asyncio.sleep(0)
