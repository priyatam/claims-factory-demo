"""AgentCore Runtime entrypoint: the adapter around the harness in claims/harness.py.

Loads the photograph from the payload (or fetches a public URL), runs one claim through the
harness, records the outcome, and returns the claim JSON.

Locally, `uv run python -m claims.localhost` serves the upload page on top of this same app. It
still calls Amazon Bedrock through the harness (MODEL_ID, your AWS credentials), exactly as the
deployed runtime does; only the hosting differs. It does not call AgentCore Runtime, so there is no
per-session microVM isolation, no SigV4 caller check, and no CloudWatch trace, which means
`cli.py --otel-logs` and `--claim-id` will not find a local claim. Results resemble the runtime's,
not match them exactly, because the model is not deterministic.

`uv run python -m claims.agent` serves only the API on :8080 until stopped and prints nothing at
startup. It listens on all interfaces (0.0.0.0) with no authentication, so use it only on a trusted
network. Check it with `curl http://127.0.0.1:8080/ping`; send a claim with a POST to /invocations.

Payloads:
    {"image_b64": "...", "media_type": "image/jpeg"}
    {"image_url": "https://..."}
    {"command": "state"}
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import trace

from claims.claim import empty_result, fetch_image
from claims.harness import MODEL_ID, build_agent, decode_payload_image, harness_state, run_claim_with_agent
from claims.telemetry import (
    SUMMARY_PREFIX,
    configure_telemetry,
    metrics_snapshot,
    record_outcome,
    run_summary,
)

app = BedrockAgentCoreApp()
_agent = None
_tracer = trace.get_tracer("claims")
log = logging.getLogger("claims")
log.setLevel(logging.INFO)



def get_agent():
    """Build the Strands agent on first use (not at import — keeps unit tests offline)."""
    global _agent
    if _agent is None:
        configure_telemetry()
        _agent = build_agent()
    return _agent


def load_image(payload: dict) -> tuple[bytes, str] | None:
    loaded = decode_payload_image(payload)
    if loaded is not None:
        return loaded
    url = payload.get("image_url")
    if isinstance(url, str) and url.strip():
        got = fetch_image(url.strip())
        return (got["data"], got["media_type"]) if got else None
    return None


def run_payload(payload: dict) -> dict:
    """One request in, one claim out. The entrypoint and claims/localhost.py both call this."""
    if payload.get("command") == "state":
        return harness_state(get_agent())

    claim_id = payload["claim_id"] if isinstance(payload.get("claim_id"), str) else uuid.uuid4().hex
    image = load_image(payload)
    if image is None:
        result = empty_result(claim_id, "unreadable")
        record_outcome(result)
        return result
    # The claim id is the correlation id: the page shows it, and `cli.py --claim-id` finds the trace by it.
    # The log record always reaches CloudWatch with this trace's id; the span only does when the trace is sampled.
    agent = get_agent()
    before, started = metrics_snapshot(agent), time.monotonic()
    with _tracer.start_as_current_span("claim", attributes={"claim_id": claim_id}):
        log.info("claim_id=%s", claim_id)
        result = run_claim_with_agent(agent, image[0], image[1], claim_id=claim_id)
        summary = run_summary(claim_id, MODEL_ID, before, metrics_snapshot(agent), time.monotonic() - started)
        log.info("%s%s", SUMMARY_PREFIX, json.dumps(summary))
    record_outcome(result)
    return result


@app.entrypoint
async def invoke(payload: dict):
    yield run_payload(payload)


if __name__ == "__main__":
    app.run(host="0.0.0.0")  # nosec B104
