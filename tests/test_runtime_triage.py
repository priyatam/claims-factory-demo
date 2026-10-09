"""Runtime triage over logged claims. Does not call a model."""

import json

from claims.eval_log import record_outcome
from evals.phases.runtime import main, summarize, triage


def ok(claim_id="c1", low=1000, high=1500, confidence=0.9):
    return {
        "claim_id": claim_id,
        "status": "ok",
        "damage": {"summary": "dented door"},
        "estimate": {"low": low, "high": high, "confidence": confidence},
    }


def test_clean_rows_and_empty_input_are_not_flagged():
    assert triage([ok()]) == []
    assert triage([]) == []


def test_non_ok_status_is_flagged():
    rows = [{"claim_id": "a", "status": "unreadable", "estimate": None},
            {"claim_id": "b", "status": "not_a_vehicle", "estimate": None}]
    flagged = triage(rows)
    assert [f["reasons"] for f in flagged] == [["status unreadable"], ["status not_a_vehicle"]]


def test_ok_without_estimate_is_empty_claim():
    assert triage([{"claim_id": "a", "status": "ok", "estimate": None}])[0]["reasons"] == ["empty claim"]


def test_low_confidence_and_wide_range_are_flagged_with_locating_fields():
    row = {**ok("c9", low=500, high=3000, confidence=0.3), "logged_at": "2026-10-08T00:00:00+00:00"}
    (item,) = triage([row])
    assert len(item["reasons"]) == 2
    assert item["claim_id"] == "c9" and item["logged_at"] == row["logged_at"]
    assert (item["low"], item["high"]) == (500, 3000)


def test_summary_counts():
    rows = [ok(), ok("c2", confidence=0.2), {"claim_id": "u", "status": "unreadable", "estimate": None}]
    summary = summarize(rows, triage(rows))
    assert summary["statuses"] == {"ok": 2, "unreadable": 1}
    assert summary["flagged"] == 2
    assert abs(summary["empty_rate"] - 1 / 3) < 1e-9
    assert summary["median_range_width"] == 500


def test_main_handles_missing_log(tmp_path, capsys):
    report = tmp_path / "runtime.md"
    assert main(tmp_path / "none.jsonl", report) == 0
    assert "No logged claims" in capsys.readouterr().out
    assert not report.exists()


def test_main_writes_report(tmp_path, capsys):
    log = tmp_path / "outcomes.jsonl"
    log.write_text(json.dumps(ok()) + "\n" + json.dumps(ok("bad", confidence=0.1)) + "\nnot json\n")
    report = tmp_path / "reports" / "runtime.md"
    assert main(log, report) == 0
    text = report.read_text()
    assert "bad" in text and "low confidence" in text
    assert "Flagged: 1" in capsys.readouterr().out


def test_record_outcome_adds_timestamp_without_photo(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAIMS_EVAL_LOG", "1")
    monkeypatch.setattr("claims.eval_log.OUTCOMES", tmp_path / "o.jsonl")
    record_outcome({**ok(), "image_b64": "xx"})
    row = json.loads((tmp_path / "o.jsonl").read_text())
    assert "logged_at" in row and "image_b64" not in row and row["claim_id"] == "c1"
