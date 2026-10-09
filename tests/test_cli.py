import cli


def test_a_claim_id_that_is_not_a_safe_token_is_rejected_before_any_aws_call(monkeypatch, capsys):
    def no_aws(*_a, **_k):
        raise AssertionError("AWS must not be called")

    monkeypatch.setattr(cli.boto3, "Session", no_aws)
    assert cli.main(["--claim-id", 'x" } { $.a = "b']) == 1
    assert "--claim-id must be" in capsys.readouterr().err


def test_claim_id_with_no_logs_says_so_and_to_try_again(monkeypatch, capsys):
    class Session:
        def client(self, _name):
            return object()

    monkeypatch.setattr(cli.boto3, "Session", Session)
    monkeypatch.setattr(cli, "runtime_arn", lambda _session: "arn:aws:bedrock-agentcore:us-west-2:1:runtime/r-1")
    seen = {}
    monkeypatch.setattr(cli, "fetch_trace", lambda _logs, group, claim_id=None: seen.update(group=group, claim_id=claim_id))

    assert cli.main(["--claim-id", "abc123"]) == 1
    err = capsys.readouterr().err
    assert "No logs found for claim abc123" in err and "try again" in err
    assert seen == {"group": "/aws/bedrock-agentcore/runtimes/r-1-DEFAULT", "claim_id": "abc123"}


def test_claim_id_prints_the_compact_trace_as_json(monkeypatch, capsys):
    class Session:
        def client(self, _name):
            return object()

    dump = {"datetime": "d", "trace_id": "t", "session_id": "s", "claim": {"status": "ok"},
            "trace_summary": {"model_id": "m"}, "spans": [], "logs": []}
    monkeypatch.setattr(cli.boto3, "Session", Session)
    monkeypatch.setattr(cli, "runtime_arn", lambda _session: "arn:aws:bedrock-agentcore:us-west-2:1:runtime/r-1")
    monkeypatch.setattr(cli, "fetch_trace", lambda *_a, **_k: dump)

    assert cli.main(["--claim-id", "abc123"]) == 0
    out = capsys.readouterr().out
    assert out.index('"datetime"') < out.index('"claim"') < out.index('"trace_summary"')
    assert '"logs"' not in out
