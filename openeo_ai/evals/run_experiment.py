# ABOUTME: Experiment harness — runs the golden dataset through JonaAI and scores (Phase G).

"""
Experiment runner.

Iterates `openeo_ai.evals.golden_dataset.GOLDEN_QUERIES` and runs each through
the JonaAI Claude SDK bridge. Captures the resulting Langfuse trace and emits
experiment-specific scores:

    - expected_tool_called (BOOLEAN, per expected tool)
    - response_contains_visualization (BOOLEAN)
    - execution_time_ms (NUMERIC)
    - judge-relevance / judge-completeness / judge-correctness (NUMERIC, via Phase F)

Runs are grouped under a Langfuse "dataset run" so you can compare experiments
side-by-side in the UI.

Usage:
    source .env
    python -m openeo_ai.evals.run_experiment --experiment v1
    python -m openeo_ai.evals.run_experiment --experiment pr-123 --only ndvi-kharg-2024q1
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import sys
import time
import uuid
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="[experiment] %(message)s")
logger = logging.getLogger(__name__)


class FakeWebSocket:
    """Minimal async WS stub that captures emitted messages for scoring.

    The JonaAI SDK bridge expects an object with `send_json`, `receive_json`,
    and `client` attributes. Only send_json is exercised by the query flow;
    receive_json is never called because we set stop_event manually.
    """

    def __init__(self):
        self.events: list[dict] = []
        self.client = type("FakeClient", (), {"host": "127.0.0.1"})()

    async def send_json(self, payload: Any) -> None:
        if isinstance(payload, dict):
            self.events.append(payload)
        else:
            self.events.append({"_raw": str(payload)[:500]})

    async def receive_json(self) -> dict:
        # Never called during a single experiment query (no stop/restore flow)
        await asyncio.Event().wait()  # blocks forever
        return {}

    def tools_called(self) -> list[str]:
        """Extract distinct tool names from captured events."""
        seen: set[str] = set()
        for e in self.events:
            if e.get("type") == "tool_start" and e.get("tool_name"):
                seen.add(e["tool_name"])
        return sorted(seen)

    def has_visualization(self) -> bool:
        return any(e.get("type") == "visualization" for e in self.events)

    def has_error(self) -> bool:
        return any(e.get("type") == "error" for e in self.events)

    def assistant_text(self) -> str:
        parts = [e.get("content", "") for e in self.events if e.get("type") == "text_delta"]
        return "".join(parts)


def _score_ungrounded_numbers(events: list) -> float:
    """Observe-mode signal: 1.0 if the assistant stated a trust-claim number
    (confidence/percent/score/delta) absent from every tool result this turn, else 0.0.
    Tool outputs are reconstructed from the captured tool_result events."""
    from openeo_ai.sdk.grounding_guard import check

    response_text = "".join(e.get("content", "") for e in events if e.get("type") == "text_delta")
    tool_texts = [str(e.get("result", "")) for e in events if e.get("type") == "tool_result"]
    return 1.0 if check(response_text, tool_texts) else 0.0


def _bridge_session_id() -> str:
    """Fresh UUID for the bridge session — ai_sessions.id is a Postgres uuid column.

    The readable "<experiment>:<query-id>" label stays on the Langfuse
    propagate_attributes metadata only; feeding it to the bridge breaks every
    turn at the session lookup with a uuid-cast error.
    """
    return str(uuid.uuid4())


# Option B: golden items expecting these tools need a running openeo
# backend (real job execution). CI runs bridge-only — pixel-execution truth is
# already pinned there by the recorded pixel-truth fixtures.
_EXECUTION_TOOLS = {
    "openeo_execute",
    "openeo_create_job",
    "openeo_get_results",
    "result_load",
}


def requires_execution(query: Any) -> bool:
    """True when the golden query's expectations need a live openeo backend."""
    return any(t in _EXECUTION_TOOLS for t in query.expected_tools)


def split_execution_queries(queries: list) -> tuple[list, list]:
    """Split (bridge-only, execution-dependent) — callers must log the skipped
    ids so a bounded run never reads as full coverage."""
    kept = [q for q in queries if not requires_execution(q)]
    skipped = [q for q in queries if requires_execution(q)]
    return kept, skipped


