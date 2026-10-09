"""Append claim JSON for a later adjuster label. Quiet unless CLAIMS_EVAL_LOG is set."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Generated eval files live here (gitignored). Not part of the deployed zip, which stages only claims/.
RUNTIME_ROOT = Path(__file__).resolve().parents[1] / ".runtime"
OUTCOMES = RUNTIME_ROOT / "outcomes.jsonl"
_ON = {"1", "true", "yes", "on"}
_DROP = {"image", "image_b64", "image_url", "photo", "data", "media_type"}


def eval_log_enabled() -> bool:
    return os.environ.get("CLAIMS_EVAL_LOG", "").strip().lower() in _ON


def outcomes_path() -> Path:
    """CLAIMS_EVAL_LOG_PATH overrides the default log file (used by the end-to-end eval)."""
    override = os.environ.get("CLAIMS_EVAL_LOG_PATH", "").strip()
    return Path(override) if override else OUTCOMES


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
        kept["logged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path = outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(kept) + "\n")
    except Exception:
        return
