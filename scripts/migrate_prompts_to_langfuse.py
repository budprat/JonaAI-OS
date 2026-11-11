#!/usr/bin/env python3
"""Phase C — Upload JonaAI system prompts + judge prompt to Langfuse.

Idempotent: if a prompt with the same name already exists, a new version
is created (bumped automatically by Langfuse). To truly skip re-upload of
unchanged prompts, the script compares the current content to the latest
existing version and only creates a new one on diff.

Usage:
    # Ensure env vars are set
    source .env

    # Dry run: print what would be uploaded
    python scripts/migrate_prompts_to_langfuse.py --dry-run

    # Real run
    python scripts/migrate_prompts_to_langfuse.py

Requires:
    - LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST in env
    - openeo_ai + langfuse importable (PYTHONPATH set by .env / start scripts)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="[migrate] %(message)s")
logger = logging.getLogger(__name__)


JUDGE_PROMPT_NAME = "jonaai/evals/response-judge"
JUDGE_PROMPT_TEXT = """You are an expert evaluator of Earth Observation AI assistant responses.

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


def _init_langfuse():
    """Bootstrap Langfuse and return the client."""
    from openeo_ai.langfuse_setup import init_langfuse, get_langfuse_client

    if not init_langfuse():
        logger.error("Langfuse failed to initialize — check LANGFUSE_* env vars")
        sys.exit(1)
    client = get_langfuse_client()
    if client is None:
        logger.error("Langfuse client unavailable after init")
        sys.exit(1)
    return client


def _latest_prompt_content(client: Any, name: str) -> Optional[Any]:
    """Return the latest production prompt content, or None if not found."""
    try:
        p = client.get_prompt(name, label="production")
        return p.prompt
    except Exception:
        return None


def _create_prompt(
    client: Any,
    name: str,
    content: Any,
    prompt_type: str,
    dry_run: bool,
) -> None:
    """Create or update a prompt, skipping if content is unchanged."""
    existing = _latest_prompt_content(client, name)
    if existing == content:
        logger.info("  ↳ %s unchanged, skipping", name)
        return

    if dry_run:
        preview = str(content)
        preview = preview[:120] + "…" if len(preview) > 120 else preview
        logger.info("  ↳ [DRY RUN] would create %s (%s chars): %s", name, len(str(content)), preview)
        return

    client.create_prompt(
        name=name,
        prompt=content,
        type=prompt_type,
        labels=["production"],
    )
    logger.info("  ↳ uploaded %s", name)


def migrate_system_prompts(client: Any, dry_run: bool) -> None:
    """Upload the 6 system prompts from PROMPT_REGISTRY."""
    from openeo_ai.sdk.prompts import PROMPT_REGISTRY, _fallback_text

    logger.info("Migrating %d system prompts...", len(PROMPT_REGISTRY))
    for lf_name, fallback_attr in PROMPT_REGISTRY.items():
        text = _fallback_text(fallback_attr)
        # Store as a chat-type prompt with a single system message
        content = [{"role": "system", "content": text}]
        _create_prompt(client, lf_name, content, prompt_type="chat", dry_run=dry_run)


def migrate_judge_prompt(client: Any, dry_run: bool) -> None:
    """Upload the LLM-as-judge prompt for Phase F."""
    logger.info("Migrating judge prompt...")
    # Judge prompt uses {{query}} and {{response}} variables.
    # Store as text type — will be compiled at runtime with .compile(query=..., response=...).
    _create_prompt(
        client,
        JUDGE_PROMPT_NAME,
        JUDGE_PROMPT_TEXT,
        prompt_type="text",
        dry_run=dry_run,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Preview uploads without creating")
    parser.add_argument(
        "--only",
        choices=["system", "judge", "all"],
        default="all",
        help="Which prompts to migrate (default: all)",
    )
    args = parser.parse_args()

    # Ensure we can find openeo_ai — support running from repo root
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo_root)
    sys.path.insert(0, os.path.join(repo_root, "openeo-fastapi"))

    client = _init_langfuse()

    if args.only in ("system", "all"):
        migrate_system_prompts(client, args.dry_run)
    if args.only in ("judge", "all"):
        migrate_judge_prompt(client, args.dry_run)

    # Ensure all spans are flushed before exit
    try:
        client.flush()
    except Exception:
        pass
    logger.info("Done.")


if __name__ == "__main__":
    main()
