"""Public upload page. API Gateway calls this Lambda; the browser never sees AWS credentials.

GET / serves the form. POST / takes JSON {policy, image_b64}. The AgentCore runtime is
invoked only when the policy code matches POLICY_CODE_ADMIN. Every failure returns the same
message so the page does not say which check failed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import uuid
from pathlib import Path

from claims.claim import media_type

MAX_CODE = 10
MAX_IMAGE = 3 * 1024 * 1024
MAX_BODY = MAX_IMAGE * 4 // 3 + 1024  # base64 of the largest photo, plus the JSON around it

ERROR = (
    "I'm sorry for the inconvenience, there's an error that occurred in processing your request. "
    "Try again after a few days."
)

# The page, its style, and its script live in claims/web. They are inlined so the content
# security policy can pin each by hash and the page needs no other route.
WEB = Path(__file__).parent / "web"
STYLE = (WEB / "style.css").read_text(encoding="utf-8")
SCRIPT = (WEB / "app.js").read_text(encoding="utf-8").replace("__ERROR__", json.dumps(ERROR))
PAGE = (WEB / "index.html").read_text(encoding="utf-8").replace("{{STYLE}}", STYLE).replace("{{SCRIPT}}", SCRIPT)


def _sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


HEADERS = {
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
    "strict-transport-security": "max-age=63072000",
    "content-security-policy": (
        "default-src 'none'; "
        f"script-src {_sha256(SCRIPT)}; style-src {_sha256(STYLE)}; "
        "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def policy_ok(submitted: object) -> bool:
    """True only when the submitted code equals POLICY_CODE_ADMIN. Fails closed; never logs either value."""
    expected = os.environ.get("POLICY_CODE_ADMIN", "")
    if not expected or len(expected) > MAX_CODE:
        return False
    if not isinstance(submitted, str) or not submitted or len(submitted) > MAX_CODE:
        return False
    return hmac.compare_digest(submitted.encode(), expected.encode())


def _parse(event: dict) -> dict | None:
    raw = event.get("body") or ""
    if len(raw) > MAX_BODY:
        return None
    try:
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode()
        body = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return body if isinstance(body, dict) else None


def _decode(raw: object) -> bytes | None:
    if not isinstance(raw, str) or not raw or len(raw) > MAX_BODY:
        return None
    try:
        image = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return image if 0 < len(image) <= MAX_IMAGE else None


def handler(event, _context, invoke=None):
    http = (event.get("requestContext") or {}).get("http", {})
    if http.get("path", "/") != "/":
        return _reply(404, ERROR)
    method = http.get("method", "GET")
    if method == "GET":
        return _reply(200, PAGE, "text/html; charset=utf-8")
    if method != "POST":
        return _reply(405, ERROR)

    body = _parse(event)
    if body is None or not policy_ok(body.get("policy")):
        return _reply(400, ERROR)
    image = _decode(body.get("image_b64"))
    kind = media_type(image) if image else None
    if kind is None:
        return _reply(400, ERROR)
    try:
        result = (invoke or _invoke_runtime)(image, kind)
    except Exception as exc:
        print("runtime call failed:", type(exc).__name__)  # class only; never the body or the code
        return _reply(500, ERROR)
    return _reply(200, json.dumps(result), "application/json")


def _invoke_runtime(image: bytes, kind: str) -> dict:
    import boto3

    client = boto3.client("bedrock-agentcore")
    payload = {"image_b64": base64.b64encode(image).decode("ascii"), "media_type": kind}
    response = client.invoke_agent_runtime(
        agentRuntimeArn=os.environ["RUNTIME_ARN"],
        runtimeSessionId=str(uuid.uuid4()),
        payload=json.dumps(payload).encode(),
    )
    for line in response["response"].iter_lines():
        if line.startswith(b"data: "):
            return json.loads(line[6:])
    raise RuntimeError("empty runtime response")


def _reply(status: int, body: str, content_type: str = "text/plain; charset=utf-8") -> dict:
    return {"statusCode": status, "headers": {"content-type": content_type, **HEADERS}, "body": body}
