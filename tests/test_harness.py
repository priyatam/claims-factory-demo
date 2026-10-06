import base64
import json
from types import SimpleNamespace

from claims.harness import (
    MODEL_ID,
    content_for,
    decode_payload_image,
    harness_state,
    image_format,
    run_claim_with_agent,
)

JPEG = b"\xff\xd8\xff" + b"\x00" * 8

OK_JSON = json.dumps(
    {
        "status": "ok",
        "vehicle": {"make": "Toyota", "model": "Camry", "colour": "blue", "confidence": 0.9},
        "plate": {"value": None, "confidence": None},
        "damage": {"summary": "left rear bumper dent with scratching", "parts": ["bumper"], "severity": "minor"},
        "estimate": {
            "low": 400,
            "high": 900,
            "currency": "USD",
            "assumptions": ["visible outer panel only"],
            "confidence": 0.7,
        },
    }
)


def test_image_format():
    assert image_format("image/jpeg") == "jpeg"
    assert image_format("image/gif") is None


def test_content_for_builds_image_and_text():
    content = content_for(JPEG, "image/jpeg")
    assert content is not None
    assert content[0]["image"]["format"] == "jpeg"
    assert content[0]["image"]["source"]["bytes"] == JPEG
    assert "status" in content[1]["text"]


def test_content_for_rejects_unknown_type():
    assert content_for(JPEG, "image/gif") is None


def test_decode_payload_image():
    payload = {
        "image_b64": base64.b64encode(JPEG).decode("ascii"),
        "media_type": "image/jpeg",
    }
    assert decode_payload_image(payload) == (JPEG, "image/jpeg")


def test_decode_payload_image_rejects_bad_b64():
    assert decode_payload_image({"image_b64": "!!!", "media_type": "image/jpeg"}) is None


def test_run_claim_with_agent_parses_ok(monkeypatch):
    result = run_claim_with_agent(
        agent=SimpleNamespace(),
        image=JPEG,
        media_type="image/jpeg",
        claim_id="c1",
        call=lambda _a, _c: OK_JSON,
    )
    assert result["status"] == "ok"
    assert result["claim_id"] == "c1"
    assert result["estimate"]["low"] == 400


def test_run_claim_with_agent_surfaces_model_error():
    def boom(_a, _c):
        raise RuntimeError("no bedrock")

    result = run_claim_with_agent(
        agent=SimpleNamespace(),
        image=JPEG,
        media_type="image/jpeg",
        claim_id="c3",
        call=boom,
    )
    assert result["status"] == "unreadable"
    assert "RuntimeError" in result["error"]


def test_run_claim_with_agent_unreadable_on_bad_json():
    result = run_claim_with_agent(
        agent=SimpleNamespace(),
        image=JPEG,
        media_type="image/jpeg",
        claim_id="c2",
        call=lambda _a, _c: "not-json",
    )
    assert result == {
        "claim_id": "c2",
        "status": "unreadable",
        "vehicle": None,
        "plate": {"value": None, "confidence": None},
        "damage": None,
        "estimate": None,
    }


def test_harness_state_shape():
    agent = SimpleNamespace(
        tool_names=["fetch_policy"],
        messages=[],
        event_loop_metrics=SimpleNamespace(
            cycle_count=2,
            accumulated_usage={"inputTokens": 1, "outputTokens": 2, "totalTokens": 3},
        ),
    )
    state = harness_state(agent)
    assert state["model"] == MODEL_ID
    assert state["tools"] == ["fetch_policy"]
    assert state["cycles"] == 2
