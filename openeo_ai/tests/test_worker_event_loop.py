# ABOUTME: The EI worker tick shares the event loop with the WS handler; its sync
# ABOUTME: Postgres calls must run off-loop or a slow DB stalls WS opening handshakes.
import ast
import asyncio
import inspect
import threading
import time

import pytest

from openeo_ai.intelligence import worker as worker_mod
from openeo_ai.intelligence.worker import EarthIntelligenceWorker


class _SlowRepo:
    """Minimal repository exposing the methods a tick touches.

    enqueue_due_watches blocks like a stalled Postgres round-trip and records
    the thread it ran on, so the test can prove it was offloaded off the loop.
    """

    def __init__(self, block_seconds: float):
        self.block_seconds = block_seconds
        self.enqueue_thread = None

    def enqueue_due_watches(self, limit=25, user_id=None):
        self.enqueue_thread = threading.get_ident()
        time.sleep(self.block_seconds)  # a stalled DB round-trip
        return []

    def claim_next_observation(self, worker_id=None, user_id=None, stale_after_seconds=None):
        return None

    def claim_pending_deliveries(self, limit=10, user_id=None):
        return []


@pytest.mark.asyncio
async def test_worker_tick_db_runs_off_loop_and_loop_stays_responsive():
    """A worker tick's DB work must not run on the event-loop thread: a stalled
    Postgres round-trip there starves the loop and WS opening handshakes time
    out (the 2026-07-24 smoke failure). Offloading keeps the loop responsive."""
    repo = _SlowRepo(block_seconds=0.5)
    worker = EarthIntelligenceWorker(repository=repo)

    beats = []

    async def _heartbeat():
        while True:
            beats.append(time.monotonic())
            await asyncio.sleep(0.02)

    beat_task = asyncio.create_task(_heartbeat())
    await asyncio.sleep(0.05)  # let the heartbeat establish a baseline
    await worker.tick(max_observations=1)
    beat_task.cancel()

    loop_thread = threading.get_ident()
    assert repo.enqueue_thread is not None, "enqueue_due_watches was never called"
    assert repo.enqueue_thread != loop_thread, "worker DB call ran on the event-loop thread"
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert max(gaps) < 0.3, (
        f"event loop starved for {max(gaps):.2f}s during the worker tick"
    )


def _async_method_bare_db_calls():
    """Bare synchronous DB calls made directly inside an `async def` body.

    A repository call (or one of the sync cascade helpers that fan out into
    repository calls) invoked directly in async code runs on the event loop.
    When offloaded it appears as a bare reference passed to asyncio.to_thread,
    not as a Call node — so any Call node here is a loop-blocking regression.
    """
    tree = ast.parse(inspect.getsource(worker_mod))
    sync_cascade = {"_create_signal", "_mark_failed"}
    violations = []

    def _is_self_repository(node: ast.AST) -> bool:
        # self.repository.<method>
        return (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "repository"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "self"
        )

    def _is_self_cascade(node: ast.AST) -> bool:
        # self._create_signal / self._mark_failed
        return (
            isinstance(node, ast.Attribute)
            and node.attr in sync_cascade
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        )

    for fn in ast.walk(tree):
        if not isinstance(fn, ast.AsyncFunctionDef):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and (
                _is_self_repository(node.func) or _is_self_cascade(node.func)
            ):
                violations.append(f"{fn.name}: {ast.unparse(node.func)}(...)")
    return violations


def test_no_bare_sync_db_calls_in_async_worker_paths():
    bare = _async_method_bare_db_calls()
    assert bare == [], (
        "sync DB calls on the event loop inside async worker methods: " + ", ".join(bare)
    )
