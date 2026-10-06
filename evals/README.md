Pre-prod scores Claude's answer on each held-out photo.

```
uv run python evals/run.py --live
```

That command runs the claims harness on every photograph in `history/images`, then scores the answer with Strands evaluators. The report is written to `evals/reports/preprod.json`.

## Evaluation

Requirements and the architecture judge quality on four points. Each point below states what the architecture asks, then what this dataset and scorer actually do.

### Working

The architecture asks for four dimensions, each measured against the fields the adjuster kept. Identity is a field-level match on make, model, and colour, or null when the photo cannot support a value. Damage is agreement on part and location with the adjuster's note, and severity is secondary. Range is whether the kept dollars fall inside the agent's low and high. Null estimate is withholding the range when the photo cannot support a price; false ok is pricing anyway.

Damage location and severity can be scored because the Hugging Face labels say front or rear, and breakage (moderate) or crushed (severe). Make, model, and colour are empty, so identity is not scored. False ok is scored when the notes say the photo should not be priced and Claude still returns ok with an estimate. The dollar comparison sits under Repair cost.

### Failure

The architecture ranks failures by what they cost the carrier. A bad range that looks usable is first: the desk trusts the low and high, and the adjuster's kept dollars fall outside that band. Wrong make, often with a wrong model, is second, because the claim is then framed on the wrong vehicle. False ok is third: status is ok with a price when the photo should have been withheld and the estimate left null.

False ok is the failure this scorer marks, using the check above. Identity is not scored, so a wrong make is not measured on this slice. A range that misses the history row is the Repair cost check, and those dollars were invented here.

### Customer inputs

The architecture asks the carrier for up to three years of closed history: the photograph, the vehicle named on the notice, adjuster labels, and the estimate the adjuster kept. It also asks for a subject-matter expert when the agent and the adjuster disagree on part or location, a desk or legacy baseline on the same held-out slice, and a live accept or correct path so realtime scores use the same meaning of kept.

This dataset is a public photo slice. Make, model, and colour are empty. There is no adjuster kept amount and no subject-matter expert on the row. The runtime log below can hold a claim until a label is applied later. That log is not the carrier's history.

### Repair cost

The architecture says an estimate is good enough when the agent's low–high range contains the dollar amount the adjuster kept. The check is meant for held-out history and for live accept or correct. A model or harness is promoted only when the share of in-range estimates rises versus the baseline, and false ok does not rise with it. When the estimate is wrong, the written record stays, the adjuster corrects it, and that pair joins the training corpus. A null stays null until a person sets a kept value.

Dollar low, high, and kept were invented here, so they are not an adjuster's kept amount. The report compares Claude's range to those amounts and marks the fields synthetic. A score does not retrain Claude. It is the gate for a later prompt change.

Runtime stores the claim JSON, without photo bytes, when `CLAIMS_EVAL_LOG=1`. Each claim is appended to `evals/runtime/outcomes.jsonl`. The response is not held for a second model call. The location, severity, and false-ok scores need an adjuster label, which is not available when the claim is returned. Apply that label later with `score_with_adjuster_label` in `evals/score.py`.

The photographs in `history/images` are from the Hugging Face dataset Car Front and Rear Damage Detection, https://huggingface.co/datasets/DrBimmer/comprehensive-car-damage (`DrBimmer/comprehensive-car-damage`). The dataset card states an MIT license. Make, model, and colour are not in the dataset, so those fields are empty. The dollar amounts in `claims.jsonl` were not part of the dataset. `python evals/fetch_history.py` re-downloads that slice.
