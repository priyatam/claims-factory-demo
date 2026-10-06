"""Claims Strands harness for AgentCore Runtime.

    uv run python -m claims.agent

Payloads:
    {"image_b64": "...", "media_type": "image/jpeg"}
    {"image_url": "https://..."}
    {"command": "state"}
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
You assess one photograph for an auto insurance claim.
Order on every claim: triage, then read, then validate with tools.
Do not invent partner facts. A tool null stays null.
When the photograph cannot support a price, status is not_a_vehicle or unreadable
and estimate is null. Reply with JSON only, no markdown."""

USER_PROMPT = """\
Return only a JSON object, no markdown, with this shape:
{
  "status": "ok" or "not_a_vehicle" or "unreadable",
  "vehicle": {"make": string or null, "model": string or null, "colour": string or null, "confidence": number or null},
  "plate": {"value": string or null, "confidence": number or null},
  "damage": {"summary": string, "parts": [string], "severity": "minor" or "moderate" or "severe"},
  "estimate": {"low": integer, "high": integer, "currency": "USD", "assumptions": [string], "confidence": number},
  "partner_facts": {"policy": object or null, "loss_history": object or null, "estimating": object or null}
}

Rules:
- Triage first: not_a_vehicle when not a vehicle; unreadable when too unclear.
- status ok only with a one-sentence damage summary, for example "left rear bumper dent with scratching".
- estimate is a visual USD range (low <= high, at least one assumption), not a repair quote.
- plate.value null unless clearly readable. Null make/model/colour when unknown.
- Call partner tools before finalizing when status is ok.
"""

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from claims.claim import empty_result
from claims.harness import build_agent, decode_payload_image, harness_state, run_claim_with_agent
from claims.images import fetch_image
from claims.telemetry import configure_telemetry

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
        yield empty_result(claim_id or "unknown", "unreadable")
        return
    yield run_claim_with_agent(get_agent(), image[0], image[1], claim_id=claim_id)


if __name__ == "__main__":
    app.run(host="0.0.0.0")  # nosec B104
