"""Limits parsing on captured-shape payloads (values are synthetic). No network."""

import json
import subprocess
import time

from claude_usage import collector

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
    assert r["session_utilization"] == 0.0 and r["session_reset"] == 0  # the old widget's keys
    assert r["weekly"] == {"pct": 0.44, "resets_at": RESET, "severity": "normal"}
    assert r["weekly_utilization"] == 0.44 and r["weekly_reset"] == RESET
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
    assert r["overage_status"] == "rejected"


def test_five_hour_when_reported():
    p = dict(PAYLOAD, five_hour={"utilization": 56.0, "resets_at": "2026-11-26T10:00:00+00:00"})
    r = collector._parse_oauth_usage(p)
    assert r["five_hour"]["pct"] == 0.56 and r["session_utilization"] == 0.56
    assert r["session_reset"] == r["five_hour"]["resets_at"] > 0


def test_missing_weekly_stays_none():
    r = collector._parse_oauth_usage({"five_hour": None})
    assert r["weekly"] is None and r["weekly_utilization"] == 0.0
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
    assert r["five_hour"] is None and r["session_utilization"] == 0.0
    assert r["weekly"] == {"pct": 0.44, "resets_at": RESET, "severity": None}
    assert r["extra_usage"]["enabled"] is False and r["extra_usage"]["reason"] == "out_of_credits"
    assert r["representative_claim"] == "seven_day" and r["source"] == "headers"
    assert r["overage_status"] == "rejected"


def test_header_path_with_five_hour():
    h = dict(HEADERS, **{"anthropic-ratelimit-unified-5h-utilization": "0.25",
                         "anthropic-ratelimit-unified-5h-reset": str(RESET * 1000)})
    r = collector._parse_rate_limit_headers(h)
    assert r["five_hour"] == {"pct": 0.25, "resets_at": RESET, "severity": None}
    assert r["session_reset"] == RESET


def test_no_headers_keeps_the_old_error():
    assert collector._parse_rate_limit_headers({}) == {"error": "No rate limit headers in response"}


class _Run:
    def __init__(self, body, status):
        self.stdout, self.returncode = body + "\n" + status, 0


def test_fetch_oauth_usage_parses_curl_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run(json.dumps(PAYLOAD), "200"))
    r = collector._fetch_oauth_usage("synthetic-token")
    assert r["weekly"]["pct"] == 0.44 and r["five_hour"] is None


def test_fetch_oauth_usage_keeps_error_strings(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run("{}", "401"))
    assert collector._fetch_oauth_usage("t") == {"error": "Credentials expired -- re-authenticate with 'claude'"}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Run("{}", "429"))
    assert collector._fetch_oauth_usage("t") == {"error": "Rate limited -- try again shortly"}


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
