"""Public upload page. API Gateway calls this Lambda; the browser never sees AWS credentials.

GET / serves the form. POST / takes JSON {policy, image_b64}. The AgentCore runtime is
invoked only when the policy code matches POLICY_CODE_ADMIN. Every failure returns the same
message so the page does not say which check failed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import uuid

from claims.gate import policy_ok

MAX_IMAGE = 3 * 1024 * 1024
MAX_BODY = MAX_IMAGE * 4 // 3 + 1024  # base64 of the largest photo, plus the JSON around it

ERROR = (
    "I'm sorry for the inconvenience, there's an error that occurred in processing your request. "
    "Try again after a few days."
)

STYLE = """
body { font: 16px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 40rem; padding: 0 1rem; }
label { display: block; margin: 0.8rem 0 0.25rem; }
button { margin-top: 1rem; }
pre { white-space: pre-wrap; }
.note { color: #444; }
"""

SCRIPT = """
const MAX = 3 * 1024 * 1024;
const ERROR = __ERROR__;
const out = document.getElementById("out");
const go = document.getElementById("go");

function show(claim) {
  const lines = ["Status: " + claim.status];
  const damage = claim.damage;
  if (damage && damage.summary) lines.push("Damage: " + damage.summary + (damage.severity ? " (" + damage.severity + ")" : ""));
  const estimate = claim.estimate;
  if (estimate && estimate.low != null) {
    lines.push("Estimate: " + estimate.low + " to " + estimate.high + " " + (estimate.currency || "") + ", confidence " + estimate.confidence);
  } else {
    lines.push("No estimate: this photo could not be priced.");
  }
  return lines.join("\\n");
}

document.getElementById("claim").onsubmit = async (event) => {
  event.preventDefault();
  const file = document.getElementById("file").files[0];
  if (!file || file.size > MAX) { out.textContent = ERROR; return; }
  go.disabled = true;
  out.textContent = "Assessing...";
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    let binary = "";
    for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
    const response = await fetch("/", {
      method: "POST",
      headers: {"content-type": "application/json"},
      body: JSON.stringify({policy: document.getElementById("policy").value, image_b64: btoa(binary)}),
    });
    if (!response.ok) throw new Error("rejected");
    out.textContent = show(await response.json());
  } catch (_) {
    out.textContent = ERROR;
  }
  go.disabled = false;
};
""".replace("__ERROR__", json.dumps(ERROR))

PAGE = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vehicle claim</title>
<style>{STYLE}</style>
</head>
<body>
<h1>Vehicle claim</h1>
<p class="note">One photo, 3 MB or less. The cost is a visual range, not a settlement offer.</p>
<form id="claim">
  <label for="policy">Policy code</label>
  <input id="policy" type="password" maxlength="10" autocomplete="off" required>
  <label for="file">Photo (JPEG, PNG, or WebP)</label>
  <input id="file" type="file" accept="image/jpeg,image/png,image/webp" required>
  <button id="go" type="submit">Submit</button>
</form>
<pre id="out"></pre>
<script>{SCRIPT}</script>
</body>
</html>"""


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


def _media_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
    kind = _media_type(image) if image else None
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
