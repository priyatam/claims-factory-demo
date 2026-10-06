"""Strands evaluators for damage location, severity, false ok, and synthetic range."""

from __future__ import annotations

from strands_evals.evaluators import Evaluator
from strands_evals.types.evaluation import NOT_APPLICABLE, EvaluationData, EvaluationOutput

from evals.score import false_ok_score, location_score, severity_score, synthetic_range_score


def _output(scored: dict) -> EvaluationOutput:
    label = NOT_APPLICABLE if scored.get("not_applicable") else None
    return EvaluationOutput(
        score=float(scored["score"]),
        test_pass=bool(scored["test_pass"]),
        reason=scored["reason"],
        label=label,
    )


class DamageLocationEvaluator(Evaluator):
    def __init__(self) -> None:
        super().__init__(name="damage_location")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        return [_output(location_score(evaluation_case.actual_output, evaluation_case.expected_output or {}))]


class DamageSeverityEvaluator(Evaluator):
    def __init__(self) -> None:
        super().__init__(name="damage_severity")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        return [_output(severity_score(evaluation_case.actual_output, evaluation_case.expected_output or {}))]


class FalseOkEvaluator(Evaluator):
    def __init__(self) -> None:
        super().__init__(name="false_ok")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        return [_output(false_ok_score(evaluation_case.actual_output, evaluation_case.expected_output or {}))]


class SyntheticRangeEvaluator(Evaluator):
    def __init__(self) -> None:
        super().__init__(name="synthetic_range")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        return [_output(synthetic_range_score(evaluation_case.actual_output, evaluation_case.expected_output or {}))]


def claim_evaluators() -> list[Evaluator]:
    return [
        DamageLocationEvaluator(),
        DamageSeverityEvaluator(),
        FalseOkEvaluator(),
        SyntheticRangeEvaluator(),
    ]
