"""Hash utilities for SCF and process graphs."""
import hashlib
import json
from typing import Any


def canonical_json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def hash_json(data: Any) -> str:
    canonical = canonical_json(data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
