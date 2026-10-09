"""End-to-end check: pre-prod, runtime, and post-prod in one decision table.

    uv run python evals/e2e.py --live                  # all three stages on real Bedrock
    uv run python evals/e2e.py --live --limit 5        # pre-prod on a 5-photo sample
    uv run python evals/e2e.py --live --skip-preprod   # reuse .runtime/reports/preprod.json
    uv run python evals/e2e.py                         # no model call: what --live would run

Stages, in order:
  1. pre-prod: evals/phases/preprod.py on the evaluation set (or a sample).
  2. runtime: dataset/img/veh2.jpg through the harness in this process with
     CLAIMS_EVAL_LOG=1, then evals/phases/runtime.py flags the logged claim.
  3. post-prod: a simulated adjuster label, scored by evals/phases/postprod_report.py and
     compared with pre-prod.

The sample claim skips cli.py: cli.py calls the deployed runtime, whose outcome log is
ephemeral and not on this machine. The in-process call uses the same path as claims/agent.py.

Output goes to .runtime/reports/e2e/ (outcomes, labels, reports, run.log) and
.runtime/reports/e2e.md. Real logs and reports are untouched.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Callable
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claims.claim import media_type
from evals.phases import postprod_report as post
from evals.phases import runtime as rt
from evals.paths import E2E_DIR as OUT_DIR, E2E_REPORT as REPORT, PREPROD

IMAGE = _ROOT / "dataset" / "img" / "veh2.jpg"
CLAIM_ID = "e2e-veh2"
QUALITY = ("damage_location", "damage_severity", "safe_pricing")
# Proposal, not a measured limit: a pre-prod rate under this needs a harness fix.
FLOOR = 0.80
COST_NOTE = (
    "Pre-prod --live sends about 100 photos to Claude (about 2 minutes, real Bedrock cost); "
    "the sample claim is one more call. Use --limit N or --skip-preprod to cut it."
)

# SIMULATED. The sample has no real adjuster. This is a fixed fixture chosen to be plausible
# for a front-damage photo; it is not derived from veh2.jpg and is never real data.
SIMULATED_ADJUSTER_LABEL = {
    "severity": "moderate",
    "dataset_label": "F_Breakage",
    "parts": ["front"],
    "damage_summary": "front bumper damage",
    "expected_status": "ok",
    "unpriceable": False,
    "kept_dollars": None,
}


class E2EError(Exception):
    """A failure with a message fit to show the operator."""


# --- pure helpers -----------------------------------------------------------------


def sample_rows(rows: list[dict], limit: int | None) -> list[dict]:
    """Evenly spaced sample, so a small limit still covers every class."""
    if not limit or limit >= len(rows):
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def payload_for(image: Path) -> dict:
    """The same payload shape cli.py sends for a local image."""
    if not image.is_file():
        raise E2EError(f"sample image not found: {image}")
    data = image.read_bytes()
    kind = media_type(data)
    if kind is None:
        raise E2EError(f"unsupported image type: {image}")
    return {"claim_id": CLAIM_ID, "image_b64": base64.b64encode(data).decode("ascii"), "media_type": kind}


def preprod_rows(preprod: dict) -> list[dict]:
    rows = []
    for name in QUALITY:
        entry = (preprod.get("quality") or {}).get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get("mean"), (int, float)):
            rows.append({"metric": name, "score": "n/a", "n": "n/a", "signal": "n/a", "who": "-"})
            continue
        low = entry["mean"] < FLOOR
        rows.append(
            {
                "metric": name,
                "score": f"{entry['mean']:.2f}",
                "n": str(entry.get("count", "n/a")),
                "signal": f"below {FLOOR:.2f}" if low else "ok",
                "who": "operator" if low else "-",
            }
        )
    return rows


def estimate_text(outcome: dict) -> str:
    estimate = outcome.get("estimate")
    if not isinstance(estimate, dict) or estimate.get("low") is None:
        return "none"
    return f"{estimate['low']}-{estimate['high']} {estimate.get('currency', 'USD')}"


def confidence_text(outcome: dict) -> str:
    confidence = (outcome.get("estimate") or {}).get("confidence")
    return "n/a" if confidence is None else f"{confidence:.2f}"


def runtime_rows(outcome: dict, flagged: list[dict]) -> list[dict]:
    status = outcome.get("status")
    bad = status != "ok"
    why = "; ".join(flagged[0]["reasons"]) if flagged else ""
    return [
        {"check": "status", "result": str(status), "signal": "not ok" if bad else "ok", "who": "operator" if bad else "-"},
        {"check": "estimate range", "result": estimate_text(outcome), "signal": "n/a" if bad else "ok", "who": "-"},
        {"check": "confidence", "result": confidence_text(outcome), "signal": "ok", "who": "-"},
        {
            "check": "flagged",
            "result": f"yes: {why}" if flagged else "no",
            "signal": "flagged" if flagged else "ok",
            "who": "adjuster" if flagged and not bad else "-",
        },
    ]


def _fmt(value: float | None, signed: bool = False) -> str:
    return "n/a" if value is None else f"{value:+.2f}" if signed else f"{value:.2f}"


def postprod_rows(compared: list[dict]) -> list[dict]:
    return [
        {
            "metric": e["metric"],
            "pre": _fmt(e["pre"]),
            "post": _fmt(e["post"]),
            "delta": _fmt(e["delta"], True),
            "verdict": e["verdict"],
            "who": "adjuster" if e["verdict"] == "regressed" else "-",
        }
        for e in compared
        if e["metric"] in QUALITY
    ]


def next_action(pre: list[dict], post: list[dict], flagged: list[dict], fixes: list[dict]) -> str:
    low = [r["metric"] for r in pre if r["signal"].startswith("below")]
    if low:
        return f"fix harness (pre-prod {', '.join(low)} below {FLOOR:.2f})"
    if any(r["verdict"] == "regressed" for r in post):
        return f"attach photos for {len(fixes)} corrections"
    if flagged:
        return f"review {len(flagged)} flagged claim{'s' if len(flagged) != 1 else ''}"
    return "release"


def who_acts(*sections: list[dict]) -> list[str]:
    return sorted({r["who"] for rows in sections for r in rows} - {"-"})


def signal_style(signal: str) -> str:
    if signal == "ok":
        return "green"
    if signal == "n/a":
        return "dim"
    return "red"


def sections(pre: list[dict], run: list[dict], post: list[dict]) -> list[dict]:
    """The three tables as data: heading, header, rows, index of the coloured column, caption."""
    return [
        {
            "heading": "Pre-prod",
            "header": ["Metric", "Score", "n", "Signal"],
            "rows": [[r["metric"], r["score"], r["n"], r["signal"]] for r in pre],
            "signal": 3,
            "right": {1, 2},
        },
        {
            "heading": f"Runtime (one sample claim, {IMAGE.name})",
            "header": ["Check", "Result", "Signal"],
            "rows": [[r["check"], r["result"], r["signal"]] for r in run],
            "signal": 2,
            "right": set(),
        },
        {
            "heading": "Post-prod (1 simulated label)",
            "header": ["Metric", "Pre-prod", "Post-prod", "Delta", "Verdict"],
            "rows": [[r["metric"], r["pre"], r["post"], r["delta"], r["verdict"]] for r in post],
            "signal": 4,
            "right": {1, 2, 3},
            "caption": "One simulated label: post-prod rates are indicative only.",
        },
    ]


def rich_table(section: dict) -> Table:
    table = Table(*section["header"], caption=section.get("caption"), title=section["heading"], title_justify="left")
    for i in section["right"]:
        table.columns[i].justify = "right"
    for cells in section["rows"]:
        cells = list(cells)
        cells[section["signal"]] = Text(cells[section["signal"]], style=signal_style(cells[section["signal"]]))
        table.add_row(*cells)
    return table


def action_panel(action: str, who: list[str]) -> Panel:
    body = Text(f"Next action: {action}", style="bold")
    if who:
        body.append(f"\nWho acts: {', '.join(who)}")
    return Panel(body, expand=False)


def render_markdown(parts: list[dict], action: str, who: list[str]) -> str:
    line = lambda cells: "| " + " | ".join(cells) + " |"
    lines = ["# End-to-end run", ""]
    for part in parts:
        aligns = ["---:" if i in part["right"] else "---" for i in range(len(part["header"]))]
        lines += [f"## {part['heading']}", "", line(part["header"]), line(aligns), *map(line, part["rows"]), ""]
        if part.get("caption"):
            lines += [f"_{part['caption']}_", ""]
    lines.append(f"**Next action:** {action}")
    if who:
        lines.append(f"\nWho acts: {', '.join(who)}")
    return "\n".join([*lines, ""])


# --- side effects at the edge -----------------------------------------------------


@contextlib.contextmanager
def captured_logs(log_file: Path, verbose: bool):
    """Send library logging and stderr to log_file; --verbose also keeps the console output."""
    log_file.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    saved_handlers, saved_level = list(root.handlers), root.level
    if not verbose:
        root.handlers = []
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    try:
        if verbose:
            yield
        else:
            with log_file.open("a", encoding="utf-8") as sink, contextlib.redirect_stderr(sink):
                yield
    finally:
        root.removeHandler(handler)
        handler.close()
        root.handlers = saved_handlers
        root.setLevel(saved_level)


def local_invoke(payload: dict) -> dict:
    """One claim through the harness in this process, as claims/agent.py invoke does."""
    from claims.agent import get_agent, load_image
    from claims.telemetry import record_outcome
    from claims.harness import run_claim_with_agent

    image = load_image(payload)
    if image is None:
        raise E2EError("sample image could not be decoded")
    result = run_claim_with_agent(get_agent(), image[0], image[1], claim_id=payload.get("claim_id"))
    record_outcome(result)
    return result


def live_preprod(limit: int | None, path: Path, on_step: Callable[[int, int], None]) -> dict:
    """Run evals/phases/preprod.py on the photos (or a sample) and return its summary document."""
    from evals.phases import preprod

    rows = sample_rows(preprod.load_claims(), limit)
    pattern = re.compile(r"^\[(\d+)/(\d+)\] .*Claude status=")
    original = preprod.LOG

    def watch(message: str) -> None:
        found = pattern.match(message)
        if found:
            on_step(int(found.group(1)), int(found.group(2)))

    preprod.LOG = watch
    try:
        preprod.run_live(rows, path=path, workers=max(1, min(100, len(rows))), history=False)
    finally:
        preprod.LOG = original
    return json.loads(path.read_text(encoding="utf-8"))


def aws_credentials_ok() -> bool:
    import boto3

    return boto3.Session().get_credentials() is not None


# --- orchestration ----------------------------------------------------------------


def preview(console: Console, limit: int | None, skip_preprod: bool, preprod_path: Path, image: Path) -> int:
    """No model call: say what --live runs and show the saved pre-prod summary, if any."""
    size = f"{limit} photos" if limit else "about 100 photos"
    steps = [
        "1. pre-prod: " + ("reuse the saved report" if skip_preprod else f"score {size} with evals/phases/preprod.py"),
        f"2. runtime: send {image.name} through the claims harness, then triage the log",
        "3. post-prod: score a simulated adjuster label and compare with pre-prod",
    ]
    console.print(Panel("\n".join([*steps, "", COST_NOTE]), title="e2e (no model call)"))
    saved = post.load_preprod(preprod_path)
    if saved:
        console.print(Text(f"Saved pre-prod summary ({preprod_path.name}):", style="bold"))
        console.print(rich_table(sections(preprod_rows(saved), [], [])[0]))
    console.print("Run the end-to-end check with: uv run python evals/e2e.py --live")
    return 0


def run_e2e(
    *,
    console: Console,
    limit: int | None = None,
    skip_preprod: bool = False,
    verbose: bool = False,
    out_dir: Path = OUT_DIR,
    report: Path = REPORT,
    preprod_path: Path = PREPROD,
    image: Path = IMAGE,
    label: dict = SIMULATED_ADJUSTER_LABEL,
    run_preprod: Callable[[int | None, Path, Callable[[int, int], None]], dict] = live_preprod,
    invoke: Callable[[dict], dict] = local_invoke,
    credentials_ok: Callable[[], bool] = aws_credentials_ok,
) -> int:
    """The three live stages. Raises E2EError with a message for the operator."""
    payload = payload_for(image)
    if not credentials_ok():
        raise E2EError("no AWS credentials found; configure them and retry")
    if skip_preprod and post.load_preprod(preprod_path) is None:
        raise E2EError(f"no pre-prod report at {preprod_path}; run without --skip-preprod first")
    console.print(COST_NOTE if not skip_preprod else "Reusing the saved pre-prod report; one Claude call for the sample claim.", style="dim")
    out_dir.mkdir(parents=True, exist_ok=True)
    outcomes, labels = out_dir / "outcomes.jsonl", out_dir / "labels.jsonl"
    outcomes.unlink(missing_ok=True)

    with captured_logs(out_dir / "run.log", verbose):
        console.rule("1/3 pre-prod")
        if skip_preprod:
            preprod = post.load_preprod(preprod_path)
            pre_path, done = preprod_path, "reused saved report"
        else:
            pre_path = out_dir / "preprod.json"
            with console.status("scoring photos with Claude...") as status:
                preprod = run_preprod(
                    limit, pre_path, lambda n, total: status.update(f"scoring photos with Claude... {n}/{total}")
                )
            done = "scored with Claude"
        pre = preprod_rows(preprod)
        console.print(f"[green]✓[/] pre-prod {done}: " + ", ".join(f"{r['metric']} {r['score']}" for r in pre))

        console.rule("2/3 runtime")
        env = {"CLAIMS_EVAL_LOG": "1", "CLAIMS_EVAL_LOG_PATH": str(outcomes)}
        with console.status("running the sample claim..."), mock.patch.dict(os.environ, env):
            result = invoke(payload)
        if result.get("error"):
            raise E2EError(f"harness error: {result['error']}")
        logged = rt.load_rows(outcomes)
        if not logged:
            raise E2EError(f"no outcome was logged at {outcomes}")
        flagged = rt.triage(logged)
        (out_dir / "runtime.md").write_text(rt.report_markdown(rt.summarize(logged, flagged), flagged), encoding="utf-8")
        run = runtime_rows(logged[-1], flagged)
        mark = "[yellow]![/]" if flagged else "[green]✓[/]"
        console.print(f"{mark} runtime: status {run[0]['result']}, {run[1]['result']}, confidence {run[2]['result']}, flagged: {run[3]['result']}")

        console.rule("3/3 post-prod (simulated label)")
        labels.write_text(json.dumps({**label, "claim_id": CLAIM_ID}) + "\n", encoding="utf-8")
        with console.status("scoring the label..."):
            analysed = post.analyse(post.read_jsonl(outcomes), post.read_jsonl(labels), preprod)
            post.write_outputs(analysed, out_dir / "postprod.md", out_dir / "corrections.jsonl")
        posts = postprod_rows(analysed["rows"])
        verdicts = [r["verdict"] for r in posts]
        counts = ", ".join(f"{v} {verdicts.count(v)}" for v in dict.fromkeys(verdicts))
        console.print(f"[green]✓[/] post-prod: 1 labelled (simulated); {counts}")

    parts = sections(pre, run, posts)
    action = next_action(pre, posts, flagged, analysed["corrections"])
    who = who_acts(pre, run, posts)
    console.print()
    for part in parts:
        console.print(rich_table(part))
    console.print(action_panel(action, who))
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_markdown(parts, action, who), encoding="utf-8")
    console.print(f"Wrote {report}; full log in {out_dir / 'run.log'}", style="dim")
    return 0


def main(argv: list[str] | None = None, *, console: Console | None = None, **overrides) -> int:
    parser = argparse.ArgumentParser(description="Pre-prod, runtime, and post-prod in one decision table.")
    parser.add_argument("--live", action="store_true", help="Run all stages on real Bedrock. Without it nothing calls a model.")
    parser.add_argument("--limit", type=int, help="Score only N pre-prod photos (an even sample).")
    parser.add_argument("--skip-preprod", action="store_true", help="Reuse .runtime/reports/preprod.json.")
    parser.add_argument("--verbose", action="store_true", help="Show library logs on the console too.")
    args = parser.parse_args(argv)
    console = console or Console(width=min(100, Console().width))
    try:
        if not args.live:
            return preview(
                console,
                args.limit,
                args.skip_preprod,
                overrides.get("preprod_path", PREPROD),
                overrides.get("image", IMAGE),
            )
        return run_e2e(console=console, limit=args.limit, skip_preprod=args.skip_preprod, verbose=args.verbose, **overrides)
    except E2EError as exc:
        console.print(f"error: {exc}", style="bold red", markup=False)
        return 1


if __name__ == "__main__":
    sys.exit(main())
