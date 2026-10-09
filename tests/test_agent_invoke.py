import asyncio
import base64

from claims.agent import invoke, load_image
from claims.harness import decode_payload_image

JPEG = b"\xff\xd8\xff" + b"\x00" * 8


async def _chunks(payload):
    return [chunk async for chunk in invoke(payload)]


def test_load_image_from_b64():
    payload = {
        "image_b64": base64.b64encode(JPEG).decode("ascii"),
        "media_type": "image/jpeg",
    }
    assert load_image(payload) == decode_payload_image(payload)


def test_invoke_state(monkeypatch):
    monkeypatch.setattr(
        "claims.agent.harness_state",
        lambda _a: {"type": "state", "model": "us.anthropic.claude-sonnet-4-6"},
    )
    assert asyncio.run(_chunks({"command": "state"}))[0]["type"] == "state"


def test_invoke_claim(monkeypatch):
    monkeypatch.setattr(
        "claims.agent.run_claim_with_agent",
        lambda *_a, **_k: {
            "claim_id": "x",
            "status": "not_a_vehicle",
            "vehicle": None,
            "plate": {"value": None, "confidence": None},
            "damage": None,
            "estimate": None,
        },
    )
    payload = {
        "image_b64": base64.b64encode(JPEG).decode("ascii"),
        "media_type": "image/jpeg",
        "claim_id": "x",
    }
    assert asyncio.run(_chunks(payload))[0]["status"] == "not_a_vehicle"


def test_invoke_missing_image():
    assert asyncio.run(_chunks({}))[0]["status"] == "unreadable"


def test_runtime_mints_a_claim_id_for_the_trace(monkeypatch):
    seen = {}

    def run(_agent, _image, _kind, claim_id=None):
        seen["claim_id"] = claim_id
        return {"claim_id": claim_id, "status": "ok"}

    monkeypatch.setattr("claims.agent.get_agent", lambda: object())
    monkeypatch.setattr("claims.agent.run_claim_with_agent", run)
    payload = {"image_b64": base64.b64encode(JPEG).decode("ascii"), "media_type": "image/jpeg"}
    result = asyncio.run(_chunks(payload))[0]
    assert len(seen["claim_id"]) == 32 and int(seen["claim_id"], 16) >= 0
    assert result["claim_id"] == seen["claim_id"]


def test_each_claim_logs_its_id_and_a_summary_inside_the_trace(monkeypatch, caplog):
    import json
    import logging

    from claims.telemetry import SUMMARY_PREFIX

    monkeypatch.setattr("claims.agent.get_agent", lambda: object())
    monkeypatch.setattr("claims.agent.run_claim_with_agent", lambda *_a, claim_id=None, **_k: {"claim_id": claim_id, "status": "ok"})
    payload = {"image_b64": base64.b64encode(JPEG).decode("ascii"), "media_type": "image/jpeg", "claim_id": "abc123"}
    with caplog.at_level(logging.INFO, logger="claims"):
        asyncio.run(_chunks(payload))
    lines = [r.getMessage() for r in caplog.records if r.name == "claims"]
    assert "claim_id=abc123" in lines
    summary = next(line for line in lines if line.startswith(SUMMARY_PREFIX))
    assert json.loads(summary[len(SUMMARY_PREFIX):])["claim_id"] == "abc123"
