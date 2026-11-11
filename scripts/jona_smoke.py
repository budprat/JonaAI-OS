# ABOUTME: Assertion-based smoke suite for the JonaAI agent — drives real chats
# ABOUTME: over /ws, correlates with the backend log + PID, and asserts per case.

import argparse
import asyncio
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid

import jwt
import websockets

WS_URL = "ws://localhost:8080/ws"
HEALTH = "http://localhost:8080/health"
SAVED_JOBS = "http://localhost:8080/saved-jobs"
LOG = "/tmp/jonaai-backend.log"

# Generic built-ins the agent must never use (the fabrication vectors).
FORBIDDEN = {
    "Bash", "BashOutput", "KillBash", "KillShell", "Write", "Edit",
    "MultiEdit", "NotebookEdit", "Task", "WebSearch", "WebFetch",
}
# Substrings that mark a real domain (openeo_*) tool.
DOMAIN_HINTS = (
    "openeo_", "spectral_", "viz_", "temporal_", "geoai_", "monitor_",
    "signal_", "result_", "export_", "fusion_", "quality_", "comparison_",
    "saved_", "discovery_", "strategy_", "goal_", "interpret",
)
# Tools that back a qualitative claim (interpretation gate).
INTERP_HINTS = ("interpret", "quality", "grade", "assess", "contextualiz", "classify")
QUALITATIVE = re.compile(
    r"\b(healthy|health|stress|severe|declin|anomal|degrad|poor|good|risk|drought|damage)\w*",
    re.I,
)


def _env(name: str) -> str:
    with open(os.path.join(os.getcwd(), ".env")) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith(f"export {name}="):
                v = line.split("=", 1)[1].strip()
                return v[1:-1] if v[:1] in "\"'" and v[-1:] == v[:1] else v
    raise SystemExit(f"{name} not in .env")


def _token(sub: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": sub, "email": f"{sub}@dev.local", "role": "authenticated",
         "aud": "authenticated", "iat": now, "exp": now + 3600},
        _SECRET, algorithm="HS256",
    )


_SECRET = _env("SUPABASE_JWT_SECRET")


def backend_pid():
    r = subprocess.run(["lsof", "-ti:8080", "-sTCP:LISTEN"],
                       capture_output=True, text=True)
    out = r.stdout.strip().splitlines()
    return out[0] if out else None


