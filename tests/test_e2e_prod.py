"""End-to-end check of the DEPLOYED stack. It calls real AWS and real Bedrock, so `uv run pytest`
leaves it out; it runs only when named:

    uv run pytest tests/test_e2e_prod.py -v

Needs AWS credentials for the stack's account and Region, the deployed ClaimsFactoryHarness stack, and
POLICY_CODE_ADMIN (environment, .env, or claims/.env, the same sources deploy.py reads).

It tests the machinery, not answer quality. One claim with one photo (dataset/img/veh1.jpeg) goes through
the page, the Lambda, the runtime, and Bedrock: the only model call, about 2 to 3 cents. The rest are
rejections and a harness-state call, which call no model. The unit-test gate in conftest.py is off for
these tests only.
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import boto3
import pytest

import cli
import deploy
from claims.harness import MODEL_ID
from claims.public import ERROR
from claims.telemetry import compact, fetch_trace, runtime_log_group

pytestmark = pytest.mark.prod

PHOTO = (Path(__file__).resolve().parents[1] / "dataset" / "img" / "veh1.jpeg").read_bytes()
TRACE_WAIT_SECONDS = 180


@pytest.fixture(scope="module")
def stack():
    session = boto3.Session()
    outputs = session.client("cloudformation").describe_stacks(StackName=cli.STACK_NAME)["Stacks"][0]["Outputs"]
    found = {o["OutputKey"]: o["OutputValue"] for o in outputs}
    return {"session": session, "page": found["PublicUrl"], "arn": found["RuntimeArn"]}


@pytest.fixture(scope="module")
def code():
    value = deploy.policy_code()
    if not value:
        pytest.skip("POLICY_CODE_ADMIN is not set in the environment, .env, or claims/.env")
    return value


def call(url: str, body: dict | None = None) -> tuple[int, dict, str]:
    """GET when body is None, else POST JSON. Returns (status, headers, text) without raising on 4xx or 5xx."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data, {"content-type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read().decode()


def upload(photo: bytes, policy: str) -> dict:
    return {"policy": policy, "image_b64": base64.b64encode(photo).decode("ascii")}


@pytest.fixture(scope="module")
def page_claim(stack, code):
    """The one model call of the run."""
    status, _headers, text = call(stack["page"], upload(PHOTO, code))
    assert status == 200, text[:200]
    return json.loads(text)


def test_page_serves_the_form_with_its_security_headers(stack):
    status, headers, text = call(stack["page"])
    headers = {k.lower(): v for k, v in headers.items()}
    assert status == 200 and headers["content-type"].startswith("text/html")
    assert 'id="policy"' in text and 'id="file"' in text
    assert "default-src 'none'" in headers["content-security-policy"]
    assert headers["cache-control"] == "no-store"


def test_wrong_code_gets_only_the_apology(stack):
    assert call(stack["page"], upload(PHOTO, "not-the-code"))[::2] == (400, ERROR)


def test_right_code_with_a_non_image_gets_only_the_apology(stack, code):
    assert call(stack["page"], upload(b"this is not an image", code))[::2] == (400, ERROR)


def test_a_photo_over_3_mb_gets_only_the_apology(stack, code):
    too_big = b"\xff\xd8\xff" + b"\x00" * (3 * 1024 * 1024)
    assert call(stack["page"], upload(too_big, code))[::2] == (400, ERROR)


def test_page_upload_runs_the_whole_path_and_returns_a_claim(page_claim):
    assert re.fullmatch(r"[0-9a-f]{32}", page_claim["claim_id"])
    assert page_claim["status"] in {"ok", "not_a_vehicle", "unreadable"}
    assert {"vehicle", "plate", "damage", "estimate"} <= page_claim.keys()


def test_runtime_answers_a_sigv4_call_without_a_model_call(stack):
    client = stack["session"].client("bedrock-agentcore")
    state = next(cli.invoke(client, stack["arn"], str(uuid.uuid4()), {"command": "state"}))
    assert state["type"] == "state" and state["model"] == MODEL_ID
    assert state["tools"]


def test_page_claim_id_finds_its_trace_in_cloudwatch(stack, page_claim):
    group = runtime_log_group(stack["arn"])
    logs = stack["session"].client("logs")
    deadline = time.monotonic() + TRACE_WAIT_SECONDS
    dump = None
    while dump is None and time.monotonic() < deadline:
        dump = fetch_trace(logs, group, page_claim["claim_id"])
        if dump is None:
            time.sleep(10)
    assert dump is not None, f"no trace for {page_claim['claim_id']} after {TRACE_WAIT_SECONDS}s"
    summary = compact(dump)
    assert summary["claim"]["claim_id"] == page_claim["claim_id"]
    assert summary["trace_summary"]["model_id"] == MODEL_ID
    assert summary["trace_summary"]["input_tokens"] > 0
