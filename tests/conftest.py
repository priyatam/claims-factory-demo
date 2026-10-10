"""Gate: no unit test may reach Bedrock, AgentCore, Anthropic, or any other remote host.

The one exception is tests/test_e2e_prod.py (marker `prod`): it calls the deployed stack on purpose.
It is left out of every directory or full-suite run and runs, with the gate off, only when named:
    uv run pytest tests/test_e2e_prod.py

Three layers, all automatic for every test:
1. AWS clients: any botocore call to a service whose name starts with "bedrock" raises.
2. HTTP: httpx requests raise (the Anthropic-style and photo-fetch paths must inject `get`/`post`).
3. Sockets: any connection to a non-local address raises, so a path the first two miss still fails.
Fake AWS credentials and a disabled metadata lookup keep a stray client from using real ones.
"""

from __future__ import annotations

import socket

import pytest


def _is_local(address) -> bool:
    if not isinstance(address, tuple):  # AF_UNIX paths and the like
        return True
    host = address[0]
    return isinstance(host, str) and (host in ("localhost", "::1", "") or host.startswith("127."))


# pytest still collects a file named on the command line, so this leaves it out of "run all" only.
collect_ignore = ["test_e2e_prod.py"]


@pytest.fixture(autouse=True)
def no_remote_calls(monkeypatch, request):
    if request.node.get_closest_marker("prod"):
        return
    import botocore.client

    original_call = botocore.client.BaseClient._make_api_call

    def blocked_call(self, operation_name, api_params):
        service = self.meta.service_model.service_name
        if service.startswith("bedrock"):
            raise RuntimeError(
                f"unit tests must not call AWS {service}.{operation_name}; inject the model call"
            )
        return original_call(self, operation_name, api_params)

    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", blocked_call)

    def blocked_http(*_a, **_k):
        raise RuntimeError("unit tests must not make HTTP requests; inject the call")

    for name in ("httpx.request", "httpx.get", "httpx.post", "httpx.Client.send", "httpx.AsyncClient.send"):
        monkeypatch.setattr(name, blocked_http)

    original_connect = socket.socket.connect

    def guarded_connect(self, address):
        if not _is_local(address):
            raise RuntimeError(f"unit tests must not open a network connection to {address[0]}")
        return original_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("CLAIMS_EVAL_LOG", raising=False)
