"""Task-result cache, live judge, and eval telemetry. Does not call a model."""

import asyncio

from strands_evals import Case, Experiment, LocalFileTaskResultStore
from strands_evals.evaluators import OutputEvaluator
from strands_evals.types.evaluation import EvaluationData

from evals.evaluators import DamageLocationEvaluator
from evals.phases.preprod import experiment_from, load_claims, main, run_live, task_results_store
from evals.telemetry import attach_evals, configure_evals_telemetry


def test_saved_task_output_loads_on_a_second_run(tmp_path):
    calls = []

    def task(case: Case) -> str:
        calls.append(case.name)
        return "cached-answer"

    case = Case[dict, dict](
        name="SYN0001",
        input={"image": "memory"},
        expected_output={"parts": ["front"], "severity": "moderate"},
    )
    store = LocalFileTaskResultStore(tmp_path)
    assert store.load("SYN0001") is None

    async def run(directory):
        experiment = Experiment[dict, dict](
            cases=[case],
            evaluators=[DamageLocationEvaluator()],
        )
        await experiment.run_evaluations_async(
            task, max_workers=1, evaluation_data_store=LocalFileTaskResultStore(directory)
        )

    asyncio.run(run(tmp_path))
    assert calls == ["SYN0001"]
    loaded = LocalFileTaskResultStore(tmp_path).load("SYN0001")
    assert isinstance(loaded, EvaluationData)
    assert loaded.actual_output == "cached-answer"

    asyncio.run(run(tmp_path))
    assert calls == ["SYN0001"]


def test_refresh_drops_saved_answers(tmp_path):
    store = task_results_store(root=tmp_path)
    store.save(
        "SYN0001",
        EvaluationData(input={"image": "memory"}, actual_output="old", name="SYN0001"),
    )
    assert task_results_store(root=tmp_path).load("SYN0001").actual_output == "old"
    assert task_results_store(refresh=True, root=tmp_path).load("SYN0001") is None


def test_output_judge_is_only_on_the_live_experiment():
    offline = [evaluator.get_name() for evaluator in experiment_from(load_claims()[:1]).evaluators]
    assert offline == ["damage_location", "damage_severity", "safe_pricing", "synthetic_range"]
    live = experiment_from(load_claims()[:1], judge=True)
    assert [evaluator.get_name() for evaluator in live.evaluators] == [*offline, "output_judge"]
    judge = live.evaluators[-1]
    assert isinstance(judge, OutputEvaluator)
    assert "1.0" in judge.rubric and "0.0" in judge.rubric
    assert "front" in judge.rubric and "rear" in judge.rubric
    assert "should not be priced" in judge.rubric.lower()
    assert "synthetic" in judge.rubric.lower()


def test_offline_main_does_not_score(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("evals.phases.preprod.TASK_RESULTS", tmp_path / "task_results")
    assert main([]) == 0
    assert not (tmp_path / "task_results").exists()
    out = capsys.readouterr().out
    assert "--refresh" in out


def test_run_live_passes_the_store(tmp_path, monkeypatch):
    root = tmp_path / "task_results"
    task_results_store(root=root).save(
        "SYN0001",
        EvaluationData(input={"image": "memory"}, actual_output="old", name="SYN0001"),
    )
    monkeypatch.setattr("evals.phases.preprod.TASK_RESULTS", root)
    monkeypatch.setattr("evals.phases.preprod.HISTORY_REPORT", tmp_path / "report.md")
    seen = {}

    class FakeExperiment:
        async def run_evaluations_async(self, task, max_workers=10, evaluation_data_store=None):
            seen["store"] = evaluation_data_store
            seen["loaded"] = evaluation_data_store.load("SYN0001")

            class Report:
                def to_dict(self):
                    return {"cases": [], "scores": []}

            return Report()

    monkeypatch.setattr("evals.phases.preprod.experiment_from", lambda rows, judge=False: FakeExperiment())
    telemetry = []
    monkeypatch.setattr("evals.phases.preprod.configure_evals_telemetry", lambda: telemetry.append("setup"))

    run_live([{"claim_id": "SYN0001"}], path=tmp_path / "preprod.json", workers=1, refresh=False)
    assert isinstance(seen["store"], LocalFileTaskResultStore)
    assert seen["loaded"].actual_output == "old"
    assert telemetry == ["setup"]

    run_live([{"claim_id": "SYN0001"}], path=tmp_path / "preprod.json", workers=1, refresh=True)
    assert seen["loaded"] is None


def test_evals_telemetry_is_a_noop_without_an_endpoint(monkeypatch):
    calls = []
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    configure_evals_telemetry(setup=lambda: calls.append("export"))
    assert calls == []

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    configure_evals_telemetry(setup=lambda: calls.append("export"))
    assert calls == ["export"]


def test_attach_evals_sets_up_otlp_exporter(monkeypatch):
    seen = []

    class FakeTelemetry:
        def setup_otlp_exporter(self):
            seen.append("otlp")
            return self

    monkeypatch.setattr("strands_evals.telemetry.StrandsEvalsTelemetry", FakeTelemetry)
    attach_evals()
    assert seen == ["otlp"]
