"""Spans, the outcome log, and the trace reader.

Strands spans for AgentCore, claim JSON kept for a later adjuster label, and the code that reads the
last claim run back out of CloudWatch (used by `cli.py --otel-logs`).
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from claims.claim import assessment_from_model, json_from_model

# Generated eval files live here (gitignored). Not part of the deployed zip, which stages only claims/.
RUNTIME_ROOT = Path(__file__).resolve().parents[1] / ".runtime"
OUTCOMES = RUNTIME_ROOT / "outcomes.jsonl"
_ON = {"1", "true", "yes", "on"}
_DROP = {"image", "image_b64", "image_url", "photo", "data", "media_type"}
OTEL_STREAM = "otel-rt-logs"  # the runtime's OpenTelemetry log records (model and tool messages)
SPANS_GROUP = "aws/spans"  # every span, once CloudWatch Transaction Search is on
_TRACE_ID = re.compile(r"[0-9a-f]{32}")
_CLAIM_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")  # also the shape claims/agent.py accepts
_IMAGE_BYTES = re.compile(r'("bytes":\s*")([A-Za-z0-9+/=]{200,})(")')


def configure_telemetry(setup: Callable[[], None] | None = None) -> None:
    """Export spans only when AgentCore has set an OTLP endpoint."""
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip():
        return
    (setup or attach_strands)()


def attach_strands() -> None:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from strands.telemetry import StrandsTelemetry

    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        # The instrument wrapper already registered this provider and its exporter.
        StrandsTelemetry(tracer_provider=provider)
        return
    StrandsTelemetry().setup_otlp_exporter()


def eval_log_enabled() -> bool:
    return os.environ.get("CLAIMS_EVAL_LOG", "").strip().lower() in _ON


def outcomes_path() -> Path:
    """CLAIMS_EVAL_LOG_PATH overrides the default log file (used by the end-to-end eval)."""
    override = os.environ.get("CLAIMS_EVAL_LOG_PATH", "").strip()
    return Path(override) if override else OUTCOMES


def record_outcome(result: dict) -> None:
    """Write one claim, without photo bytes. Quiet unless CLAIMS_EVAL_LOG is set; never raises into the response."""
    if not eval_log_enabled():
        return
    try:
        kept = {
            key: value
            for key, value in result.items()
            if key not in _DROP and not isinstance(value, (bytes, bytearray))
        }
        kept["logged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        path = outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(kept) + "\n")
    except Exception:
        return


# --- What the runtime logs for each claim, so a trace can be summarised even when no spans were kept ---


def metrics_snapshot(agent) -> dict:
    """Strands' running totals for one agent: tokens, cycle times, and per-tool calls. They grow across claims."""
    metrics = getattr(agent, "event_loop_metrics", None)
    usage = getattr(metrics, "accumulated_usage", None) or {}
    tools = {
        name: (m.call_count, m.error_count, m.total_time)
        for name, m in (getattr(metrics, "tool_metrics", None) or {}).items()
    }
    return {
        "input": usage.get("inputTokens", 0),
        "output": usage.get("outputTokens", 0),
        "cycles": list(getattr(metrics, "cycle_durations", None) or []),
        "tools": tools,
    }


def run_summary(claim_id: str, model_id: str, before: dict, after: dict, seconds: float) -> dict:
    """What one claim cost: the change in the agent's totals between two snapshots."""
    tools = []
    for name, (calls, errors, spent) in after["tools"].items():
        was_calls, was_errors, was_spent = before["tools"].get(name, (0, 0, 0.0))
        if calls > was_calls:
            tools.append(
                {
                    "name": name,
                    "calls": calls - was_calls,
                    "duration_ms": round((spent - was_spent) * 1000),
                    "status": "error" if errors > was_errors else "success",
                }
            )
    return {
        "claim_id": claim_id,
        "model_id": model_id,
        "input_tokens": after["input"] - before["input"],
        "output_tokens": after["output"] - before["output"],
        "agent_duration_ms": round(seconds * 1000),
        "model_calls": [{"duration_ms": round(d * 1000)} for d in after["cycles"][len(before["cycles"]) :]],
        "tools_called": tools,
    }


SUMMARY_PREFIX = "claim_summary "
_CLAIM_LINE = re.compile(r"claim_id=([A-Za-z0-9_-]{1,64})")


def logged_summary(logs: list[dict]) -> dict | None:
    """The run_summary the runtime logged for this trace, if its log record arrived."""
    for record in reversed(logs):
        body = record.get("body")
        if isinstance(body, str) and body.startswith(SUMMARY_PREFIX):
            try:
                return json.loads(body[len(SUMMARY_PREFIX) :])
            except ValueError:
                return None
    return None


