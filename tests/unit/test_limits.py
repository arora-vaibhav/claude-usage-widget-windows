"""Limits parsing on captured-shape payloads (values are synthetic). No network."""

import json
import os
import stat
import subprocess
import time

import pytest

from claude_usage import auth_watchdog, collector, snapshot, store

PAYLOAD = {
    "five_hour": None,
    "seven_day": {"utilization": 44.0, "resets_at": "2026-12-02T01:00:00.221328+00:00", "limit_dollars": None},
    "seven_day_opus": None,
    "extra_usage": {"is_enabled": False, "monthly_limit": 1500, "used_credits": 0.0, "utilization": 0.0,
                    "currency": "CAD", "decimal_places": 2, "disabled_reason": "out_of_credits",
                    "spend_limit_reached": False},
    "limits": [
        {"kind": "weekly_all", "group": "weekly", "percent": 44, "severity": "normal",
         "resets_at": "2026-12-02T01:00:00.221328+00:00", "scope": None, "is_active": True},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 0, "severity": "normal",
         "resets_at": "2026-12-02T01:00:00+00:00",
         "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}, "is_active": False},
    ],
    "spend": {"used": {"amount_minor": 250, "currency": "CAD", "exponent": 2}},
    "seven_day_breakdown": {
        "as_of": "2026-11-26T05:15:53+00:00", "window_started_at": "2026-11-25T01:00:00.221328+00:00",
        "rows": [{"key": "claude_code", "display_name": "Claude Code", "percent": 100, "percent_of_limit": 44},
                 {"key": "chat", "display_name": "Chats", "percent": 0, "percent_of_limit": 0}]},
    "weekly_scoped_shares": [{"limit_index": 1, "used_percent_of_weekly": 0, "allowance_percent_of_weekly": 50}],
}
RESET = 1796173200  # 2026-12-02T01:00:00Z


def test_null_five_hour_is_none_not_zero():
    r = collector._parse_oauth_usage(PAYLOAD)
    assert r["five_hour"] is None
    assert "session_utilization" not in r  # the old widget's 0.0-when-absent keys are gone
    assert r["weekly"] == {"pct": 0.44, "resets_at": RESET, "severity": "normal"}
    assert r["source"] == "oauth"


def test_scoped_limits_join_their_share():
    r = collector._parse_oauth_usage(PAYLOAD)
    scoped = [x for x in r["limits"] if x["kind"] == "weekly_scoped"]
    assert scoped == [{"kind": "weekly_scoped", "group": "weekly", "name": "Fable", "pct": 0.0, "severity": "normal",
                       "resets_at": RESET, "active": False, "allowance": 0.5, "used_of_weekly": 0.0}]
    assert r["limits"][0]["allowance"] is None


def test_surfaces_window_and_extra_usage():
    r = collector._parse_oauth_usage(PAYLOAD)
    assert r["surfaces"][0] == {"key": "claude_code", "name": "Claude Code", "pct": 1.0, "pct_of_limit": 0.44}
    assert r["window_started_at"] == RESET - 7 * 86400
    assert r["extra_usage"] == {"enabled": False, "monthly_limit": 15.0, "used": 2.5, "currency": "CAD",
                                "reason": "out_of_credits", "spend_limit_reached": False}


def test_five_hour_when_reported():
    p = dict(PAYLOAD, five_hour={"utilization": 56.0, "resets_at": "2026-11-26T10:00:00+00:00"})
    r = collector._parse_oauth_usage(p)
    assert r["five_hour"]["pct"] == 0.56 and r["five_hour"]["resets_at"] > 0


def test_missing_weekly_stays_none():
    r = collector._parse_oauth_usage({"five_hour": None})
    assert r["weekly"] is None
    assert r["limits"] == [] and r["surfaces"] == [] and r["extra_usage"] is None


HEADERS = {
    "anthropic-ratelimit-unified-overage-status": "rejected",
    "anthropic-ratelimit-unified-representative-claim": "seven_day",
    "anthropic-ratelimit-unified-7d-reset": str(RESET),
    "anthropic-ratelimit-unified-7d-status": "allowed",
    "anthropic-ratelimit-unified-overage-disabled-reason": "out_of_credits",
    "anthropic-ratelimit-unified-7d-utilization": "0.44",
    "anthropic-ratelimit-unified-status": "allowed",
}


def test_header_path_without_five_hour():
    r = collector._parse_rate_limit_headers(HEADERS)
    assert r["five_hour"] is None and "session_utilization" not in r
    assert r["weekly"] == {"pct": 0.44, "resets_at": RESET, "severity": None}
    assert r["extra_usage"]["enabled"] is False and r["extra_usage"]["reason"] == "out_of_credits"
    assert r["representative_claim"] == "seven_day" and r["source"] == "headers"


