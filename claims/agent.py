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

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from claims.claim import empty_result, fetch_image
from claims.harness import build_agent, decode_payload_image, harness_state, run_claim_with_agent
from claims.telemetry import configure_telemetry, record_outcome

app = BedrockAgentCoreApp()
_agent = None


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

    claim_id = payload["claim_id"] if isinstance(payload.get("claim_id"), str) else None
    image = load_image(payload)
    if image is None:
        result = empty_result(claim_id or "unknown", "unreadable")
        record_outcome(result)
        yield result
        return
    result = run_claim_with_agent(get_agent(), image[0], image[1], claim_id=claim_id)
    record_outcome(result)
    yield result


if __name__ == "__main__":
    app.run(host="0.0.0.0")  # nosec B104
