"""Invoke the deployed claims harness (SigV4).

    uv run cli.py dataset/img/veh1.jpeg
    uv run cli.py --url https://example.com/car.jpg
    uv run cli.py --state
    uv run cli.py dataset/img/veh1.jpeg --json   # raw JSON, for scripts
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import uuid
from pathlib import Path

import boto3
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from claims.claim import media_type

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


def _text(value) -> str:
    return "n/a" if value in (None, "") else escape(str(value))


def render_claim(result: dict) -> Panel:
    """Compact view of one claim result: status, damage, estimate, partner facts."""
    status = result.get("status")
    colour = "green" if status == "ok" else "yellow"
    damage = result.get("damage") or {}
    estimate = result.get("estimate") or {}
    vehicle = result.get("vehicle") or {}
    plate = result.get("plate") or {}
    facts = result.get("partner_facts") or {}
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold")
    table.add_column()
    table.add_row("Status", f"[{colour}]{_text(status)}[/]")
    if result.get("error"):
        table.add_row("Error", f"[red]{escape(str(result['error']))}[/]")
    table.add_row("Vehicle", " ".join(_text(vehicle.get(k)) for k in ("colour", "make", "model")))
    table.add_row("Plate", _text(plate.get("value")))
    table.add_row("Damage", _text(damage.get("summary")))
    table.add_row("Parts", escape(", ".join(damage.get("parts") or [])) or "n/a")
    table.add_row("Severity", _text(damage.get("severity")))
    if estimate.get("low") is None:
        table.add_row("Estimate", "none")
    else:
        confidence = estimate.get("confidence")
        confidence_text = "n/a" if confidence is None else f"{confidence:.2f}"
        table.add_row(
            "Estimate",
            f"{estimate['low']:,}-{estimate['high']:,} {estimate.get('currency', 'USD')}  (confidence {confidence_text})",
        )
        table.add_row("Assumptions", escape("; ".join(estimate.get("assumptions") or [])) or "n/a")
    for name, value in facts.items():
        table.add_row(name.replace("_", " ").capitalize(), "n/a" if value is None else escape(json.dumps(value)))
    return Panel(Group(table), title=f"claim {_text(result.get('claim_id'))}", border_style=colour)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Invoke the deployed claims harness")
    parser.add_argument("image", nargs="?", type=Path, help="local JPEG/PNG/WebP path")
    parser.add_argument("--url", help="public image URL")
    parser.add_argument("--state", action="store_true", help="print harness state")
    parser.add_argument("--json", action="store_true", help="print the raw JSON result")
    args = parser.parse_args(argv)

    err = Console(stderr=True)
    try:
        session = boto3.Session()
        client = session.client("bedrock-agentcore")
        arn = runtime_arn(session)
    except Exception as exc:
        err.print(f"error: could not find the deployed runtime: {exc}", style="bold red", markup=False)
        return 1
    session_id = str(uuid.uuid4())

    if args.state:
        payload = {"command": "state"}
    elif args.url:
        payload = {"image_url": args.url}
    elif args.image is not None:
        data = args.image.read_bytes()
        kind = media_type(data)
        if kind is None:
            err.print("error: unsupported image type", style="bold red")
            return 1
        payload = {"image_b64": base64.b64encode(data).decode("ascii"), "media_type": kind}
    else:
        parser.error("provide an image path, --url, or --state")

    try:
        if args.json:
            result = next(invoke(client, arn, session_id, payload))
        else:
            with err.status("calling the deployed runtime..."):
                result = next(invoke(client, arn, session_id, payload))
    except Exception as exc:
        err.print(f"error: invoke failed: {exc}", style="bold red", markup=False)
        return 1

    if args.json or args.state:
        json.dump(result, sys.stdout, indent=2, default=str)
        print()
    else:
        Console().print(render_claim(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
