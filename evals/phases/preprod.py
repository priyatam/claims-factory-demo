"""Score the harness on the evaluation set (dataset/history).

    uv run python evals/phases/preprod.py --live

Runs each photo through the harness, scores the answer with Strands evaluators and an
LLM judge, and writes .runtime/reports/preprod.json and report.md. Saved answers in
.runtime/reports/task_results are reused unless --refresh is passed. Does not retrain Claude.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from strands_evals import Case, Experiment, LocalFileTaskResultStore, eval_task

from evals.paths import PREPROD, PREPROD_HISTORY, TASK_RESULTS
from evals.evaluators import claim_evaluators, output_judge
from evals.telemetry import configure_evals_telemetry

HISTORY = _ROOT / "dataset" / "history"
CLAIMS = HISTORY / "claims.jsonl"
IMAGES = HISTORY / "images"
REPORT = PREPROD
HISTORY_REPORT = PREPROD_HISTORY

QUALITY = ("damage_location", "damage_severity", "safe_pricing")
SYNTHETIC_FIELDS = {
    "kept_dollars": "Invented by dataset/fetch_history.py. Not an adjuster's kept amount.",
    "estimate_low": "Invented band on the history row. Not an adjuster range.",
    "estimate_high": "Invented band on the history row. Not an adjuster range.",
}


def load_claims() -> list[dict]:
    rows = []
    for line in CLAIMS.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def label_from_row(row: dict) -> dict:
    return {
        "parts": row["parts"],
        "severity": row["severity"],
        "damage_summary": row["damage_summary"],
        "dataset_label": row["dataset_label"],
        "expected_status": row["expected_status"],
        "unpriceable": row["unpriceable"],
        "kept_dollars": row["kept_dollars"],
        "dollars_synthetic": True,
    }


def case_from(row: dict) -> Case:
    return Case[dict, dict](
        name=row["claim_id"],
        input={"image": str(IMAGES / row["image"]), "media_type": "image/jpeg"},
        expected_output=label_from_row(row),
        metadata={
            "dataset_label": row["dataset_label"],
            "dollars_synthetic": True,
            "failure_mode": row["failure_mode"],
        },
    )


def experiment_from(rows: list[dict], *, judge: bool = False) -> Experiment:
    evaluators = [*claim_evaluators(), output_judge()] if judge else claim_evaluators()
    return Experiment[dict, dict](cases=[case_from(row) for row in rows], evaluators=evaluators)


def task_results_store(refresh: bool = False, root: Path | None = None) -> LocalFileTaskResultStore:
    """Saved Claude answers, one JSON file per claim. --refresh drops them first."""
    directory = root or TASK_RESULTS
    if refresh and directory.exists():
        for path in directory.glob("*.json"):
            path.unlink()
    return LocalFileTaskResultStore(directory)


def jpeg_ok(path: Path) -> bool:
    if not path.is_file():
        return False
    data = path.read_bytes()
    return data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9") and 8_000 <= len(data) <= 400_000


def in_range(row: dict) -> bool:
    kept = row["kept_dollars"]
    return row["estimate_low"] <= kept <= row["estimate_high"]


def offline_score(rows: list[dict]) -> dict:
    missing = [row["claim_id"] for row in rows if not jpeg_ok(IMAGES / row["image"])]
    outside = [row["claim_id"] for row in rows if not in_range(row)]
    ids = [row["claim_id"] for row in rows]
    return {
        "count": len(rows),
        "unique_ids": len(set(ids)),
        "images_ok": len(rows) - len(missing),
        "in_range": len(rows) - len(outside),
        "missing": missing,
        "outside": outside,
        "ok": len(rows) == 100 and len(set(ids)) == 100 and not missing and not outside,
    }


def _print(message: str) -> None:
    print(message, flush=True)


# Progress lines go through LOG so evals/e2e.py can swap in a progress bar.
LOG = _print


def _log(message: str) -> None:
    LOG(message)


_count_lock = threading.Lock()


def _take_number() -> tuple[int, int | str]:
    with _count_lock:
        assess_claim.n = getattr(assess_claim, "n", 0) + 1
        return assess_claim.n, getattr(assess_claim, "total", "?")


def _status_from(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return "no json"
    try:
        payload = json.loads(text[start:])
    except json.JSONDecodeError:
        return "no json"
    status = payload.get("status")
    return status if isinstance(status, str) else "no status"


@eval_task()
def assess_claim(case: Case) -> str:
    """Run the harness on one photo and return Claude's text. Called only from --live."""
    from claims.harness import build_agent, content_for

    number, total = _take_number()
    _log(f"[{number}/{total}] {case.name}: sending photo to Claude")
    image = Path(case.input["image"]).read_bytes()
    media_type = case.input.get("media_type", "image/jpeg")
    content = content_for(image, media_type)
    if content is None:
        _log(f"[{number}/{total}] {case.name}: photo could not be read")
        return json.dumps({"status": "unreadable", "damage": None, "estimate": None})
    text = str(build_agent()(content))
    _log(f"[{number}/{total}] {case.name}: Claude status={_status_from(text)}")
    return text


