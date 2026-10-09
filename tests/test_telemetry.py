import json
from types import SimpleNamespace

from opentelemetry.sdk.trace import TracerProvider

from claims import telemetry
from claims.telemetry import attach_strands, configure_telemetry


def test_configure_telemetry_runs_setup_only_when_endpoint_is_set(monkeypatch):
    calls = []
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    configure_telemetry(setup=lambda: calls.append("export"))
    assert calls == []

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    configure_telemetry(setup=lambda: calls.append("export"))
    assert calls == ["export"]


def test_attach_reuses_existing_provider(monkeypatch):
    provider = TracerProvider()
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: provider)
    seen = {}

    class FakeTelemetry:
        def __init__(self, tracer_provider=None):
            seen["provider"] = tracer_provider

        def setup_otlp_exporter(self):
            seen["export"] = True
            return self

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", FakeTelemetry)
    attach_strands()
    assert seen == {"provider": provider}


def test_attach_exports_when_no_provider_is_registered(monkeypatch):
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: object())
    seen = []

    class FakeTelemetry:
        def __init__(self, tracer_provider=None):
            seen.append(tracer_provider)

        def setup_otlp_exporter(self):
            seen.append("export")
            return self

    monkeypatch.setattr("strands.telemetry.StrandsTelemetry", FakeTelemetry)
    attach_strands()
    assert seen == [None, "export"]


# --- Reading the last claim run back from CloudWatch ---

TRACE = "a" * 32
OTHER = "b" * 32
ANSWER = {
    "status": "ok",
    "vehicle": {"make": "Honda", "model": "Civic", "colour": "blue", "confidence": 0.8},
    "plate": {"value": None, "confidence": None},
    "damage": {"summary": "left rear bumper dent", "parts": ["bumper"], "severity": "moderate"},
    "estimate": {"low": 500, "high": 900, "currency": "USD", "assumptions": ["visual only"], "confidence": 0.7},
}
PHOTO = "A" * 400


def event(record: dict, timestamp: int) -> dict:
    return {"timestamp": timestamp, "message": json.dumps(record)}


def span(name: str, span_id: str, start: int, parent: str = "", trace: str = TRACE, **attributes) -> dict:
    return {
        "traceId": trace,
        "spanId": span_id,
        "parentSpanId": parent,
        "name": name,
        "startTimeUnixNano": start,
        "durationNano": 2_000_000_000,
        "attributes": attributes,
    }


def model_log(trace: str = TRACE, text: str = "") -> dict:
    return {
        "traceId": trace,
        "attributes": {"gen_ai.system": "strands-agents", "session.id": "s1"},
        "body": {"finish_reason": "end_turn", "message": {"role": "assistant", "content": [{"text": text}]}},
    }


def test_latest_trace_is_the_newest_model_step_and_ignores_startup_noise():
    records = telemetry.parse_events(
        [
            event(model_log(OTHER), 1),
            event(model_log(TRACE), 2),
            event({"traceId": "c" * 32, "body": "Found credentials", "attributes": {}}, 3),
            event({"body": "no trace id at all"}, 4),
        ]
    )
    assert telemetry.latest_trace_id(records) == TRACE
    assert telemetry.latest_trace_id([]) is None


def test_redact_images_hides_photo_bytes_in_objects_and_embedded_json():
    nested = {"image": {"source": {"bytes": PHOTO}}, "note": "keep"}
    embedded = json.dumps({"image": {"source": {"bytes": PHOTO}}})
    cleaned = telemetry.redact_images({"a": [nested], "b": embedded})
    assert PHOTO not in json.dumps(cleaned)
    assert cleaned["a"][0]["note"] == "keep"
    assert "400 base64 characters omitted" in json.dumps(cleaned)


