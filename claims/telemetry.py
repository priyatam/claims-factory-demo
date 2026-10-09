"""Spans and the outcome log: Strands spans for AgentCore, and claim JSON kept for a later adjuster label."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

# Generated eval files live here (gitignored). Not part of the deployed zip, which stages only claims/.
RUNTIME_ROOT = Path(__file__).resolve().parents[1] / ".runtime"
OUTCOMES = RUNTIME_ROOT / "outcomes.jsonl"
_ON = {"1", "true", "yes", "on"}
_DROP = {"image", "image_b64", "image_url", "photo", "data", "media_type"}


def configure_telemetry(setup: Callable[[], None] | None = None) -> None:
    """Export spans only when AgentCore has set an OTLP endpoint."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        return
    (setup or attach_strands)()


def attach_strands() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from strands.telemetry import StrandsTelemetry

    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        # The instrument wrapper already registered this provider and its exporter.
        StrandsTelemetry(tracer_provider=provider)
        return
    StrandsTelemetry().setup_otlp_exporter()


def eval_log_enabled() -> bool:
    return os.environ.get("CLAIMS_EVAL_LOG", "").strip().lower() in _ON


def outcomes_path() -> Path:
    """CLAIMS_EVAL_LOG_PATH overrides the default log file (used by the end-to-end eval)."""
    override = os.environ.get("CLAIMS_EVAL_LOG_PATH", "").strip()
    return Path(override) if override else OUTCOMES


def record_outcome(result: dict) -> None:
    """Write one claim, without photo bytes. Quiet unless CLAIMS_EVAL_LOG is set; never raises into the response."""
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