def wait_health(timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(HEALTH, timeout=3) as resp:
                if b'"status":"healthy"' in resp.read():
                    return True
        except Exception:
            pass
        time.sleep(1)
    return False


def saved_job_ids(token: str):
    req = urllib.request.Request(SAVED_JOBS, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    rows = data if isinstance(data, list) else data.get("saved_jobs") or data.get("jobs") or data.get("results") or []
    ids = []
    for r in rows:
        if isinstance(r, dict):
            ids.append(r.get("save_id") or r.get("id") or r.get("job_id"))
    return [i for i in ids if i]


async def run_query(prompt: str, token: str, timeout: float, answer_clarification=False) -> dict:
    res = {"session_id": None, "tools": [], "tool_calls": [], "tool_results": [],
           "viz": [], "viz_payloads": [], "text": "", "cost": None,
           "turns": None, "clarification": False, "error": None, "done": False,
           "elapsed": 0.0, "saved_ids": []}
    started = time.time()
    try:
        async with websockets.connect(WS_URL, max_size=None) as ws:
            async def recv():
                return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))

            res["session_id"] = (await recv()).get("session_id")
            await ws.send(json.dumps({"type": "authenticate", "token": token}))
            sent = False
            while (time.time() - started) < timeout:
                try:
                    ev = await recv()
                except asyncio.TimeoutError:
                    break
                t = ev.get("type", "?")
                if t == "ping":
                    await ws.send(json.dumps({"type": "pong"})); continue
                if not sent and (ev.get("user_id") or t in ("authenticated", "auth_result")):
                    if t == "auth_result" and not ev.get("authenticated"):
                        res["error"] = "auth_failed"; break
                    await ws.send(json.dumps({"type": "message", "content": prompt}))
                    sent = True; continue
                if t in ("text", "text_delta", "content", "assistant"):
                    res["text"] += ev.get("content") or ev.get("text") or ""
                elif t in ("tool_use", "tool", "tool_call", "tool_start"):
                    name = ev.get("tool") or ev.get("name") or ev.get("tool_name") or "?"
                    res["tools"].append(name)
                    res["tool_calls"].append(
                        {"name": name, "input": ev.get("input") or ev.get("tool_input") or {}}
                    )
                elif t == "tool_result":
                    res["tool_results"].append({
                        "name": ev.get("tool_name") or ev.get("tool") or "?",
                        "text": str(ev.get("result") or ev.get("content") or "")[:20000],
                    })
                elif t == "visualization":
                    viz = ev.get("visualization") or {}
                    res["viz"].append(viz.get("type", "?"))
                    res["viz_payloads"].append(viz)
                elif t == "clarification":
                    res["clarification"] = True
                    if not answer_clarification:
                        break
                    qs = ev.get("questions", [])
                    ans = {q.get("header", f"q{i}"): (q.get("options", [{}])[0].get("label", "yes"))
                           for i, q in enumerate(qs)}
                    await ws.send(json.dumps({"type": "clarification_response", "answers": ans}))
                elif t == "saved_job_created":
                    sid = ev.get("save_id")
                    if sid:
                        res["saved_ids"].append(sid)
                elif t == "usage_metrics":
                    u = ev.get("usage", {})
                    res["cost"], res["turns"] = u.get("total_cost_usd"), u.get("num_turns")
                elif t == "error":
                    res["error"] = ev.get("content"); break
                elif t in ("done", "complete", "end"):
                    res["done"] = True; break
    except Exception as e:  # noqa: BLE001
        res["error"] = f"{type(e).__name__}: {e}"
    res["elapsed"] = round(time.time() - started, 1)
    return res


def log_delta(offset: int):
    try:
        size = os.path.getsize(LOG)
        with open(LOG, "rb") as fh:
            fh.seek(offset)
            return fh.read().decode("utf-8", "replace"), size
    except FileNotFoundError:
        return "", offset


