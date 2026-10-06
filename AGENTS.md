# Agent harnesses

Minimalist functional Python. Local run and AWS deploy of the same harness. Concise docs and comments. Tests for behavior, not wiring trivia.

Reference style: `../sample-riv-cop404/01-simple-strands-harness`.

## Code

- Small pure functions. Side effects at the edge (`claims/agent.py`, deploy, HTTP).
- Typed dicts / plain dicts over class hierarchies. No framework beyond Strands and AgentCore.
- One fixed path: prompt, tools, stop. Do not add open tool-routing.
- Model id lives in one place and matches the IAM profile in `deploy.py`.
- This account: `us.anthropic.claude-sonnet-4-6`.

## Local and AWS

- Same `claims/agent.py` serves locally (`uv run python -m claims.agent`, `:8080`) and on AgentCore Runtime.
- Deploy is CDK from the repo root: code zip for linux/arm64 Python 3.12, no container.
- `deploy.py` synths when `CDK_OUTDIR` is set; otherwise it enables CloudWatch Omni (Transaction Search, idempotent) and deploys.
- Callers use SigV4. No Cognito. No secrets in the repo (`.env` stays gitignored).
- Do not deploy unless asked.

## Git

- Do not commit or push unless asked.
- Features go on a branch. Do not commit feature work straight to `main`.
- Commit messages are one or two sentences: what changed and why. No file lists, no "WIP".
- Stage only the files that belong to that change. Never commit `.env`, keys, or tokens.
- Do not force-push, amend, or skip hooks unless asked.

## Docs

- Lead with purpose, then what the code does. A few lines per idea. No essays.
- Spell out a term the first time it appears. Do not stack acronyms.
- README is how to run it locally and how to deploy. Architecture notes are the design, not a second README.
- Update the doc in the same change as the behavior it describes.
- Comments only when the next line does not say it. No narration of obvious code.
- Do not put secrets, account ids, or live ARNs in docs.

## Tests

- Write tests with the change. Inject the model or HTTP call; do not hit Bedrock in unit tests.
- `tests/conftest.py` fails the run if a test reaches Bedrock or Anthropic HTTP.
- Run `uv run pytest` before calling the work done.
