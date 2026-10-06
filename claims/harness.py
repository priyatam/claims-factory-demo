"""Fixed Strands harness: triage → read → validate."""

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


def build_agent() -> Agent:
    from claims.agent import SYSTEM_PROMPT

    return Agent(
        model=MODEL_ID,
        system_prompt=SYSTEM_PROMPT,
        tools=PARTNER_TOOLS,
        callback_handler=None,
    )


def image_format(media_type: str) -> str | None:
    return _FORMAT.get(media_type)


def content_for(image: bytes, media_type: str) -> list[dict[str, Any]] | None:
    from claims.agent import USER_PROMPT

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
