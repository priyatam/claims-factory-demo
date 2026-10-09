"""End-to-end eval orchestration with fakes. No Bedrock, no HTTP."""

import io
import json
import sys
from pathlib import Path

import pytest
from rich.console import Console

import cli
from claims.eval_log import record_outcome
from evals import e2e

PREPROD = {
    "quality": {
        "damage_location": {"count": 100, "mean": 0.98},
        "damage_severity": {"count": 100, "mean": 0.85},
        "safe_pricing": {"count": 100, "mean": 1.0},
    }
}
GOOD = {
    "claim_id": e2e.CLAIM_ID,
    "status": "ok",
    "damage": {"summary": "front bumper dent", "parts": ["front"], "severity": "moderate"},
    "estimate": {"low": 900, "high": 1300, "currency": "USD", "confidence": 0.85},
}


def console():
    return Console(file=io.StringIO(), width=100, force_terminal=False)


def out(c):
    return c.file.getvalue()


@pytest.fixture
def paths(tmp_path):
    image = tmp_path / "car.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 40 + b"\xff\xd9")
    pre = tmp_path / "preprod.json"
    pre.write_text(json.dumps(PREPROD))
    return {"out_dir": tmp_path / "e2e", "report": tmp_path / "e2e.md", "preprod_path": pre, "image": image}


def fake_invoke(result=GOOD, calls=None):
    def invoke(payload):
        if calls is not None:
            calls.append("invoke")
        record_outcome(result)  # honours CLAIMS_EVAL_LOG and CLAIMS_EVAL_LOG_PATH
        return result

    return invoke


def never(*_a, **_k):
    raise AssertionError("must not be called")


def run(paths, c=None, **kw):
    c = c or console()
    kw.setdefault("run_preprod", never)
    kw.setdefault("invoke", fake_invoke())
    kw.setdefault("credentials_ok", lambda: True)
    code = e2e.run_e2e(console=c, skip_preprod=True, **paths, **kw)
    return code, c


def test_without_live_calls_nothing_and_shows_saved_summary(paths, monkeypatch):
    monkeypatch.setattr(e2e, "run_e2e", never)
    monkeypatch.setattr(e2e, "local_invoke", never)
    c = console()
    assert e2e.main([], console=c, preprod_path=paths["preprod_path"], image=paths["image"]) == 0
    text = out(c)
    assert "no model call" in text and "car.jpg" in text and "--live" in text
    assert "damage_location" in text and "0.98" in text


def test_without_live_and_no_saved_report_still_exits_zero(paths):
    c = console()
    assert e2e.main([], console=c, preprod_path=paths["out_dir"] / "none.json", image=paths["image"]) == 0
    assert "Saved pre-prod summary" not in out(c)


def test_live_runs_stages_in_order_and_isolates_outputs(paths, monkeypatch):
    calls = []

    def fake_preprod(limit, path, on_step):
        calls.append(("preprod", limit))
        on_step(1, 2)
        path.write_text(json.dumps(PREPROD))
        return PREPROD

    c = console()
    monkeypatch.setattr(e2e, "run_e2e", e2e.run_e2e)
    code = e2e.main(
        ["--live", "--limit", "5"],
        console=c,
        run_preprod=fake_preprod,
        invoke=fake_invoke(calls=calls),
        credentials_ok=lambda: True,
        **paths,
    )
    assert code == 0
    assert calls == [("preprod", 5), "invoke"]
    out_dir = paths["out_dir"]
    for name in ("outcomes.jsonl", "labels.jsonl", "runtime.md", "postprod.md", "corrections.jsonl", "preprod.json", "run.log"):
        assert (out_dir / name).exists(), name
    assert paths["report"].exists()
    # the real log was not touched
    assert "e2e" in json.loads((out_dir / "outcomes.jsonl").read_text())["claim_id"]


def test_three_sections_next_action_and_simulated_flag(paths):
    code, c = run(paths)
    assert code == 0
    text = out(c)
    for needle in ("Pre-prod", "Metric", "Score", "damage_location", "0.98",
                   "Runtime (one sample claim", "estimate range", "900-1300 USD", "0.85",
                   "Post-prod (1 simulated label)", "Pre-prod", "Post-prod", "Delta", "Verdict", "+0.02", "+0.15",
                   "indicative only", "Next action: release"):
        assert needle in text, needle
    assert "Who acts" not in text and "Next actor" not in text
    assert "pre 0.98 ->" not in text
    assert max(len(line) for line in text.splitlines()) <= 100
    md = paths["report"].read_text()
    for heading in ("## Pre-prod", "## Runtime", "## Post-prod (1 simulated label)"):
        assert heading in md
    assert "| damage_severity | 0.85 | 1.00 | +0.15 | ok |" in md
    assert "**Next action:** release" in md and "Who acts" not in md


