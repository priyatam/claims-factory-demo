import base64
import os
from typing import Callable

import httpx

PROMPT = """You assess one photograph for an auto insurance claim.

Return only a JSON object, no markdown, with this shape:
{
  "status": "ok" or "not_a_vehicle" or "unreadable",
  "vehicle": {"make": string or null, "model": string or null, "colour": string or null, "confidence": number or null},
  "plate": {"value": string or null, "confidence": number or null},
  "damage": {"summary": string, "parts": [string], "severity": "minor" or "moderate" or "severe"},
  "estimate": {"low": integer, "high": integer, "currency": "USD", "assumptions": [string], "confidence": number}
}

Rules:
- status is not_a_vehicle when the image is not a vehicle. Then set damage and estimate to null.
- status is unreadable when the photo is too unclear to judge.
- status is ok only when you can name the damage in one sentence, for example "left rear bumper dent with scratching".
- estimate is a range in whole USD with low less than or equal to high and at least one assumption. It is a visual guess, not a repair quote.
- plate.value is null unless a plate is clearly readable. Do not guess characters.
- Use null for make, model, or colour when you cannot tell. Do not invent them.
"""

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
                    {"type": "text", "text": PROMPT},
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