def logged_claim_id(logs: list[dict]) -> str | None:
    """The claim id the runtime logged inside this trace."""
    for record in logs:
        body = record.get("body")
        match = _CLAIM_LINE.fullmatch(body) if isinstance(body, str) else None
        if match:
            return match[1]
    return None


# --- Reading the last claim run back from CloudWatch ---


def runtime_log_group(arn: str) -> str:
    return f"/aws/bedrock-agentcore/runtimes/{arn.rsplit('/', 1)[-1]}-DEFAULT"


def parse_events(events: list[dict]) -> list[dict]:
    """CloudWatch events to OpenTelemetry records, keeping the CloudWatch time for ordering."""
    records = []
    for event in events:
        try:
            record = json.loads(event["message"])
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(record, dict):
            records.append({**record, "cloudwatch_timestamp": event.get("timestamp")})
    return records


def is_agent_record(record: dict) -> bool:
    """A record from a model or tool step, as opposed to startup or request-handling noise."""
    name = str(record.get("name") or "")
    attributes = record.get("attributes") or {}
    return bool(_TRACE_ID.fullmatch(str(record.get("traceId") or ""))) and (
        "gen_ai.system" in attributes or name.startswith(("invoke_agent", "chat", "execute_tool"))
    )


def latest_trace_id(records: list[dict]) -> str | None:
    """The newest claim run: records arrive oldest first, so scan from the end."""
    return next((r["traceId"] for r in reversed(records) if is_agent_record(r)), None)


def redact_images(value):
    """Replace photo bytes with their length so the dump stays readable."""
    if isinstance(value, str):
        return _IMAGE_BYTES.sub(lambda m: f"{m[1]}<{len(m[2])} base64 characters omitted>{m[3]}", value)
    if isinstance(value, dict):
        return {
            key: f"<{len(item)} base64 characters omitted>"
            if key == "bytes" and isinstance(item, str) and len(item) > 200
            else redact_images(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_images(item) for item in value]
    return value


def claim_from_logs(logs: list[dict], claim_id: str) -> dict | None:
    """Rebuild the claim record the caller received from the model's final answer.

    The claim id is the one the runtime logged, or the trace id for runs before it logged one.
    """
    for record in reversed(logs):
        body = record.get("body")
        if not (isinstance(body, dict) and body.get("finish_reason") == "end_turn"):
            continue
        content = (body.get("message") or {}).get("content")
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict)) if isinstance(content, list) else str(content or "")
        try:
            return assessment_from_model(json_from_model(text), claim_id)
        except Exception:
            return None
    return None


