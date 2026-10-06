"""Attach Strands spans to the AgentCore OpenTelemetry provider."""

from __future__ import annotations

import os
from collections.abc import Callable


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