def test_header_path_with_five_hour():
    h = dict(HEADERS, **{"anthropic-ratelimit-unified-5h-utilization": "0.25",
                         "anthropic-ratelimit-unified-5h-reset": str(RESET * 1000)})
    r = collector._parse_rate_limit_headers(h)
    assert r["five_hour"] == {"pct": 0.25, "resets_at": RESET, "severity": None}


def test_no_headers_is_an_error():
    assert collector._parse_rate_limit_headers({}) == {"error": "The usage API sent no limits."}


class _Run:
    def __init__(self, body, status):
        self.stdout, self.returncode = body + "\n" + status, 0


def test_fetch_oauth_usage_parses_curl_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run(json.dumps(PAYLOAD), "200"))
    r = collector._fetch_oauth_usage("synthetic-token")
    assert r["weekly"]["pct"] == 0.44 and r["five_hour"] is None


def test_tokens_go_to_curl_on_stdin_not_the_command_line(monkeypatch, tmp_path):
    # Another local process can read a command line while curl runs.
    seen = []

    def run(args, **kw):
        seen.append((args, kw.get("input") or ""))
        return _Run(json.dumps(PAYLOAD), "200")

    monkeypatch.setattr(subprocess, "run", run)
    collector._fetch_oauth_usage("synthetic-secret")
    collector._fetch_unified_headers_via_messages("synthetic-secret")
    creds = tmp_path / ".credentials.json"
    creds.write_text(json.dumps({"claudeAiOauth": {"accessToken": "a", "refreshToken": "synthetic-refresh", "expiresAt": 0}}),
                     encoding="utf-8")
    monkeypatch.setattr(collector, "_LAST_REFRESH_ATTEMPT", 0.0)
    collector._refresh_access_token_if_needed(str(creds))
    assert len(seen) == 3
    for args, stdin in seen:
        assert not any("synthetic" in a for a in args) and "synthetic-" in stdin
    assert [s.startswith("Authorization: Bearer ") for _a, s in seen] == [True, True, False]


def test_fetch_errors_are_short_sentences(monkeypatch, tmp_path):
    # They reach the tray tooltip, the OSD and the app verbatim; the watchdog and the snapshot read them too.
    seen = []
    for status in ("401", "429", "500"):
        monkeypatch.setattr(subprocess, "run", lambda *a, s=status, **k: _Run("{}", s))
        seen.append(collector._fetch_oauth_usage("t")["error"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run("[]", "200"))
    seen.append(collector._fetch_oauth_usage("t")["error"])
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    seen.append(collector.fetch_rate_limits(str(tmp_path))["error"])
    assert seen == ["Credentials expired. Run claude to sign in again.", "Rate limited by the usage API. Retrying shortly.",
                    "The usage API returned error 500.", "The usage API sent an unexpected reply.",
                    "No credentials found. Run claude to sign in."]
    assert [auth_watchdog.is_auth_error(e) for e in seen] == [True, False, False, False, True]
    assert [snapshot.now_state(store.connect(":memory:"), {"error": e})["state"] for e in seen] == [
        "signed_out", "error", "error", "error", "signed_out"]


def test_no_credentials_file_skips_the_token_refresh(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    tried = []
    monkeypatch.setattr(collector, "_refresh_access_token_if_needed", tried.append)
    assert collector._load_credentials(str(tmp_path)) is None and tried == []
    (tmp_path / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"accessToken": "synthetic"}}),
                                                encoding="utf-8")
    assert collector._load_credentials(str(tmp_path)) == "synthetic" and len(tried) == 1


@pytest.mark.parametrize("tier,sub,plan", [
    ("default_claude_max_20x", "max", "Ultra"),  # Max 20x was renamed Ultra on 2026-10-30
    ("default_claude_ultra", "ultra", "Ultra"),
    ("default_claude_max_5x", "max", "Max"),
    ("default_claude_pro", "pro", "Pro"),
    (None, "max", None),  # both Max tiers said "max"
    (None, None, None),
])
def test_plan_comes_from_the_credentials(tmp_path, monkeypatch, tier, sub, plan):
    (tmp_path / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "synthetic", "rateLimitTier": tier, "subscriptionType": sub}}),
        encoding="utf-8")
    monkeypatch.setattr(collector, "_fetch_limits", lambda d: collector._parse_oauth_usage(PAYLOAD))
    assert collector.fetch_rate_limits(str(tmp_path))["plan"] == plan
    assert collector._plan(str(tmp_path / "missing")) is None


def _claude_dir(tmp_path, expires_ms):
    (tmp_path / "claude-usage-token").write_text("synthetic-long-lived", encoding="utf-8")
    (tmp_path / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "synthetic-oauth", "expiresAt": expires_ms}}), encoding="utf-8")
    return str(tmp_path)


