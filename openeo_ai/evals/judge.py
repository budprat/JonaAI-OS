# ABOUTME: LLM-as-judge async scorer for assistant responses (Phase F).

"""
LLM-as-judge for JonaAI response quality scoring.

Runs asynchronously after each user query to rate the assistant's response
on three dimensions: relevance, completeness, correctness. Scores are
attached to the original Langfuse trace so they appear alongside user
feedback, tool metrics, and quality auto-scores in the trace UI.

The judge uses Claude Haiku (cheap + fast) via the direct Anthropic SDK.
The judge prompt is fetched from Langfuse prompt management (stored as
`jonaai/evals/response-judge` by `scripts/migrate_prompts_to_langfuse.py`)
with a code-level fallback so the app works even without Langfuse.

Design:
- Never blocks the user query. Errors are logged and swallowed.
- Runs in an asyncio.create_task so it doesn't delay the WS done event.
- Skips if ANTHROPIC_API_KEY is missing or Langfuse is disabled.
- Caches the judge prompt per-process via @lru_cache (same as sdk/prompts.py).
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from typing import Any, Optional

from openeo_ai import budget
from openeo_ai.langfuse_setup import emit_score, get_langfuse_client
from openeo_ai.retry import retryable

logger = logging.getLogger(__name__)

# Fallback prompt if Langfuse is unavailable. Kept in sync with the version
# uploaded by scripts/migrate_prompts_to_langfuse.py.
_JUDGE_PROMPT_NAME = "jonaai/evals/response-judge"
_JUDGE_PROMPT_FALLBACK = """You are an expert evaluator of Earth Observation AI assistant responses.

Given the user's query and the assistant's final response, rate three dimensions on a 0.0-1.0 scale:

1. relevance: Does the response address what the user actually asked?
2. completeness: Does the response fully answer the question, including any tools executed and visualizations shown?
3. correctness: Are the technical claims accurate (dates, band names, satellite collections, geography, indices)?

Return ONLY a JSON object with no prose, no code fences, no explanation:

{"relevance": <float 0-1>, "completeness": <float 0-1>, "correctness": <float 0-1>, "reasoning": "one-sentence summary"}

User query:
{{query}}

Assistant response:
{{response}}
"""

# Haiku is the cheap judge model. Override via env if the user wants a different judge.
_JUDGE_MODEL = os.environ.get("LANGFUSE_JUDGE_MODEL", "claude-haiku-4-5-20251001")
_JUDGE_MAX_TOKENS = 500  # JSON response is small
_JUDGE_TIMEOUT = 30.0  # seconds


@lru_cache(maxsize=1)
def _load_judge_prompt() -> str:
    """Fetch the judge prompt from Langfuse, with fallback.

    Cached per-process. Restart required to pick up a new version (matches
    the strategy in sdk/prompts.py).
    """
    client = get_langfuse_client()
    if client is None:
        return _JUDGE_PROMPT_FALLBACK
    try:
        p = client.get_prompt(_JUDGE_PROMPT_NAME, label="production")
        return p.prompt if isinstance(p.prompt, str) else _JUDGE_PROMPT_FALLBACK
    except Exception as exc:  # noqa: BLE001
        logger.debug("[judge] fetch %s failed, using fallback: %s", _JUDGE_PROMPT_NAME, exc)
        return _JUDGE_PROMPT_FALLBACK


def _substitute(template: str, query: str, response: str) -> str:
    """Simple {{var}} substitution — Langfuse-compatible template syntax."""
    return template.replace("{{query}}", query).replace("{{response}}", response)


def _extract_json_object(text: str) -> Optional[dict]:
    """Parse the judge's response as a JSON object.

    The prompt asks for bare JSON, but models sometimes wrap it in code
    fences or add a trailing period. This strips common wrappers and
    extracts the first {...} block.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip markdown code fence
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    # Find the first balanced { } block
    start = cleaned.find("{")
    if start < 0:
        return None
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(cleaned[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def score_response(query: str, response: str) -> Optional[dict]:
    """Run the LLM judge and return the parsed {relevance, completeness, correctness, reasoning}
    dict (dims in [0,1] per the prompt), or None if unavailable/unparseable. Never raises.

    Shared by judge_response (which emits the dims to Langfuse) and the persona A/B
    harness (which averages the dims into a lift score). Requires ANTHROPIC_API_KEY.
    """
    if not query or not response:
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.debug("[judge] ANTHROPIC_API_KEY missing — skipping")
        return None

    # Truncate inputs to keep judge cheap. Most queries are short; responses
    # can be 10k+ chars from tool-heavy workflows.
    q_truncated = query[:2000]
    r_truncated = response[:8000]
    prompt = _substitute(_load_judge_prompt(), q_truncated, r_truncated)

    try:
        import anthropic

        @retryable(max_attempts=2, retry_on=(anthropic.APIConnectionError, anthropic.RateLimitError))
        async def _complete() -> str:
            client = anthropic.AsyncAnthropic(api_key=api_key, timeout=_JUDGE_TIMEOUT)
            msg = await client.messages.create(
                model=_JUDGE_MODEL,
                max_tokens=_JUDGE_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            return "".join(
                block.text for block in msg.content if hasattr(block, "text")
            ).strip()

        text = await _complete()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[judge] Anthropic call failed: %s", exc)
        return None

    parsed = _extract_json_object(text)
    if not parsed:
        logger.warning("[judge] failed to parse response: %s", text[:200])
        return None
    return parsed


async def judge_response(
    trace_id: Optional[str],
    query: str,
    response: str,
    user_id: Optional[str] = None,
) -> None:
    """Score a completed assistant response on relevance/completeness/correctness.

    Fires asynchronously — safe to call via `asyncio.create_task` from the
    WebSocket handler. All errors are logged and swallowed; the judge never
    raises and never delays the user flow.

    Args:
        trace_id: Langfuse trace_id to attach scores to. If None, uses the
                  currently-active trace (not usually valid in a detached
                  task — prefer passing the explicit id).
        query:    The user's original prompt.
        response: The assistant's final text response.
    """
    if not query or not response:
        return

    if get_langfuse_client() is None:
        logger.debug("[judge] Langfuse disabled — skipping")
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        logger.debug("[judge] ANTHROPIC_API_KEY missing — skipping")
        return

    # Observe-mode budget tag before the network call so failed attempts meter too.
    if user_id:
        try:
            budget.record_llm_call(user_id, "judge", _JUDGE_MODEL)
        except Exception:  # noqa: BLE001
            logger.debug("[budget] judge tag failed", exc_info=True)

    parsed = await score_response(query, response)
    if not parsed:
        return

    reasoning = str(parsed.get("reasoning", ""))[:300]
    for dim in ("relevance", "completeness", "correctness"):
        raw_val = parsed.get(dim)
        try:
            val = float(raw_val)
        except (TypeError, ValueError):
            logger.warning("[judge] %s: non-numeric value %r", dim, raw_val)
            continue
        # Clamp to [0, 1] since the prompt specified that range
        val = max(0.0, min(1.0, val))
        emit_score(
            name=f"judge-{dim}",
            value=val,
            trace_id=trace_id,
            comment=reasoning,
        )
    logger.info(
        "[judge] scored trace=%s relevance=%.2f completeness=%.2f correctness=%.2f",
        (trace_id or "?")[:8],
        parsed.get("relevance", -1),
        parsed.get("completeness", -1),
        parsed.get("correctness", -1),
    )
