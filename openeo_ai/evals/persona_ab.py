# ABOUTME: Persona A/B lift harness. For each research persona, run its owned query
# ABOUTME: shapes with and without specialist delegation, record actual routing, score the lift.

from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

# The four research personas under test. Sentinel is EXEMPT (spine-critical durable monitoring —
# not an "is this worth it?" question). data/viz/ml/qa are compute specialists, also excluded.
PERSONAS: List[str] = ["data_scout", "methodologist", "publisher", "timeseries"]

# Multiple owned query shapes per persona so the roster verdict rests on a mean over several
# runs, not n=1. A separate dataset from GOLDEN_QUERIES so it never pollutes the main metrics.
PERSONA_QUERIES: List[Dict[str, str]] = [
    # data_scout — data availability / "what imagery exists?"
    {"persona": "data_scout",
     "query": ("What Sentinel-2 imagery is available over the Sundarbans for "
               "January–March 2024, and is coverage good enough to analyse mangrove change?")},
    {"persona": "data_scout",
     "query": ("Which Sentinel-2 tiles cover the Okavango Delta, and how many cloud-free "
               "scenes are there across the 2024 dry season?")},
    {"persona": "data_scout",
     "query": ("Is there enough clear Sentinel-2 imagery over Iceland's Vatnajökull to compare "
               "2023 against 2024, or are there too many gaps?")},
    # methodologist — method validity
    {"persona": "methodologist",
     "query": ("I want to detect crop stress in Fresno County using NDVI over one growing "
               "season — is NDVI the right index, and is a single-date snapshot a valid method?")},
    {"persona": "methodologist",
     "query": ("To measure glacier retreat, is differencing NDSI on two dates a sound method, "
               "or do I need a different approach?")},
    {"persona": "methodologist",
     "query": ("I'm using a 30-day baseline to detect deforestation — is that window valid, "
               "or will phenology confound the change signal?")},
    # publisher — reproducibility
    {"persona": "publisher",
     "query": ("I finished an NDVI change analysis of Lake Oroville — how do I make it "
               "reproducible so a colleague can rerun it and cite the exact data and method?")},
    {"persona": "publisher",
     "query": ("Package my Sundarbans mangrove analysis so another lab can reproduce the exact "
               "collection, date range, and processing steps.")},
    {"persona": "publisher",
     "query": ("What metadata and provenance do I need to record to cite this NBR burn-severity "
               "result in a peer-reviewed paper?")},
    # timeseries — temporal decomposition
    {"persona": "timeseries",
     "query": ("Decompose the NDVI signal over the Punjab wheat belt for 2022–2024 into its "
               "seasonal cycle versus a long-term trend, and tell me which dominates.")},
    {"persona": "timeseries",
     "query": ("Break down the NDWI signal for Lake Chad over 2019–2024 into trend, seasonal, "
               "and residual components.")},
    {"persona": "timeseries",
     "query": ("Is the NDVI decline in the Congo basin a genuine long-term trend, or interannual "
               "variability around a stable mean?")},
]

# Per-arm timeouts (seconds). The multi-agent arm legitimately runs longer; both env-overridable
# so a roster run can give slow turns room without editing code.
_TIMEOUT_COMPLEX = int(os.environ.get("JONA_PERSONA_AB_TIMEOUT_COMPLEX", "480"))
_TIMEOUT_MODERATE = int(os.environ.get("JONA_PERSONA_AB_TIMEOUT_MODERATE", "240"))
# Cap queries per persona (for a cheaper bounded run); 0/unset = use all.
_MAX_PER_PERSONA = int(os.environ.get("JONA_PERSONA_AB_MAX_PER_PERSONA", "0"))

# The two routing arms, mapped to a forced tier for bridge.query(force_tier=...).
# with_delegation = complex tier (orchestrator MAY delegate to the persona specialist);
# orchestrator_only = moderate tier (planner+worker, no specialist delegation).
ARMS = ("orchestrator_only", "with_delegation")


def _arm_to_tier(arm: str) -> str:
    return "complex" if arm == "with_delegation" else "moderate"


def _specialists_from_events(events: List[dict]) -> set:
    """The specialists that actually fired this turn, from the specialist_delegated events."""
    return {
        e.get("specialist")
        for e in events
        if isinstance(e, dict) and e.get("type") == "specialist_delegated" and e.get("specialist")
    }


