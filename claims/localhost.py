"""Local upload page on http://127.0.0.1:8080, served by the same app as claims/agent.py.

    uv run python -m claims.localhost

Reuses the page and checks from claims/public.py (the Lambda handler) and runs the claim in this
process through run_payload. The harness still calls Amazon Bedrock with your AWS credentials; there
is no AgentCore Runtime, microVM, API Gateway, or Lambda. Type the policy code (default "local",
or POLICY_CODE_ADMIN). Never deployed: deploy.py leaves this file out of the zip.
"""

from __future__ import annotations

import base64
import os

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

os.environ.setdefault("POLICY_CODE_ADMIN", "local")

from claims.agent import app, run_payload  # noqa: E402
from claims.public import handler  # noqa: E402


def local_invoke(image: bytes, kind: str) -> dict:
    return run_payload({"image_b64": base64.b64encode(image).decode("ascii"), "media_type": kind})


def serve(event: dict) -> Response:
    reply = handler(event, None, invoke=local_invoke)
    return Response(reply["body"], reply["statusCode"], reply["headers"])


async def page(request: Request) -> Response:
    body = (await request.body()).decode() if request.method == "POST" else None
    event = {"requestContext": {"http": {"method": request.method, "path": "/"}}, "body": body}
    return await run_in_threadpool(serve, event)


app.add_route("/", page, methods=["GET", "POST"])


if __name__ == "__main__":
    print("Local page at http://127.0.0.1:8080 (policy code: %s)" % os.environ["POLICY_CODE_ADMIN"], flush=True)
    app.run(host="127.0.0.1")
