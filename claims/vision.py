import base64
import os
from typing import Callable

import httpx

from claims.agent import USER_PROMPT

text_of = lambda body: next(
    block["text"] for block in body.get("content", []) if block.get("type") == "text"
)


def message_body(image: bytes, media_type: str, model: str) -> dict:
    return {
        "model": model,
        "max_tokens": 1024,
        "thinking": {"type": "disabled"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64.b64encode(image).decode("ascii"),
                        },
                    },
                    {"type": "text", "text": USER_PROMPT},
                ],
            }
        ],
    }


def assess_with_claude(
    image: bytes,
    media_type: str,
    *,
    post: Callable | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    key = api_key if api_key is not None else os.environ["ANTHROPIC_API_KEY"]
    model_id = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
    sender = post or _post
    response = sender(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json=message_body(image, media_type, model_id),
        timeout=60.0,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"claude {response.status_code}")
    return text_of(response.json())


def _post(url: str, **kwargs):
    return httpx.post(url, **kwargs)
