# Claims Factory Overview

This project takes one photograph of a damaged vehicle and returns a typed estimate: make, model, colour, a damage summary, and a rough repair-cost range. An adjuster reviews that result and decides whether to accept or correct it, and the software does not authorize payment. The running system is a Strands harness on Amazon Bedrock AgentCore Runtime, a fixed path of prompt, tools, and stop, deployed as a code zip and invoked with AWS Signature Version 4. The models are the Claude Sonnet family, and the model id can be swapped. Each session runs in its own microVM, partner tools are a fixed allowlist, and logs and traces are vended to CloudWatch.

Documentation:

- [Requirements](docs/requirements.md)
- [Architecture](docs/architecture.md)
- [MIT License](LICENSE)

![Claims factory](docs/claims-factory.svg)

## Prerequisites

- **Python 3.12.x** and [uv](https://docs.astral.sh/uv/)
- **Node.js** (for `npx aws-cdk`)
- **AWS CLI** configured with an access key and secret key (or another credential source) for the target account and Region (default `us-west-2`)
- IAM principal as admin (non prod) or able to:
  - **Amazon Bedrock** — `InvokeModel` / converse on the inference profile in use (default `us.anthropic.claude-sonnet-4-6`)
  - **Bedrock AgentCore** — create and invoke AgentCore Runtime (deploy + `cli.py`)
  - **AgentCore Gateway** — when partner MCP via Gateway is wired (not required for the current stub tools)
  - **CloudFormation / CDK / IAM / CloudWatch Logs / X-Ray** — for `deploy.py` (stack, roles, Transaction Search → Omni)
- **AWS Marketplace** — Claude access is per model. Run `[scripts/enable_claude.py](scripts/enable_claude.py)` once. Pass the **foundation model** id (`claude-sonnet-4-6 or other model ids`); the harness calls the **US inference profile** (`us.anthropic.claude-sonnet-4-6`). A Marketplace agreement of AVAILABLE does not mean this always works: this account is denied  at account level sometimes from aws.

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
uv run cli.py dataset/img/veh1.jpeg          # add --json for raw JSON
uv run pytest
npx aws-cdk destroy
```

The idempotent `deploy.py` enables CloudWatch Transaction Search, deploys the AgentCore code zip with tracing, and sends application/usage logs to `/aws/vendedlogs/bedrock-agentcore/ClaimsFactoryHarness`. Local harness: `uv run python -m claims.agent`. After redeploy, GenAI Observability on that runtime shows the harness spans.

**See logs / Omni:** CloudWatch (same Region) → GenAI Observability or Omni → AgentCore; Log groups → `ClaimsFactoryHarness`.

## Evals

The evals score Claude's written answers; they do not retrain Claude. The default is one end-to-end run that covers pre-prod, runtime, and post-prod and ends with a decision table for the operator and the adjuster.

```sh
uv run python evals/e2e.py --live
```

`--live` calls Bedrock: it scores the photographs in `dataset/history/images`, sends the sample claim `dataset/img/veh2.jpg` through the harness, triages the logged claim, and compares a simulated adjuster label with the pre-prod result. Without `--live` it calls no model. Add `--limit N` to score only N photographs, or `--skip-preprod` to reuse `.runtime/reports/preprod.json`. Output goes to `.runtime/reports/e2e.md`. More in [evals/README.md](evals/README.md#end-to-end-run); the design is in [docs/architecture.md#evaluation](docs/architecture.md#evaluation).

The photographs are from the Hugging Face dataset Car Front and Rear Damage Detection, [DrBimmer/comprehensive-car-damage](https://huggingface.co/datasets/DrBimmer/comprehensive-car-damage). The dataset card states an MIT license. Make, model, and colour are not in that set, so identity is not scored. The dollar amounts in `dataset/history/claims.jsonl` were not part of the dataset. `uv run python dataset/fetch_history.py` re-downloads the slice. How the evaluation questions land on this slice is answered once in the [Eval FAQ](docs/architecture.md#eval-faq).

### Run one phase

Each phase also runs on its own; the design of each is linked.

| Phase | Command | Reads | Writes | Design |
| --- | --- | --- | --- | --- |
| Pre-prod | `uv run python evals/phases/preprod.py --live` | `dataset/history/images`, `dataset/history/claims.jsonl` | `.runtime/reports/preprod.json`, `.runtime/reports/report.md` | [Pre-prod](docs/architecture.md#pre-prod) |
| Runtime | `uv run python evals/phases/runtime.py` | `.runtime/outcomes.jsonl` | `.runtime/reports/runtime.md` | [Runtime](docs/architecture.md#runtime) |
| Post-prod | `uv run python evals/phases/postprod_report.py` | `.runtime/outcomes.jsonl`, `.runtime/labels.jsonl`, `.runtime/reports/preprod.json` | `.runtime/reports/postprod.md`, `.runtime/corrections.jsonl` | [Post-prod](docs/architecture.md#post-prod) |

- **Pre-prod** ([`evals/phases/preprod.py`](evals/phases/preprod.py)) sends every photograph to the claims harness and scores the answer. Damage location and severity use the dataset labels: front or rear, and breakage (moderate) or crushed (severe). Safe pricing fails when the answer has status `ok` with an estimate but the expected label says the photo should not be priced. Saved answers are reused unless you pass `--refresh`.
- **Runtime** ([`evals/phases/runtime.py`](evals/phases/runtime.py)) needs `CLAIMS_EVAL_LOG=1` on the machine that runs the harness, which appends each claim, without the photo, to `.runtime/outcomes.jsonl`. It flags claims that were not read, had no estimate, or had low confidence or a wide range. It does not call a model.
- **Post-prod** ([`evals/phases/postprod_report.py`](evals/phases/postprod_report.py)) needs the adjuster's kept values saved as `.runtime/labels.jsonl`, one row per line with `claim_id`, `severity`, `dataset_label` or `parts`, and optionally `kept_dollars`. It prints a table comparing post-prod rates with the pre-prod report and lists the claims the adjuster corrected. The log keeps no photographs, so if a metric regressed, attach the image file for each listed claim and add the rows to `dataset/history/claims.jsonl` before the next release.

## Local test server for direct Claude testing (optional)

`claims/localhost.py` is a small local page for trying the model on a photo with no AWS infrastructure. It calls Claude directly with your Anthropic API key, so it skips AgentCore, Bedrock, API Gateway, and Lambda, and it has no policy code. It is for testing only: it listens on 127.0.0.1 and `deploy.py` leaves it out of the deployed zip.

```sh
uv sync --all-groups
export ANTHROPIC_API_KEY=your-key
uv run python -m claims.localhost    # http://127.0.0.1:8080
```

## Public upload page

`uv run deploy.py` also publishes a page on an API Gateway URL (stack output `PublicUrl`). Anyone with the URL can upload one photo of 3 MB or less with a policy code of up to 10 characters and see the result on the same page. The AgentCore runtime is called only when the code equals `POLICY_CODE_ADMIN`, and every failure shows the same apology message. Set the code in the deploy shell and keep it out of the repo; if it is unset, the page rejects every submit.

```sh
export POLICY_CODE_ADMIN='<code, 10 characters or fewer>'
uv run deploy.py
```

## Costs

Rough guesses at US list prices, not a quote; check current AWS pricing before relying on them. **Partner data integration (policy, loss history, estimating) is not included**: the partner tools are stubs today, and a real provider charges its own fees on top.

| Item | 100 live claims | 100 images, one pre-prod eval run |
| --- | --- | --- |
| Claude Sonnet 4.6 on Bedrock, the claim answers | about $3.00 | about $3.00 |
| Claude judge call per photo, evals only | none | about $0.60 |
| AgentCore Runtime compute | about $0.10 | none (eval runs the harness on your machine) |
| API Gateway and Lambda | under $0.02 | none |
| CloudWatch logs and traces | under $0.05 | none |
| **Total** | **about $3.20** | **about $3.60** |

The stack costs close to nothing when unused, apart from a little CloudWatch log storage.