def test_build_dump_has_the_claim_summary_and_ordered_spans():
    answer = "All three tools returned.\n" + json.dumps(ANSWER)
    records = telemetry.parse_events(
        [
            event(model_log(text=answer), 10),
            event({"traceId": TRACE, "attributes": {}, "body": {"image": {"source": {"bytes": PHOTO}}}}, 5),
        ]
    )
    spans = telemetry.parse_events(
        [
            event(span("chat", "2", 200, parent="1"), 1),
            event(
                span(
                    "invoke_agent Strands Agents",
                    "1",
                    100,
                    **{"gen_ai.request.model": "m", "gen_ai.usage.input_tokens": 7, "gen_ai.usage.output_tokens": 3},
                ),
                2,
            ),
            event(span("chat", "2", 200, parent="1"), 3),  # the same span twice is kept once
        ]
    )
    dump = telemetry.build_dump(TRACE, records, spans)

    assert dump["trace_id"] == TRACE
    assert dump["session_id"] == "s1"
    assert dump["claim"]["status"] == "ok"
    assert dump["claim"]["claim_id"] == TRACE
    assert dump["claim"]["estimate"]["low"] == 500
    assert dump["trace_summary"] == {"model_id": "m", "input_tokens": 7, "output_tokens": 3, "agent_duration_ms": 2000}
    assert [s["spanId"] for s in dump["spans"]] == ["1", "2"]
    assert [r.get("cloudwatch_timestamp") for r in dump["logs"]] == [5, 10]
    assert PHOTO not in json.dumps(dump)


def test_compact_puts_the_claim_first_and_lists_model_and_tool_calls():
    spans = telemetry.parse_events(
        [
            event(span("invoke_agent Strands Agents", "1", 100, **{"gen_ai.request.model": "m"}), 1),
            event(span("chat", "2", 200, parent="1", **{"gen_ai.usage.input_tokens": 10, "gen_ai.usage.output_tokens": 4}), 2),
            event(span("chat m", "3", 210, parent="2", **{"gen_ai.response.finish_reasons": ["tool_use"]}), 3),
            event(span("execute_tool fetch_policy", "4", 300, parent="1", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "fetch_policy", "gen_ai.tool.status": "success"}), 4),
        ]
    )
    records = telemetry.parse_events([event(model_log(text=json.dumps(ANSWER)), 5)])
    short = telemetry.compact(telemetry.build_dump(TRACE, records, spans))

    assert list(short) == ["datetime", "claim", "trace_summary"]
    assert short["claim"]["status"] == "ok"
    assert short["trace_summary"]["model_calls"] == [
        {"duration_ms": 2000, "input_tokens": 10, "output_tokens": 4, "finish_reason": "tool_use"}
    ]
    assert short["trace_summary"]["tools_called"] == [{"name": "fetch_policy", "duration_ms": 2000, "status": "success"}]
    assert short["trace_summary"]["spans"] == 4
    assert "logs" not in short and "spans" not in short


def test_start_time_is_shown_in_pacific_time():
    summer = span("invoke_agent x", "1", 1_791_542_285_000_000_000)  # 2026-10-09 10:38:05 UTC
    winter = span("invoke_agent x", "1", 1_798_000_000_000_000_000)  # 2026-12-23 04:26:40 UTC
    assert telemetry.pacific_time([summer], []) == "2026-10-09 03:38:05 PDT"
    assert telemetry.pacific_time([winter], []) == "2026-12-22 20:26:40 PST"
    assert telemetry.pacific_time([], [{"cloudwatch_timestamp": 1_791_542_285_000}]) == "2026-10-09 03:38:05 PDT"
    assert telemetry.pacific_time([], []) is None


def test_claim_is_none_when_the_final_answer_is_not_json():
    records = telemetry.parse_events([event(model_log(text="sorry, no claim"), 1)])
    assert telemetry.build_dump(TRACE, records, [])["claim"] is None


