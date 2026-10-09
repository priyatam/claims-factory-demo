"""Attach Strands evals spans when an OTLP endpoint is set."""

from __future__ import annotations

import os
from collections.abc import Callable


def configure_evals_telemetry(setup: Callable[[], None] | None = None) -> None:
    """Export eval spans only when an OTLP endpoint is set. No socket otherwise."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        return
    (setup or attach_evals)()


def attach_evals() -> None:
    from strands_evals.telemetry import StrandsEvalsTelemetry

    StrandsEvalsTelemetry().setup_otlp_exporter()
