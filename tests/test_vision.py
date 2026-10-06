from types import SimpleNamespace

import pytest

from claims.vision import assess_with_claude, message_body, text_of

JPEG = b"\xff\xd8\xff" + b"\x00" * 4


def test_message_body_uses_sonnet_without_sampling_or_thinking():
    body = message_body(JPEG, "image/jpeg", "claude-sonnet-5")
    assert body["model"] == "claude-sonnet-5"
    assert body["thinking"] == {"type": "disabled"}
    assert "temperature" not in body
    image = body["messages"][0]["content"][0]
    assert image["source"]["media_type"] == "image/jpeg"
    assert image["source"]["type"] == "base64"


def test_text_of_skips_non_text_blocks():
    body = {"content": [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "{\"status\":\"ok\"}"}]}
    assert text_of(body) == '{"status":"ok"}'


def test_assess_posts_to_anthropic():
    seen = {}

    def post(url, headers, json, timeout):
        seen["url"] = url
        seen["json"] = json
        seen["key"] = headers["x-api-key"]
        seen["timeout"] = timeout
        return SimpleNamespace(status_code=200, json=lambda: {"content": [{"type": "text", "text": "{\"status\":\"not_a_vehicle\"}"}]})

    text = assess_with_claude(JPEG, "image/jpeg", post=post, api_key="test-key", model="claude-sonnet-5")
    assert text.startswith("{")
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["json"]["thinking"] == {"type": "disabled"}
    assert seen["key"] == "test-key"


def test_assess_raises_without_the_response_body():
    def post(url, headers, json, timeout):
        return SimpleNamespace(status_code=400, json=lambda: {"error": "secret-echo"})

    with pytest.raises(RuntimeError, match="400") as caught:
        assess_with_claude(JPEG, "image/jpeg", post=post, api_key="test-key", model="claude-sonnet-5")
    assert "secret-echo" not in str(caught.value)
