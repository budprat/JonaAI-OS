#!/usr/bin/env python3
"""Phase G — Upload the JonaAI golden dataset to Langfuse.

Creates or updates a Langfuse dataset named `jonaai-golden-v1` containing
every query defined in `openeo_ai.evals.golden_dataset.GOLDEN_QUERIES`.
Existing items with the same input are updated (Langfuse handles diffing).

Usage:
    source .env
    python scripts/create_golden_dataset.py
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="[dataset] %(message)s")
logger = logging.getLogger(__name__)

DATASET_NAME = "jonaai-golden-v1"
DATASET_DESCRIPTION = (
    "Curated reference queries for JonaAI regression testing. "
    "Covers NDVI, change detection, multi-band, water, quality metrics, "
    "fire, urban, agriculture, SAR, error paths, discovery, and geocoding. "
    "See openeo_ai.evals.golden_dataset for the source of truth."
)


def _init():
    """Bootstrap + return the Langfuse client."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, repo_root)
    sys.path.insert(0, os.path.join(repo_root, "openeo-fastapi"))

    from openeo_ai.langfuse_setup import init_langfuse, get_langfuse_client

    if not init_langfuse():
        logger.error("Langfuse failed to initialize — check LANGFUSE_* env vars")
        sys.exit(1)
    client = get_langfuse_client()
    if client is None:
        logger.error("Langfuse client unavailable after init")
        sys.exit(1)
    return client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    client = _init()

    from openeo_ai.evals.golden_dataset import GOLDEN_QUERIES

    if args.dry_run:
        logger.info("[DRY RUN] would create dataset %s with %d items", DATASET_NAME, len(GOLDEN_QUERIES))
        for q in GOLDEN_QUERIES:
            logger.info("  - %s (%s): %s", q.id, q.category, q.query[:80])
        return

    # Create or update the dataset (idempotent)
    try:
        client.create_dataset(name=DATASET_NAME, description=DATASET_DESCRIPTION)
        logger.info("Created dataset: %s", DATASET_NAME)
    except Exception as exc:
        logger.info("Dataset %s may already exist (%s) — continuing", DATASET_NAME, exc)

    # Upload each item
    for q in GOLDEN_QUERIES:
        try:
            client.create_dataset_item(
                dataset_name=DATASET_NAME,
                input=q.as_langfuse_input(),
                expected_output=q.as_langfuse_expected(),
                metadata=q.as_metadata(),
                id=q.id,  # stable id so re-uploads update in place
            )
            logger.info("  ↳ uploaded %s", q.id)
        except Exception as exc:
            logger.warning("  ↳ %s: %s", q.id, exc)

    try:
        client.flush()
    except Exception:
        pass
    logger.info("Done. View at: %s/project/.../datasets/%s", os.environ.get("LANGFUSE_HOST", ""), DATASET_NAME)


if __name__ == "__main__":
    main()