def evaluate(case: dict, res: dict, logtext: str, pid_ok: bool):
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    called = res["tools"]
    forbidden_hit = [t for t in called if t in FORBIDDEN]
    add("no_forbidden_tools", not forbidden_hit, f"used={forbidden_hit}")

    for sub in case.get("expect_tools", []):
        hit = [t for t in called if sub in t]
        add(f"tool~{sub}", bool(hit), f"matched={hit}")

    if case.get("expect_any_tool"):
        subs = case["expect_any_tool"]
        hit = [t for t in called if any(s in t for s in subs)]
        add(f"tool_any~{'|'.join(subs)}", bool(hit), f"matched={hit[:4]}")

    if case.get("expect_domain_tool"):
        dom = [t for t in called if any(h in t for h in DOMAIN_HINTS)]
        add("domain_tool_called", bool(dom), f"domain={dom[:6]}")

    for vt in case.get("expect_viz", []):
        add(f"viz~{vt}", vt in res["viz"], f"viz={res['viz']}")

    if case.get("expect_clarification"):
        add("asked_clarification", res["clarification"], "")

    if case.get("heuristic_interp"):
        q = QUALITATIVE.search(res["text"])
        interp = [t for t in called if any(h in t.lower() for h in INTERP_HINTS)]
        # gate passes if no qualitative claim OR an interp tool backed it
        add("interp_gate[heuristic]", (not q) or bool(interp),
            f"qualitative={'yes' if q else 'no'} interp_tools={interp}")

    if case.get("heuristic_nodata"):
        nd = re.search(r"no (cloud|data|imagery|scenes|results)|insufficient|empty|unavailable|not available",
                       res["text"], re.I)
        add("nodata_grace[heuristic]", bool(nd) and not res["error"],
            f"matched={'yes' if nd else 'no'} error={res['error']}")

    if case.get("forbid_delivery_leak"):
        leak = re.search(r"https?://|smtp|@[\w.-]+\.\w+", res["text"], re.I)
        add("no_delivery_target_leak[heuristic]", not leak, f"leak={bool(leak)}")

    if case.get("require_no_crash", True):
        add("backend_no_crash", pid_ok, "")

    # --- Output-content assertions: verify the answer/output is correct, not
    # just that a tool was called. ---
    text = res["text"]
    for sub in case.get("expect_text_contains", []):
        add(f"text_has~{sub}", sub.lower() in text.lower(), "")

    for spec in case.get("expect_value_in_range", []):
        vals = [float(x) for x in re.findall(spec["pattern"], text)]
        in_range = [v for v in vals if spec["min"] <= v <= spec["max"]]
        add(f"value_in_range[{spec['label']}]", bool(in_range),
            f"found={vals[:6]} range=[{spec['min']},{spec['max']}]")

    for spec in case.get("expect_tool_input", []):
        matches = [c for c in res["tool_calls"] if spec["tool"] in c["name"]]
        ok, detail = False, f"no call ~{spec['tool']}"
        for c in matches:
            v = c["input"].get(spec["key"])
            detail = f"{spec['tool']}.{spec['key']}={str(v)[:80]}"
            if "equals" in spec:
                ok = str(v).lower() == str(spec["equals"]).lower()
            elif "contains" in spec:
                ok = spec["contains"].lower() in str(v).lower()
            elif "bbox" in spec:
                try:
                    ok = [round(float(x), 3) for x in v] == [round(float(x), 3) for x in spec["bbox"]]
                except (TypeError, ValueError):
                    ok = False
            if ok:
                break
        add(f"tool_input[{spec['tool']}.{spec['key']}]", ok, detail)

    # Graph-process assertion: the named openEO process must appear in the run's
    # recorded tool events (inputs OR result payloads) — hard, non-heuristic.
    # Proves e.g. change detection ran as ONE difference-raster graph (merge_cubes),
    # not two snapshot jobs with prose subtraction.
    # NOTE: this matches a graph that was GENERATED (a generated graph carries the
    # process_id too), not necessarily executed. Pair it with expect_viz (a map
    # only renders from an executed raster) and expect_tools (threshold_classify
    # runs on the executed result) — as t2_dnbr does — to also assert execution.
    for pid in case.get("expect_graph_process", []):
        pat = re.compile(r'["\']process_id["\']\s*:\s*["\']' + re.escape(pid) + r'["\']')
        in_inputs = [c["name"] for c in res["tool_calls"]
                     if pat.search(json.dumps(c["input"], default=str))]
        in_results = [tr["name"] for tr in res["tool_results"] if pat.search(tr["text"])]
        add(f"graph_process~{pid}", bool(in_inputs or in_results),
            f"inputs={in_inputs[:3]} results={in_results[:3]}")

    # Chain handoff: a file path emitted by a producer tool's RESULT must appear
    # in a consumer tool's INPUT — proves the artifact was actually transferred
    # (e.g. openeo job download → earth_agent_analyze), not merely that both ran.
    if case.get("expect_chain_file_handoff"):
        spec = case["expect_chain_file_handoff"]
        pat = re.compile(spec["path_pattern"])
        produced = []
        for tr in res["tool_results"]:
            if any(p in tr["name"] for p in spec["producers"]):
                produced += pat.findall(tr["text"])
        produced = list(dict.fromkeys(produced))
        consumers = [c for c in res["tool_calls"] if spec["consumer"] in c["name"]]
        handed = [p for p in produced
                  if any(p in json.dumps(c["input"]) for c in consumers)]
        # A this-run archive copy is the same produced file (save_job copies it;
        # the save_id is embedded in the archive path the specialists hand over).
        handed += [f"archive:{sid}" for sid in res["saved_ids"]
                   if any(sid in json.dumps(c["input"]) for c in consumers)]
        add("chain_file_handoff", bool(handed),
            f"produced={len(produced)} saved={len(res['saved_ids'])} "
            f"consumer_calls={len(consumers)} handed={handed[:2]}")

    # tier is informational (routing is probabilistic) — recorded, never fails
    m = re.search(r"Tier=(\w+)", logtext)
    tier = m.group(1) if m else "?"
    denies = re.findall(r"DENY session=\S+ tool=(\S+)", logtext)

    hard = [c for c in checks if "[heuristic]" not in c["check"]]
    passed = all(c["ok"] for c in hard)
    return {
        "id": case["id"], "tier_group": case.get("tier"), "passed": passed,
        "tier_observed": tier, "tools": called, "viz": res["viz"],
        "denies": denies, "cost": res["cost"], "elapsed": res["elapsed"],
        "done": res["done"], "error": res["error"], "checks": checks,
        "text_head": res["text"][:200],
    }


