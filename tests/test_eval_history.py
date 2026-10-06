"""Shape of the synthetic claim history. Does not call a model."""

import json
from pathlib import Path

HISTORY = Path(__file__).resolve().parents[1] / "evals" / "history"
CLAIMS = HISTORY / "claims.jsonl"
IMAGES = HISTORY / "images"


def _rows() -> list[dict]:
    return [json.loads(line) for line in CLAIMS.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_history_has_100_in_range_claims_and_jpegs():
    rows = _rows()
    assert len(rows) == 100
    ids = [row["claim_id"] for row in rows]
    assert len(set(ids)) == 100
    names = [row["image"] for row in rows]
    assert len(set(names)) == 100
    assert {path.name for path in IMAGES.iterdir() if path.is_file()} == set(names)
    for row in rows:
        assert row["estimate_low"] <= row["kept_dollars"] <= row["estimate_high"]
        assert row["make"] is None and row["model"] is None
        assert row["dataset_label"] in {"F_Breakage", "F_Crushed", "R_Breakage", "R_Crushed"}
        data = (IMAGES / row["image"]).read_bytes()
        assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")
        assert 8_000 <= len(data) <= 400_000
