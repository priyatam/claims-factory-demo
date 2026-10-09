"""The harness core: one fixed Strands agent (triage → read → validate) and the helpers to run a claim.

Holds the system and user prompts, builds the agent with its model and partner tools, shapes a
photograph into model content, runs one claim, and turns the answer into a typed record. It has
no AgentCore code; claims/agent.py is the runtime entrypoint that calls it.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any, Callable

from strands import Agent

from claims.claim import ClaimResult, assessment_from_model, empty_result, json_from_model
from claims.partners import PARTNER_TOOLS

# Must match PROFILE in deploy.py.
MODEL_ID = "us.anthropic.claude-sonnet-4-6"

_FORMAT = {"image/jpeg": "jpeg", "image/png": "png", "image/webp": "webp"}

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


def build_agent() -> Agent:
    return Agent(
        model=MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
        tools=PARTNER_TOOLS,
        callback_handler=None,
    )


def image_format(media_type: str) -> str | None:
    return _FORMAT.get(media_type)


def content_for(image: bytes, media_type: str) -> list[dict[str, Any]] | None:
    fmt = image_format(media_type)
    if fmt is None:
        return None
    return [
        {"image": {"format": fmt, "source": {"bytes": image}}},
        {"text": USER_PROMPT},
    ]


def decode_payload_image(payload: dict) -> tuple[bytes, str] | None:
    media_type = payload.get("media_type")
    raw_b64 = payload.get("image_b64")
    if not (isinstance(raw_b64, str) and raw_b64.strip() and isinstance(media_type, str)):
        return None
    if image_format(media_type) is None:
        return None
    try:
        return base64.b64decode(raw_b64, validate=True), media_type
    except Exception:
        return None


def run_claim_with_agent(
    agent: Agent,
    image: bytes,
    media_type: str,
    claim_id: str | None = None,
    *,
    call: Callable[[Agent, list], Any] | None = None,
) -> ClaimResult:
    cid = claim_id or uuid.uuid4().hex
    content = content_for(image, media_type)
    if content is None:
        return empty_result(cid, "unreadable")
    invoke = call or (lambda a, c: a(c))
    try:
        text = str(invoke(agent, content))
    except Exception as exc:
        # Model/IAM failures are not "unreadable photos" — surface them.
        return {**empty_result(cid, "unreadable"), "error": f"{type(exc).__name__}: {exc}"}
    try:
        payload = json_from_model(text)
    except Exception:
        return empty_result(cid, "unreadable")
    return assessment_from_model(payload, cid)


def harness_state(agent: Agent) -> dict:
    metrics = agent.event_loop_metrics
    return {
        "type": "state",
        "model": MODEL_ID,
        "tools": sorted(agent.tool_names),
        "messages": agent.messages,
        "cycles": metrics.cycle_count,
        "tokens": dict(metrics.accumulated_usage),
    }
