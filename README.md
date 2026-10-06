# Claims Factory Overview

This project takes one photograph of a damaged vehicle and returns a typed estimate: make, model, colour, a damage summary, and a rough repair-cost range. An adjuster reviews that result and decides whether to accept or correct it, and the software does not authorize payment. The running system is a Strands harness on Amazon Bedrock AgentCore Runtime, a fixed path of prompt, tools, and stop, deployed as a code zip and invoked with AWS Signature Version 4. The models are the Claude Sonnet family, and the model id can be swapped. Each session runs in its own microVM, partner tools are a fixed allowlist, and logs and traces are vended to CloudWatch.

Documentation:

- [Requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [Eval architecture](docs/architecture-evals.md)
- [MIT License](LICENSE)

![Claims factory](docs/claims-factory.svg)

## Prerequisites

- **Python 3.12.x** and [uv](https://docs.astral.sh/uv/)
- **Node.js** (for `npx aws-cdk`)
- **AWS CLI** configured with an access key and secret key (or another credential source) for the target account and Region (default `us-west-2`)
- IAM principal able to:
  - **Amazon Bedrock** — `InvokeModel` / converse on the inference profile in use (default `us.anthropic.claude-sonnet-4-6`)
  - **Bedrock AgentCore** — create and invoke AgentCore Runtime (deploy + `cli.py`)
  - **AgentCore Gateway** — when partner MCP via Gateway is wired (not required for the current stub tools)
  - **CloudFormation / CDK / IAM / CloudWatch Logs / X-Ray** — for `deploy.py` (stack, roles, Transaction Search → Omni)
- **AWS Marketplace** — Claude access is per model. Run `[scripts/enable_claude.py](scripts/enable_claude.py)` once. Pass the **foundation model** id (`anthropic.claude-sonnet-4-6`); the harness calls the **US inference profile** (`us.anthropic.claude-sonnet-4-6`). A Marketplace agreement of AVAILABLE does not mean Converse works: this account is denied `claude-sonnet-5` / `claude-opus-5-5` until AWS sales allowlists them.

```sh
uv run scripts/enable_claude.py \
  --company "YourOrg" \
  --website "https://example.com" \
  --use-case "Vehicle damage photo estimating prototype" \
  --model anthropic.claude-sonnet-4-6
```



## Deploy on AWS Bedrock AgentCore

Strands harness (triage → read → validate) on Bedrock AgentCore Runtime. Model: `us.anthropic.claude-sonnet-4-6`.

```sh
uv sync --all-groups
npx aws-cdk bootstrap          # once per account and Region, from this directory
uv run deploy.py               # Omni/Transaction Search + stack (idempotent)
uv run cli.py img/veh1.jpeg
uv run pytest
npx aws-cdk destroy
```

`deploy.py` enables CloudWatch Transaction Search (idempotent), deploys the AgentCore code zip with tracing, and sends application/usage logs to `/aws/vendedlogs/bedrock-agentcore/ClaimsFactoryHarness`. Local harness: `uv run python -m claims.agent`. After redeploy, GenAI Observability on that runtime shows the harness spans.

**See logs / Omni:** CloudWatch (same Region) → GenAI Observability or Omni → AgentCore; Log groups → `ClaimsFactoryHarness`.

## Evals

Pre-prod scores Claude's written answer on each held-out photo. It does not retrain Claude. The eval architecture, including the two workflows, is in [docs/architecture-evals.md](docs/architecture-evals.md).

```sh
uv run python evals/run.py --live
```

That command sends every photograph in `evals/history/images` to the claims harness, then scores the answer. Damage location and severity use the dataset labels: front or rear, and breakage (moderate) or crushed (severe). False ok is a status of `ok` with an estimate when the notes say the photo should not be priced.

The full result of the latest run is `evals/reports/preprod.json`. Each finished run also appends a dated score block to `evals/reports/report.md`.

A live claim can be saved for a later adjuster label. Set `CLAIMS_EVAL_LOG=1` and the claim JSON, without the photo, is appended to `evals/runtime/outcomes.jsonl`. Apply that label with `score_with_adjuster_label` in `evals/score.py`.

The photographs are from the Hugging Face dataset Car Front and Rear Damage Detection, [DrBimmer/comprehensive-car-damage](https://huggingface.co/datasets/DrBimmer/comprehensive-car-damage). The dataset card states an MIT license. Make, model, and colour are not in that set, so identity is not scored. The dollar amounts in `evals/history/claims.jsonl` were not part of the dataset. `uv run python evals/fetch_history.py` re-downloads the slice.

Requirements and the architecture judge quality on four points.

**Working.** The architecture asks for identity, damage, range, and a null estimate when the photo cannot support a price. Damage location and severity can be scored from the photo labels. Make, model, and colour are empty, so identity is not scored. False ok is scored when the notes say not to price the photo and Claude still returns `ok` with an estimate.

**Failure.** The architecture ranks a bad usable range first, a wrong make second, and false ok third. This scorer marks false ok. A wrong make is not measured on this slice. The range check uses dollars that were invented here.

**Customer inputs.** The architecture asks for closed claim history, the vehicle on the notice, the estimate the adjuster kept, and a subject-matter expert when the agent and the adjuster disagree. This dataset is a public photo slice. It is not that history.

**Repair cost.** An estimate is good enough when the agent's low–high range contains the dollar amount the adjuster kept. Those dollars were invented here, so they are not an adjuster's kept amount. The report compares Claude's range to them and marks the fields synthetic. A score is the gate for a later prompt change.

## Local FastAPI with direct call to Claude (optional)

```sh
uv sync --all-groups
export ANTHROPIC_API_KEY=your-key
uv run python -m claims.web    # http://127.0.0.1:8080
```



