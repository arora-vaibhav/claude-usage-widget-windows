"""Snapshots for every UI surface, computed from the store, the latest limits and the clock.

``build`` returns the whole app view for one range and project filter;
``now_state`` returns the small range-independent part the OSD needs. Both
follow tests/unit/snapshot_contract.py. Times are ISO 8601 with the local
offset, money is API-equivalent USD computed from tokens at read time.

``now`` must be the current time: the current range counts every stored call
from its start on, so a ``now`` in the past would also count later calls
(the previous-period comparison is exact either way). Module caches are
keyed on the store file and its rollup generation, so they are safe to share
between the worker and the GUI thread; in-memory stores are not cached.
"""

from __future__ import annotations

import bisect
import math
import sqlite3
import statistics
import time as _time
from datetime import date, datetime, time, timedelta
from typing import Optional

from claude_usage import pricing, store, tokens

SCHEMA = 1
RANGES = {
    "24h": (1, "hour", "last 24 hours"),
    "7d": (7, "6h", "last 7 days"),
    "30d": (30, "day", "last 30 days"),
    "90d": (90, "day", "last 90 days"),
    "all": (None, "week", "all time"),
}
TOP = 5
OTHER = -1
LEGACY = -2  # timeline series of days that only have the old usage-daily totals
SESSION_CAP = 2000
TOP_SESSIONS = 10
ULTRA_NOTE = "Ultra has no 5-hour limit, so your weekly limit is the one that counts."
ACTIVITY_DAYS = 84
LIVE_MS = 2 * 60_000
ACTIVE_MS = 5 * 60_000
STALE_S = 15 * 60
WEEK_S = 7 * 86400
HIT = 0.995  # utilization is reported in whole percents; 100% reads as 1.0
USUAL_STEPS = 56  # one usual-week point every 3 hours
COST_NOTE = ("Cost is API-equivalent: every API call in your local Claude Code transcripts, subagents included, "
             "priced at Anthropic's published per-token rates (as of {as_of}), with 5-minute and 1-hour cache "
             "writes priced separately. It is not a bill. Claude Code's own /cost also counts side requests it "
             "does not save in transcripts, such as web searches and title or summary calls, and for a session "
             "older than its cleanup period it can read much higher because older transcript files are gone. "
             "Subagent transcripts written by Claude Code builds before about October 2026 often kept only the "
             "first streamed line of a reply, so their output reads low.")

_SUMS = "COUNT(*), SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), SUM(cache_write_1h), SUM(web_search)"
_PROJ = "coalesce(project, 'Unknown')"


# ---------------------------------------------------------------- small helpers

def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    t = _time.localtime(ts)  # several thousand per build; faster than datetime.astimezone()
    off = t.tm_gmtoff // 60
    return _time.strftime("%Y-%m-%dT%H:%M:%S", t) + f"{'-' if off < 0 else '+'}{abs(off) // 60:02d}:{abs(off) % 60:02d}"


def _midnight(d: date) -> float:
    return datetime.combine(d, time()).timestamp()


def _money(x: float) -> str:
    if 0 < x < 0.005:
        return "<$0.01"
    return f"${x:,.0f}" if x >= 100 else f"${x:,.2f}"


def _dur(ms: float) -> str:
    m = int(round(ms / 60000))
    if ms < 60000:
        return f"{int(ms // 1000)}s"
    return f"{m}m" if m < 60 else f"{m // 60}h {m % 60}m"


def _md(ts: float) -> str:
    d = datetime.fromtimestamp(ts)
    return f"{d:%b} {d.day}"


def _clock(ts: float, now: float) -> str:
    d = datetime.fromtimestamp(ts)
    hm = f"{d.hour % 12 or 12}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"
    return hm if d.date() == datetime.fromtimestamp(now).date() else f"{d:%a} {hm}"


def model_name(cid: str) -> str:
    """'claude-opus-5-5' -> 'Opus 5.5'; unknown ids are returned as they are."""
    parts = cid.split("-")
    if len(parts) < 3 or parts[0] != "claude":
        return cid
    nums = [p for p in parts[2:] if p.isdigit() and len(p) <= 2]
    return parts[1].capitalize() + (" " + ".".join(nums) if nums else "")


_rate = pricing.scaled_rates  # (canonical id, priced, ri, ro, rr, r5, r1) per MTok


def _price(model, speed, long, i, o, r, w5, w1, ws) -> tuple:
    """Cost parts (input incl. web search, output, cache read, cache write) and caching savings."""
    _, _, ri, ro, rr, r5, r1 = _rate(model, speed, long)
    saved = (r * (ri - rr) - w5 * (r5 - ri) - w1 * (r1 - ri)) / 1e6
    return i * ri / 1e6 + ws * pricing.WEB_SEARCH_USD, o * ro / 1e6, r * rr / 1e6, (w5 * r5 + w1 * r1) / 1e6, saved


def _pfilter(project: Optional[str]) -> tuple:
    return (f" AND {_PROJ} = ?", (project,)) if project else ("", ())


# ---------------------------------------------------------------- data access

def _call_rows(conn, t0_ms: int, t1_ms: int, project: Optional[str]) -> list:
    """Rows like _rollup_rows(hourly=True) for a span shorter than an hour, straight from calls."""
    pf, pa = _pfilter(project)
    return conn.execute(
        f"SELECT day, hour, session_id, is_subagent, {_PROJ}, model, coalesce(speed, ''), "
        f"input + cache_read + cache_write_5m + cache_write_1h > {pricing.LONG_PROMPT_TOKENS}, {_SUMS}, "
        f"MIN(ts_ms), MAX(ts_ms) FROM calls WHERE ts_ms >= ? AND ts_ms < ?{pf} GROUP BY 1, 2, 3, 4, 5, 6, 7, 8",
        (t0_ms, t1_ms) + pa).fetchall()


def _rollup_rows(conn, d0: str, d1: str, project: Optional[str], hourly: bool) -> list:
    """(day, hour or None, session, is_subagent, project, model, speed, long, calls, input, output, cache_read,
    write_5m, write_1h, web_search, first_ms, last_ms) for local days d0..d1."""
    pf, pa = _pfilter(project)
    if hourly:
        return conn.execute(
            "SELECT day, hour, session_id, is_subagent, project, model, speed, long, SUM(calls), SUM(input), "
            "SUM(output), SUM(cache_read), SUM(cache_write_5m), SUM(cache_write_1h), SUM(web_search), "
            f"MIN(first_ms), MAX(last_ms) FROM rollup WHERE day >= ? AND day <= ?{pf} GROUP BY 1, 2, 3, 4, 5, 6, 7, 8",
            (d0, d1) + pa).fetchall()
    return conn.execute(
        "SELECT day, NULL, session_id, is_subagent, project, model, speed, long, calls, input, output, cache_read, "
        f"cache_write_5m, cache_write_1h, web_search, first_ms, last_ms FROM rollup_day WHERE day >= ? AND day <= ?{pf}",
        (d0, d1) + pa).fetchall()


