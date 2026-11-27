"""Limits parsing on captured-shape payloads (values are synthetic). No network."""

import json
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
