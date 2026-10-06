"""Score injected model text. Does not call a model."""

import asyncio
import base64
import json
from pathlib import Path

from strands_evals.types.evaluation import EvaluationData

from claims.agent import invoke
from evals.evaluators import DamageLocationEvaluator, DamageSeverityEvaluator, FalseOkEvaluator, SyntheticRangeEvaluator
from evals.run import experiment_from, load_claims, main, write_report
from evals.score import kept_inside_agent_range, score_with_adjuster_label

JPEG = b"\xff\xd8\xff" + b"\x00" * 8

FRONT_MODERATE = json.dumps(
    {
        "status": "ok",
        "damage": {"summary": "front bumper cracked", "parts": ["front bumper"], "severity": "moderate"},
        "estimate": {"low": 400, "high": 900, "currency": "USD"},
    }
)
REAR_SEVERE = json.dumps(
    {
        "status": "ok",
        "damage": {"summary": "rear quarter crushed", "parts": ["rear"], "severity": "severe"},
        "estimate": {"low": 3000, "high": 7000, "currency": "USD"},
    }
)
WITHHELD = json.dumps({"status": "unreadable", "damage": None, "estimate": None})


def _data(text: str, label: dict) -> EvaluationData:
    return EvaluationData(input={"image": "memory"}, actual_output=text, expected_output=label)


def test_damage_location_match():
    label = {"parts": ["front"], "severity": "moderate", "expected_status": "ok"}
    matched = DamageLocationEvaluator().evaluate(_data(FRONT_MODERATE, label))[0]
    missed = DamageLocationEvaluator().evaluate(_data(REAR_SEVERE, label))[0]
    assert matched.score == 1.0 and matched.test_pass
    assert missed.score == 0.0 and not missed.test_pass


def test_damage_severity_match():
    moderate = {"parts": ["front"], "severity": "moderate", "expected_status": "ok"}
    severe = {"parts": ["rear"], "severity": "severe", "expected_status": "ok"}
    assert DamageSeverityEvaluator().evaluate(_data(FRONT_MODERATE, moderate))[0].score == 1.0
    assert DamageSeverityEvaluator().evaluate(_data(FRONT_MODERATE, severe))[0].score == 0.0
    assert DamageSeverityEvaluator().evaluate(_data(REAR_SEVERE, severe))[0].score == 1.0


def test_false_ok_when_notes_say_not_to_price():
    withhold = {"parts": ["front"], "severity": "moderate", "expected_status": "unreadable", "false_ok_case": True}
    priced = FalseOkEvaluator().evaluate(_data(FRONT_MODERATE, withhold))[0]
    held = FalseOkEvaluator().evaluate(_data(WITHHELD, withhold))[0]
    assert priced.score == 0.0 and priced.test_pass is False
    assert "False ok" in priced.reason
    assert held.score == 1.0 and held.test_pass is True


def test_synthetic_range_is_marked_and_compared():
    label = {
        "parts": ["front"],
        "severity": "moderate",
        "expected_status": "ok",
        "kept_dollars": 500,
        "dollars_synthetic": True,
    }
    inside = SyntheticRangeEvaluator().evaluate(_data(FRONT_MODERATE, label))[0]
    label["kept_dollars"] = 50
    outside = SyntheticRangeEvaluator().evaluate(_data(FRONT_MODERATE, label))[0]
    assert inside.score == 1.0 and "Synthetic dollars" in inside.reason
    assert outside.score == 0.0 and outside.test_pass is False
    assert kept_inside_agent_range(400, 900, 500)
    assert not kept_inside_agent_range(400, 900, 50)


def test_later_adjuster_label_scores_a_stored_claim():
    stored = {
        "claim_id": "live-1",
        "status": "ok",
        "vehicle": None,
        "plate": {"value": None, "confidence": None},
        "damage": {"summary": "front bumper cracked", "parts": ["front bumper"], "severity": "moderate"},
        "estimate": {"low": 400, "high": 900, "currency": "USD", "assumptions": ["panel"], "confidence": 0.4},
    }
    scores = score_with_adjuster_label(
        stored,
        {"parts": ["front"], "severity": "severe", "expected_status": "not_a_vehicle", "dollars_synthetic": False},
    )
    assert scores["damage_location"]["score"] == 1.0
    assert scores["damage_severity"]["score"] == 0.0
    assert scores["false_ok"]["false_ok"] is True
    assert scores["synthetic_range"]["synthetic"] is False


def test_preprod_experiment_uses_damage_labels():
    experiment = experiment_from(load_claims())
    assert [evaluator.get_name() for evaluator in experiment.evaluators] == [
        "damage_location",
        "damage_severity",
        "false_ok",
        "synthetic_range",
    ]
    assert len(experiment.cases) == 100
    for case in experiment.cases:
        assert case.expected_output["dollars_synthetic"] is True
        assert case.metadata["dollars_synthetic"] is True
        assert "make" not in case.expected_output
        assert isinstance(case.input["image"], str)


def test_report_marks_synthetic_dollars(tmp_path: Path):
    class Report:
        def to_dict(self):
            return {
                "overall_score": 0.5,
                "scores": [1.0, 0.0],
                "cases": [
                    {"evaluator": "damage_location", "name": "SYN0001"},
                    {"evaluator": "synthetic_range", "name": "SYN0001"},
                ],
                "test_passes": [True, False],
                "reasons": ["front", "Synthetic dollars."],
            }

    path = write_report(Report(), tmp_path / "preprod.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["retrains_claude"] is False
    assert "kept_dollars" in document["synthetic_fields"]
    assert document["quality"]["damage_location"]["mean"] == 1.0
    assert "synthetic_range" not in document["quality"]
    assert document["synthetic_range"]["synthetic"] is True


def test_cli_points_at_live_scoring(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "--live" in out
    assert "preprod.json" in out
    assert "images_ok" not in out


async def _chunks(payload):
    return [chunk async for chunk in invoke(payload)]


def test_runtime_log_is_off_unless_enabled(monkeypatch, tmp_path: Path):
    dest = tmp_path / "outcomes.jsonl"
    monkeypatch.setattr("claims.eval_log.OUTCOMES", dest)
    monkeypatch.setattr(
        "claims.agent.run_claim_with_agent",
        lambda *_a, **_k: {"claim_id": "x", "status": "unreadable", "estimate": None},
    )
    payload = {
        "image_b64": base64.b64encode(JPEG).decode("ascii"),
        "media_type": "image/jpeg",
        "claim_id": "x",
    }
    assert asyncio.run(_chunks(payload))[0]["status"] == "unreadable"
    assert not dest.exists()

    monkeypatch.setenv("CLAIMS_EVAL_LOG", "1")
    monkeypatch.setattr(
        "claims.agent.run_claim_with_agent",
        lambda *_a, **_k: {
            "claim_id": "x",
            "status": "ok",
            "estimate": {"low": 1, "high": 2},
            "image_b64": "do-not-store",
            "photo": b"\xff\xd8",
        },
    )
    result = asyncio.run(_chunks(payload))[0]
    assert result["status"] == "ok"
    assert result["image_b64"] == "do-not-store"
    line = json.loads(dest.read_text(encoding="utf-8").strip())
    assert line["claim_id"] == "x"
    assert "image_b64" not in line
    assert "photo" not in line