def _hours(conn, a: datetime, b: datetime, project: Optional[str]) -> list:
    lo, hi = (a.date().isoformat(), a.hour), (b.date().isoformat(), b.hour)
    return [r for r in _rollup_rows(conn, lo[0], hi[0], project, True) if lo <= (r[0], r[1]) < hi]


def _span(conn, t0: float, t1: float, project: Optional[str]) -> list:
    """Rows covering exactly [t0, t1): part-hours from calls, whole hours and whole days from the rollups."""
    a, b = datetime.fromtimestamp(t0), datetime.fromtimestamp(t1)
    h0 = a.replace(minute=0, second=0, microsecond=0)
    h0 = h0 if h0 == a else h0 + timedelta(hours=1)
    h1 = b.replace(minute=0, second=0, microsecond=0)
    if h0 >= h1:
        return _call_rows(conn, int(t0 * 1000), int(t1 * 1000), project)
    rows = (_call_rows(conn, int(t0 * 1000), int(h0.timestamp() * 1000), project)
            + _call_rows(conn, int(h1.timestamp() * 1000), int(t1 * 1000), project))
    d0 = h0.replace(hour=0)
    d0 = d0 if d0 == h0 else d0 + timedelta(days=1)
    d1 = h1.replace(hour=0)
    if d0 >= d1:
        return rows + _hours(conn, h0, h1, project)
    return (rows + _hours(conn, h0, d0, project) + _hours(conn, d1, h1, project)
            + _rollup_rows(conn, d0.date().isoformat(), (d1.date() - timedelta(days=1)).isoformat(), project, False))


def _db(conn) -> Optional[str]:
    # None for an in-memory store: those are not cached, since a closed one's id(conn) can come back.
    return conn.execute("PRAGMA database_list").fetchone()[2] or None


def _gen(conn) -> Optional[tuple]:
    """Changes whenever the rollups do: the store, and how many times its rollups were rebuilt."""
    db = _db(conn)
    return (db, store.get_meta(conn, "rollup_gen", 0)) if db else None


_SESS: dict = {}


def _session_facts(conn) -> tuple:
    """Distinct subagents per session over the whole session, and the sessions with human turns."""
    key = _gen(conn)
    if key:  # a prompt can land without a call, so new turns count too
        key += (conn.execute("SELECT MAX(rowid) FROM turns").fetchone()[0],)
    hit = _SESS.get(key) if key else None  # .get: the other thread may clear the dict between a test and a read
    if hit is None:
        hit = (dict(conn.execute("SELECT session_id, COUNT(DISTINCT agent_id) FROM rollup "
                                 "WHERE agent_id IS NOT NULL GROUP BY session_id")),
               {sid for (sid,) in conn.execute("SELECT DISTINCT session_id FROM turns")})
        if key:
            _SESS.clear()
            _SESS[key] = hit
    return hit


def _turn_rows(conn, t0_ms: int, t1_ms: int, project: Optional[str], hourly: bool) -> list:
    """(day, hour or None, session, project, model, agent ms, turns)."""
    pf, pa = _pfilter(project)
    return conn.execute(
        f"SELECT day, {'hour' if hourly else 'NULL'}, session_id, {_PROJ}, model, SUM(duration_ms), COUNT(*) "
        f"FROM turns WHERE ts_ms >= ? AND ts_ms < ?{pf} GROUP BY 1, 2, 3, 4, 5", (t0_ms, t1_ms) + pa).fetchall()


def _legacy(conn, cov: Optional[str]) -> dict:
    """Usable legacy rows before coverage_from: day -> (cost, tokens, messages)."""
    if not cov:
        return {}
    return {d: (c, t, m) for d, c, t, m in conn.execute(
        "SELECT day, cost, tokens, messages FROM legacy_days WHERE quality = 'legacy' AND cost > 0 AND day < ?",
        (cov,))}


_ALLTIME: dict = {}


def _alltime(conn) -> tuple:
    """All-time cost per project and per canonical model."""
    key = _gen(conn)
    hit = _ALLTIME.get(key) if key else None
    if hit is not None:
        return hit
    pc: dict = {}
    mc: dict = {}
    for project, model, speed, long, *s in conn.execute(
            f"SELECT project, model, speed, long, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), "
            f"SUM(cache_write_1h), SUM(web_search) FROM rollup_day GROUP BY 1, 2, 3, 4"):
        c = sum(_price(model, speed, long, *s)[:4])
        pc[project] = pc.get(project, 0.0) + c
        cid = _rate(model, speed, long)[0]
        mc[cid] = mc.get(cid, 0.0) + c
    out = (pc, mc)
    if key:
        _ALLTIME.clear()
        _ALLTIME[key] = out
    return out


def _top(costs: dict) -> list:
    """The TOP entities by cost: the keys a timeline draws."""
    return sorted((k for k, c in costs.items() if k and c > 0), key=lambda k: (-costs[k], k))[:TOP]


def _tops(conn, now: float, alltime: dict) -> dict:
    """Every range's unfiltered timeline keys per group: the sets whose members must not share a colour."""
    out: dict = {"project": [], "model": []}
    for n_days, kind, _label in RANGES.values():
        costs = alltime  # shortcut: ignores legacy days replacing ledger days in "all"; build() still checks its chart
        if n_days is not None:
            s0 = _buckets(kind, n_days, now, datetime.fromtimestamp(now).date())[0][0]
            d0 = s0.date().isoformat()
            table, where, args = (("rollup", "day >= ? AND (day > ? OR hour >= ?)", (d0, d0, s0.hour))
                                  if kind in ("hour", "6h") else ("rollup_day", "day >= ?", (d0,)))
            costs = {"project": {}, "model": {}}
            for proj, model, speed, long, *t in conn.execute(
                    f"SELECT project, model, speed, long, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), "
                    f"SUM(cache_write_1h), SUM(web_search) FROM {table} WHERE {where} GROUP BY 1, 2, 3, 4", args):
                c = sum(_price(model, speed, long, *t)[:4])
                for g, k in (("project", proj), ("model", _rate(model, speed, long)[0])):
                    costs[g][k] = costs[g].get(k, 0.0) + c
        for g in out:
            out[g].append(set(_top(costs[g])))
    return out


def _clash(slots: dict, sets: dict) -> bool:
    """A set with a member that has no slot, or two members on one slot."""
    return any(len({slots[g].get(k) for k in s} - {None}) < len(s) for g in sets for s in sets[g])


