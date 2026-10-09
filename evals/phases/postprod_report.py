"""Compare adjuster-labelled live claims with the pre-prod baseline.

    uv run python evals/phases/postprod_report.py

Joins .runtime/outcomes.jsonl to .runtime/labels.jsonl by claim_id (a label is a
dataset/history/claims.jsonl row plus claim_id) and scores each pair. Compares the rates
with .runtime/reports/preprod.json, prints the table, writes .runtime/reports/postprod.md,
and writes claims the adjuster corrected to .runtime/corrections.jsonl. No model call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.paths import CORRECTIONS, LABELS, OUTCOMES, POSTPROD as REPORT, PREPROD
from evals.score import score_with_adjuster_label

DIMENSIONS = ("damage_location", "damage_severity", "safe_pricing", "synthetic_range")
# Proposal: a post-prod rate more than 0.05 (absolute) below pre-prod is a regression.
TOLERANCE = 0.05
# Row schema of dataset/history/claims.jsonl.
HISTORY_FIELDS = (
    "claim_id", "plate", "make", "model", "colour", "damage_summary", "severity", "parts",
    "estimate_low", "estimate_high", "currency", "kept_dollars", "label", "image",
    "dataset_label", "source_file", "expected_status", "unpriceable", "failure_mode",
    "decoy_low", "decoy_high", "agent_make", "agent_model",
)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def join_by_claim_id(outcomes: list[dict], labels: list[dict]) -> tuple[list[tuple[dict, dict]], int]:
    """Pair each outcome with its label. Returns (pairs, unlabelled count)."""
    by_id = {row["claim_id"]: row for row in labels if row.get("claim_id")}
    pairs = [(o, by_id[o["claim_id"]]) for o in outcomes if o.get("claim_id") in by_id]
    return pairs, len(outcomes) - len(pairs)


def score_pairs(pairs: list[tuple[dict, dict]]) -> list[dict]:
    return [
        {"claim_id": outcome["claim_id"], "scores": score_with_adjuster_label(outcome, label)}
        for outcome, label in pairs
    ]


def applicable(score: dict) -> bool:
    return not score.get("not_applicable")


def rates(scored: list[dict]) -> dict[str, tuple[int, int]]:
    """Per dimension: (passed, scored). Dollar checks without a kept amount are skipped."""
    out = {}
    for name in DIMENSIONS:
        scores = [row["scores"][name] for row in scored if applicable(row["scores"][name])]
        if name == "synthetic_range":
            scores = [s for s in scores if not s.get("synthetic")]
        out[name] = (sum(1 for s in scores if s["test_pass"]), len(scores))
    return out


def failures(scored: list[dict]) -> list[tuple[str, str]]:
    return [
        (row["claim_id"], f"{name}: {score['reason']}")
        for row in scored
        for name, score in row["scores"].items()
        if applicable(score) and not score["test_pass"] and not (name == "synthetic_range" and score.get("synthetic"))
    ]


def _rate(passed: int, total: int) -> str:
    return f"{passed}/{total} ({passed / total:.0%})" if total else "n/a"


def load_preprod(path: Path = PREPROD) -> dict | None:
    """Latest pre-prod summary, or None when the file is missing or unreadable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def preprod_rate(preprod: dict | None, name: str) -> float | None:
    """Pre-prod mean for a dimension. Invented-dollar results are not a baseline."""
    entry = ((preprod or {}).get("quality") or {}).get(name) or (preprod or {}).get(name)
    if not isinstance(entry, dict) or entry.get("synthetic") or not isinstance(entry.get("mean"), (int, float)):
        return None
    return float(entry["mean"])


def compare(rate: dict[str, tuple[int, int]], preprod: dict | None) -> list[dict]:
    """One row per dimension: pre-prod, post-prod, delta, verdict, labelled sample size.

    Verdict is `regressed` when post-prod falls more than TOLERANCE below pre-prod,
    `ok` otherwise, and `n/a` when either side has no usable number.
    """
    rows = []
    for name, (passed, total) in rate.items():
        pre = preprod_rate(preprod, name)
        post = passed / total if total else None
        delta = post - pre if pre is not None and post is not None else None
        verdict = "n/a" if delta is None else "regressed" if delta < -TOLERANCE else "ok"
        rows.append({"metric": name, "pre": pre, "post": post, "delta": delta, "verdict": verdict, "n": total})
    return rows


def _num(value: float | None, signed: bool = False) -> str:
    return "n/a" if value is None else f"{value:+.2f}" if signed else f"{value:.2f}"


def table_cells(rows: list[dict]) -> list[list[str]]:
    header = ["Metric", "Pre-prod", "Post-prod", "Delta", "Verdict", "Labelled n"]
    body = [
        [r["metric"], _num(r["pre"]), _num(r["post"]), _num(r["delta"], True), r["verdict"], str(r["n"])]
        for r in rows
    ]
    return [header, *body]


