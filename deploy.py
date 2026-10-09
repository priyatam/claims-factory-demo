"""Deploy the claims harness to AgentCore Runtime as a code zip.

    uv run deploy.py
    npx aws-cdk deploy
    npx aws-cdk destroy

When CDK_OUTDIR is set, synth only. Otherwise deploy.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import Duration, RemovalPolicy
from aws_cdk import aws_apigatewayv2 as apigwv2
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk.aws_apigatewayv2_integrations import HttpLambdaIntegration

ROOT = Path(__file__).parent
BUILD = ROOT / ".build"
STACK = "ClaimsFactoryHarness"
PROFILE = "us.anthropic.claude-sonnet-4-6"  # must match MODEL_ID in claims/harness.py
MODEL = PROFILE.split(".", 1)[1]
LOG_GROUP = f"/aws/vendedlogs/bedrock-agentcore/{STACK}"


def log(message: str) -> None:
    print(f"[deploy.py] {message}", file=sys.stderr, flush=True)


def stage() -> Path:
    """Stage claims/ and linux/arm64 deps for the AgentCore code zip."""
    started = time.monotonic()
    log("staging dependencies for linux/arm64 on Python 3.12")
    shutil.rmtree(BUILD, ignore_errors=True)
    BUILD.mkdir()
    requirements = BUILD / "requirements.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-emit-project",
            "--no-hashes",
            "--quiet",
            "--output-file",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--quiet",
            "--target",
            str(BUILD),
            "--python-platform",
            "aarch64-manylinux_2_34",
            "--python-version",
            "3.12",
            "--only-binary",
            ":all:",
            "--requirement",
            str(requirements),
        ],
        cwd=ROOT,
        check=True,
    )
    shutil.copytree(ROOT / "claims", BUILD / "claims", dirs_exist_ok=True)
    for name in ("web.py", "vision.py"):
        (BUILD / "claims" / name).unlink(missing_ok=True)
    for junk in (BUILD / "claims").rglob("__pycache__"):
        shutil.rmtree(junk, ignore_errors=True)
    # AgentCore EntryPoint allows at most two items: the ADOT wrapper, then this file.
    (BUILD / "agent.py").write_text(
        "from claims.agent import app\n\nif __name__ == '__main__':\n    app.run(host='0.0.0.0')\n",
        encoding="utf-8",
    )
    files = [p for p in BUILD.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files) / 1e6
    log(f"staged {len(files)} files, {size:.0f} MB, in {time.monotonic() - started:.0f}s")
    return BUILD


def build_app() -> cdk.App:
    app = cdk.App()
    stack = cdk.Stack(app, STACK)

    log_group = logs.LogGroup(
        stack,
        "HarnessLogs",
        log_group_name=LOG_GROUP,
        retention=logs.RetentionDays.ONE_MONTH,
        removal_policy=RemovalPolicy.DESTROY,
    )

    runtime = agentcore.Runtime(
        stack,
        "Harness",
        runtime_name="claims_factory_harness",
        agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_code_asset(
            path=str(stage()),
            runtime=agentcore.AgentCoreRuntime.PYTHON_3_12,
            entrypoint=["opentelemetry-instrument", "agent.py"],
        ),
        tracing_enabled=True,
        logging_configs=[
            agentcore.LoggingConfig(
                log_type=agentcore.LogType.APPLICATION_LOGS,
                destination=agentcore.LoggingDestination.cloud_watch_logs(log_group),
            ),
            agentcore.LoggingConfig(
                log_type=agentcore.LogType.USAGE_LOGS,
                destination=agentcore.LoggingDestination.cloud_watch_logs(log_group),
            ),
        ],
    )

    runtime.add_to_role_policy(
        iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                f"arn:aws:bedrock:{stack.region}:{stack.account}:inference-profile/{PROFILE}",
                f"arn:aws:bedrock:*::foundation-model/{MODEL}",
                f"arn:aws:bedrock:::foundation-model/{MODEL}",
            ],
        )
    )

    page = lambda_.Function(
        stack,
        "PublicPage",
        runtime=lambda_.Runtime.PYTHON_3_12,
        handler="claims.public.handler",
        code=lambda_.Code.from_asset(str(_public_asset())),
        timeout=Duration.seconds(60),
        memory_size=256,
        environment=_public_env(runtime.agent_runtime_arn),
    )
    page.add_to_role_policy(
        iam.PolicyStatement(
            actions=["bedrock-agentcore:InvokeAgentRuntime"],
            resources=[runtime.agent_runtime_arn, f"{runtime.agent_runtime_arn}/runtime-endpoint/*"],
        )
    )
    http_api = apigwv2.HttpApi(
        stack,
        "PublicHttp",
        default_integration=HttpLambdaIntegration("PublicPage", page),
    )
    default_stage = http_api.default_stage.node.default_child
    default_stage.default_route_settings = apigwv2.CfnStage.RouteSettingsProperty(
        throttling_rate_limit=1,
        throttling_burst_limit=2,
    )

    cdk.CfnOutput(stack, "RuntimeArn", value=runtime.agent_runtime_arn)
    cdk.CfnOutput(stack, "LogGroupName", value=log_group.log_group_name)
    cdk.CfnOutput(stack, "PublicUrl", value=http_api.url or "")
    return app


def _public_asset() -> Path:
    """Zip only the public page and the policy check. The Lambda runtime already has boto3."""
    dest = ROOT / ".public"
    package = dest / "claims"
    shutil.rmtree(dest, ignore_errors=True)
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for name in ("public.py", "gate.py"):
        shutil.copy(ROOT / "claims" / name, package / name)
    return dest


def _public_env(runtime_arn: str) -> dict[str, str]:
    """Pass POLICY_CODE_ADMIN from the deploy shell to the page, up to 10 characters. Never print it."""
    env = {"RUNTIME_ARN": runtime_arn}
    code = os.environ.get("POLICY_CODE_ADMIN", "")
    if code and len(code) <= 10:
        env["POLICY_CODE_ADMIN"] = code
    else:
        log("POLICY_CODE_ADMIN is unset or over 10 characters; the public page will reject every submit")
    return env


def ensure_cloudwatch_omni() -> None:
    """Idempotent: X-Ray spans → CloudWatch Logs (must be ACTIVE before AgentCore tracing deploys)."""
    import boto3  # deploy-time only
    from botocore.exceptions import ClientError

    session = boto3.Session()
    region = session.region_name or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
    account = session.client("sts").get_caller_identity()["Account"]
    logs_client = session.client("logs", region_name=region)
    xray = session.client("xray", region_name=region)

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "TransactionSearchXRayAccess",
                "Effect": "Allow",
                "Principal": {"Service": "xray.amazonaws.com"},
                "Action": "logs:PutLogEvents",
                "Resource": [
                    f"arn:aws:logs:{region}:{account}:log-group:aws/spans:*",
                    f"arn:aws:logs:{region}:{account}:log-group:/aws/application-signals/data:*",
                ],
                "Condition": {
                    "ArnLike": {"aws:SourceArn": f"arn:aws:xray:{region}:{account}:*"},
                    "StringEquals": {"aws:SourceAccount": account},
                },
            }
        ],
    }
    try:
        logs_client.put_resource_policy(
            policyName="AgentCoreTransactionSearch",
            policyDocument=json.dumps(policy),
        )
        log(f"CloudWatch Transaction Search policy ready in {region}")
    except ClientError as exc:
        raise SystemExit(f"CloudWatch Omni setup failed (logs policy): {exc}") from exc

    try:
        current = xray.get_trace_segment_destination()
        if current.get("Destination") != "CloudWatchLogs" or current.get("Status") != "ACTIVE":
            xray.update_trace_segment_destination(Destination="CloudWatchLogs")
            log("X-Ray trace destination set to CloudWatchLogs; waiting until ACTIVE")
        else:
            log("X-Ray trace destination already ACTIVE → CloudWatchLogs")
            return
    except ClientError as exc:
        raise SystemExit(f"CloudWatch Omni setup failed (xray destination): {exc}") from exc

    # CFN rejects AgentCore X-Ray delivery while destination is PENDING.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        got = xray.get_trace_segment_destination()
        dest, status = got.get("Destination"), got.get("Status")
        log(f"X-Ray destination={dest} status={status}")
        if dest == "CloudWatchLogs" and status == "ACTIVE":
            return
        time.sleep(5)
    raise SystemExit(
        "CloudWatch Omni setup timed out waiting for X-Ray destination ACTIVE. Retry uv run deploy.py."
    )


def deploy() -> None:
    subprocess.run(
        ["npx", "--yes", "aws-cdk", "deploy", "--require-approval", "never"],
        cwd=ROOT,
        check=True,
    )
    print(f"\nDeployed. Logs: {LOG_GROUP}")
    print("Omni / GenAI Observability: CloudWatch console (same Region). Then: uv run cli.py dataset/img/veh1.jpeg")


# Always prepare Omni before synth or deploy. cdk invoke sets CDK_OUTDIR and only synths.
ensure_cloudwatch_omni()
if os.environ.get("CDK_OUTDIR"):
    build_app().synth()
else:
    deploy()
