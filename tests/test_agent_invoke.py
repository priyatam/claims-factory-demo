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
