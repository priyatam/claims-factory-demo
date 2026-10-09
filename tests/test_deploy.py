import deploy


def test_policy_code_comes_from_the_environment_first(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("POLICY_CODE_ADMIN=from-file\n")
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    monkeypatch.setenv("POLICY_CODE_ADMIN", "from-env")
    assert deploy.policy_code() == "from-env"


def test_policy_code_falls_back_to_dot_env(monkeypatch, tmp_path):
    monkeypatch.delenv("POLICY_CODE_ADMIN", raising=False)
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    for line in ("POLICY_CODE_ADMIN=abc123!", "export POLICY_CODE_ADMIN='abc123!'", 'POLICY_CODE_ADMIN="abc123!"'):
        (tmp_path / ".env").write_text(f"# comment\nOTHER=x\n{line}\n")
        assert deploy.policy_code() == "abc123!"


def test_policy_code_is_also_read_from_claims_dot_env(monkeypatch, tmp_path):
    monkeypatch.delenv("POLICY_CODE_ADMIN", raising=False)
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    (tmp_path / "claims").mkdir()
    (tmp_path / "claims" / ".env").write_text("POLICY_CODE_ADMIN=from-claims\n")
    assert deploy.policy_code() == "from-claims"
    (tmp_path / ".env").write_text("POLICY_CODE_ADMIN=from-root\n")
    assert deploy.policy_code() == "from-root"


def test_policy_code_is_empty_when_nowhere_set(monkeypatch, tmp_path):
    monkeypatch.delenv("POLICY_CODE_ADMIN", raising=False)
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    assert deploy.policy_code() == ""
    (tmp_path / ".env").write_text("OTHER=x\n")
    assert deploy.policy_code() == ""


def test_missing_or_long_code_is_reported_as_an_error(monkeypatch, tmp_path, capsys):
    assert "not set" in deploy.policy_problem("")
    assert "longer than 10" in deploy.policy_problem("x" * 11)
    assert deploy.policy_problem("x" * 10) is None

    monkeypatch.delenv("POLICY_CODE_ADMIN", raising=False)
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    env = deploy._public_env("arn:example")
    assert "POLICY_CODE_ADMIN" not in env
    assert "ERROR: POLICY_CODE_ADMIN is not set" in capsys.readouterr().err


def test_a_valid_code_is_passed_to_the_page_and_never_printed(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(deploy, "ROOT", tmp_path)
    monkeypatch.setenv("POLICY_CODE_ADMIN", "s3cret!")
    env = deploy._public_env("arn:example")
    assert env == {"RUNTIME_ARN": "arn:example", "POLICY_CODE_ADMIN": "s3cret!"}
    assert "s3cret" not in capsys.readouterr().err
