"""Block Bedrock (and Anthropic HTTP) in unit tests."""

from __future__ import annotations

import pytest


FORBIDDEN_SERVICES = frozenset({"bedrock", "bedrock-runtime", "bedrock-agentcore"})


@pytest.fixture(autouse=True)
def no_bedrock_or_anthropic(monkeypatch):
    import botocore.client

    original = botocore.client.BaseClient._make_api_call

    def blocked(self, operation_name, api_params):
        service = self.meta.service_model.service_name
        if service in FORBIDDEN_SERVICES:
            raise RuntimeError(
                f"unit tests must not call AWS {service}.{operation_name}; inject the model call"
            )
        return original(self, operation_name, api_params)

    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", blocked)

    def blocked_http(*_a, **_k):
        raise RuntimeError("unit tests must not call Anthropic HTTP; inject post=")

    monkeypatch.setattr("httpx.post", blocked_http)
    monkeypatch.setattr("httpx.Client.post", blocked_http)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
