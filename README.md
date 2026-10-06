# Vehicle claims

One damaged-vehicle photo → typed estimate (make, model, colour, damage, rough range) for adjuster review. Software does not pay.

- Architecture: `[docs/architecture.md](docs/architecture.md)`
- Requirements: `[docs/requirements.md](docs/requirements.md)`

## Prerequisites

- **Python 3.12.x** and [uv](https://docs.astral.sh/uv/)
- **Node.js** (for `npx aws-cdk`)
- **AWS CLI** configured with an access key and secret key (or another credential source) for the target account and Region (default `us-west-2`)
- IAM principal able to:
  - **Amazon Bedrock** — `InvokeModel` / converse on the inference profile in use (default `us.anthropic.claude-sonnet-4-6`)
  - **Bedrock AgentCore** — create and invoke AgentCore Runtime (deploy + `cli.py`)
  - **AgentCore Gateway** — when partner MCP via Gateway is wired (not required for the current stub tools)
  - **CloudFormation / CDK / IAM / CloudWatch Logs / X-Ray** — for `deploy.py` (stack, roles, Transaction Search → Omni)
  - **AWS Marketplace** — `Subscribe`, `ViewSubscriptions`, `Unsubscribe` for first-time Claude model enablement (see below)
- **Claude model access** (per account, once per model): run `[scripts/enable_claude.py](scripts/enable_claude.py)` before relying on Bedrock Claude. That script submits Anthropic’s use-case form, creates the Marketplace agreement, prints full `get_foundation_model_availability` JSON, and Converse-smokes the `us.` inference profile. Agreement `AVAILABLE` is not enough if Converse returns “not available for this account” (sales/allowlist — contact AWS; redeploy will not fix it). Example:

```sh
uv run scripts/enable_claude.py \
  --company "YourOrg" \
  --website "https://example.com" \
  --use-case "Vehicle damage photo estimating prototype" \
  --model anthropic.claude-sonnet-5 \
  --model anthropic.claude-opus-5-5
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

`deploy.py` enables CloudWatch Transaction Search (idempotent), deploys the AgentCore code zip with tracing, and sends application/usage logs to `/aws/vendedlogs/bedrock-agentcore/ClaimsFactoryHarness`. Local harness: `uv run python -m claims.agent`.

**See logs / Omni:** CloudWatch (same Region) → GenAI Observability or Omni → AgentCore; Log groups → `ClaimsFactoryHarness`.

## Local FastAPI (optional)

```sh
uv sync --all-groups
export ANTHROPIC_API_KEY=your-key
uv run python -m claims.web    # http://127.0.0.1:8080
```

