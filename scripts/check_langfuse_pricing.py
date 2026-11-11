#!/usr/bin/env python3
"""Check whether Langfuse has pricing configured for all models JonaAI uses.

Scans recent generation observations in Langfuse and reports any where
`totalCost` is 0 or missing despite having `outputTokens > 0`. That's the
signal that Langfuse doesn't know the model's price and the Cost dashboard
will under-report spend until pricing is added in Settings → Models.

Usage:
    source .env
    python scripts/check_langfuse_pricing.py              # last 500 generations
    python scripts/check_langfuse_pricing.py --limit 2000

Exit code 0 if all generations are priced, 1 if any are missing pricing.
Designed to be run on-demand or in a nightly cron as a cheap cost-visibility
sanity check (takes ~5 seconds, one REST call).
"""
from __future__ import annotations

import argparse
import base64
import os
import sys
from collections import Counter, defaultdict

import requests


def _auth_headers() -> dict[str, str]:
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        print("ERROR: LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set", file=sys.stderr)
        sys.exit(2)
    token = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _base_url() -> str:
    return (
        os.environ.get("LANGFUSE_HOST")
        or os.environ.get("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    )


def _fetch_generations(limit: int) -> list[dict]:
    """Fetch recent generation observations from the Langfuse public API.

    Uses `/api/public/observations?type=GENERATION` which is the stable v1
    endpoint. Each response is paginated at up to 100 per page.
    """
    base = _base_url()
    headers = _auth_headers()
    url = f"{base}/api/public/observations"
    results: list[dict] = []
    page = 1
    per_page = min(100, limit)

    while len(results) < limit:
        params = {"type": "GENERATION", "limit": per_page, "page": page}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"ERROR: fetch failed: {exc}", file=sys.stderr)
            sys.exit(2)
        batch = r.json().get("data", [])
        if not batch:
            break
        results.extend(batch)
        if len(batch) < per_page:
            break
        page += 1

    return results[:limit]


def _extract_tokens_and_cost(obs: dict) -> tuple[int, float | None]:
    """Return (output_tokens, total_cost) from an observation.

    Langfuse response shapes observed in practice (v1 public API, 2026-04):
      - Tokens: `usageDetails.output` (primary), `usage.output`,
        `completionTokens` (flat), or `usage.outputTokens`
      - Cost:   `calculatedTotalCost` (primary), `costDetails.total`,
        `totalCost`, or `totalPrice`

    We check them in priority order and return the first non-null value.
    """
    # ── Tokens ──
    output_tokens = 0
    for container, key in (
        (obs.get("usageDetails") or {}, "output"),
        (obs.get("usage") or {}, "output"),
        (obs.get("usage") or {}, "outputTokens"),
        (obs.get("usage") or {}, "completionTokens"),
        (obs, "completionTokens"),
    ):
        raw = container.get(key) if isinstance(container, dict) else None
        if raw:
            try:
                output_tokens = int(raw)
                break
            except (TypeError, ValueError):
                pass

    # ── Cost ──
    total_cost: float | None = None
    for container, key in (
        (obs, "calculatedTotalCost"),
        (obs.get("costDetails") or {}, "total"),
        (obs, "totalCost"),
        (obs, "totalPrice"),
    ):
        raw = container.get(key) if isinstance(container, dict) else None
        if raw is not None:
            try:
                total_cost = float(raw)
                break
            except (TypeError, ValueError):
                pass

    return output_tokens, total_cost


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Max generations to scan (default 500, max 2000)",
    )
    args = parser.parse_args()
    limit = min(max(args.limit, 1), 2000)

    print(f"Scanning up to {limit} recent generations from {_base_url()}...")
    gens = _fetch_generations(limit)
    print(f"  fetched {len(gens)} generations")

    if not gens:
        print("No generations found — nothing to check. Run a few queries first.")
        return 0

    # Group by model. For each model, count total generations and those with
    # output_tokens > 0 but total_cost == 0 or None (= missing pricing).
    total_by_model: Counter[str] = Counter()
    unpriced_by_model: Counter[str] = Counter()
    unpriced_examples: dict[str, str] = {}

    for obs in gens:
        model = obs.get("model") or "unknown"
        total_by_model[model] += 1
        output_tokens, total_cost = _extract_tokens_and_cost(obs)
        if output_tokens > 0 and (total_cost is None or total_cost == 0):
            unpriced_by_model[model] += 1
            if model not in unpriced_examples:
                unpriced_examples[model] = obs.get("id", "?")

    print()
    print("=" * 70)
    print(f"{'model':<45} {'total':>8} {'unpriced':>10}")
    print("-" * 70)
    any_unpriced = False
    for model, count in sorted(total_by_model.items(), key=lambda x: -x[1]):
        unp = unpriced_by_model.get(model, 0)
        status = ""
        if unp > 0:
            any_unpriced = True
            status = "  ⚠ MISSING PRICING"
        print(f"{model:<45} {count:>8} {unp:>10}{status}")
    print("=" * 70)

    if any_unpriced:
        print()
        print("⚠️  One or more models are missing pricing in Langfuse.")
        print()
        print("Fix: Langfuse UI → Settings → Models → Add a custom price for each")
        print("flagged model. Use Anthropic's public rates:")
        print("  https://www.anthropic.com/pricing#anthropic-api")
        print()
        print("Example unpriced generation IDs:")
        for model, obs_id in unpriced_examples.items():
            print(f"  {model}: {obs_id}")
        return 1

    print()
    print("✓ All scanned generations have pricing attached.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