def _tool_matches(expected: str, tools: list) -> bool:
    """True when a called tool equals `expected` or its MCP-prefixed form.

    Replaces the old `expected in t or t.endswith(expected)` substring test,
    which matched "load" inside "download_result".
    """
    for t in tools:
        if t == expected or t.rsplit("__", 1)[-1] == expected:
            return True
    return False


async def _run_one(
    bridge: Any,
    query: Any,
    experiment_name: str,
) -> dict:
    """Run a single golden query through the bridge and score the trace."""
    from langfuse import propagate_attributes
    from openeo_ai.langfuse_setup import (
        current_trace_id,
        emit_score,
        set_root_trace_name,
    )

    fake_ws = FakeWebSocket()
    stop_event = asyncio.Event()
    started = time.monotonic()
    trace_id: Optional[str] = None
    error_text: Optional[str] = None
    bridge_session = _bridge_session_id()

    try:
        with propagate_attributes(
            user_id=f"experiment:{experiment_name}",
            session_id=f"{experiment_name}:{query.id}",
            tags=["jonaai", "experiment", experiment_name],
            metadata={"query_id": query.id, "category": query.category},
        ):
            set_root_trace_name(
                name=f"experiment.{query.id}",
                input_data={"query": query.query, "category": query.category},
            )
            try:
                await asyncio.wait_for(
                    bridge.query(
                        prompt=query.query,
                        websocket=fake_ws,
                        session_id=bridge_session,
                        stop_event=stop_event,
                    ),
                    timeout=180,  # 3 min per query max
                )
            except asyncio.TimeoutError:
                error_text = "experiment timeout >180s"
            trace_id = current_trace_id()
    # CancelledError included deliberately: a per-item wait_for cancellation
    # can leak through the SDK's anyio cancel scopes as raw CancelledError
    # (not TimeoutError) — one item must never kill the whole experiment.
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
        error_text = str(exc)[:500] or type(exc).__name__
        # A scope leak may have marked THIS task cancelling — clear it so the
        # scoring/judge awaits below don't re-raise at the next checkpoint.
        _absorb_stray_cancellation()

    duration_ms = int((time.monotonic() - started) * 1000)
    tools = fake_ws.tools_called()
    has_viz = fake_ws.has_visualization()
    response_text = fake_ws.assistant_text()

    # Expected-tool checks — one boolean score per expected tool
    for expected in query.expected_tools:
        matched = _tool_matches(expected, tools)
        emit_score(
            name=f"exp-tool-{expected}",
            value=1.0 if matched else 0.0,
            trace_id=trace_id,
            data_type="BOOLEAN",
            comment=f"expected={expected} matched={matched}",
        )

    # Typed artifact check — only when the golden query sets an expectation.
    # expected_bbox/expected_temporal exist on GoldenQuery but are not scored
    # until an anchor populates them.
    if getattr(query, "expected_collection", ""):
        from openeo_ai.evals.artifacts import score_process_graph

        artifact = score_process_graph(fake_ws.events)
        emit_score(
            name="exp-artifact-collection",
            value=1.0 if artifact["collection"] == query.expected_collection else 0.0,
            trace_id=trace_id,
            data_type="BOOLEAN",
            comment=f"expected={query.expected_collection} got={artifact['collection']}",
        )

    # Visualization presence check (where expected)
    if "visualization" in query.expected_output or "map" in query.expected_output:
        emit_score(
            name="exp-has-visualization",
            value=1.0 if has_viz else 0.0,
            trace_id=trace_id,
            data_type="BOOLEAN",
        )

    # Grounding: did the assistant state a trust-claim number absent from tool
    # results? Grounded subset only — error-category queries have no tool output to ground
    # against. Observe-mode: a NEGATIVE dimension in the regression gate (lower is better).
    if query.category != "error":
        emit_score(
            name="exp-ungrounded-numbers",
            value=_score_ungrounded_numbers(fake_ws.events),
            trace_id=trace_id,
            data_type="BOOLEAN",
        )

    # Execution time
    emit_score(
        name="exp-execution-ms",
        value=float(duration_ms),
        trace_id=trace_id,
        comment=f"query_id={query.id}",
    )

    # Error flag
    if error_text:
        emit_score(
            name="exp-errored",
            value=1.0,
            trace_id=trace_id,
            comment=error_text,
            data_type="BOOLEAN",
        )

    # LLM-as-judge (Phase F) — fire and wait so CI can gate on it
    if response_text and trace_id:
        with contextlib.suppress(Exception):
            from openeo_ai.evals.judge import judge_response
            await judge_response(trace_id=trace_id, query=query.query, response=response_text)

    logger.info(
        "  %s: %dms, tools=%s, viz=%s, err=%s",
        query.id,
        duration_ms,
        tools,
        has_viz,
        error_text or "-",
    )
    return {
        "query_id": query.id,
        "trace_id": trace_id,
        "duration_ms": duration_ms,
        "tools": tools,
        "has_visualization": has_viz,
        "error": error_text,
    }


