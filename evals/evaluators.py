"""Strands evaluators for damage location, severity, safe pricing, and synthetic range."""

from __future__ import annotations

from strands_evals.evaluators import Evaluator, OutputEvaluator
from strands_evals.types.evaluation import NOT_APPLICABLE, EvaluationData, EvaluationOutput

from evals.score import location_score, safe_pricing_score, severity_score, synthetic_range_score

JUDGE_RUBRIC = (
    "Score 1.0 when the damage location (front or rear) matches the label and the severity "
    "(breakage or moderate versus crushed or severe) matches. "
    "Score 0.0 when the photo should not be priced but the answer has status ok with an estimate. "
    "Dollar fields are synthetic and must not decide the score."
)


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


class SafePricingEvaluator(Evaluator):
    def __init__(self) -> None:
        super().__init__(name="safe_pricing")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        return [_output(safe_pricing_score(evaluation_case.actual_output, evaluation_case.expected_output or {}))]


class SyntheticRangeEvaluator(Evaluator):
    def __init__(self) -> None:
        super().__init__(name="synthetic_range")

    def evaluate(self, evaluation_case: EvaluationData) -> list[EvaluationOutput]:
        return [_output(synthetic_range_score(evaluation_case.actual_output, evaluation_case.expected_output or {}))]


def claim_evaluators() -> list[Evaluator]:
    return [
        DamageLocationEvaluator(),
        DamageSeverityEvaluator(),
        SafePricingEvaluator(),
        SyntheticRangeEvaluator(),
    ]


def output_judge() -> OutputEvaluator:
    """LLM judge for the live run. Construction does not call the model."""
    from claims.harness import MODEL_ID

    return OutputEvaluator(rubric=JUDGE_RUBRIC, model=MODEL_ID, name="output_judge")
