# Evals

Scores Claude's claim answers before release (pre-prod), watches live claims (runtime), and compares them with adjuster labels (post-prod). Nothing here retrains Claude. Design: [docs/architecture.md#evaluation](../docs/architecture.md#evaluation).

## Run

```sh
uv run python evals/e2e.py --live    # all three stages on real Bedrock, one decision table
uv run python evals/e2e.py           # no model call: shows what --live would run
```

`--live` scores the evaluation set, sends `dataset/img/veh2.jpg` through the harness in this process, flags the logged claim, then scores it against a simulated adjuster label (a fixed fixture, marked `simulated`) and compares with pre-prod. Pre-prod sends about 100 photos to Claude: about 2 minutes and real Bedrock cost. It skips `cli.py` because a deployed runtime's outcome log is not on this machine.

| Flag | Effect |
| --- | --- |
| `--limit N` | Score an even sample of N photos. |
| `--skip-preprod` | Reuse `.runtime/reports/preprod.json`. |
| `--verbose` | Show library logs; otherwise they go to `.runtime/reports/e2e/run.log`. |

Output: `.runtime/reports/e2e.md`. The run's own logs and reports stay in `.runtime/reports/e2e/`, so real data is untouched.

## Run one phase

| Phase | Command | Reads | Writes |
| --- | --- | --- | --- |
| Pre-prod | `uv run python evals/phases/preprod.py --live` | `dataset/history` | `.runtime/reports/preprod.json`, `report.md` |
| Runtime | `uv run python evals/phases/runtime.py` | `.runtime/outcomes.jsonl` | `.runtime/reports/runtime.md` |
| Post-prod | `uv run python evals/phases/postprod_report.py` | `.runtime/outcomes.jsonl`, `.runtime/labels.jsonl`, `.runtime/reports/preprod.json` | `.runtime/reports/postprod.md`, `.runtime/corrections.jsonl` |

- **Pre-prod** reuses saved answers in `.runtime/reports/task_results`; pass `--refresh` after any prompt, tool, or model change. `--workers N` sets parallel photos (default 100).
- **Runtime** needs `CLAIMS_EVAL_LOG=1` on the machine that runs the harness; each claim is appended, without the photo, to `.runtime/outcomes.jsonl` (`CLAIMS_EVAL_LOG_PATH` overrides the file). It flags status not ok, no estimate, confidence below 0.6, and range wider than its midpoint.
- **Post-prod** needs `.runtime/labels.jsonl`: one row per claim with `claim_id`, `severity`, `dataset_label` or `parts`, and optionally `kept_dollars`. It prints pre-prod vs post-prod rates (regression: more than 0.05 below) and lists corrected claims. Attach the photo for each, then add the rows to `dataset/history/claims.jsonl`.

The 0.6, 1.0, 0.05, and the e2e 0.80 floor are proposals, set as constants at the top of each script.

## Where things are

| Path | What |
| --- | --- |
| `dataset/history/` | Evaluation set: `claims.jsonl` and `images/` (100 labelled photos). Tracked. |
| `dataset/img/` | Sample claim photos. Tracked. |
| `.runtime/` | Generated logs and reports. Gitignored. |
| `evals/paths.py` | Every generated path, defined once. The root is in `claims/telemetry.py`. |
| `evals/phases/` | `preprod.py`, `runtime.py`, `postprod_report.py`: one script per phase. |
| `evals/e2e.py` | The end-to-end run over all three phases. |
| `evals/score.py`, `evaluators.py` | Scorers, and their Strands evaluator wrappers plus the LLM judge. |
| `evals/telemetry.py` | Sends eval spans when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. |
| `dataset/fetch_history.py` | Re-downloads the evaluation set from Hugging Face. |

## Tests

```sh
uv run pytest tests/test_claim_scores.py tests/test_eval_history.py tests/test_eval_cache.py \
  tests/test_runtime_triage.py tests/test_postprod_report.py tests/test_e2e.py
```

They use fakes and tmp paths, never Bedrock or the real `.runtime/`. Coverage: scorers, dataset integrity, cached task results, runtime flags, post-prod comparison and corrections, and the e2e orchestration and table.

## Data

Photos come from [DrBimmer/comprehensive-car-damage](https://huggingface.co/datasets/DrBimmer/comprehensive-car-damage) (MIT per the dataset card). Make, model, and colour are not in it, so identity is not scored. Severity comes from the class folder, every row is priceable (so safe pricing reads 1.00 by construction), and the dollar amounts are synthetic.