def dimension_rates(report: dict) -> dict:
    grouped: dict[str, list[float]] = {}
    for case, score in zip(report.get("cases") or [], report.get("scores") or []):
        grouped.setdefault(case.get("evaluator", "unknown"), []).append(float(score))
    rates = {}
    for name, values in grouped.items():
        rates[name] = {
            "count": len(values),
            "mean": sum(values) / len(values) if values else 0.0,
            "synthetic": name == "synthetic_range",
        }
    return rates


def report_document(report: dict) -> dict:
    rates = dimension_rates(report)
    return {
        "retrains_claude": False,
        "scored": "Claude's text from the claims harness on each photograph",
        "quality_note": "quality is damage location, severity, and safe pricing. synthetic_range uses invented dollars and stays out of quality.",
        "identity": "Make, model, and colour are unlabeled, so identity is not scored.",
        "quality": {name: rates[name] for name in QUALITY if name in rates},
        "synthetic_fields": SYNTHETIC_FIELDS,
        "synthetic_range": rates.get("synthetic_range"),
        "evaluation_report": report,
    }


def history_entry(document: dict, when: datetime) -> str:
    count = 0
    for rate in document["quality"].values():
        count = rate["count"]
        break
    location = document["quality"].get("damage_location", {})
    severity = document["quality"].get("damage_severity", {})
    safe_pricing = document["quality"].get("safe_pricing", {})
    dollars = document.get("synthetic_range") or {}
    stamp = when.strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            f"## {stamp}",
            "",
            f"{count} damaged-car photos were sent to Claude at once. Each photo is labeled front or rear, and breakage or crushed. The dollar amounts in the notes were invented and are not a repair bill.",
            "",
            f"1. Damage location: {location.get('mean', 0):.2f}. Claude named front or rear correctly on about {round(location.get('mean', 0) * count)} of {count} photos.",
            f"2. Damage severity: {severity.get('mean', 0):.2f}. Claude matched breakage versus crushed on about {round(severity.get('mean', 0) * count)} of {count} photos.",
            f"3. Safe pricing: {safe_pricing.get('mean', 0):.2f}. Claude did not price a photo that should not be priced.",
            f"4. Dollar range: {dollars.get('mean', 0):.2f}. Claude’s low-to-high covered the invented amount on about {round(dollars.get('mean', 0) * count)} photos. This is not a quality grade.",
            "",
        ]
    )


def append_history_report(document: dict, when: datetime | None = None) -> Path:
    """Add one timestamped entry. Earlier entries stay."""
    when = when or datetime.now().astimezone()
    entry = history_entry(document, when)
    previous = HISTORY_REPORT.read_text(encoding="utf-8") if HISTORY_REPORT.exists() else ""
    HISTORY_REPORT.parent.mkdir(parents=True, exist_ok=True)
    if previous.strip():
        HISTORY_REPORT.write_text(previous.rstrip() + "\n\n" + entry, encoding="utf-8")
    else:
        HISTORY_REPORT.write_text(entry, encoding="utf-8")
    return HISTORY_REPORT


def write_report(report, path: Path = REPORT) -> Path:
    document = report_document(report.to_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return path


def run_live(
    rows: list[dict],
    path: Path = REPORT,
    workers: int = 100,
    refresh: bool = False,
    history: bool = True,
) -> Path:
    configure_evals_telemetry()
    assess_claim.n = 0
    assess_claim.total = len(rows)
    store = task_results_store(refresh=refresh)
    if refresh:
        _log("Ignoring saved answers and calling Claude again.")
    else:
        _log(f"Reusing saved answers from {TASK_RESULTS} when a photo was already scored.")
    _log(f"Starting {len(rows)} photos, {workers} at a time. The score file is written at the end.")
    report = asyncio.run(
        experiment_from(rows, judge=True).run_evaluations_async(
            assess_claim, max_workers=workers, evaluation_data_store=store
        )
    )
    saved = write_report(report, path)
    document = report_document(report.to_dict())
    _log(f"Wrote {saved}")
    if history:
        _log(f"Appended {append_history_report(document)}")
    for name, rate in document["quality"].items():
        _log(f"score {name}: {rate['mean']:.2f} across {rate['count']} photos")
    if document.get("synthetic_range"):
        rate = document["synthetic_range"]
        _log(f"score synthetic_range: {rate['mean']:.2f} across {rate['count']} photos (invented dollars, not quality)")
    return saved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score Claude's claim answers against damage labels.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run the harness on each photo and write .runtime/reports/preprod.json.",
    )
    parser.add_argument("--workers", type=int, default=100, help="How many photos to send to Claude at once.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore saved Claude answers in .runtime/reports/task_results and call Claude again.",
    )
    args = parser.parse_args(argv)
    if not args.live:
        print("Score Claude's answers with: python evals/phases/preprod.py --live")
        print("The report is written to .runtime/reports/preprod.json. This does not retrain Claude.")
        print("A second --live reuses .runtime/reports/task_results. Pass --refresh to call Claude again.")
        return 0
    path = run_live(load_claims(), workers=max(1, args.workers), refresh=args.refresh)
    _log(f"Done. Report: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
