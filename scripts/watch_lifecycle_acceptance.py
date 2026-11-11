# ABOUTME: Acceptance — proves a cadence watch is created LIVE (next_run_at
# ABOUTME: set, alert policy stored) and the worker due-query materializes it.
#
# Usage: watch_lifecycle_acceptance.py [--sub UUID] [--base URL]
# Exit 0 iff the persisted row is live AND enqueue materializes an observation.

import argparse
import os
import sys
import uuid

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jona_chat import _mint_token  # noqa: E402

NDVI_GRAPH = {"load": {"process_id": "load_collection", "arguments": {"id": "sentinel-2-l2a"}}}
POLICY = {
    "metric": "NDVI", "direction": "drop", "threshold": 0.1,
    "alert_targets": [{"channel": "webhook", "target": "https://example.test/hook"}],
}


def _check(name, ok, detail=""):
    print(("  PASS " if ok else "  FAIL ") + name + (f" — {detail}" if detail else ""))
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sub", default="98b1beb8-0580-49d5-8bfe-803b3ae2c3ad")
    ap.add_argument("--base", default="http://127.0.0.1:8080")
    args = ap.parse_args()

    h = {"Authorization": f"Bearer {_mint_token(args.sub)}"}
    ok = True
    with httpx.Client(base_url=args.base, headers=h, timeout=30) as c:
        asset = c.post("/api/assets", json={
            "name": f"lifecycle-check {uuid.uuid4().hex[:8]}", "bbox": [-119.9, 36.0, -119.7, 36.2]}).json()
        watch = c.post("/api/watches", json={
            "asset_id": asset["id"], "name": "Weekly NDVI", "process_graph": NDVI_GRAPH,
            "cadence": "P1W", "quality_policy": POLICY}).json()
        wid = watch["id"]

        row = c.get(f"/api/watches/{wid}").json()
        ok &= _check("watch is scheduled (next_run_at set)", row.get("next_run_at") is not None,
                     f"next_run_at={row.get('next_run_at')}")
        ok &= _check("trigger_type == scheduled", row.get("trigger_type") == "scheduled",
                     f"trigger_type={row.get('trigger_type')}")
        qp = row.get("quality_policy") or {}
        ok &= _check("alert policy stored (threshold + targets)",
                     qp.get("threshold") == 0.1 and bool(qp.get("alert_targets")), f"quality_policy={qp}")

        enq = c.post("/api/watches/due/enqueue").json()
        mine = [o for o in enq.get("observations", []) if o.get("watch_id") == wid]
        ok &= _check("due-query materializes an observation", len(mine) >= 1,
                     f"enqueued {enq.get('count')} total, {len(mine)} for this watch")

        obs = c.get("/api/observations", params={"watch_id": wid}).json()
        ok &= _check("observation persisted for the watch", (obs.get("count") or 0) >= 1,
                     f"count={obs.get('count')}")

    print(f"\nWATCH_LIFECYCLE_ACCEPTANCE: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
