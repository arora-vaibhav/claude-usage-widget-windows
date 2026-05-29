import claude_usage.collector as collector


def test_env_token_preferred(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_USAGE_TOKEN", "  env-tok  ")
    assert collector._load_long_lived_token(str(tmp_path)) == "env-tok"


def test_file_token_used_when_no_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    (tmp_path / collector.LONG_LIVED_TOKEN_FILENAME).write_text("file-tok\n", encoding="utf-8")
    assert collector._load_long_lived_token(str(tmp_path)) == "file-tok"


def test_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    assert collector._load_long_lived_token(str(tmp_path)) is None


def test_blank_file_is_none(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    (tmp_path / collector.LONG_LIVED_TOKEN_FILENAME).write_text("   \n", encoding="utf-8")
    assert collector._load_long_lived_token(str(tmp_path)) is None


def test_load_credentials_prefers_long_lived(tmp_path, monkeypatch):
    # With a long-lived token present, _load_credentials returns it without
    # ever touching the rotating .credentials.json path.
    monkeypatch.setenv("CLAUDE_USAGE_TOKEN", "ll-token")
    assert collector._load_credentials(str(tmp_path)) == "ll-token"