def test_flagged_claim_is_explained_and_routes_to_adjuster(paths):
    weak = {**GOOD, "estimate": {**GOOD["estimate"], "confidence": 0.3}}
    code, c = run(paths, invoke=fake_invoke(weak))
    text = out(c)
    assert "low confidence 0.30" in text
    assert "review 1 flagged claim" in text
    assert "Who acts: adjuster" in text


def test_low_preprod_rate_says_fix_harness(paths):
    paths["preprod_path"].write_text(
        json.dumps({"quality": {**PREPROD["quality"], "damage_severity": {"count": 100, "mean": 0.31}}})
    )
    _, c = run(paths)
    assert "fix harness (pre-prod damage_severity below 0.80)" in out(c)
    assert "Who acts: operator" in out(c)


def test_regression_asks_for_photos(paths):
    wrong = {**GOOD, "damage": {**GOOD["damage"], "severity": "severe"}}
    pre = {"quality": {**PREPROD["quality"], "damage_severity": {"count": 100, "mean": 0.9}}}
    paths["preprod_path"].write_text(json.dumps(pre))
    _, c = run(paths, invoke=fake_invoke(wrong))
    assert "attach photos for 1 corrections" in out(c)
    assert "Who acts: adjuster" in out(c)
    assert (paths["out_dir"] / "corrections.jsonl").read_text().strip()


def test_failures_have_clear_messages(paths):
    c = console()
    with pytest.raises(e2e.E2EError, match="sample image not found"):
        run(paths | {"image": Path("/nope.jpg")})
    with pytest.raises(e2e.E2EError, match="no AWS credentials"):
        run(paths, credentials_ok=lambda: False)
    with pytest.raises(e2e.E2EError, match="no pre-prod report"):
        run(paths | {"preprod_path": paths["out_dir"] / "missing.json"})
    with pytest.raises(e2e.E2EError, match="harness error: boom"):
        run(paths, invoke=lambda p: {**GOOD, "error": "boom"})
    with pytest.raises(e2e.E2EError, match="no outcome was logged"):
        run(paths, invoke=lambda p: GOOD)  # fake does not log
    code = e2e.main(["--live", "--skip-preprod"], console=c, credentials_ok=lambda: False, **paths)
    assert code == 1 and "error: no AWS credentials" in out(c)


def test_sample_rows_is_even_and_bounded():
    rows = [{"i": i} for i in range(100)]
    assert [r["i"] for r in e2e.sample_rows(rows, 5)] == [0, 20, 40, 60, 80]
    assert e2e.sample_rows(rows, None) == rows and e2e.sample_rows(rows, 500) == rows


def test_logs_go_to_file_unless_verbose(tmp_path, capfd):
    import logging

    log = tmp_path / "run.log"
    with e2e.captured_logs(log, verbose=False):
        logging.getLogger("strands").warning("noisy")
        print("stderr noise", file=sys.stderr)
    assert "noisy" in log.read_text() and "stderr noise" in log.read_text()
    assert "noisy" not in capfd.readouterr().err


def test_cli_render_claim_and_json_flag(monkeypatch, capsys):
    result = {**GOOD, "plate": {"value": None}, "partner_facts": {"policy": None}}
    c = console()
    c.print(cli.render_claim(result))
    text = out(c)
    for needle in ("ok", "front bumper dent", "moderate", "900-1,300 USD", "0.85", "Policy"):
        assert needle in text

    class Stream:
        def iter_lines(self):
            yield b"data: " + json.dumps(result).encode()

    class Client:
        def invoke_agent_runtime(self, **_k):
            return {"response": Stream()}

    class Session:
        def client(self, name):
            return Client()

    monkeypatch.setattr(cli.boto3, "Session", Session)
    monkeypatch.setattr(cli, "runtime_arn", lambda s: "arn")
    image = Path(__file__).parent / "none"
    assert cli.main(["--state", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["claim_id"] == e2e.CLAIM_ID
    assert cli.main(["--url", "https://x/y.jpg"]) == 0
    assert "front bumper dent" in capsys.readouterr().out


def test_postprod_print_table(capsys):
    from evals.phases.postprod_report import compare, print_table

    print_table(compare({"damage_severity": (1, 2)}, {"quality": {"damage_severity": {"mean": 0.8}}}))
    text = capsys.readouterr().out
    assert "damage_severity" in text and "regressed" in text and "-0.30" in text
