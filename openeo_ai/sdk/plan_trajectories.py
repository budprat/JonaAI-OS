# ABOUTME: Retrieval-augmented planning store — persist successful query plans
# ABOUTME: and retrieve the k most similar past plans to seed the planner.

"""Learn / retrieve / render helpers for plan-router.

Every function is fail-open: any storage or parsing error returns a safe empty
value and never raises into a live turn. Retrieval is strictly ``user_id``-scoped,
and the rendered seed block never includes step arguments (bboxes, dates, place
names) — only the past query text and its ordered tool sequence.
"""

import difflib
import re
import sys
from typing import Any, Dict, List

_MIN_SCORE = 0.35
_MAX_CORPUS = 200
_SEED_CHAR_CAP = 1200
_QUERY_TRUNC = 120

_WORD_RE = re.compile(r"[a-z0-9]+")


def _tool_sequence_from_plan(plan_json: Dict[str, Any]) -> List[str]:
    seq: List[str] = []
    for step in (plan_json.get("steps") or []):
        if isinstance(step, dict):
            tool = step.get("tool")
            if tool:
                seq.append(str(tool))
    return seq


def save_trajectory(user_id: Any, query_text: str, tier: str, plan_json: Dict[str, Any],
                    succeeded: bool = True) -> bool:
    """Persist a successfully executed plan. Returns True on success, False on
    any error (never raises). Skips failed runs and trivial plans (<2 tool steps)
    — only genuine, non-trivial successes become exemplars."""
    try:
        if not succeeded:
            return False  # never learn from a failed/empty execution
        if not user_id or not query_text or not isinstance(plan_json, dict):
            return False
        tool_sequence = _tool_sequence_from_plan(plan_json)
        if len(tool_sequence) < 2:
            return False  # 0/1-step plans add noise, never seed from them
        from ..storage import job_archive
        from ..storage.models import PlanTrajectory
        session = job_archive._get_session()
        try:
            session.add(PlanTrajectory(
                user_id=str(user_id),
                query_text=str(query_text),
                tier=str(tier or ""),
                plan_json=plan_json,
                tool_sequence=tool_sequence,
                step_count=len(tool_sequence),
                succeeded=True,
                source="execute_workflow",
            ))
            session.commit()
            return True
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 — must never break a turn
        print(f"[plan-router] save_trajectory failed: {e}", file=sys.stderr)
        return False


def _normalize(text: str) -> str:
    return " ".join(_WORD_RE.findall(text.lower()))


def _tokens(text: str) -> set:
    return set(_WORD_RE.findall(text.lower()))


def _similarity(a: str, b: str) -> float:
    """Deterministic, stdlib-only similarity: token-set Jaccard averaged with
    difflib sequence ratio. (Upgrade path: text embeddings later.)"""
    ta, tb = _tokens(a), _tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if (ta and tb) else 0.0
    ratio = difflib.SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()
    return (jaccard + ratio) / 2.0


def retrieve_similar(user_id: Any, query_text: str, k: int = 3) -> List[Dict[str, Any]]:
    """The ``k`` most similar past successful trajectories for this user
    (score >= 0.35). Fail-open: any error returns ``[]``."""
    try:
        if not user_id or not query_text:
            return []
        from sqlalchemy import desc, select
        from ..storage import job_archive
        from ..storage.models import PlanTrajectory
        session = job_archive._get_session()
        try:
            rows = session.execute(
                select(PlanTrajectory)
                .where(PlanTrajectory.user_id == str(user_id))
                .where(PlanTrajectory.succeeded.is_(True))
                .order_by(desc(PlanTrajectory.created_at))
                .limit(_MAX_CORPUS)
            ).scalars().all()
        finally:
            session.close()
        scored: List[Dict[str, Any]] = []
        for row in rows:
            score = _similarity(query_text, row.query_text or "")
            if score >= _MIN_SCORE:
                d = row.to_dict()
                d["_score"] = score
                scored.append(d)
        scored.sort(key=lambda d: d["_score"], reverse=True)
        # Dedup by tool_sequence so re-running the same analysis can't crowd out
        # the k budget with near-identical seeds.
        deduped: List[Dict[str, Any]] = []
        seen: set = set()
        for d in scored:
            key = tuple(d.get("tool_sequence") or [])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(d)
        return deduped[:k]
    except Exception as e:  # noqa: BLE001
        print(f"[plan-router] retrieve_similar failed: {e}", file=sys.stderr)
        return []


def filter_valid(trajectories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop trajectories whose tool_sequence references a tool absent from the
    current TOOL_DEFINITIONS — a renamed/removed tool would misguide the planner."""
    try:
        from .client import TOOL_DEFINITIONS  # lazy: circular-import guard
        valid = {t["name"] for t in TOOL_DEFINITIONS}
    except Exception:
        return list(trajectories)  # can't validate → don't silently drop everything
    out: List[Dict[str, Any]] = []
    for t in trajectories:
        seq = t.get("tool_sequence") or []
        if all(name in valid for name in seq):
            out.append(t)
    return out


def render_plan_seeds(trajectories: List[Dict[str, Any]]) -> str:
    """Compact, injection-safe seed block: ONLY the past query text (truncated,
    single-line, backtick-stripped) and the ordered tool sequence — NEVER step
    arguments. Capped at ~1200 chars."""
    if not trajectories:
        return ""
    lines = ["PAST SUCCESSFUL PLANS for similar queries (adapt, don't copy blindly):"]
    for t in trajectories:
        # Neutralize injection: drop backticks and collapse EVERY unicode
        # whitespace/line-separator (incl. U+2028/U+2029/NEL/VT/FF) to spaces so
        # the stored query can't forge a second structural line. Sanitize before
        # truncate.
        raw = str(t.get("query_text") or "").replace("`", " ")
        q = " ".join(raw.split())[:_QUERY_TRUNC]
        seq = t.get("tool_sequence") or []
        seq_str = " -> ".join(str(s) for s in seq)
        lines.append(f'- "{q}" ({t.get("step_count", len(seq))} steps): {seq_str}')
    return "\n".join(lines)[:_SEED_CHAR_CAP]