def render_text_table(rows: list[dict]) -> str:
    cells = table_cells(rows)
    widths = [max(len(row[i]) for row in cells) for i in range(len(cells[0]))]
    line = lambda row: "  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip()
    return "\n".join([line(cells[0]), "  ".join("-" * w for w in widths), *map(line, cells[1:])])


def render_md_table(rows: list[dict]) -> str:
    cells = table_cells(rows)
    line = lambda row: "| " + " | ".join(row) + " |"
    return "\n".join([line(cells[0]), line(["---"] * len(cells[0])), *map(line, cells[1:])])


def corrections(pairs: list[tuple[dict, dict]], scored: list[dict]) -> list[dict]:
    """Labelled claims the agent got wrong, as dataset/history/claims.jsonl rows.

    The adjuster's values are the expected labels. `image` stays null: the log keeps no photo.
    """
    labels = {outcome["claim_id"]: label for outcome, label in pairs}
    rows = []
    for row in scored:
        wrong = any(
            applicable(s) and not s["test_pass"] and not (n == "synthetic_range" and s.get("synthetic"))
            for n, s in row["scores"].items()
        )
        if wrong:
            label = labels[row["claim_id"]]
            rows.append({key: label.get(key) for key in HISTORY_FIELDS} | {"claim_id": row["claim_id"]})
    return rows


def summary_lines(labelled: int, unlabelled: int, rows: list[dict], fixes: list[dict], has_preprod: bool) -> list[str]:
    lines = [f"Scored {labelled} claims; {unlabelled} unlabelled (not scored)."]
    if not has_preprod:
        lines.append(f"No pre-prod report at {PREPROD}; run `uv run python evals/phases/preprod.py` for a baseline.")
    if any(r["verdict"] == "regressed" for r in rows):
        ids = ", ".join(f["claim_id"] for f in fixes)
        lines.append(
            f"Regressed: {len(fixes)} claims below need to be added to the evaluation set; see {CORRECTIONS}. "
            f"The log keeps no photographs, so supply the image file for each: {ids}."
        )
    return lines


def render(
    labelled: int,
    unlabelled: int,
    rate: dict[str, tuple[int, int]],
    failed: list[tuple[str, str]],
    rows: list[dict] | None = None,
) -> str:
    lines = [
        "# Post-prod report",
        "",
        *([f"Post-prod against pre-prod (regressed: more than {TOLERANCE} below pre-prod).", "", render_md_table(rows), ""] if rows else []),
        f"Labelled: {labelled}. Unlabelled (not scored): {unlabelled}.",
        "",
        "| Dimension | Pass rate |",
        "| --- | --- |",
        *[f"| {name} | {_rate(*value)} |" for name, value in rate.items()],
        "",
        "## Failed pairs",
        "",
    ]
    if not failed:
        return "\n".join([*lines, "None.", ""])
    return "\n".join([*lines, "| claim_id | reason |", "| --- | --- |", *[f"| {c} | {r} |" for c, r in failed], ""])


def build_report(outcomes: list[dict], labels: list[dict], preprod: dict | None = None) -> str:
    return analyse(outcomes, labels, preprod)["report"]


def analyse(outcomes: list[dict], labels: list[dict], preprod: dict | None) -> dict:
    pairs, unlabelled = join_by_claim_id(outcomes, labels)
    scored = score_pairs(pairs)
    rows = compare(rates(scored), preprod)
    fixes = corrections(pairs, scored)
    return {
        "report": render(len(pairs), unlabelled, rates(scored), failures(scored), rows),
        "rows": rows,
        "corrections": fixes,
        "summary": summary_lines(len(pairs), unlabelled, rows, fixes, preprod is not None),
    }


def write_outputs(result: dict, report: Path = REPORT, corrections_path: Path = CORRECTIONS) -> None:
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(result["report"], encoding="utf-8")
    corrections_path.write_text("".join(json.dumps(r) + "\n" for r in result["corrections"]), encoding="utf-8")


def print_table(rows: list[dict], console=None) -> None:
    """Rich table on a terminal; the same columns as plain text when piped."""
    from rich.console import Console
    from rich.table import Table

    console = console or Console()
    colour = {"ok": "green", "regressed": "red", "n/a": "dim"}
    cells = table_cells(rows)
    table = Table(*cells[0])
    for cell in cells[1:]:
        table.add_row(*cell[:4], f"[{colour.get(cell[4], 'white')}]{cell[4]}[/]", cell[5])
    console.print(table)


def main() -> None:
    result = analyse(read_jsonl(OUTCOMES), read_jsonl(LABELS), load_preprod())
    write_outputs(result)
    print_table(result["rows"])
    print("\n".join(result["summary"]))
    print(f"{REPORT}\n{CORRECTIONS}")


if __name__ == "__main__":
    main()
