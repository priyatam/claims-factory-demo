from evals.phases.postprod_report import (
    TOLERANCE,
    analyse,
    build_report,
    compare,
    corrections,
    failures,
    join_by_claim_id,
    rates,
    render_md_table,
    render_text_table,
    score_pairs,
    summary_lines,
)

PREPROD = {
    "quality": {
        "damage_location": {"mean": 0.98, "synthetic": False},
        "damage_severity": {"mean": 0.80, "synthetic": False},
        "safe_pricing": {"mean": 1.0, "synthetic": False},
    },
    "synthetic_range": {"mean": 0.17, "synthetic": True},
}


def outcome(cid, severity="minor", text="front bumper", low=100, high=300):
    return {
        "claim_id": cid,
        "status": "ok",
        "damage": {"severity": severity, "summary": text, "parts": []},
        "estimate": {"low": low, "high": high},
    }


def label(cid, severity="minor", dataset="F_Minor", kept=None):
    return {
        "claim_id": cid,
        "severity": severity,
        "dataset_label": dataset,
        "parts": [],
        "damage_summary": "",
        "expected_status": "ok",
        "unpriceable": False,
        "kept_dollars": kept,
    }


def test_join_counts_unlabelled():
    pairs, unlabelled = join_by_claim_id([outcome("a"), outcome("b")], [label("a"), label("z")])
    assert [o["claim_id"] for o, _ in pairs] == ["a"]
    assert unlabelled == 1


def test_rates_and_failed_pair():
    pairs, _ = join_by_claim_id(
        [outcome("a"), outcome("b", severity="severe")], [label("a"), label("b", severity="minor")]
    )
    scored = score_pairs(pairs)
    r = rates(scored)
    assert r["damage_location"] == (2, 2)
    assert r["damage_severity"] == (1, 2)
    assert r["synthetic_range"] == (0, 0)
    assert [c for c, _ in failures(scored)] == ["b"]


def test_range_scored_only_when_kept_and_not_synthetic():
    pairs, _ = join_by_claim_id([outcome("a"), outcome("b")], [label("a", kept=200), label("b", kept=900)])
    r = rates(score_pairs(pairs))
    assert r["synthetic_range"] == (1, 2)


def test_report_text():
    text = build_report([outcome("a"), outcome("b")], [label("a", severity="severe")])
    assert "Labelled: 1. Unlabelled (not scored): 1." in text
    assert "| a | damage_severity:" in text


def by_metric(rows):
    return {r["metric"]: r for r in rows}


def test_compare_ok_regressed_and_na():
    rate = {"damage_location": (98, 100), "damage_severity": (1, 2), "safe_pricing": (0, 0), "synthetic_range": (1, 2)}
    rows = by_metric(compare(rate, PREPROD))
    assert rows["damage_location"]["verdict"] == "ok"
    assert rows["damage_severity"]["verdict"] == "regressed"
    assert rows["damage_severity"]["delta"] == 0.5 - 0.8
    assert rows["damage_severity"]["n"] == 2
    assert rows["safe_pricing"]["verdict"] == "n/a"  # no post-prod data
    assert rows["synthetic_range"]["verdict"] == "n/a"  # pre-prod dollars were invented


def test_compare_within_tolerance_is_ok():
    rows = by_metric(compare({"damage_severity": (76, 100)}, PREPROD))
    assert 0.80 - 0.76 < TOLERANCE
    assert rows["damage_severity"]["verdict"] == "ok"


def test_compare_missing_preprod():
    rows = compare({"damage_location": (1, 1)}, None)
    assert rows[0]["verdict"] == "n/a" and rows[0]["pre"] is None
    lines = summary_lines(1, 0, rows, [], has_preprod=False)
    assert "No pre-prod report" in lines[1]


def test_corrections_use_adjuster_values_and_history_schema():
    pairs, _ = join_by_claim_id(
        [outcome("a"), outcome("b", severity="severe"), outcome("c", low=100, high=300)],
        [label("a"), label("b", severity="minor"), label("c", kept=900)],
    )
    fixes = corrections(pairs, score_pairs(pairs))
    assert [f["claim_id"] for f in fixes] == ["b", "c"]
    assert fixes[0]["severity"] == "minor"
    assert fixes[0]["image"] is None
    assert fixes[1]["kept_dollars"] == 900
    assert "agent_make" in fixes[0]


def test_tables_render():
    rows = compare({"damage_severity": (1, 2)}, PREPROD)
    text = render_text_table(rows)
    assert "Metric" in text and "regressed" in text and "-0.30" in text
    md = render_md_table(rows)
    assert md.splitlines()[1].startswith("| ---")
    assert "| damage_severity | 0.80 | 0.50 | -0.30 | regressed | 2 |" in md


def test_analyse_next_action_and_report_top():
    result = analyse(
        [outcome("a"), outcome("b", severity="severe")], [label("a"), label("b")], PREPROD
    )
    assert result["report"].index("| Metric") < result["report"].index("| Dimension")
    assert any("1 claims below need to be added" in line and "b" in line for line in result["summary"])
