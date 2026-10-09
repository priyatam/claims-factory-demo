"""AgentCore Runtime entrypoint: the adapter around the harness in claims/harness.py.

    uv run python -m claims.agent   # same entrypoint locally, on :8080

Loads the photograph from the payload (or fetches a public URL), runs one claim through the
harness, records the outcome, and returns the claim JSON.

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


@app.entrypoint
async def invoke(payload: dict):
    if payload.get("command") == "state":
        yield harness_state(get_agent())
        return

    claim_id = payload["claim_id"] if isinstance(payload.get("claim_id"), str) else uuid.uuid4().hex
    image = load_image(payload)
    if image is None:
        result = empty_result(claim_id, "unreadable")
        record_outcome(result)
        yield result
        return
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
    yield result


if __name__ == "__main__":
    app.run(host="0.0.0.0")  # nosec B104