class FakeLogs:
    def __init__(self, tail, groups, fail_spans=False):
        self.tail, self.groups, self.fail_spans, self.patterns = tail, groups, fail_spans, []

    def get_log_events(self, **kwargs):
        assert kwargs["startFromHead"] is False
        return {"events": self.tail}

    def get_paginator(self, name):
        assert name == "filter_log_events"
        return self

    def paginate(self, logGroupName, filterPattern, startTime, **_extra):
        self.patterns.append(filterPattern)
        if logGroupName == telemetry.SPANS_GROUP and self.fail_spans:
            raise RuntimeError("no such log group")
        if filterPattern.startswith('"claim_id='):
            yield {"events": self.groups.get("claimlog", [])}
            return
        if "attributes.claim_id" in filterPattern:
            yield {"events": self.groups.get("claim", [])}
            return
        yield {"events": self.groups[logGroupName]}


def test_fetch_trace_filters_by_trace_id_and_tolerates_missing_spans():
    group = "/aws/bedrock-agentcore/runtimes/r-DEFAULT"
    tail = [event(model_log(text=json.dumps(ANSWER)), 1_000)]
    logs = FakeLogs(tail, {group: tail, telemetry.SPANS_GROUP: []}, fail_spans=True)
    dump = telemetry.fetch_trace(logs, group)
    assert dump["claim"]["status"] == "ok"
    assert dump["spans"] == []
    assert logs.patterns[0] == f'{{ $.traceId = "{TRACE}" }}'


def test_fetch_trace_returns_none_without_a_claim_run():
    tail = [event({"traceId": TRACE, "body": "startup", "attributes": {}}, 1)]
    assert telemetry.fetch_trace(FakeLogs(tail, {}), "group") is None


def test_runtime_log_group_comes_from_the_runtime_arn():
    arn = "arn:aws:bedrock-agentcore:us-west-2:111122223333:runtime/claims_factory_harness-Abc123"
    assert telemetry.runtime_log_group(arn) == "/aws/bedrock-agentcore/runtimes/claims_factory_harness-Abc123-DEFAULT"


def test_fetch_trace_by_claim_id_follows_the_trace_the_claim_tagged():
    group = "/aws/bedrock-agentcore/runtimes/r-DEFAULT"
    tagged = [event(span("claim", "9", 50, claim_id="abc123"), 1_000)]
    records = [event(model_log(text=json.dumps(ANSWER)), 1_000)]
    spans = [event(span("invoke_agent x", "1", 100), 1_000)]
    logs = FakeLogs([], {"claim": tagged, group: records, telemetry.SPANS_GROUP: spans})
    dump = telemetry.fetch_trace(logs, group, "abc123")
    assert dump["trace_id"] == TRACE
    assert dump["claim"]["status"] == "ok"
    assert logs.patterns[0] == '"claim_id=abc123"'
    assert logs.patterns[1] == '{ $.attributes.claim_id = "abc123" }'
    assert logs.patterns[2] == f'{{ $.traceId = "{TRACE}" }}'


def test_fetch_trace_by_claim_id_is_none_when_nothing_is_tagged_yet():
    logs = FakeLogs([], {"claim": []})
    assert telemetry.fetch_trace(logs, "group", "abc123") is None


def test_claim_id_must_be_a_safe_token_before_it_reaches_a_filter_pattern():
    logs = FakeLogs([], {})
    assert telemetry.fetch_trace(logs, "group", 'x" } { $.a = "b') is None
    assert logs.patterns == []
    assert telemetry.valid_claim_id("6ac8c406530a6f705e17c5da2bda2cc3")
    assert not telemetry.valid_claim_id("")


def test_claim_id_is_found_from_the_runtime_log_even_when_no_span_was_sampled():
    group = "/aws/bedrock-agentcore/runtimes/r-DEFAULT"
    json_record = event({"traceId": TRACE, "body": "claim_id=abc123", "attributes": {}}, 1_000)
    records = [event(model_log(text=json.dumps(ANSWER)), 1_000)]
    logs = FakeLogs([], {"claimlog": [json_record], group: records, telemetry.SPANS_GROUP: []})
    dump = telemetry.fetch_trace(logs, group, "abc123")
    assert dump["trace_id"] == TRACE
    assert dump["claim"]["status"] == "ok"
    assert logs.patterns[0] == '"claim_id=abc123"'  # the span attribute is never needed


