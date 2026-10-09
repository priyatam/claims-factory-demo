"""Local test server on 127.0.0.1:8080 that calls Claude directly with ANTHROPIC_API_KEY.

It needs no AWS infrastructure: no AgentCore, Bedrock, API Gateway, or Lambda. Use it to try the
model and prompt on a photo from a browser. It is never deployed; deploy.py leaves it out of the zip.
"""

import base64
import html
import logging
import os
import uuid
from typing import Callable

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from claims.harness import USER_PROMPT
from claims.claim import MAX_BYTES, ClaimResult, empty_result, fetch_image, media_type, redact, run_claim

log = logging.getLogger("claims")

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


def render_page(result: ClaimResult | None) -> str:
    outcome = _outcome(result) if result else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vehicle claim</title>
<style>
body {{ font: 16px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 40rem; padding: 0 1rem; }}
label {{ display: block; margin: 0.8rem 0 0.25rem; }}
button {{ margin-top: 1rem; }}
.note {{ color: #444; }}
</style>
</head>
<body>
<h1>Vehicle claim</h1>
<p class="note">One photo. The cost is a visual range, not a settlement offer.</p>
<form action="/" method="post" enctype="multipart/form-data">
  <label for="file">Upload a photo</label>
  <input id="file" name="file" type="file" accept="image/jpeg,image/png,image/webp">
  <label for="url">Or an image URL</label>
  <input id="url" name="url" type="url" placeholder="https://">
  <button type="submit">Assess</button>
</form>
{outcome}
</body>
</html>"""


def _outcome(result: ClaimResult) -> str:
    plate = result["plate"]
    plate_line = "Plate detected" if plate["confidence"] is not None else "Plate not detected"
    lines = [
        f"<p>Status: {html.escape(result['status'])}</p>",
        f"<p>{plate_line}</p>",
    ]
    if result["status"] != "ok" or result["damage"] is None or result["estimate"] is None:
        if result["status"] == "not_a_vehicle":
            lines.append("<p>This does not look like a vehicle. No estimate.</p>")
        return "\n".join(lines)
    vehicle = result["vehicle"] or {}
    damage = result["damage"]
    estimate = result["estimate"]
    assumptions = "".join(f"<li>{html.escape(item)}</li>" for item in estimate["assumptions"])
    identity = " ".join(
        html.escape(part)
        for part in (vehicle.get("make"), vehicle.get("model"), vehicle.get("colour"))
        if part
    )
    lines.append(
        "<section>"
        f"<p>{identity or 'Vehicle not identified'}</p>"
        f"<p>{html.escape(damage['summary'])}</p>"
        f"<p>${estimate['low']}–${estimate['high']} {html.escape(estimate['currency'])}</p>"
        f"<ul>{assumptions}</ul>"
        "</section>"
    )
    return "\n".join(lines)


def create_app(assess: Callable | None = None, fetch: Callable | None = None) -> FastAPI:
    app = FastAPI()
    assess_fn = assess or assess_with_claude
    fetch_fn = fetch or fetch_image

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    def home():
        return render_page(None)

    @app.post("/", response_class=HTMLResponse)
    async def home_submit(request: Request):
        return render_page(await take(request, assess_fn, fetch_fn))

    @app.post("/claims")
    async def claims(request: Request):
        return JSONResponse(await take(request, assess_fn, fetch_fn))

    return app


async def take(request: Request, assess: Callable, fetch: Callable) -> ClaimResult:
    claim_id = uuid.uuid4().hex
    data, kind, problem = await _load(request, fetch)
    if problem == "missing":
        raise HTTPException(status_code=400, detail="Provide a photo or a URL")
    if problem == "fetch":
        result = empty_result(claim_id, "fetch_failed")
    elif problem == "unreadable" or data is None or kind is None:
        result = empty_result(claim_id, "unreadable")
    else:
        try:
            result = run_claim(data, kind, assess, claim_id)
        except Exception:
            log.warning("vision call failed for %s", claim_id)
            raise HTTPException(status_code=502, detail="The photo could not be assessed") from None
    log.info("claim %s", redact(result))
    return result


async def _load(request: Request, fetch: Callable) -> tuple[bytes | None, str | None, str | None]:
    content_type = request.headers.get("content-type", "")
    if "multipart/form-data" not in content_type and "application/x-www-form-urlencoded" not in content_type:
        return None, None, "missing"
    form = await request.form()
    upload = form.get("file")
    url = form.get("url")
    if upload is not None and getattr(upload, "filename", None):
        data = await upload.read()
        if data:
            if len(data) > MAX_BYTES:
                return None, None, "unreadable"
            kind = media_type(data)
            if kind is None:
                return None, None, "unreadable"
            return data, kind, None
    if isinstance(url, str) and url.strip():
        loaded = fetch(url.strip())
        if loaded is None:
            return None, None, "fetch"
        return loaded["data"], loaded["media_type"], None
    return None, None, "missing"


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()
