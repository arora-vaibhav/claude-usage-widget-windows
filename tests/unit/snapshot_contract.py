"""The snapshot contract between the data layer and every UI surface.

``snapshot.build()`` must return a dict that passes :func:`check`, and every
UI fixture must pass it too. Times are ISO 8601 strings with a UTC offset,
days are ``YYYY-MM-DD`` local dates, money is USD floats, utilization is a
0..1 float, durations are integer milliseconds. See
docs/superpowers/specs/2026-11-26-native-app-design.md for field meanings.
"""

from __future__ import annotations

from typing import Any

NUM = (int, float)


class Opt:
    """A value that may also be None."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner


class OneOf:
    """A string limited to the given values."""

    def __init__(self, *values: str) -> None:
        self.values = values


LEVEL = OneOf("none", "normal", "caution", "critical", "limited")
STATE = OneOf("loading", "ok", "stale", "signed_out", "error")

METER = {
    "pct": NUM,
    "resets_at": Opt(str),
    "level": LEVEL,
    "forecast": Opt(str),
    "eta": Opt(str),
}

NOW = {
    "primary": OneOf("weekly", "session"),
    "weekly": Opt(METER),
    "session": Opt(METER),
    "today_cost": NUM,
    "today_tokens": int,
    "tokens_per_min": NUM,
    "active_sessions": int,
    "active_subagents": int,
    "live": bool,
    "state": STATE,
    "as_of": Opt(str),
    "message": Opt(str),
    "error_kind": Opt(OneOf("fetch", "store")),  # what the last error came from; None without one
}

LIMITS = {
    "weekly": Opt({
        "pct": NUM,
        "resets_at": Opt(str),
        "window_start": Opt(str),
        "level": LEVEL,
        "elapsed": NUM,
        "pace": Opt(NUM),
        "usual": Opt(NUM),
        "eta": Opt(str),
        "hits_before_reset": bool,
        "locked_hours": Opt(NUM),
        "daily_budget": Opt(NUM),
        "hourly_rate": Opt(NUM),
        "series": [{"t": str, "pct": NUM}],
        "usual_series": [{"elapsed": NUM, "pct": NUM}],
    }),
    "session": Opt({"pct": NUM, "resets_at": Opt(str), "level": LEVEL}),
    "session_note": Opt(str),
    "scoped": [{
        "name": str,
        "pct": NUM,
        "active": bool,
        "allowance": Opt(NUM),
        "resets_at": Opt(str),
    }],
    "surfaces": [{"name": str, "pct": NUM, "pct_of_limit": NUM}],
    "extra_usage": Opt({
        "enabled": bool,
        "monthly_limit": Opt(NUM),
        "used": NUM,
        "currency": str,
        "reason": Opt(str),
    }),
    "past_weeks": [{"start": str, "peak": NUM, "hours_at_limit": NUM, "current": bool}],
    "weeks_hit": int,
    "weeks_counted": int,
    # how the limits were fetched: "headers" leaves out scoped limits, surfaces and extra usage
    "source": Opt(OneOf("oauth", "headers")),
}

KPI = {
    "key": OneOf("cost", "tokens", "sessions", "agent_time", "cache_hit"),
    "label": str,
    "format": OneOf("usd", "tokens", "int", "duration", "pct"),
    "value": Opt(NUM),  # None only for a cache hit rate with no prompt tokens in range
    "prev": Opt(NUM),
    "delta": Opt(NUM),
    "delta_unit": OneOf("pct", "pts"),
    "series": [NUM],
    "note": str,
    "hint": Opt(str),
}

METRICS = ("cost", "tokens", "messages", "agent_time")
GROUP = {
    "keys": [str],
    "colors": [int],
    "values": {m: [[NUM]] for m in METRICS},
}

TIMELINE = {
    "bucket": OneOf("hour", "6h", "day", "week"),
    "starts": [str],
    "partial_last": bool,
    "totals": {m: NUM for m in METRICS},
    "by": {"project": GROUP, "model": GROUP},
}

PROJECT = {"name": str, "sessions": int, "cost": NUM, "color": int, "idle": bool}

SESSION = {
    "id": str,
    "title": Opt(str),
    "project": str,
    "color": int,
    "model": str,
    "entrypoint": str,
    "started": str,
    "ended": str,
    "agent_ms": int,
    "turns": int,
    "tokens": int,
    "cost": NUM,
    "cache_hit": Opt(NUM),
    "subagents": int,
    "interactive": bool,
}

ACTIVITY = {
    "days": [{"date": str, "agent_ms": int, "cost": NUM, "level": int, "partial": bool}],
    "active_days": int,
    "span_days": int,
    "streak": int,
    "longest_streak": int,
    "facts": [{"label": str, "value": str, "detail": Opt(str)}],
}

MODEL = {
    "name": str,
    "id": str,
    "cost": NUM,
    "share": NUM,
    "output": int,
    "cache_hit": Opt(NUM),
    "calls": int,
    "color": int,
    "priced": OneOf("exact", "family", "unpriced"),
}

PART = {"cost": NUM, "tokens": int}

SNAPSHOT = {
    "schema": int,
    "generated_at": str,
    "range": {
        "key": OneOf("24h", "7d", "30d", "90d", "all"),
        "days": Opt(int),
        "from": str,
        "to": str,
        "label": str,
    },
    "project": Opt(str),
    "status": {
        "state": STATE,
        "message": Opt(str),
        "updated_at": Opt(str),
        "limits_as_of": Opt(str),
        "indexing": Opt({"done": int, "total": int}),
        "plan": Opt(str),  # "Ultra", "Max" or "Pro" from the credentials
        "transcripts": int,
        "coverage_from": Opt(str),
        "legacy_before": Opt(str),
    },
    "now": NOW,
    "limits": LIMITS,
    "kpis": [KPI],
    "timeline": TIMELINE,
    "projects": [PROJECT],
    "sessions": [SESSION],
    "top_sessions": [SESSION],  # the ten costliest of every session in range
    "sessions_total": int,
    "sessions_total_all": int,  # sessions in range over every project, whatever the filter
    "activity": ACTIVITY,
    "models": [MODEL],
    "cost_split": {
        "input": PART,
        "output": PART,
        "cache_read": PART,
        "cache_write": PART,
        "caching_saved": NUM,
    },
}


def check(value: Any, spec: Any = SNAPSHOT, path: str = "snapshot") -> None:
    """Raise AssertionError naming the first place *value* breaks *spec*."""
    if isinstance(spec, Opt):
        if value is not None:
            check(value, spec.inner, path)
        return
    if isinstance(spec, OneOf):
        assert value in spec.values, f"{path}: {value!r} not in {spec.values}"
        return
    if isinstance(spec, dict):
        assert isinstance(value, dict), f"{path}: expected object, got {type(value).__name__}"
        missing = set(spec) - set(value)
        assert not missing, f"{path}: missing keys {sorted(missing)}"
        for key, sub in spec.items():
            check(value[key], sub, f"{path}.{key}")
        return
    if isinstance(spec, list):
        assert isinstance(value, list), f"{path}: expected list, got {type(value).__name__}"
        for i, item in enumerate(value):
            check(item, spec[0], f"{path}[{i}]")
        return
    if spec is int:
        assert isinstance(value, int) and not isinstance(value, bool), f"{path}: expected int, got {value!r}"
        return
    if spec == NUM:
        assert isinstance(value, NUM) and not isinstance(value, bool), f"{path}: expected number, got {value!r}"
        return
    assert isinstance(value, spec), f"{path}: expected {spec.__name__}, got {value!r}"


def check_consistency(snap: dict) -> None:
    """Cross-field rules the type check can't express."""
    tl = snap["timeline"]
    n = len(tl["starts"])
    for by in ("project", "model"):
        g = tl["by"][by]
        assert len(g["keys"]) == len(g["colors"]), f"timeline.by.{by}: keys/colors length differ"
        for m in METRICS:
            assert len(g["values"][m]) == len(g["keys"]), f"timeline.by.{by}.values.{m}: one series per key"
            for s in g["values"][m]:
                assert len(s) == n, f"timeline.by.{by}.values.{m}: series length {len(s)} != {n} starts"
    for k in snap["kpis"]:
        assert len(k["series"]) >= 2, f"kpi {k['key']}: sparkline needs 2+ points"
    assert snap["sessions_total"] >= len(snap["sessions"]) >= len(snap["top_sessions"])
