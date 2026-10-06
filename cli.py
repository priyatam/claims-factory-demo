"""Invoke the deployed claims harness (SigV4).

    uv run cli.py img/veh1.jpeg
    uv run cli.py --url https://example.com/car.jpg
    uv run cli.py --state
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import uuid
from pathlib import Path

import boto3

from claims.images import media_type

STACK_NAME = "ClaimsFactoryHarness"


def runtime_arn(session: boto3.Session) -> str:
    outputs = session.client("cloudformation").describe_stacks(StackName=STACK_NAME)["Stacks"][0][
        "Outputs"
    ]
    return next(o["OutputValue"] for o in outputs if o["OutputKey"] == "RuntimeArn")


def invoke(client, arn: str, session_id: str, payload: dict):
    response = client.invoke_agent_runtime(
        agentRuntimeArn=arn,
        runtimeSessionId=session_id,
        payload=json.dumps(payload).encode(),
    )
    for line in response["response"].iter_lines():
        if line.startswith(b"data: "):
            yield json.loads(line[6:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke the deployed claims harness")
    parser.add_argument("image", nargs="?", type=Path, help="local JPEG/PNG/WebP path")
    parser.add_argument("--url", help="public image URL")
    parser.add_argument("--state", action="store_true", help="print harness state")
    args = parser.parse_args(argv)

    session = boto3.Session()
    client = session.client("bedrock-agentcore")
    arn = runtime_arn(session)
    session_id = str(uuid.uuid4())

    if args.state:
        json.dump(next(invoke(client, arn, session_id, {"command": "state"})), sys.stdout, indent=2, default=str)
        print()
        return 0

    if args.url:
        payload = {"image_url": args.url}
    elif args.image is not None:
        data = args.image.read_bytes()
        kind = media_type(data)
        if kind is None:
            print("unsupported image type", file=sys.stderr)
            return 1
        payload = {"image_b64": base64.b64encode(data).decode("ascii"), "media_type": kind}
    else:
        parser.error("provide an image path, --url, or --state")

    json.dump(next(invoke(client, arn, session_id, payload)), sys.stdout, indent=2)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