SUITE = [
    dict(id="t1_knowledge", tier="1",
         prompt="What is NDVI, and when should I use EVI instead? Answer in 3 sentences.",
         expect_domain_tool=False, timeout=120),
    dict(id="t1_discovery", tier="1",
         prompt="Which satellite collections and spectral indices can you work with? "
                "List them concisely — do not run any analysis.",
         expect_tools=["list_collections", "spectral_index"], expect_domain_tool=True,
         # content: the listed catalog must include a collection the backend really serves
         expect_text_contains=["sentinel-2"], timeout=220),
    dict(id="t1_ndvi", tier="1", user="userA",
         prompt="Compute NDVI for the area west=75.0 south=30.5 east=75.2 north=30.7 "
                "for June 2024. Show the map and report the mean NDVI.",
         expect_domain_tool=True, expect_viz=["map"], heuristic_interp=True,
         # content: a reported NDVI value must be physically valid ([-1, 1])
         expect_value_in_range=[
             {"label": "ndvi", "pattern": r"(?i)ndvi[^0-9=\-]{0,15}(-?\d\.\d+)",
              "min": -1.0, "max": 1.0},
         ], timeout=340),
    dict(id="t1_clarify", tier="1",
         prompt="Analyze my farm.",
         expect_clarification=True, timeout=240),
    dict(id="t2_change", tier="2",
         prompt="Compare vegetation health between June 2020 and June 2024 for "
                "west=75.0 south=30.5 east=75.2 north=30.7. Rate the severity of any "
                "change and suggest likely causes.",
         expect_domain_tool=True, heuristic_interp=True, timeout=400),
    dict(id="t2_dnbr", tier="2",
         # Verbatim prompt from the 2026-07-24 live fabrication defect — place name
         # (not bbox) on purpose so geocoding is exercised end-to-end too.
         prompt="Map the burn severity of the Palisades Fire in Pacific Palisades, "
                "Los Angeles. Compare NBR before the fire (2024-12-15 to 2025-01-05) "
                "against after (2025-01-20 to 2025-02-15) and show me the change "
                "(dNBR) with a burn-severity map.",
         # content: the change must be a REAL per-pixel difference — either an
         # in-graph merge_cubes OR the result_difference tool (subtracts two
         # executed rasters pixel-by-pixel; verified as the live path 2026-07-24) —
         # NOT scalar-mean subtraction. The map must be delivered and class areas
         # must come from threshold_classify.
         expect_any_tool=["result_difference", "merge_cubes"], expect_viz=["map"],
         expect_tools=["threshold_classify"], expect_domain_tool=True, timeout=600),
    dict(id="t2_monitor", tier="2",
         prompt="Set up weekly monitoring of NDVI for west=75.0 south=30.5 east=75.2 "
                "north=30.7 and alert me if vegetation health drops.",
         expect_tools=["monitor_"], forbid_delivery_leak=True,
         # content: the Watch must be created with the requested cadence, over the
         # requested area (verifies the request was honored, not just that a tool ran)
         expect_tool_input=[
             {"tool": "monitor_create_watch", "key": "cadence", "equals": "weekly"},
             {"tool": "monitor_create_asset", "key": "bbox", "bbox": [75.0, 30.5, 75.2, 30.7]},
         ], timeout=220),
    dict(id="t2_ea_handoff", tier="2",
         prompt="Run an openEO job computing NDVI for west=75.0 south=30.5 east=75.2 "
                "north=30.7 for June 2024, download the job result file, and then "
                "analyze that downloaded file with the earth agent. Summarize what "
                "the earth agent found.",
         expect_tools=["earth_agent"], expect_domain_tool=True,
         # the downloaded artifact itself must reach the earth agent
         expect_chain_file_handoff={
             "producers": ["get_results", "load_result", "result_inspect"],
             "consumer": "earth_agent",
             "path_pattern": r"/\S+?/results/\S+?\.(?:tiff?|nc|json)",
         },
         timeout=600),
    dict(id="x_nodata", tier="X",
         prompt="Compute NDVI for west=-140.0 south=0.0 east=-139.9 north=0.1 (open "
                "Pacific ocean) for 2024-01-01 to 2024-01-02.",
         heuristic_nodata=True, timeout=260),
    dict(id="x_oom", tier="X",
         prompt="Compute monthly NDVI for all of India for every month from 2019 to "
                "2024 at full resolution.",
         require_no_crash=True, timeout=320),
]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--out", default="/tmp/jona_smoke_results.json")
    args = ap.parse_args()
    only = set(filter(None, args.only.split(",")))
    cases = [c for c in SUITE if not only or c["id"] in only]

    tokens = {"userA": _token(f"userA-{uuid.uuid4()}"), "userB": _token(f"userB-{uuid.uuid4()}")}
    results = []
    print(f"Running {len(cases)} case(s)\n", flush=True)

    for c in cases:
        if not wait_health(60):
            results.append({"id": c["id"], "passed": False, "error": "backend_unhealthy"})
            continue
        pid0, off0 = backend_pid(), (os.path.getsize(LOG) if os.path.exists(LOG) else 0)
        print(f"▶ {c['id']} …", flush=True)
        res = await run_query(c["prompt"], tokens.get(c.get("user", "userA")),
                              c["timeout"], answer_clarification=False)
        wait_health(30)
        pid1 = backend_pid()
        logtext, _ = log_delta(off0)
        pid_ok = bool(pid1) and pid0 == pid1
        r = evaluate(c, res, logtext, pid_ok)
        r["pid_stable"] = pid_ok
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"  {mark}  tier={r['tier_observed']} tools={len(r['tools'])} "
              f"viz={r['viz']} cost={r['cost']} {r['elapsed']}s crash={'no' if pid_ok else 'YES'}",
              flush=True)
        for ch in r["checks"]:
            print(f"      {'✓' if ch['ok'] else '✗'} {ch['check']} {ch['detail']}", flush=True)
        with open(args.out, "w") as fh:
            json.dump(results, fh, indent=2, default=str)

    # tenancy (REST) — after userA's t1_ndvi auto-saves
    if any(r.get("id") == "t1_ndvi" for r in results):
        try:
            a, b = saved_job_ids(tokens["userA"]), saved_job_ids(tokens["userB"])
            overlap = set(a) & set(b)
            ok = (len(a) > 0) and not overlap
            results.append({"id": "x_tenancy", "tier": "X", "passed": ok,
                            "detail": f"A_saves={len(a)} B_saves={len(b)} overlap={list(overlap)}"})
            print(f"\n▶ x_tenancy  {'PASS' if ok else 'FAIL'}  A={len(a)} B={len(b)} overlap={list(overlap)}", flush=True)
        except Exception as e:  # noqa: BLE001
            results.append({"id": "x_tenancy", "passed": False, "error": str(e)})
            print(f"\n▶ x_tenancy  ERROR {e}", flush=True)

    with open(args.out, "w") as fh:
        json.dump(results, fh, indent=2, default=str)

    npass = sum(1 for r in results if r.get("passed"))
    print(f"\n{'='*60}\nSMOKE SUMMARY: {npass}/{len(results)} passed\n{'='*60}", flush=True)
    for r in results:
        print(f"  {'PASS' if r.get('passed') else 'FAIL'}  {r['id']:<14} "
              f"tier={r.get('tier_observed','-')} crash={'YES' if r.get('pid_stable') is False else 'no'} "
              f"{r.get('error') or ''}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