def _absorb_stray_cancellation() -> None:
    """Clear a cancelling flag left on the current task by SDK anyio scope
    mis-unwinds — a task marked cancelling re-raises CancelledError at every
    later checkpoint, which is how one bad item killed whole CI experiments."""
    cur = asyncio.current_task()
    while cur is not None and cur.cancelling():
        cur.uncancel()


async def _run_item_isolated(bridge: Any, query: Any, experiment_name: str) -> dict:
    """Run one golden item in its OWN task so an SDK cancel-scope leak kills
    the child, not the runner; absorb any stray cancellation that still
    reaches the runner and record the item as errored."""
    item_task = asyncio.ensure_future(_run_one(bridge, query, experiment_name))
    try:
        return await item_task
    except (Exception, asyncio.CancelledError) as exc:  # noqa: BLE001
        return {
            "query_id": query.id,
            "trace_id": None,
            "duration_ms": 0,
            "tools": [],
            "has_visualization": False,
            "error": (str(exc) or type(exc).__name__)[:500],
        }
    finally:
        _absorb_stray_cancellation()


async def run_experiment(
    experiment_name: str,
    only: Optional[str] = None,
    skip_execution: bool = False,
) -> list[dict]:
    """Run the full golden dataset and return per-query results."""
    from openeo_ai.langfuse_setup import init_langfuse
    from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
    from openeo_ai.sdk.client import OpenEOAIConfig
    from openeo_ai.evals.golden_dataset import GOLDEN_QUERIES

    init_langfuse()

    bridge = ClaudeSDKBridge(config=OpenEOAIConfig())

    queries = GOLDEN_QUERIES
    if skip_execution:
        queries, skipped = split_execution_queries(queries)
        logger.info(
            "Bridge-only mode: skipping %d execution-dependent queries: %s",
            len(skipped), [q.id for q in skipped],
        )
    if only:
        queries = [q for q in queries if q.id == only]
        if not queries:
            raise ValueError(f"No query with id={only}")

    logger.info("Running experiment '%s' with %d queries", experiment_name, len(queries))
    results = []
    for q in queries:
        res = await _run_item_isolated(bridge, q, experiment_name)
        results.append(res)

    # Flush Langfuse before exit
    try:
        from openeo_ai.langfuse_setup import get_langfuse_client
        lf = get_langfuse_client()
        if lf:
            lf.flush()
    except Exception:
        pass

    # Disconnect bridge sessions
    with contextlib.suppress(Exception):
        await bridge.disconnect_all()

    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", required=True, help="Experiment name (e.g. 'v1', 'pr-123')")
    parser.add_argument("--only", help="Run only this query id")
    parser.add_argument("--json-out", help="Write summary JSON to this path")
    parser.add_argument(
        "--skip-execution", action="store_true",
        help="Bridge-only mode: skip golden items needing a live openeo backend (CI)",
    )
    args = parser.parse_args()

    # Ensure imports resolve when run as a script
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
        sys.path.insert(0, os.path.join(repo_root, "openeo-fastapi"))

    results = asyncio.run(run_experiment(
        args.experiment, only=args.only, skip_execution=args.skip_execution,
    ))

    n_total = len(results)
    n_errored = sum(1 for r in results if r.get("error"))
    n_success = n_total - n_errored
    logger.info("")
    logger.info("Summary: %d/%d succeeded, %d errored", n_success, n_total, n_errored)

    if args.json_out:
        with open(args.json_out, "w") as f:
            json.dump({"experiment": args.experiment, "results": results}, f, indent=2)
        logger.info("Wrote summary to %s", args.json_out)

    sys.exit(1 if n_errored > 0 else 0)


if __name__ == "__main__":
    main()