def test_long_lived_token_prefers_unexpired_oauth_token(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    d = _claude_dir(tmp_path, (time.time() + 3600) * 1000)
    used = []
    monkeypatch.setattr(collector, "_fetch_oauth_usage", lambda t: used.append(t) or collector._parse_oauth_usage(PAYLOAD))
    monkeypatch.setattr(collector, "_fetch_unified_headers_via_messages", lambda t: {"error": "should not run"})
    r = collector.fetch_rate_limits(d)
    assert used == ["synthetic-oauth"] and r["source"] == "oauth"


def test_long_lived_token_falls_back_to_headers_when_oauth_expired(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    d = _claude_dir(tmp_path, (time.time() - 60) * 1000)
    monkeypatch.setattr(collector, "_fetch_oauth_usage", lambda t: {"error": "should not run"})
    monkeypatch.setattr(collector, "_fetch_unified_headers_via_messages",
                        lambda t: collector._parse_rate_limit_headers(HEADERS) if t == "synthetic-long-lived" else {})
    assert collector.fetch_rate_limits(d)["source"] == "headers"


# ---- which path answers: Claude Code's OAuth token first, the long-lived token's headers as the fallback

class _Log(list):
    replies: dict


class _Done:
    def __init__(self, stdout, returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, "", returncode


@pytest.fixture
def paths(monkeypatch):
    """Fake endpoints and token refresh; the calls made, in order."""
    monkeypatch.delenv("CLAUDE_USAGE_TOKEN", raising=False)
    monkeypatch.setattr(collector, "_LAST_REFRESH_ATTEMPT", 0.0)
    monkeypatch.setattr(collector, "_oauth_paused_until", 0.0)
    calls = _Log()
    calls.replies = {"oauth": PAYLOAD, "refresh": None}

    def oauth(t):
        calls.append(("oauth", t))
        r = calls.replies["oauth"]
        return dict(r) if "error" in r else collector._parse_oauth_usage(r)

    def refresh(args, **kw):
        calls.append(("refresh", "synthetic-refresh" in kw.get("input", "")))
        return _Done(json.dumps(calls.replies["refresh"] or {"error": "invalid_grant"}))

    monkeypatch.setattr(collector, "_fetch_oauth_usage", oauth)
    monkeypatch.setattr(collector, "_fetch_unified_headers_via_messages",
                        lambda t: calls.append(("headers", t)) or collector._parse_rate_limit_headers(HEADERS))
    monkeypatch.setattr(subprocess, "run", refresh)
    return calls


def _creds(tmp_path, expires_s, long_lived=True):
    if long_lived:
        (tmp_path / "claude-usage-token").write_text("synthetic-long-lived", encoding="utf-8")
    path = tmp_path / ".credentials.json"
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "synthetic-oauth", "refreshToken": "synthetic-refresh",
                                                  "expiresAt": int((time.time() + expires_s) * 1000),
                                                  "rateLimitTier": "default_claude_max_20x"}}), encoding="utf-8")
    return path


def test_valid_oauth_token_reads_the_full_payload_even_with_a_long_lived_token(tmp_path, paths):
    _creds(tmp_path, 3600)
    r = collector.fetch_rate_limits(str(tmp_path))
    assert paths == [("oauth", "synthetic-oauth")]  # no refresh, no headers call
    assert r["source"] == "oauth" and r["limits"] and r["surfaces"] and r["extra_usage"] and r["plan"] == "Ultra"


def test_expired_oauth_token_is_refreshed_and_written_back_without_a_long_lived_token(tmp_path, paths):
    path = _creds(tmp_path, -60, long_lived=False)
    paths.replies["refresh"] = {"access_token": "synthetic-new", "refresh_token": "synthetic-rotated", "expires_in": 28800,
                                "refresh_token_expires_in": 30 * 86400}
    r = collector.fetch_rate_limits(str(tmp_path))
    assert paths == [("refresh", True), ("oauth", "synthetic-new")] and r["source"] == "oauth"
    text = path.read_text(encoding="utf-8")
    saved = json.loads(text)["claudeAiOauth"]
    assert saved["accessToken"] == "synthetic-new" and saved["refreshToken"] == "synthetic-rotated"
    assert saved["expiresAt"] > (time.time() + 28000) * 1000 and saved["rateLimitTier"] == "default_claude_max_20x"
    assert saved["refreshTokenExpiresAt"] > (time.time() + 29 * 86400) * 1000  # the new token's, not the old one's
    assert "\n" not in text and ": " not in text  # compact, as Claude Code writes it
    assert sorted(p.name for p in tmp_path.iterdir()) == [".credentials.json"]  # no .tmp left
    if os.name != "nt":  # the tokens stay readable by the owner only
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_refresh_whose_write_fails_leaves_the_file_and_no_copy_of_the_tokens(tmp_path, paths, monkeypatch):
    path = _creds(tmp_path, -60, long_lived=False)
    before = path.read_text(encoding="utf-8")
    paths.replies["refresh"] = {"access_token": "synthetic-new", "refresh_token": "synthetic-rotated", "expires_in": 28800}

    def locked(*_a):
        raise PermissionError("the file is in use")

    monkeypatch.setattr(collector.os, "replace", locked)
    collector.fetch_rate_limits(str(tmp_path))
    assert paths[0] == ("refresh", True)
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [".credentials.json"]


