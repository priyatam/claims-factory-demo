"""The gate in conftest.py itself: if these fail, the other tests could be reaching AWS."""

import socket

import boto3
import httpx
import pytest

from claims.harness import build_agent


@pytest.mark.parametrize("service", ["bedrock-runtime", "bedrock-agentcore", "bedrock-agentcore-control"])
def test_bedrock_clients_are_blocked(service):
    client = boto3.client(service, region_name="us-west-2")
    with pytest.raises(RuntimeError, match="must not call AWS"):
        client._make_api_call(client.meta.service_model.operation_names[0], {})


def test_the_real_harness_cannot_reach_bedrock():
    agent = build_agent()
    with pytest.raises(Exception) as caught:
        agent("hello")
    assert "must not call AWS" in repr(caught.value) or "must not call AWS" in repr(caught.value.__cause__)


def test_http_is_blocked():
    with pytest.raises(RuntimeError, match="must not make HTTP"):
        httpx.get("https://example.com")
    with pytest.raises(RuntimeError, match="must not make HTTP"):
        httpx.Client().send(httpx.Request("GET", "https://example.com"))


def test_remote_sockets_are_blocked_and_local_ones_are_not():
    with pytest.raises(RuntimeError, match="must not open a network connection"):
        socket.create_connection(("93.184.216.34", 443), timeout=1)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    socket.create_connection(listener.getsockname(), timeout=1).close()
    listener.close()
