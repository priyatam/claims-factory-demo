"""Flag live claims that need review: status not ok, no estimate, low confidence, wide range.

    uv run python evals/phases/runtime.py

Reads .runtime/outcomes.jsonl (written when CLAIMS_EVAL_LOG=1), prints a summary, and
writes .runtime/reports/runtime.md. No model call.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evals.paths import OUTCOMES, RUNTIME_REPORT as REPORT

# Proposals, not measured limits. Tune them against the pre-prod report.
MIN_CONFIDENCE = 0.6
MAX_RANGE_RATIO = 1.0  # (high - low) / midpoint


def load_rows(path: Path = OUTCOMES) -> list[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def range_ratio(estimate: dict) -> float | None:
    low, high = estimate.get("low"), estimate.get("high")
    if not isinstance(low, (int, float)) or not isinstance(high, (int, float)):
        return None
    middle = (low + high) / 2
    return (high - low) / middle if middle > 0 else None


def reasons(row: dict, min_confidence: float, max_ratio: float) -> list[str]:
    status = row.get("status")
    if status != "ok":
        return [f"status {status}"]
    estimate = row.get("estimate")
    if not isinstance(estimate, dict):
        return ["empty claim"]
    found = []
    confidence = estimate.get("confidence")
    if isinstance(confidence, (int, float)) and confidence < min_confidence:
        found.append(f"low confidence {confidence:.2f}")
    ratio = range_ratio(estimate)
    if ratio is not None and ratio > max_ratio:
        found.append(f"wide range {ratio:.2f} of midpoint")
    return found


def triage(
    rows: list[dict],
    min_confidence: float = MIN_CONFIDENCE,
    max_ratio: float = MAX_RANGE_RATIO,
) -> list[dict]:
    """One entry per flagged claim, with the fields an operator needs to find it."""
    flagged = []
    for row in rows:
        found = reasons(row, min_confidence, max_ratio)
        if not found:
            continue
        estimate = row.get("estimate") or {}
        flagged.append(
            {
                "claim_id": row.get("claim_id"),
                "logged_at": row.get("logged_at"),
                "status": row.get("status"),
                "reasons": found,
                "low": estimate.get("low"),
                "high": estimate.get("high"),
                "confidence": estimate.get("confidence"),
                "damage": (row.get("damage") or {}).get("summary"),
            }
        )
    return flagged


def summarize(rows: list[dict], flagged: list[dict]) -> dict:
    statuses: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status"))
        statuses[key] = statuses.get(key, 0) + 1
    estimates = [row["estimate"] for row in rows if isinstance(row.get("estimate"), dict)]
    confidences = [e["confidence"] for e in estimates if isinstance(e.get("confidence"), (int, float))]
    widths = [e["high"] - e["low"] for e in estimates if range_ratio(e) is not None]
    total = len(rows)
    return {
        "claims": total,
        "statuses": statuses,
        "empty_rate": (total - len(estimates)) / total if total else 0.0,
        "mean_confidence": statistics.fmean(confidences) if confidences else None,
        "low_confidence": sum(1 for c in confidences if c < MIN_CONFIDENCE),
        "median_range_width": statistics.median(widths) if widths else None,
        "flagged": len(flagged),
    }


def _num(value, spec: str = ".2f") -> str:
    return "n/a" if value is None else format(value, spec)


def summary_lines(summary: dict) -> list[str]:
    counts = ", ".join(f"{k} {v}" for k, v in sorted(summary["statuses"].items())) or "none"
    return [
        f"Claims logged: {summary['claims']} ({counts})",
        f"Empty-claim rate: {summary['empty_rate']:.0%}",
        f"Mean confidence: {_num(summary['mean_confidence'])}; below {MIN_CONFIDENCE}: {summary['low_confidence']}",
        f"Median range width: {_num(summary['median_range_width'], ',.0f')}",
        f"Flagged: {summary['flagged']}",
    ]


def report_markdown(summary: dict, flagged: list[dict]) -> str:
    lines = ["# Runtime triage", "", *[f"- {line}" for line in summary_lines(summary)], ""]
    lines += [
        f"Flag rules (proposals): status not ok, no estimate, confidence below {MIN_CONFIDENCE}, "
        f"range wider than {MAX_RANGE_RATIO} of its midpoint.",
        "",
    ]
    if not flagged:
        return "\n".join([*lines, "No claims flagged.", ""])
    lines += ["| claim_id | logged_at | status | reasons | range | confidence | damage |", "|---|---|---|---|---|---|---|"]
    for item in flagged:
        span = "" if item["low"] is None else f"{item['low']}-{item['high']}"
        lines.append(
            f"| {item['claim_id']} | {item['logged_at'] or 'n/a'} | {item['status']} | "
            f"{'; '.join(item['reasons'])} | {span} | {_num(item['confidence'])} | "
            f"{(item['damage'] or '').replace('|', '/')} |"
        )
    return "\n".join([*lines, ""])


def main(path: Path = OUTCOMES, report: Path = REPORT) -> int:
    rows = load_rows(path)
    if not rows:
        print(f"No logged claims at {path}. Run the harness with CLAIMS_EVAL_LOG=1 first.")
        return 0
    flagged = triage(rows)
    summary = summarize(rows, flagged)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(report_markdown(summary, flagged), encoding="utf-8")
    print("\n".join(summary_lines(summary)))
    print(f"Wrote {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
