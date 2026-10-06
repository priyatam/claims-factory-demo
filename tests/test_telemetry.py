from opentelemetry.sdk.trace import TracerProvider

from claims.telemetry import attach_strands, configure_telemetry


def test_configure_telemetry_runs_setup_only_when_endpoint_is_set(monkeypatch):
    calls = []
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    configure_telemetry(setup=lambda: calls.append("export"))
    assert calls == []

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    configure_telemetry(setup=lambda: calls.append("export"))
    assert calls == ["export"]


def test_attach_reuses_existing_provider(monkeypatch):
    provider = TracerProvider()
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    seen = {}

    class FakeTelemetry:
        def __init__(self, tracer_provider=None):
            seen["provider"] = tracer_provider

        def setup_otlp_exporter(self):
            seen["export"] = True
            return self

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", FakeTelemetry)
    attach_strands()
    assert seen == {"provider": provider}


def test_attach_exports_when_no_provider_is_registered(monkeypatch):
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: object())
    seen = []

    class FakeTelemetry:
        def __init__(self, tracer_provider=None):
            seen.append(tracer_provider)

        def setup_otlp_exporter(self):
            seen.append("export")
            return self

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", FakeTelemetry)
    attach_strands()
    assert seen == [None, "export"]