def test_a_refresh_write_waits_out_a_reader_holding_the_file(tmp_path, paths, monkeypatch):
    # losing the rotated refresh token would sign Claude Code out, so a brief lock is retried
    path = _creds(tmp_path, -60, long_lived=False)
    paths.replies["refresh"] = {"access_token": "synthetic-new", "refresh_token": "synthetic-rotated", "expires_in": 28800}
    real, tries = collector.os.replace, []

    def busy_twice(*a):
        tries.append(1)
        if len(tries) <= 2:
            raise PermissionError("the file is in use")
        return real(*a)

    monkeypatch.setattr(collector.os, "replace", busy_twice)
    r = collector.fetch_rate_limits(str(tmp_path))
    assert len(tries) == 3 and r["source"] == "oauth"
    assert json.loads(path.read_text(encoding="utf-8"))["claudeAiOauth"]["refreshToken"] == "synthetic-rotated"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".credentials.json"]


def test_with_a_long_lived_token_an_expired_sign_in_is_never_refreshed(tmp_path, paths):
    # rotating Claude Code's refresh token would strand the one a running claude process holds
    path = _creds(tmp_path, -60)
    before = path.read_text(encoding="utf-8")
    for _ in range(3):
        r = collector.fetch_rate_limits(str(tmp_path))
        assert r["source"] == "headers" and r["limits"] == [] and r["plan"] == "Ultra"
    assert paths == [("headers", "synthetic-long-lived")] * 3  # no refresh, no OAuth call with a dead token
    assert path.read_text(encoding="utf-8") == before


def test_with_a_long_lived_token_a_sign_in_renewed_by_claude_code_is_used_again(tmp_path, paths):
    _creds(tmp_path, -60)
    assert collector.fetch_rate_limits(str(tmp_path))["source"] == "headers"
    _creds(tmp_path, 3600)  # Claude Code refreshed its own sign-in meanwhile
    assert collector.fetch_rate_limits(str(tmp_path))["source"] == "oauth"
    assert paths == [("headers", "synthetic-long-lived"), ("oauth", "synthetic-oauth")]


def test_no_credentials_file_uses_the_headers(tmp_path, paths):
    (tmp_path / "claude-usage-token").write_text("synthetic-long-lived", encoding="utf-8")
    r = collector.fetch_rate_limits(str(tmp_path))
    assert paths == [("headers", "synthetic-long-lived")] and r["source"] == "headers"


def test_a_429_on_oauth_pauses_it_instead_of_a_storm(tmp_path, paths):
    _creds(tmp_path, 3600)
    paths.replies["oauth"] = {"error": "Rate limited by the usage API. Retrying shortly."}
    assert collector.fetch_rate_limits(str(tmp_path))["source"] == "headers"
    for _ in range(3):  # the fetches inside the pause go straight to the headers
        collector.fetch_rate_limits(str(tmp_path))
    assert paths == [("oauth", "synthetic-oauth")] + [("headers", "synthetic-long-lived")] * 4
    collector._oauth_paused_until = 0.0
    paths.replies["oauth"] = PAYLOAD
    assert collector.fetch_rate_limits(str(tmp_path))["source"] == "oauth"


def test_any_oauth_failure_pauses_it_while_the_headers_can_stand_in(tmp_path, paths):
    _creds(tmp_path, 3600)
    paths.replies["oauth"] = {"error": "The usage API returned error 500."}
    for _ in range(3):  # not an OAuth call and a headers call on every fetch
        assert collector.fetch_rate_limits(str(tmp_path))["source"] == "headers"
    assert paths == [("oauth", "synthetic-oauth")] + [("headers", "synthetic-long-lived")] * 3


def test_without_a_long_lived_token_a_429_is_reported_and_other_errors_try_the_headers(tmp_path, paths):
    _creds(tmp_path, 3600, long_lived=False)
    paths.replies["oauth"] = {"error": "Rate limited by the usage API. Retrying shortly."}
    assert collector.fetch_rate_limits(str(tmp_path))["error"].startswith("Rate limited")
    assert paths == [("oauth", "synthetic-oauth")]
    paths.clear()
    paths.replies["oauth"] = {"error": "The usage API returned error 500."}
    assert collector.fetch_rate_limits(str(tmp_path))["source"] == "headers"
    assert paths == [("oauth", "synthetic-oauth"), ("headers", "synthetic-oauth")]  # Bearer via curl, not x-api-key