def test_trace_id_is_read_from_a_json_record_or_a_text_log_line():
    assert telemetry._trace_id_of(json.dumps({"traceId": TRACE})) == TRACE
    line = f"2026-10-09 17:07:18 INFO [claims] [trace_id={TRACE} span_id=ab trace_sampled=False] - claim_id=abc123"
    assert telemetry._trace_id_of(line) == TRACE
    assert telemetry._trace_id_of("no trace here") is None
    assert telemetry._trace_id_of(json.dumps({"traceId": "not-hex"})) is None


def fake_agent(input_tokens, output_tokens, cycles, tools):
    metrics = SimpleNamespace(
        accumulated_usage={"inputTokens": input_tokens, "outputTokens": output_tokens},
        cycle_durations=cycles,
        tool_metrics={n: SimpleNamespace(call_count=c, error_count=e, total_time=s) for n, (c, e, s) in tools.items()},
    )
    return SimpleNamespace(event_loop_metrics=metrics)


def test_run_summary_is_the_change_in_the_agents_running_totals():
    before = telemetry.metrics_snapshot(fake_agent(1000, 100, [2.0], {"fetch_policy": (1, 0, 0.5)}))
    after = telemetry.metrics_snapshot(
        fake_agent(1900, 160, [2.0, 6.0, 7.5], {"fetch_policy": (2, 0, 0.503), "fetch_estimating": (1, 1, 0.002)})
    )
    summary = telemetry.run_summary("c1", "m", before, after, 13.8)
    assert summary == {
        "claim_id": "c1",
        "model_id": "m",
        "input_tokens": 900,
        "output_tokens": 60,
        "agent_duration_ms": 13800,
        "model_calls": [{"duration_ms": 6000}, {"duration_ms": 7500}],
        "tools_called": [
            {"name": "fetch_policy", "calls": 1, "duration_ms": 3, "status": "success"},
            {"name": "fetch_estimating", "calls": 1, "duration_ms": 2, "status": "error"},
        ],
    }


def test_snapshot_tolerates_an_agent_without_metrics():
    assert telemetry.metrics_snapshot(object()) == {"input": 0, "output": 0, "cycles": [], "tools": {}}


def test_summary_and_claim_id_come_from_logs_when_no_spans_were_kept():
    logged = {
        "claim_id": "abc123", "model_id": "m", "input_tokens": 900, "output_tokens": 60, "agent_duration_ms": 13800,
        "model_calls": [{"duration_ms": 6000}],
        "tools_called": [{"name": "fetch_policy", "calls": 1, "duration_ms": 3, "status": "success"}],
    }
    records = telemetry.parse_events(
        [
            event({"traceId": TRACE, "body": "claim_id=abc123", "attributes": {}}, 1),
            event(model_log(text=json.dumps(ANSWER)), 2),
            event({"traceId": TRACE, "body": telemetry.SUMMARY_PREFIX + json.dumps(logged), "attributes": {}}, 3),
        ]
    )
    short = telemetry.compact(telemetry.build_dump(TRACE, records, []))
    assert short["claim"]["claim_id"] == "abc123"
    assert short["trace_summary"]["model_id"] == "m"
    assert (short["trace_summary"]["input_tokens"], short["trace_summary"]["output_tokens"]) == (900, 60)
    assert short["trace_summary"]["agent_duration_ms"] == 13800
    assert short["trace_summary"]["model_calls"] == [{"duration_ms": 6000}]
    assert short["trace_summary"]["tools_called"][0]["name"] == "fetch_policy"


def test_spans_win_over_the_logged_summary_when_they_exist():
    records = telemetry.parse_events([event({"traceId": TRACE, "body": telemetry.SUMMARY_PREFIX + json.dumps({"model_id": "logged"}), "attributes": {}}, 1)])
    spans = telemetry.parse_events([event(span("invoke_agent x", "1", 100, **{"gen_ai.request.model": "from-span"}), 1)])
    assert telemetry.build_dump(TRACE, records, spans)["trace_summary"]["model_id"] == "from-span"
