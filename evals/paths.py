"""Paths the eval scripts read and write under .runtime/. The root is set in claims/eval_log.py."""

from claims.eval_log import OUTCOMES, RUNTIME_ROOT

REPORTS = RUNTIME_ROOT / "reports"
LABELS = RUNTIME_ROOT / "labels.jsonl"
POSTPROD = REPORTS / "postprod.md"
CORRECTIONS = RUNTIME_ROOT / "corrections.jsonl"
PREPROD = REPORTS / "preprod.json"
PREPROD_HISTORY = REPORTS / "report.md"
TASK_RESULTS = REPORTS / "task_results"
RUNTIME_REPORT = REPORTS / "runtime.md"
E2E_DIR = REPORTS / "e2e"
E2E_REPORT = REPORTS / "e2e.md"

__all__ = [n for n in dir() if n.isupper()]