def _compute_lift(baseline_score: float, delegated_score: float) -> float:
    """Lift = delegated arm minus orchestrator-only arm. Positive = delegation helped."""
    return round(delegated_score - baseline_score, 4)


def _aggregate_score(dims: Optional[dict]) -> float:
    """Mean of the judge's relevance/completeness/correctness dims, clamped to [0,1]. 0 if absent."""
    if not dims:
        return 0.0
    vals = []
    for k in ("relevance", "completeness", "correctness"):
        try:
            vals.append(max(0.0, min(1.0, float(dims[k]))))
        except (KeyError, TypeError, ValueError):
            continue
    return round(sum(vals) / len(vals), 4) if vals else 0.0


async def _default_score_fn(query: str, response: str) -> float:
    """Live scorer: the LLM judge's averaged dims. Requires ANTHROPIC_API_KEY."""
    from openeo_ai.evals.judge import score_response
    return _aggregate_score(await score_response(query, response))


async def _run_arm(bridge: Any, query: str, arm: str,
                   score_fn: Callable[[str, str], Awaitable[float]]) -> dict:
    """Run one query through one routing arm; capture routing + score."""
    from openeo_ai.evals.run_experiment import FakeWebSocket

    ws = FakeWebSocket()
    stop = asyncio.Event()
    sid = str(uuid.uuid4())
    timed_out = False
    # The multi-agent arm legitimately runs longer (planner + several sequential specialists).
    timeout = _TIMEOUT_COMPLEX if arm == "with_delegation" else _TIMEOUT_MODERATE
    try:
        await asyncio.wait_for(
            bridge.query(
                prompt=query,
                websocket=ws,
                session_id=sid,
                stop_event=stop,
                force_tier=_arm_to_tier(arm),
            ),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        timed_out = True
    finally:
        # Disconnect this arm's SDK client so its bundled subprocess doesn't orphan and
        # hang the harness — the bridge keeps main-session clients alive by default.
        try:
            await bridge.disconnect_session(sid)
        except Exception:
            pass
    text = ws.assistant_text()
    return {
        "arm": arm,
        "response_text": text,
        "tools": ws.tools_called(),
        "specialists": sorted(_specialists_from_events(ws.events)),
        "timed_out": timed_out,
        "score": await score_fn(query, text),
    }


def _append_jsonl(path: str, row: dict) -> None:
    """Append one completed row and flush immediately — so a later hang keeps every finished row."""
    with open(path, "a") as f:
        f.write(json.dumps(row, default=str) + "\n")
        f.flush()


def load_rows_jsonl(path: str) -> List[dict]:
    """Read whatever rows completed (usable even after a mid-run hang)."""
    rows: List[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    return rows


async def run_persona_ab(
    bridge: Any,
    score_fn: Optional[Callable[[str, str], Awaitable[float]]] = None,
    personas: Optional[List[Dict[str, str]]] = None,
    out_path: Optional[str] = None,
) -> List[dict]:
    """Run the A/B for every persona query through both arms; return per-persona lift rows.

    Each completed row is persisted to out_path (JSONL) and printed immediately, so a hang in a
    later turn still leaves every finished row on disk. Each row records `persona_fired` —
    whether the persona's OWN specialist actually ran in the delegation arm — so a null result
    isn't misread as "delegation didn't help" when the (LLM-driven) router never routed to it.
    """
    score_fn = score_fn or _default_score_fn
    personas = _capped_queries(personas or PERSONA_QUERIES)
    out_path = out_path or os.environ.get("JONA_PERSONA_AB_JSONL")
    rows: List[dict] = []
    try:
        for i, pq in enumerate(personas, 1):
            arms = {arm: await _run_arm(bridge, pq["query"], arm, score_fn) for arm in ARMS}
            delegated = arms["with_delegation"]
            row = {
                "persona": pq["persona"],
                "query": pq["query"],
                "baseline_score": arms["orchestrator_only"]["score"],
                "delegated_score": delegated["score"],
                "lift": _compute_lift(arms["orchestrator_only"]["score"], delegated["score"]),
                "persona_fired": pq["persona"] in delegated["specialists"],
                "delegated_specialists": delegated["specialists"],
                "timed_out": {a: arms[a]["timed_out"] for a in ARMS},
            }
            rows.append(row)
            if out_path:
                _append_jsonl(out_path, row)
            print(f"[{i}/{len(personas)}] {row['persona']:<13} fired={row['persona_fired']!s:<5} "
                  f"base={row['baseline_score']:.3f} deleg={row['delegated_score']:.3f} "
                  f"lift={row['lift']:+.3f} timed_out={row['timed_out']}", flush=True)
    finally:
        # Safety net: tear down any SDK clients still held so the process can exit cleanly.
        try:
            await bridge.disconnect_all()
        except Exception:
            pass
    return rows


def _capped_queries(personas: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Optionally cap the number of queries per persona for a cheaper bounded run."""
    if _MAX_PER_PERSONA <= 0:
        return personas
    seen: Dict[str, int] = {}
    out: List[Dict[str, str]] = []
    for pq in personas:
        n = seen.get(pq["persona"], 0)
        if n < _MAX_PER_PERSONA:
            out.append(pq)
            seen[pq["persona"]] = n + 1
    return out


def aggregate_by_persona(rows: List[dict]) -> List[dict]:
    """Roll per-query rows up to one verdict row per persona: mean lift over its queries, the
    fraction of queries where the persona's OWN specialist actually fired, and how many turns
    hit the timeout (truncated → unreliable). A verdict needs high fired-rate AND low timeouts."""
    by: Dict[str, List[dict]] = {}
    for r in rows:
        by.setdefault(r["persona"], []).append(r)
    out: List[dict] = []
    for persona, rs in by.items():
        n = len(rs)
        fired = sum(1 for r in rs if r["persona_fired"])
        timeouts = sum(1 for r in rs if any((r.get("timed_out") or {}).values()))
        out.append({
            "persona": persona,
            "n_queries": n,
            "fired_rate": round(fired / n, 3) if n else 0.0,
            "mean_baseline": round(sum(r["baseline_score"] for r in rs) / n, 3) if n else 0.0,
            "mean_delegated": round(sum(r["delegated_score"] for r in rs) / n, 3) if n else 0.0,
            "mean_lift": round(sum(r["lift"] for r in rs) / n, 3) if n else 0.0,
            "n_timeouts": timeouts,
        })
    return out


def format_lift_table(rows: List[dict]) -> str:
    """A human-readable per-query A3 lift table."""
    lines = ["persona        fired  baseline  delegated   lift",
             "-------------  -----  --------  ---------  -----"]
    for r in rows:
        lines.append(
            f"{r['persona']:<13}  {'yes' if r['persona_fired'] else 'no ':<5}  "
            f"{r['baseline_score']:>8.3f}  {r['delegated_score']:>9.3f}  {r['lift']:>+5.3f}"
        )
    return "\n".join(lines)


def format_verdict_table(agg: List[dict]) -> str:
    """The per-persona roster verdict: mean lift, fired-rate, timeout count."""
    lines = ["persona        n  fired_rate  mean_base  mean_deleg  mean_lift  timeouts",
             "-------------  -  ----------  ---------  ----------  ---------  --------"]
    for a in agg:
        lines.append(
            f"{a['persona']:<13}  {a['n_queries']}  {a['fired_rate']:>10.2f}  "
            f"{a['mean_baseline']:>9.3f}  {a['mean_delegated']:>10.3f}  "
            f"{a['mean_lift']:>+9.3f}  {a['n_timeouts']:>8}"
        )
    return "\n".join(lines)


def _print_report(rows: List[dict]) -> None:
    print("\n=== PER-QUERY ===")
    print(format_lift_table(rows))
    print("\n=== ROSTER VERDICT (per persona) ===")
    print(format_verdict_table(aggregate_by_persona(rows)))


async def _amain() -> None:
    from openeo_ai.sdk.claude_sdk_bridge import ClaudeSDKBridge
    from openeo_ai.sdk.client import OpenEOAIConfig

    out_path = os.environ.get("JONA_PERSONA_AB_JSONL", "/tmp/persona_ab_rows.jsonl")
    open(out_path, "w").close()  # start fresh so a partial read is only this run
    bridge = ClaudeSDKBridge(config=OpenEOAIConfig())
    try:
        rows = await run_persona_ab(bridge, out_path=out_path)
    finally:
        # Aggregate from disk so a mid-run hang still yields whatever completed.
        rows = load_rows_jsonl(out_path)
        _print_report(rows)
        print(f"\n({len(rows)} rows persisted to {out_path})")


if __name__ == "__main__":
    asyncio.run(_amain())