_CHECKED: dict = {}


def _slots(conn, now: float, charts: dict, alltime: dict) -> dict:
    """Each project's and model's palette slot, kept in meta 'colors' so a colour survives range and rank changes.

    An entity gets a slot when a range first draws it: the lowest free one that nothing it can be drawn with
    holds, else one held only by entities no range draws now (a long-idle project lends its colour). A slot
    moves only when two of its holders come to be drawn in one range; the one with less all-time cost moves.
    While no slot is shared this costs one meta read; then every range is checked once per rollup and hour.
    """
    n = len(tokens.SERIES_LIGHT)
    saved = store.get_meta(conn, "colors") or {}
    out = {g: {k: v for k, v in (saved.get(g) or {}).items() if isinstance(v, int) and 0 <= v < n} for g in charts}
    sets = {g: [set(keys)] for g, keys in charts.items()}
    key = (_gen(conn), int(now // 3600))
    if not _clash(out, sets) and (all(len(set(m.values())) == len(m) for m in out.values())
                                  or key[0] and _CHECKED.get("key") == key):
        return out
    for g, more in _tops(conn, now, alltime).items():
        sets[g] += more
    if _clash(out, sets):
        for g in charts:
            _assign(out[g], sets[g], sets[g][0], alltime[g], n)
        try:
            store.set_meta(conn, "colors", out)
        except sqlite3.Error:
            return out  # a locked or read-only store: this build still has its colours, the next one saves them
    if key[0]:
        _CHECKED["key"] = key
    return out


def _assign(held: dict, sets: list, chart: set, cost: dict, n: int) -> None:
    """Give every member of *sets* a slot in 0..n-1 that no member of a set it is in uses (updates *held*)."""
    union = sorted(set().union(*sets), key=lambda k: (-cost.get(k, 0.0), k))  # the costlier keeps its slot
    placed: dict = {}
    for k in union:
        mates = set().union(*(s for s in sets if k in s)) - {k}
        must = chart - {k} if k in chart else set()  # a chart never draws one colour twice
        holders: dict = {}
        for j, v in held.items():
            if j != k:
                holders.setdefault(v, set()).add(j)

        def score(i, mates=mates, must=must, holders=holders):
            h = holders.get(i, set())
            return (sum(placed.get(j) == i for j in must), sum(placed.get(j) == i for j in mates),
                    bool(h & (mates - placed.keys())), bool(h.intersection(union)), bool(h), i)

        if k not in held or score(held[k])[:2] != (0, 0):
            held[k] = min(range(n), key=score)
        placed[k] = held[k]


# ---------------------------------------------------------------- range windows

def _buckets(kind: str, n_days: Optional[int], now: float, first_day: date) -> tuple:
    """Bucket starts (naive local datetimes) and a mapper from (day, hour) to bucket index."""
    nd = datetime.fromtimestamp(now)
    if kind == "hour":
        # shortcut: on DST fall-back the repeated local hour is one (day, hour) rollup bucket, so for about a day
        # the 24h view spans 25 real hours; read that hour from calls by ts_ms if it ever matters.
        b = nd.replace(minute=0, second=0, microsecond=0)
        starts = [b - timedelta(hours=23 - i) for i in range(24)]
        idx = {(s.date().isoformat(), s.hour): i for i, s in enumerate(starts)}
        return starts, lambda day, hour: idx.get((day, hour))
    if kind == "6h":
        b = nd.replace(hour=nd.hour - nd.hour % 6, minute=0, second=0, microsecond=0)
        starts = [b - timedelta(hours=6 * (27 - i)) for i in range(28)]
        idx = {(s.date().isoformat(), s.hour): i for i, s in enumerate(starts)}
        return starts, lambda day, hour: idx.get((day, hour - hour % 6))
    today = nd.date()
    if kind == "day":
        starts = [datetime.combine(today - timedelta(days=n_days - 1 - i), time()) for i in range(n_days)]
        idx = {s.date().isoformat(): i for i, s in enumerate(starts)}
        return starts, lambda day, hour: idx.get(day)
    first = min(first_day, today)
    w0 = first - timedelta(days=first.weekday())
    w1 = today - timedelta(days=today.weekday())
    n = max(2, (w1 - w0).days // 7 + 1)
    starts = [datetime.combine(w1 - timedelta(weeks=n - 1 - i), time()) for i in range(n)]
    d0 = starts[0].date()

    def week(day, hour):
        i = (date.fromisoformat(day) - d0).days // 7
        return i if 0 <= i < n else None

    return starts, week


# ---------------------------------------------------------------- limits and the pace engine

def _samples(conn, t0: float = 0.0) -> list:
    return conn.execute("SELECT ts, weekly, session FROM samples WHERE ts >= ? ORDER BY ts", (t0,)).fetchall()


def _at(w: dict, t: float) -> Optional[float]:
    """Utilization in past window w at time t: the last sample at or before t, None outside the window."""
    if t >= w["end"]:
        return None
    i = bisect.bisect_right(w["ts"], t) - 1
    return w["pts"][i][1] if i >= 0 else None


_PAST: dict = {}


def _past_windows(conn, reset: Optional[float], now: float) -> tuple:
    """Weekly windows before the current one, and the last time a 5-hour value was reported.

    Windows are split where utilization drops (a reset), because resets have
    moved and come early. A reset seen across a sampling gap is placed on the
    current reset schedule when that falls inside the gap. With no reset time
    the samples run up to now, so a last window that began within a week is
    the current one and is left out.
    """
    cur_start = reset - WEEK_S if reset else now // 3600 * 3600  # hourly steps keep the cache warm
    last = conn.execute("SELECT MAX(ts) FROM samples WHERE ts < ?", (cur_start,)).fetchone()[0]
    db = _db(conn)
    key = (db, bool(reset), round(cur_start), last)
    hit = _PAST.get(key) if db else None
    if hit is not None:
        return hit
    wins: list = []
    last5 = None
    prev = None
    for t, u, s5 in conn.execute("SELECT ts, weekly, session FROM samples WHERE ts < ? ORDER BY ts", (cur_start,)):
        if s5:
            last5 = t
        if u is None:
            continue
        if prev is None or prev[1] - u > 0.04:
            start = t
            if prev is not None and reset:
                sched = cur_start - math.ceil((cur_start - t) / WEEK_S) * WEEK_S
                start = sched if sched > prev[0] else t
            if wins:
                wins[-1]["end"] = start
            wins.append({"start": start, "end": cur_start, "pts": [], "ts": [], "peak": 0.0, "hit": None})
        w = wins[-1]
        w["pts"].append((t, u))
        w["ts"].append(t)
        w["peak"] = max(w["peak"], u)
        if u >= HIT and w["hit"] is None:
            w["hit"] = t
        prev = (t, u)
    if not reset and wins and wins[-1]["start"] > cur_start - WEEK_S:
        wins.pop()
    out = []
    for w in wins:
        w["seen"] = w["ts"][-1] - w["ts"][0]
        if w["seen"] >= 4 * 86400 or w["hit"] is not None:
            out.append(w)
    if db:
        _PAST.clear()
        _PAST[key] = (out, last5)
    return out, last5


def _limits(conn, limits: Optional[dict], now: float, thresholds: tuple) -> tuple:
    """The LIMITS section and the weekly/5-hour projections now_state reuses."""
    lim = limits or {}
    wk = lim.get("weekly") or None
    reset = (wk or {}).get("resets_at")
    start = lim.get("window_started_at") or (reset - WEEK_S if reset else None)
    past, last5 = _past_windows(conn, reset, now)
    weekly = None
    proj = {"weekly": None, "session": None}
    cur_pts: list = []
    if wk and wk.get("pct") is not None and reset and start:
        u = float(wk["pct"])
        cur = _samples(conn, start)
        cur_pts = [(t, w) for t, w, _s in cur if w is not None]
        last5 = max([last5 or 0.0] + [t for t, _w, s5 in cur if s5]) or None
        span = max(reset - start, 1.0)
        elapsed = min(max((now - start) / span, 0.0), 1.0)
        hours = max((now - start) / 3600, 1e-6)
        avg_rate = u / hours
        recent = [p for p in cur_pts if p[0] >= now - 86400]
        rate24 = None
        if len(recent) >= 2 and recent[-1][0] - recent[0][0] >= 2 * 3600:
            rate24 = max((recent[-1][1] - recent[0][1]) / ((recent[-1][0] - recent[0][0]) / 3600), 0.0)
        rate = max(avg_rate, rate24 or 0.0)
        eta = None
        if u >= HIT:
            eta = next((t for t, w in cur_pts if w >= HIT), now)
        elif rate > 0:
            eta = now + (1 - u) / rate * 3600
        hits = eta is not None and eta < reset
        series: dict = {}
        for t, w in cur_pts:
            h = int(t // 3600) * 3600
            series[h] = max(series.get(h, 0.0), w)
        usable = [w for w in past if w["seen"] >= 5 * 86400]
        usual_series = []
        if len(usable) >= 2:
            for i in range(USUAL_STEPS + 1):
                vals = [v for v in (_at(w, w["start"] + i / USUAL_STEPS * WEEK_S) for w in usable)
                        if v is not None]
                if len(vals) >= 2:
                    usual_series.append({"elapsed": round(i / USUAL_STEPS, 4), "pct": round(statistics.median(vals), 4)})
        usual = None
        if len(usable) >= 2:
            vals = [v for v in (_at(w, w["start"] + elapsed * WEEK_S) for w in usable) if v is not None]
            usual = round(statistics.median(vals), 4) if len(vals) >= 2 else None
        days_left = (reset - now) / 86400
        weekly = {
            "pct": u,
            "resets_at": _iso(reset),
            "window_start": _iso(start),
            "level": tokens.level(u, *thresholds),
            "elapsed": round(elapsed, 4),
            "pace": round(u / elapsed, 3) if elapsed > 0.01 else None,
            "usual": usual,
            "eta": _iso(eta) if hits else None,
            "hits_before_reset": hits,
            "locked_hours": round((reset - eta) / 3600, 1) if hits else None,
            "daily_budget": round(max(1 - u, 0.0) / days_left, 4) if days_left > 0 else None,
            "hourly_rate": round(rate, 5),
            "series": [{"t": _iso(h), "pct": round(v, 4)} for h, v in sorted(series.items())],
            "usual_series": usual_series,
        }
        at_reset = min(u + rate * max(reset - now, 0) / 3600, 9.99)
        proj["weekly"] = {"pct": u, "reset": reset, "eta": eta if hits else None, "at_reset": at_reset}
    elif wk and wk.get("pct") is not None:
        # No reset time (a header fallback, or a null right after a reset): show the meter, skip the pace.
        u = float(wk["pct"])
        weekly = {"pct": u, "resets_at": None, "window_start": _iso(start), "level": tokens.level(u, *thresholds),
                  "elapsed": 0.0, "pace": None, "usual": None, "eta": None, "hits_before_reset": False,
                  "locked_hours": None, "daily_budget": None, "hourly_rate": None, "series": [], "usual_series": []}
        proj["weekly"] = {"pct": u, "reset": None, "eta": None, "at_reset": None}

    five = lim.get("five_hour") or None
    session = None
    if five and five.get("pct") is not None:
        p, r5 = float(five["pct"]), five.get("resets_at")
        session = {"pct": p, "resets_at": _iso(r5), "level": tokens.level(p, *thresholds)}
        eta5, at_reset5 = None, p
        if r5:
            hours = max((now - (r5 - 5 * 3600)) / 3600, 1e-6)
            rate5 = p / hours
            at_reset5 = p + rate5 * max(r5 - now, 0) / 3600
            if p < HIT and rate5 > 0 and now + (1 - p) / rate5 * 3600 < r5:
                eta5 = now + (1 - p) / rate5 * 3600
            elif p >= HIT:
                eta5 = now
        proj["session"] = {"pct": p, "reset": r5, "eta": eta5, "at_reset": at_reset5}

    note = None
    if session is None:
        note = (ULTRA_NOTE if lim.get("plan") == "Ultra" else
                f"Your plan has not reported a 5-hour limit since {_md(last5)}." if last5
                else "Your plan does not report a 5-hour limit.")

    scoped = []
    for x in lim.get("limits") or []:
        if x.get("kind") == "weekly_all" or x.get("pct") is None:
            continue
        scoped.append({"name": x.get("name") or str(x.get("kind") or "Limit").replace("_", " ").capitalize(),
                       "pct": x["pct"], "active": bool(x.get("active")), "allowance": x.get("allowance"),
                       "resets_at": _iso(x.get("resets_at"))})
    surfaces = [{"name": s["name"], "pct": s["pct"], "pct_of_limit": s["pct_of_limit"]}
                for s in lim.get("surfaces") or [] if s.get("pct") is not None and s.get("pct_of_limit") is not None]
    ex = lim.get("extra_usage")
    extra = None
    if ex and ex.get("used") is not None:
        extra = {"enabled": bool(ex.get("enabled")), "monthly_limit": ex.get("monthly_limit"), "used": ex["used"],
                 "currency": ex.get("currency") or "", "reason": ex.get("reason")}

    weeks = []
    for w in past[-12:]:
        hours_at = (w["end"] - w["hit"]) / 3600 if w["hit"] else 0.0
        weeks.append({"start": _iso(w["start"]), "peak": round(min(w["peak"], 1.0), 4),
                      "hours_at_limit": round(hours_at, 1), "current": False})
    counted = len(weeks)
    hit = sum(1 for w in weeks if w["peak"] >= HIT)
    if weekly is not None and weekly["window_start"]:
        cur_hit = next((t for t, w in cur_pts if w >= HIT), None)
        weeks.append({"start": weekly["window_start"], "peak": round(max([w for _t, w in cur_pts] + [weekly["pct"]]), 4),
                      "hours_at_limit": round((now - cur_hit) / 3600, 1) if cur_hit else 0.0, "current": True})
    source = lim.get("source") if lim.get("source") in ("oauth", "headers") else None
    section = {"weekly": weekly, "session": session, "session_note": note, "scoped": scoped, "surfaces": surfaces,
               "extra_usage": extra, "past_weeks": weeks, "weeks_hit": hit, "weeks_counted": counted, "source": source}
    return section, proj


def _forecast(p: Optional[dict], now: float) -> tuple:
    if not p or not p["reset"]:
        return None, None
    if p["eta"] is not None:
        if p["pct"] >= HIT:
            return f"Limited until {_clock(p['reset'], now)}", _iso(p["eta"])
        return f"At this pace the limit is reached {_clock(p['eta'], now)}", _iso(p["eta"])
    return f"About {round(min(p['at_reset'], 1.0) * 100)}% at reset", None


def _state(limits: Optional[dict], now: float) -> tuple:
    lim = limits or {}
    err = lim.get("error")
    if err and ("credentials" in err.lower() or "log in" in err.lower() or "re-authenticate" in err.lower()):
        return "signed_out", err
    has_data = bool(lim.get("weekly") or lim.get("five_hour"))
    if err:
        return ("stale" if has_data else "error"), err
    if not limits:
        return "loading", None
    fetched = lim.get("fetched_at")
    if fetched and now - fetched > STALE_S:
        return "stale", None
    return "ok", None


# ---------------------------------------------------------------- now

def now_state(conn: sqlite3.Connection, limits: Optional[dict], now: Optional[float] = None,
              thresholds: Optional[tuple] = None) -> dict:
    """The range-independent state the OSD shows. ``limits`` is the latest good
    fetch_rate_limits() result, with an ``error`` key added when the last fetch failed."""
    now = _time.time() if now is None else now
    thresholds = thresholds or (tokens.CAUTION, tokens.CRITICAL)
    _section, proj = _limits(conn, limits, now, thresholds)
    return _now(conn, limits, now, thresholds, proj)


def _now(conn, limits, now, thresholds, proj) -> dict:
    lim = limits or {}

    def meter(p):
        if not p:
            return None
        forecast, eta = _forecast(p, now)
        return {"pct": p["pct"], "resets_at": _iso(p["reset"]), "level": tokens.level(p["pct"], *thresholds),
                "forecast": forecast, "eta": eta}

    weekly, session = meter(proj["weekly"]), meter(proj["session"])
    primary = "weekly"
    pw, ps = proj["weekly"], proj["session"]
    if ps and (ps["eta"] is not None and (not pw or pw["eta"] is None or ps["eta"] <= pw["eta"])
               or (not pw or pw["eta"] is None) and ps["pct"] > (pw or {}).get("pct", -1)):
        primary = "session"
    today = datetime.fromtimestamp(now).date().isoformat()
    cost = 0.0
    tok = 0
    for model, speed, long, n, i, o, r, w5, w1, ws in conn.execute(
            f"SELECT model, speed, long, SUM(calls), SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), "
            f"SUM(cache_write_1h), SUM(web_search) FROM rollup_day WHERE day = ? GROUP BY 1, 2, 3", (today,)):
        cost += sum(_price(model, speed, long, i, o, r, w5, w1, ws)[:4])
        tok += i + o + r + w5 + w1
    now_ms = int(now * 1000)
    out5, sessions, last = conn.execute(
        "SELECT SUM(output), COUNT(DISTINCT session_id), MAX(ts_ms) FROM calls WHERE ts_ms >= ?",
        (now_ms - ACTIVE_MS,)).fetchone()
    agents = conn.execute("SELECT COUNT(DISTINCT agent_id) FROM calls WHERE ts_ms >= ? AND is_subagent = 1",
                          (now_ms - LIVE_MS,)).fetchone()[0]
    state, message = _state(limits, now)
    return {
        "primary": primary,
        "weekly": weekly,
        "session": session,
        "today_cost": round(cost, 4),
        "today_tokens": int(tok),
        "tokens_per_min": round((out5 or 0) / (ACTIVE_MS / 60000), 1),
        "active_sessions": int(sessions or 0),
        "active_subagents": int(agents or 0),
        "live": bool(last and last >= now_ms - LIVE_MS),
        "state": state,
        "as_of": _iso(lim.get("fetched_at")),
        "message": message,
        # the worker retries a failed fetch by itself; a store it can't use ("store") needs the user
        "error_kind": (lim.get("error_kind") or "fetch") if lim.get("error") else None,
    }


# ---------------------------------------------------------------- build

class _Tot:
    __slots__ = ("cost", "tok", "calls", "cr", "prompt", "agent", "turns", "sessions", "sub")

    def __init__(self):
        self.cost = 0.0
        self.tok = self.calls = self.cr = self.prompt = self.agent = self.turns = 0
        self.sub = 0.0
        self.sessions: set = set()

    def add(self, sid, n, i, o, r, w5, w1, cost, sub):
        self.cost += cost
        self.tok += i + o + r + w5 + w1
        self.calls += n
        self.cr += r
        self.prompt += i + r + w5 + w1
        self.sessions.add(sid)
        if sub:
            self.sub += cost

    def hit(self):
        return self.cr / self.prompt if self.prompt else None


def build(conn: sqlite3.Connection, range_key: str = "7d", project: Optional[str] = None,
          now: Optional[float] = None, limits: Optional[dict] = None, *, indexing: Optional[dict] = None,
          updated_at: Optional[float] = None, thresholds: Optional[tuple] = None) -> dict:
    """The whole app view. ``project`` filters everything except ``limits``, ``now``, ``projects`` and
    ``sessions_total_all``.

    Takes 50-300 ms on a large store: call it off the GUI thread, on a connection of that thread.
    Its one write is the colour slots in meta when an entity is first drawn (see :func:`_slots`).
    """
    now = _time.time() if now is None else now
    thresholds = thresholds or (tokens.CAUTION, tokens.CRITICAL)
    n_days, kind, label = RANGES[range_key]
    now_ms = int(now * 1000)
    today = datetime.fromtimestamp(now).date()
    cov = store.get_meta(conn, "coverage_from")
    cov_ts = _midnight(date.fromisoformat(cov)) if cov else None
    legacy_all = _legacy(conn, cov)
    legacy = legacy_all if project is None else {}
    first = conn.execute("SELECT MIN(day) FROM rollup_day").fetchone()[0]
    first_day = min(([date.fromisoformat(first)] if first else [today])
                    + ([date.fromisoformat(min(legacy))] if legacy else []))
    starts, bucket_of = _buckets(kind, n_days, now, first_day)
    nb = len(starts)
    t0 = starts[0].timestamp()
    t0_ms = int(t0 * 1000)
    hourly = kind in ("hour", "6h")
    # Every project's rows: the sidebar lists all of them whatever the filter; the rest uses the filtered ones.
    rows = _rollup_rows(conn, starts[0].date().isoformat(), today.isoformat(), None, hourly)
    turns = _turn_rows(conn, t0_ms, now_ms + 1, project, hourly)
    pc_all, mc_all = _alltime(conn)

    # Days before coverage_from fall back to the legacy totals when those are larger.
    priced = []
    day_cost: dict = {}
    for row in rows:
        parts = _price(row[5], row[6], row[7], *row[9:15])
        priced.append((row, parts))
        if cov and row[0] < cov:
            day_cost[row[0]] = day_cost.get(row[0], 0.0) + sum(parts[:4])
    # Legacy days have no per-project split: the unfiltered view skips their ledger rows, a project's view keeps them.
    skip = {d for d, v in legacy_all.items() if v[0] > day_cost.get(d, 0.0)} if range_key == "all" else set()
    legacy_used = {d: legacy_all[d] for d in skip} if project is None else {}

    total = _Tot()
    buckets = [_Tot() for _ in range(nb)]
    by = {"project": {}, "model": {}}
    ent_cost = {"project": {}, "model": {}}
    models: dict = {}
    sessions: dict = {}
    proj_range: dict = {}  # project -> [cost, sessions] in range, over every project
    all_sids: set = set()
    split = [[0.0, 0] for _ in range(4)]
    saved = 0.0
    day_tot: dict = {}

    def series(group, key):
        g = by[group].get(key)
        if g is None:
            g = by[group][key] = {m: [0.0] * nb for m in ("cost", "tokens", "messages", "agent_time")}
        return g

    for row, parts in priced:
        day, hour, sid, is_sub, proj, model, speed, long, n, i, o, r, w5, w1, ws, f_ms, l_ms = row
        b = bucket_of(day, hour)
        if b is None:
            continue
        cost = parts[0] + parts[1] + parts[2] + parts[3]
        if day not in skip:
            pr = proj_range.setdefault(proj, [0.0, set()])
            pr[0] += cost
            pr[1].add(sid)
            all_sids.add(sid)
        if day in legacy_used or project and proj != project:
            continue
        tk = i + o + r + w5 + w1
        cid, prc = _rate(model, speed, long)[:2]
        total.add(sid, n, i, o, r, w5, w1, cost, is_sub)
        buckets[b].add(sid, n, i, o, r, w5, w1, cost, is_sub)
        for grp, key in (("project", proj), ("model", cid)):
            s = series(grp, key)
            s["cost"][b] += cost
            s["tokens"][b] += tk
            s["messages"][b] += n
            ent_cost[grp][key] = ent_cost[grp].get(key, 0.0) + cost
        m = models.setdefault(cid, {"cost": 0.0, "output": 0, "calls": 0, "cr": 0, "prompt": 0, "priced": prc})
        m["cost"] += cost
        m["output"] += o
        m["calls"] += n
        m["cr"] += r
        m["prompt"] += i + r + w5 + w1
        for k, (c, t) in enumerate(((parts[0], i), (parts[1], o), (parts[2], r), (parts[3], w5 + w1))):
            split[k][0] += c
            split[k][1] += t
        saved += parts[4]
        s = sessions.setdefault(sid, {"cost": 0.0, "tok": 0, "cr": 0, "prompt": 0, "models": {}, "projects": {},
                                      "first": f_ms, "last": l_ms, "agent": 0, "turns": 0})
        s["cost"] += cost
        s["tok"] += tk
        s["cr"] += r
        s["prompt"] += i + r + w5 + w1
        s["models"][cid] = s["models"].get(cid, 0.0) + cost
        s["projects"][proj] = s["projects"].get(proj, 0.0) + cost
        s["first"], s["last"] = min(s["first"], f_ms), max(s["last"], l_ms)
        dk = (day, hour) if range_key == "24h" else (day, None)
        day_tot[dk] = day_tot.get(dk, 0.0) + cost

    for day, hour, sid, proj, model, dur, nt in turns:
        b = bucket_of(day, hour)
        if b is None:
            continue
        total.agent += dur
        total.turns += nt
        buckets[b].agent += dur
        buckets[b].turns += nt
        series("project", proj)["agent_time"][b] += dur
        series("model", _rate(model, "", 0)[0] if model else "")["agent_time"][b] += dur
        if sid in sessions:
            sessions[sid]["agent"] += dur
            sessions[sid]["turns"] += nt

    leg_series = None
    leg_cost = leg_msgs = 0
    if legacy_used:
        leg_series = {m: [0.0] * nb for m in ("cost", "tokens", "messages", "agent_time")}
        for d, (c, t, msg) in legacy_used.items():
            b = bucket_of(d, None)
            if b is None:
                continue
            for tot in (total, buckets[b]):
                tot.cost += c
                tot.tok += t
                tot.calls += msg
            leg_series["cost"][b] += c
            leg_series["tokens"][b] += t
            leg_series["messages"][b] += msg
            leg_cost += c
            leg_msgs += msg

    # ---- timeline groups: top five by cost in range, each in its persistent colour slot
    tops = {g: _top(ent_cost[g]) for g in ent_cost}
    slots = _slots(conn, now, tops, {"project": pc_all, "model": mc_all})
    # Lists colour only what the chart draws; the rest is in the Other band.
    colour = {g: {k: slots[g][k] for k in tops[g]} for g in tops}

    def group(grp, name):
        keys = tops[grp]
        out = {"keys": [name(k) for k in keys], "colors": [colour[grp][k] for k in keys],
               "values": {m: [[round(v, 4) for v in by[grp][k][m]] for k in keys] for m in by[grp].get(keys[0], {})}
               if keys else {m: [] for m in ("cost", "tokens", "messages", "agent_time")}}
        rest = [k for k in by[grp] if k not in keys]
        other = {m: [sum(by[grp][k][m][b] for k in rest) for b in range(nb)]
                 for m in ("cost", "tokens", "messages", "agent_time")}
        if any(any(v) for v in other.values()):
            out["keys"].append("Other")
            out["colors"].append(OTHER)
            for m in other:
                out["values"].setdefault(m, []).append([round(v, 4) for v in other[m]])
        if leg_series:
            out["keys"].append("Legacy")
            out["colors"].append(LEGACY)
            for m in leg_series:
                out["values"].setdefault(m, []).append([round(v, 4) for v in leg_series[m]])
        for m in ("cost", "tokens", "messages", "agent_time"):
            out["values"].setdefault(m, [])
        return out

    timeline = {
        "bucket": kind,
        "starts": [_iso(s.timestamp()) for s in starts],
        "partial_last": True,
        "totals": {"cost": round(total.cost, 4), "tokens": total.tok, "messages": total.calls,
                   "agent_time": total.agent},
        "by": {"project": group("project", str), "model": group("model", model_name)},
    }

    # ---- previous equal-length window, ending where this one starts
    prev = None
    p0 = t0 - (now - t0)
    if range_key != "all" and cov_ts is not None and p0 >= cov_ts:
        p0_ms = int(p0 * 1000)
        prev = _Tot()
        for row in _span(conn, p0, t0, project):
            parts = _price(row[5], row[6], row[7], *row[9:15])
            prev.add(row[2], *row[8:14], sum(parts[:4]), row[3])
        pf, pa = _pfilter(project)
        prev.agent = conn.execute(f"SELECT coalesce(SUM(duration_ms), 0) FROM turns WHERE ts_ms >= ? AND ts_ms < ?{pf}",
                                  (p0_ms, t0_ms) + pa).fetchone()[0]

    note = ("all time" if range_key == "all" else
            f"vs prior {'24 hours' if range_key == '24h' else str(n_days) + ' days'}" if prev else
            "no complete prior period")

    def kpi(key, label, fmt, value, pval, unit, ser, hint=None):
        delta = None
        if pval is not None and value is not None:
            if unit == "pts":
                delta = round(value - pval, 4)
            elif pval:
                delta = round((value - pval) / pval, 4)
        return {"key": key, "label": label, "format": fmt, "value": value,
                "prev": pval, "delta": delta, "delta_unit": unit, "series": ser, "note": note, "hint": hint}

    hit_now = total.hit()
    kpis = [
        kpi("cost", "Cost", "usd", round(total.cost, 2), round(prev.cost, 2) if prev else None, "pct",
            [round(b.cost, 2) for b in buckets],
            f"{round(total.sub / total.cost * 100)}% from subagents" if total.cost and total.sub else None),
        kpi("tokens", "Tokens", "tokens", total.tok, prev.tok if prev else None, "pct", [b.tok for b in buckets],
            f"{round(total.cr / total.tok * 100)}% cache reads" if total.tok else None),
        kpi("sessions", "Sessions", "int", len(total.sessions), len(prev.sessions) if prev else None, "pct",
            [len(b.sessions) for b in buckets]),
        kpi("agent_time", "Agent time", "duration", int(total.agent), int(prev.agent) if prev else None, "pct",
            [int(b.agent) for b in buckets], "Parallel sessions add up" if total.agent else None),
        kpi("cache_hit", "Cache hit rate", "pct", round(hit_now, 4) if hit_now is not None else None,
            round(prev.hit(), 4) if prev and prev.hit() is not None else None, "pts",
            [round(b.hit() or 0.0, 4) for b in buckets]),
    ]

    # ---- sidebar projects: every project ever seen, active ones first, the same whatever the filter;
    # coloured as the unfiltered chart of this range draws them
    pcol = colour["project"]
    if project is not None:
        ptop = _top({k: v[0] for k, v in proj_range.items()})
        pslots = _slots(conn, now, {"project": ptop, "model": tops["model"]}, {"project": pc_all, "model": mc_all})
        pcol = {k: pslots["project"][k] for k in ptop}
    projects = []
    for name in sorted(pc_all, key=lambda k: (k not in proj_range, -(proj_range.get(k, [0.0])[0]), -pc_all[k], k)):
        pr = proj_range.get(name)
        projects.append({"name": name, "sessions": len(pr[1]) if pr else 0, "cost": round(pr[0], 2) if pr else 0.0,
                         "color": pcol.get(name, OTHER), "idle": pr is None})

    # ---- sessions, ranked by cost over the whole range before the cap
    sids = sorted(sessions, key=lambda k: (-sessions[k]["cost"], -sessions[k]["last"]))
    meta = {}
    for i in range(0, len(sids), 500):
        chunk = sids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for sid, title, entry in conn.execute(
                f"SELECT session_id, title, entrypoint FROM sessions WHERE session_id IN ({marks})", chunk):
            meta[sid] = (title, entry)
        # A session can start before the range: 'started' is its first call ever.
        for sid, f_ms in conn.execute(
                f"SELECT session_id, MIN(first_ms) FROM rollup_day WHERE session_id IN ({marks}) GROUP BY 1", chunk):
            sessions[sid]["first"] = min(sessions[sid]["first"], f_ms)
    agents, has_turns = _session_facts(conn)
    sess_out, first_of = [], {}
    for sid in sids:
        s = sessions[sid]
        first_of[sid or ""] = s["first"]
        shown = len(sess_out) < max(SESSION_CAP, TOP_SESSIONS)  # rows past both caps only feed the facts
        title, entry = meta.get(sid, (None, None))
        proj = max(s["projects"], key=s["projects"].get)
        sess_out.append({
            "id": sid or "",
            "title": title,
            "project": proj,
            "color": colour["project"].get(proj, OTHER),
            "model": model_name(max(s["models"], key=s["models"].get)),
            "entrypoint": entry or "unknown",
            "started": _iso(s["first"] / 1000) if shown else "",
            "ended": _iso(s["last"] / 1000) if shown else "",
            "agent_ms": int(s["agent"]),
            "turns": int(s["turns"]),
            "tokens": int(s["tok"]),
            "cost": round(s["cost"], 4),
            "cache_hit": round(s["cr"] / s["prompt"], 4) if s["prompt"] else None,
            "subagents": agents.get(sid, 0),
            "interactive": bool(entry) and not entry.startswith("sdk") and sid in has_turns,
        })

    # ---- models and cost split
    # Legacy days have no per-model split; one 'legacy' entry keeps the models adding up to the Cost KPI.
    if leg_cost:
        models["legacy"] = {"cost": leg_cost, "output": 0, "calls": leg_msgs, "cr": 0, "prompt": 0, "priced": "family"}
    all_cost = sum(x["cost"] for x in models.values())
    model_out = [{"name": "Old daily totals" if cid == "legacy" else model_name(cid), "id": cid, "cost": round(m["cost"], 4),
                  "share": round(m["cost"] / all_cost, 4) if all_cost else 0.0,
                  "output": int(m["output"]), "cache_hit": round(m["cr"] / m["prompt"], 4) if m["prompt"] else None,
                  "calls": int(m["calls"]), "color": LEGACY if cid == "legacy" else colour["model"].get(cid, OTHER),
                  "priced": m["priced"]}
                 for cid, m in sorted(models.items(), key=lambda kv: -kv[1]["cost"])]
    cost_split = {k: {"cost": round(split[i][0], 4), "tokens": int(split[i][1])}
                  for i, k in enumerate(("input", "output", "cache_read", "cache_write"))}
    cost_split["caching_saved"] = round(saved, 2)

    section, proj_now = _limits(conn, limits, now, thresholds)
    activity = _activity(conn, project, now, cov, legacy, sess_out, first_of, day_tot, range_key, section, t0)
    state, message = _state(limits, now)
    if state == "ok" and indexing and indexing.get("done", 0) < indexing.get("total", 0) and not total.calls:
        state = "loading"
    return {
        "schema": SCHEMA,
        "generated_at": _iso(now),
        "range": {"key": range_key, "days": n_days, "from": _iso(t0), "to": _iso(now), "label": label},
        "project": project,
        "status": {
            "state": state,
            "message": message,
            "updated_at": _iso(updated_at if updated_at is not None else now),
            "limits_as_of": _iso((limits or {}).get("fetched_at")),
            "indexing": indexing,
            "plan": (limits or {}).get("plan"),
            "transcripts": conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            "coverage_from": cov,
            "legacy_before": cov if legacy else None,
        },
        "now": _now(conn, limits, now, thresholds, proj_now),
        "limits": section,
        "kpis": kpis,
        "timeline": timeline,
        "projects": projects,
        "sessions": sess_out[:SESSION_CAP],
        "top_sessions": sess_out[:TOP_SESSIONS],
        "sessions_total": len(sess_out),
        "sessions_total_all": len(all_sids),
        "activity": activity,
        "models": model_out,
        "cost_split": cost_split,
    }


def cost_note() -> str:
    """One line for the UI's "How cost is computed" popover."""
    return COST_NOTE.format(as_of=pricing.PRICES_AS_OF)


# ---------------------------------------------------------------- activity

def _activity(conn, project, now, cov, legacy, sessions, first_of, day_tot, range_key, section, t0) -> dict:
    today = datetime.fromtimestamp(now).date()
    days = [today - timedelta(days=ACTIVITY_DAYS - 1 - i) for i in range(ACTIVITY_DAYS)]
    d0 = days[0].isoformat()
    pf, pa = _pfilter(project)
    cost: dict = {}
    for day, model, speed, long, *s in conn.execute(
            f"SELECT day, model, speed, long, SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), "
            f"SUM(cache_write_1h), SUM(web_search) FROM rollup_day WHERE day >= ?{pf} GROUP BY 1, 2, 3, 4",
            (d0,) + pa):
        cost[day] = cost.get(day, 0.0) + sum(_price(model, speed, long, *s)[:4])
    for d, (c, _t, _m) in legacy.items():
        if d >= d0 and c > cost.get(d, 0.0):
            cost[d] = c
    agent = dict(conn.execute(f"SELECT day, SUM(duration_ms) FROM turns WHERE day >= ?{pf} GROUP BY day",
                              (d0,) + pa).fetchall())
    vals = sorted(v for v in agent.values() if v > 0)
    p95 = vals[min(len(vals) - 1, int(len(vals) * 0.95))] if vals else 0
    out_days = []
    for d in days:
        k = d.isoformat()
        a, c = int(agent.get(k, 0)), cost.get(k, 0.0)
        level = 0
        if a > 0 and p95:
            level = 1 + min(3, int(a / p95 * 4 - 1e-9))
        elif c > 0:
            level = 1
        out_days.append({"date": k, "agent_ms": a, "cost": round(c, 2), "level": max(level, 1 if c > 0 else 0),
                         "partial": (cov is None or k < cov) or d == today})
    active = [x["cost"] > 0 for x in out_days]
    longest = run = 0
    for on in active:
        run = run + 1 if on else 0
        longest = max(longest, run)
    streak = 0
    i = len(active) - 1 if active[-1] else len(active) - 2
    while i >= 0 and active[i]:
        streak += 1
        i -= 1

    facts = []
    if day_tot:
        (bd, bh), bc = max(day_tot.items(), key=lambda kv: kv[1])
        if range_key == "24h":
            bt = datetime.combine(date.fromisoformat(bd), time(hour=bh)).timestamp()
            facts.append({"label": "Busiest hour", "value": _money(bc), "detail": _clock(bt, now)})
        else:
            a = agent.get(bd, 0)
            facts.append({"label": "Busiest day", "value": _money(bc),
                          "detail": _md(_midnight(date.fromisoformat(bd))) + (f", {_dur(a)} agent time" if a else "")})
    if sessions:
        longest_s = max(sessions, key=lambda s: s["agent_ms"])
        if longest_s["agent_ms"]:
            first = first_of[longest_s["id"]] / 1000  # its agent time counts only in range, so say it began before
            facts.append({"label": "Longest session", "value": _dur(longest_s["agent_ms"]),
                          "detail": f"{longest_s['project']}, {'started ' if first < t0 else ''}{_md(first)}"})
        n = len(sessions)
        facts.append({"label": "Average per session",
                      "value": f"{_dur(sum(s['agent_ms'] for s in sessions) / n)}, {_money(sum(s['cost'] for s in sessions) / n)}",
                      "detail": None})
        facts.append({"label": "Median session cost", "value": _money(statistics.median(s["cost"] for s in sessions)),
                      "detail": None})
        inter = [s for s in sessions if s["interactive"]]
        turns = sum(s["turns"] for s in inter)
        if turns:
            facts.append({"label": "Cost per turn", "value": _money(sum(s["cost"] for s in inter) / turns),
                          "detail": "interactive sessions"})
    wk = section.get("weekly")
    if project is None and wk and wk["pct"] > 0.05 and wk["window_start"]:
        ws = datetime.fromisoformat(wk["window_start"]).timestamp()
        spent = sum(sum(_price(r[5], r[6], r[7], *r[9:15])[:4]) for r in _span(conn, ws, now + 1, None))
        if spent > 0:
            facts.append({"label": "Weekly limit value", "value": f"{_money(spent / (wk['pct'] * 100))} per 1%",
                          "detail": f"about {_money(spent / wk['pct'])} a week at API prices"})
    return {"days": out_days, "active_days": sum(active), "span_days": ACTIVITY_DAYS, "streak": streak,
            "longest_streak": longest, "facts": facts}
