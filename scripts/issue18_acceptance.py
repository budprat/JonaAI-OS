# ABOUTME: Live acceptance gate for the curated core toolset — 3 runs of the reference prompt
# ABOUTME: must show zero ToolSearch, a real job-creating call, and a map event.
#
# Usage: issue18_acceptance.py [--runs N] [--timeout S] [--sub UUID]
# Exit 0 = all runs pass all three assertions; exit 1 otherwise.

import argparse
import asyncio
import json
import os
import sys
import time
import uuid

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jona_chat import WS_URL, _mint_token  # noqa: E402

PROMPT = "Show me NDWI water extent for Lake Oroville California for June 2025"
JOB_TOOLS = {"openeo_create_job", "execute_workflow", "openeo_start_job"}


async def _one_run(sub: str, timeout: float) -> dict:
    token = _mint_token(sub)
    tools, viz_types = [], []
    async with websockets.connect(WS_URL, max_size=None) as ws:
        async def recv():
            return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))

        await recv()  # connect banner
        await ws.send(json.dumps({"type": "authenticate", "token": token}))
        while True:
            ev = await recv()
            if ev.get("type") == "ping":
                await ws.send(json.dumps({"type": "pong"}))
                continue
            if ev.get("type") == "auth_result" and not ev.get("authenticated"):
                raise SystemExit(f"auth failed: {ev.get('error')}")
            if ev.get("user_id") or ev.get("type") in ("authenticated", "auth_success", "auth_result"):
                break

        await ws.send(json.dumps({"type": "message", "content": PROMPT}))
        started = time.time()
        while (time.time() - started) < timeout:
            try:
                ev = await recv()
            except asyncio.TimeoutError:
                break
            etype = ev.get("type", "?")
            if etype == "ping":
                await ws.send(json.dumps({"type": "pong"}))
            elif etype in ("tool_use", "tool", "tool_call", "tool_start"):
                tools.append(ev.get("tool") or ev.get("name") or ev.get("tool_name") or "?")
            elif etype == "visualization":
                viz_types.append((ev.get("visualization") or {}).get("type", "?"))
            elif etype in ("done", "complete", "end", "error"):
                break
    return {"tools": tools, "viz_types": viz_types}


def _judge(run: dict) -> list:
    failures = []
    n_search = sum(1 for t in run["tools"] if t == "ToolSearch")
    if n_search:
        failures.append(f"ToolSearch called {n_search}x (must be 0 — tools should be eager)")
    if not (set(run["tools"]) & JOB_TOOLS):
        failures.append(f"no job-creating tool called (need one of {sorted(JOB_TOOLS)})")
    if "map" not in run["viz_types"]:
        failures.append(f"no map visualization event (saw: {run['viz_types'] or 'none'})")
    return failures


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--sub", default=None, help="JWT sub (default: random dev id)")
    args = ap.parse_args()

    sub = args.sub or f"{uuid.uuid4()}"
    all_ok = True
    for i in range(1, args.runs + 1):
        run = await _one_run(sub, args.timeout)
        failures = _judge(run)
        status = "PASS" if not failures else "FAIL"
        all_ok = all_ok and not failures
        print(f"run {i}/{args.runs}: {status}")
        print(f"  tools ({len(run['tools'])}): {run['tools']}")
        print(f"  viz: {run['viz_types']}")
        for f in failures:
            print(f"  ✗ {f}")
    print(f"\nISSUE18_ACCEPTANCE: {'PASS' if all_ok else 'FAIL'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
