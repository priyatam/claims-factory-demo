"""Append claim JSON for a later adjuster label. Quiet unless CLAIMS_EVAL_LOG is set."""

from __future__ import annotations

import json
import os
from pathlib import Path

OUTCOMES = Path(__file__).resolve().parents[1] / "evals" / "runtime" / "outcomes.jsonl"
_ON = {"1", "true", "yes", "on"}
_DROP = {"image", "image_b64", "image_url", "photo", "data", "media_type"}


def eval_log_enabled() -> bool:
    return os.environ.get("CLAIMS_EVAL_LOG", "").strip().lower() in _ON


def record_outcome(result: dict) -> None:
    """Write one claim, without photo bytes. Never raises into the response."""
    if not eval_log_enabled():
        return
    try:
        kept = {
            key: value
            for key, value in result.items()
            if key not in _DROP and not isinstance(value, (bytes, bytearray))
        }
        OUTCOMES.parent.mkdir(parents=True, exist_ok=True)
        with OUTCOMES.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(kept) + "\n")
    except Exception:
        return
