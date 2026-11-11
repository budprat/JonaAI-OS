# ABOUTME: CI gate — fail the build if eval scores regress from the baseline (Phase I).

"""
Regression gate.

Compares aggregate scores from a baseline experiment (e.g. `production`)
against the current PR experiment, and exits non-zero if any dimension
regressed by more than the tolerance threshold.

Usage:
    python -m openeo_ai.evals.assert_no_regression \\
        --baseline production \\
        --current pr-123 \\
        --threshold 0.05

The threshold is the maximum allowable regression as an absolute score
delta. E.g. threshold=0.05 means `judge-relevance` on `pr-123` must be
within 0.05 of the production mean or the gate fails.

Dimensions checked (must exist on both experiments):
    - judge-relevance
    - judge-completeness
    - judge-correctness
    - exp-execution-ms       (comparison inverted — higher is worse)

How it fetches data:
    Uses the Langfuse REST API via requests. `langfuse-cli` could also
    be used, but direct REST is simpler for a single purpose and avoids
    a Node dep in the Python CI matrix.
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import statistics
import sys
import time
from typing import Any, Optional

import requests

logging.basicConfig(level=logging.INFO, format="[regression] %(message)s")
logger = logging.getLogger(__name__)


# Dimensions where higher scores = better quality. Regression = baseline - current > threshold.
POSITIVE_DIMENSIONS = (
    "judge-relevance",
    "judge-completeness",
    "judge-correctness",
    "exp-has-visualization",
    "user-thumbs",
    "implicit-load-on-map",
)

# Dimensions where lower is better. Regression = current - baseline > threshold (scaled).
NEGATIVE_DIMENSIONS = ("exp-execution-ms", "exp-errored", "implicit-tool-errors",
                       "exp-ungrounded-numbers")


def _langfuse_auth_header() -> dict:
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    if not pk or not sk:
        logger.error("LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY not set")
        sys.exit(2)
    token = base64.b64encode(f"{pk}:{sk}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _langfuse_host() -> str:
    return (
        os.environ.get("LANGFUSE_HOST")
        or os.environ.get("LANGFUSE_BASE_URL")
        or "https://cloud.langfuse.com"
    )


def _fetch_scores_for_tag(tag: str, limit: int = 500) -> tuple[int, dict[str, list[float]]]:
    """Fetch all trace scores for traces tagged with `tag`.

    Returns (trace_count, scores_by_name). The count is reported separately
    because Langfuse ingests scores asynchronously: traces can exist while
    their scores are not queryable yet — callers must not confuse that state
    with "no traces".
    """
    base = _langfuse_host()
    headers = _langfuse_auth_header()

    # 1. List traces with the tag (paginated)
    traces: list[str] = []
    page = 1
    while True:
        url = f"{base}/api/public/traces?tags={tag}&limit=100&page={page}"
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Failed to fetch traces: %s", exc)
            return 0, {}
        data = r.json()
        batch = data.get("data", [])
        if not batch:
            break
        traces.extend(t["id"] for t in batch if t.get("id"))
        if len(batch) < 100 or len(traces) >= limit:
            break
        page += 1

    logger.info("  tag=%s: %d traces", tag, len(traces))
    if not traces:
        return 0, {}

    # 2. For each trace, fetch its scores
    scores_by_name: dict[str, list[float]] = {}
    for trace_id in traces[:limit]:
        url = f"{base}/api/public/scores?traceId={trace_id}&limit=100"
        try:
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
        except requests.RequestException:
            continue
        for s in r.json().get("data", []):
            name = s.get("name")
            val = s.get("value")
            if name is None or val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            scores_by_name.setdefault(name, []).append(fval)

    return len(traces), scores_by_name


def _mean_or_none(values: list[float]) -> Optional[float]:
    if not values:
        return None
    return statistics.mean(values)


def compare(
    baseline_tag: str,
    current_tag: str,
    threshold: float,
    allow_missing_baseline: bool = False,
    score_wait_attempts: int = 3,
    score_wait_seconds: float = 60,
) -> dict[str, Any]:
    """Compare scores between two experiment tags. Returns a result dict.

    Every return includes a "regressions" list — main() iterates it, and the
    early-exit paths crashed CI with KeyError before that was guaranteed.
    """
    logger.info("Fetching baseline scores (tag=%s)...", baseline_tag)
    _b_traces, baseline = _fetch_scores_for_tag(baseline_tag)
    logger.info("Fetching current scores (tag=%s)...", current_tag)
    c_traces, current = _fetch_scores_for_tag(current_tag)

    # Langfuse ingests scores asynchronously: right after an experiment the
    # traces exist but their scores may not be queryable yet (observed live on
    # the first real gate run). Wait bounded, then fail closed honestly.
    attempt = 1
    while c_traces and not current and attempt < score_wait_attempts:
        logger.warning(
            "tag=%s has %d traces but no scores yet — Langfuse score ingestion "
            "lag; retrying in %ss (attempt %d/%d)",
            current_tag, c_traces, score_wait_seconds, attempt, score_wait_attempts,
        )
        time.sleep(score_wait_seconds)
        c_traces, current = _fetch_scores_for_tag(current_tag)
        attempt += 1

    if not baseline:
        logger.warning(
            "No baseline traces found for tag=%s — failing closed unless "
            "--allow-missing-baseline is set",
            baseline_tag,
        )
        return {
            "passed": bool(allow_missing_baseline),
            "reason": "no-baseline",
            "regressions": [],
            "details": {},
        }
    if not current:
        if c_traces:
            logger.error(
                "tag=%s has %d traces but scores never became queryable — "
                "failing closed (score ingestion incomplete?)",
                current_tag, c_traces,
            )
            return {"passed": False, "reason": "no-current-scores",
                    "regressions": [], "details": {}}
        logger.error("No traces for current tag=%s — nothing to compare", current_tag)
        return {"passed": False, "reason": "no-current-traces",
                "regressions": [], "details": {}}

    details: dict[str, Any] = {}
    regressions: list[str] = []

    for dim in POSITIVE_DIMENSIONS:
        b_mean = _mean_or_none(baseline.get(dim, []))
        c_mean = _mean_or_none(current.get(dim, []))
        if b_mean is None or c_mean is None:
            details[dim] = {"baseline": b_mean, "current": c_mean, "status": "skip"}
            continue
        delta = c_mean - b_mean
        regressed = delta < -threshold
        details[dim] = {
            "baseline": round(b_mean, 4),
            "current": round(c_mean, 4),
            "delta": round(delta, 4),
            "status": "REGRESSED" if regressed else "ok",
        }
        if regressed:
            regressions.append(f"{dim}: {b_mean:.3f} → {c_mean:.3f} (Δ{delta:+.3f})")

    for dim in NEGATIVE_DIMENSIONS:
        b_mean = _mean_or_none(baseline.get(dim, []))
        c_mean = _mean_or_none(current.get(dim, []))
        if b_mean is None or c_mean is None:
            details[dim] = {"baseline": b_mean, "current": c_mean, "status": "skip"}
            continue
        # Lower is better — regression when current > baseline + tolerance
        # Use relative tolerance for exp-execution-ms since values vary widely
        tolerance = threshold * (b_mean if dim == "exp-execution-ms" else 1.0)
        delta = c_mean - b_mean
        regressed = delta > tolerance
        details[dim] = {
            "baseline": round(b_mean, 4),
            "current": round(c_mean, 4),
            "delta": round(delta, 4),
            "status": "REGRESSED" if regressed else "ok",
        }
        if regressed:
            regressions.append(f"{dim}: {b_mean:.3f} → {c_mean:.3f} (Δ{delta:+.3f})")

    passed = len(regressions) == 0
    return {
        "passed": passed,
        "regressions": regressions,
        "details": details,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True, help="Baseline experiment tag (e.g. 'production')")
    parser.add_argument("--current", required=True, help="Current experiment tag (e.g. 'pr-123')")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Maximum allowable score regression (default 0.05)",
    )
    parser.add_argument(
        "--allow-missing-baseline",
        action="store_true",
        default=False,
        help="Pass instead of fail when the baseline tag has no traces (bootstrap only)",
    )
    args = parser.parse_args()

    result = compare(args.baseline, args.current, args.threshold,
                     allow_missing_baseline=args.allow_missing_baseline)

    logger.info("")
    logger.info("Regression comparison (%s vs %s, threshold=%s):", args.baseline, args.current, args.threshold)
    for dim, info in result["details"].items():
        status = info.get("status", "?")
        b = info.get("baseline")
        c = info.get("current")
        d = info.get("delta")
        if status == "skip":
            logger.info("  %-30s skip (baseline=%s current=%s)", dim, b, c)
        else:
            mark = "❌" if status == "REGRESSED" else "✓"
            logger.info("  %s %-30s baseline=%.3f current=%.3f Δ%+.3f", mark, dim, b, c, d)

    if not result["passed"]:
        logger.error("")
        if result.get("reason"):
            logger.error("Gate failed closed: %s", result["reason"])
        if result.get("regressions"):
            logger.error("REGRESSED dimensions:")
            for r in result["regressions"]:
                logger.error("  - %s", r)
        sys.exit(1)

    logger.info("")
    logger.info("✓ No regressions detected")
    sys.exit(0)


if __name__ == "__main__":
    main()