def pacific_time(spans: list[dict], logs: list[dict]) -> str | None:
    """When the run started, in US Pacific time (PST in winter, PDT in summer)."""
    if spans:
        seconds = min(s["startTimeUnixNano"] for s in spans) / 1e9
    else:
        stamps = [r["cloudwatch_timestamp"] for r in logs if r.get("cloudwatch_timestamp")]
        if not stamps:
            return None
        seconds = min(stamps) / 1000
    from zoneinfo import ZoneInfo  # imported here: the page Lambda loads this module and may lack tz data

    return datetime.fromtimestamp(seconds, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")


def build_dump(trace_id: str, records: list[dict], span_records: list[dict]) -> dict:
    """One trace: its spans (parent to child by parentSpanId), log records, and the claim they produced."""
    unique = {r["spanId"]: r for r in [*records, *span_records] if "startTimeUnixNano" in r and r.get("spanId")}
    spans = sorted(unique.values(), key=lambda r: r["startTimeUnixNano"])
    logs = sorted((r for r in records if "startTimeUnixNano" not in r), key=lambda r: r.get("cloudwatch_timestamp") or 0)
    agent = next((s for s in spans if str(s.get("name", "")).startswith("invoke_agent")), {})
    attributes = agent.get("attributes") or {}
    session = next(
        ((r.get("attributes") or {}).get("session.id") for r in [*spans, *logs] if (r.get("attributes") or {}).get("session.id")),
        None,
    )
    duration = agent.get("durationNano")
    logged = logged_summary(logs) or {}
    claim_id = logged_claim_id(logs) or trace_id
    return redact_images(
        {
            "datetime": pacific_time(spans, logs),
            "trace_id": trace_id,
            "session_id": session,
            "claim": claim_from_logs(logs, claim_id),
            "trace_summary": {
                "model_id": attributes.get("gen_ai.request.model") or logged.get("model_id"),
                "input_tokens": attributes.get("gen_ai.usage.input_tokens", logged.get("input_tokens")),
                "output_tokens": attributes.get("gen_ai.usage.output_tokens", logged.get("output_tokens")),
                "agent_duration_ms": round(duration / 1e6) if duration else logged.get("agent_duration_ms"),
            },
            "logged_summary": logged or None,
            "spans": spans,
            "logs": logs,
        }
    )


def compact(dump: dict) -> dict:
    """The claim first, then a summary: who ran, the model calls, and the tools called."""
    spans = dump["spans"]
    attr = lambda span, key: (span.get("attributes") or {}).get(key)  # noqa: E731
    finish = {
        s.get("parentSpanId"): (attr(s, "gen_ai.response.finish_reasons") or [None])[0]
        for s in spans
        if attr(s, "gen_ai.response.finish_reasons")
    }
    model_calls = [
        {
            "duration_ms": round(s["durationNano"] / 1e6),
            "input_tokens": attr(s, "gen_ai.usage.input_tokens"),
            "output_tokens": attr(s, "gen_ai.usage.output_tokens"),
            "finish_reason": finish.get(s["spanId"]),
        }
        for s in spans
        if s.get("name") == "chat"
    ]
    tools_called = [
        {
            "name": attr(s, "gen_ai.tool.name") or str(s.get("name", "")).removeprefix("execute_tool "),
            "duration_ms": round(s["durationNano"] / 1e6),
            "status": attr(s, "gen_ai.tool.status"),
        }
        for s in spans
        if attr(s, "gen_ai.operation.name") == "execute_tool"
    ]
    logged = dump.get("logged_summary") or {}
    return {
        "datetime": dump["datetime"],
        "claim": dump["claim"],
        "trace_summary": {
            "trace_id": dump["trace_id"],
            "session_id": dump["session_id"],
            **dump["trace_summary"],
            "model_calls": model_calls or logged.get("model_calls", []),
            "tools_called": tools_called or logged.get("tools_called", []),
            "spans": len(spans),
            "log_records": len(dump["logs"]),
        },
    }


def valid_claim_id(claim_id: object) -> bool:
    return isinstance(claim_id, str) and bool(_CLAIM_ID.fullmatch(claim_id))


def _filter(logs, group: str, pattern: str, start: int, streams: list[str] | None = None) -> list[dict]:
    extra = {"logStreamNames": streams} if streams else {}
    pages = logs.get_paginator("filter_log_events").paginate(logGroupName=group, filterPattern=pattern, startTime=start, **extra)
    return [event for page in pages for event in page["events"]]


def _trace_id_of(message: str) -> str | None:
    """The trace id in one runtime log event: an OpenTelemetry JSON record, or a text line with trace_id=."""
    try:
        found = json.loads(message).get("traceId")
    except (ValueError, AttributeError):
        found = (re.search(r"trace_id=([0-9a-f]{32})", message) or [None, None])[1]
    return found if _TRACE_ID.fullmatch(str(found or "")) else None


def trace_id_for_claim(logs, log_group: str, claim_id: str, now_ms: int | None = None) -> tuple[str, int] | None:
    """The trace that ran this claim.

    claims/agent.py logs `claim_id=<id>` inside the claim's span, and log records reach CloudWatch with
    the trace id even for traces whose spans were not sampled. The span's claim_id attribute is the
    fallback, found in aws/spans only when the trace was sampled.
    """
    if not valid_claim_id(claim_id):
        return None
    start = (now_ms or int(time.time() * 1000)) - 24 * 3600 * 1000
    for event in _filter(logs, log_group, f'"claim_id={claim_id}"', start):
        trace_id = _trace_id_of(event.get("message", ""))
        if trace_id:
            return trace_id, start
    spans = parse_events(_filter(logs, SPANS_GROUP, f'{{ $.attributes.claim_id = "{claim_id}" }}', start))
    trace_id = next((r["traceId"] for r in spans if _TRACE_ID.fullmatch(str(r.get("traceId") or ""))), None)
    return (trace_id, start) if trace_id else None


def fetch_trace(logs, log_group: str, claim_id: str | None = None) -> dict | None:
    """Pull every record of one claim run from CloudWatch Logs: the given claim, else the newest run.

    None when there is no such run (or its spans have not arrived yet).
    """
    if claim_id is not None:
        found = trace_id_for_claim(logs, log_group, claim_id)
        if found is None:
            return None
        trace_id, start = found
    else:
        tail = logs.get_log_events(logGroupName=log_group, logStreamName=OTEL_STREAM, startFromHead=False, limit=500)["events"]
        trace_id = latest_trace_id(parse_events(tail))
        if trace_id is None:
            return None
        start = max(e["timestamp"] for e in tail) - 3 * 3600 * 1000
    pattern = f'{{ $.traceId = "{trace_id}" }}'
    records = parse_events(_filter(logs, log_group, pattern, start, [OTEL_STREAM]))
    try:
        spans = parse_events(_filter(logs, SPANS_GROUP, pattern, start))
    except Exception:
        spans = []  # CloudWatch Transaction Search is off, or its spans have not arrived yet
    return build_dump(trace_id, records, spans)
