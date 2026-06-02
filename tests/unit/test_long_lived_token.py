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


def test_long_lived_token_routes_to_unified_headers(tmp_path, monkeypatch):
    # A long-lived token (setup-token) lacks the user:profile scope, so it must
    # NOT hit /api/oauth/usage (which 403s) — it must read the /v1/messages
    # unified rate-limit headers instead.
    monkeypatch.setenv("CLAUDE_USAGE_TOKEN", "sk-ant-oat01-LONGLIVED")
    called = {"oauth": 0, "messages": 0}

    def fake_oauth(_tok):
        called["oauth"] += 1
        return {"error": "OAuth usage error 403"}

    def fake_messages(tok):
        called["messages"] += 1
        assert tok == "sk-ant-oat01-LONGLIVED"
        return {"session_utilization": 0.22, "weekly_utilization": 0.09}

    monkeypatch.setattr(collector, "_fetch_oauth_usage", fake_oauth)
    monkeypatch.setattr(collector, "_fetch_unified_headers_via_messages", fake_messages)

    result = collector.fetch_rate_limits(str(tmp_path))
    assert result["session_utilization"] == 0.22
    assert called["messages"] == 1
    assert called["oauth"] == 0  # never touched the 403-ing endpoint


def test_subscription_token_uses_oauth_usage(tmp_path, monkeypatch):
    # Without a long-lived token, the subscription path (/api/oauth/usage) is used.
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    import json as _json
    creds = {"claudeAiOauth": {"accessToken": "sub-token", "expiresAt": 0}}
    (tmp_path / ".credentials.json").write_text(_json.dumps(creds), encoding="utf-8")
    called = {"oauth": 0}

    def fake_oauth(tok):
        called["oauth"] += 1
        return {"session_utilization": 0.5, "weekly_utilization": 0.3}

    # Avoid the real curl refresh attempt during the test.
    monkeypatch.setattr(collector, "_refresh_access_token_if_needed", lambda _p: None)
    monkeypatch.setattr(collector, "_fetch_oauth_usage", fake_oauth)
    result = collector.fetch_rate_limits(str(tmp_path))
    assert result["session_utilization"] == 0.5
    assert called["oauth"] == 1
