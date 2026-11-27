"""The app's own SQLite store: API calls, turns, sessions, utilization samples.

Claude Code deletes transcripts after ``cleanupPeriodDays``, so this store is
the system of record for history. Cost is never stored: it is computed from
tokens and :mod:`claude_usage.pricing` when read, so a price fix corrects all
past days. Use one connection per thread (:func:`connect`); WAL lets the GUI
read while the worker writes.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, timedelta
from typing import Any, Optional

from claude_usage import pricing

SCHEMA_VERSION = 2

# Additive only: each entry upgrades the schema from version i to i + 1.
MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, size INTEGER, mtime INTEGER, offset INTEGER);
    CREATE TABLE IF NOT EXISTS calls(
        msg_id TEXT PRIMARY KEY, ts_ms INTEGER NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL,
        session_id TEXT, agent_id TEXT, is_subagent INTEGER NOT NULL DEFAULT 0, project TEXT, cwd TEXT,
        model TEXT, speed TEXT, input INTEGER NOT NULL DEFAULT 0, output INTEGER NOT NULL DEFAULT 0,
        cache_read INTEGER NOT NULL DEFAULT 0, cache_write_5m INTEGER NOT NULL DEFAULT 0,
        cache_write_1h INTEGER NOT NULL DEFAULT 0, web_search INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS calls_day ON calls(day);
    CREATE INDEX IF NOT EXISTS calls_session ON calls(session_id);
    CREATE INDEX IF NOT EXISTS calls_ts ON calls(ts_ms);
    CREATE TABLE IF NOT EXISTS turns(
        uuid TEXT PRIMARY KEY, session_id TEXT, ts_ms INTEGER NOT NULL, day TEXT NOT NULL, hour INTEGER NOT NULL,
        project TEXT, duration_ms INTEGER NOT NULL DEFAULT 0, src TEXT, model TEXT);
    CREATE INDEX IF NOT EXISTS turns_day ON turns(day);
    CREATE INDEX IF NOT EXISTS turns_ts ON turns(ts_ms);
    CREATE INDEX IF NOT EXISTS turns_session ON turns(session_id, ts_ms);
    CREATE TABLE IF NOT EXISTS rollup(
        day TEXT NOT NULL, hour INTEGER NOT NULL, session_id TEXT, agent_id TEXT, is_subagent INTEGER, project TEXT,
        model TEXT, speed TEXT, long INTEGER, calls INTEGER, input INTEGER, output INTEGER, cache_read INTEGER,
        cache_write_5m INTEGER, cache_write_1h INTEGER, web_search INTEGER, first_ms INTEGER, last_ms INTEGER);
    CREATE INDEX IF NOT EXISTS rollup_by_day ON rollup(day);
    CREATE TABLE IF NOT EXISTS rollup_day(
        day TEXT NOT NULL, session_id TEXT, is_subagent INTEGER, project TEXT, model TEXT, speed TEXT, long INTEGER,
        calls INTEGER, input INTEGER, output INTEGER, cache_read INTEGER, cache_write_5m INTEGER,
        cache_write_1h INTEGER, web_search INTEGER, first_ms INTEGER, last_ms INTEGER);
    CREATE INDEX IF NOT EXISTS rollup_day_day ON rollup_day(day);
    CREATE TABLE IF NOT EXISTS dirty_days(day TEXT PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY, title TEXT, title_src TEXT, entrypoint TEXT, cwd TEXT, project TEXT);
    CREATE TABLE IF NOT EXISTS samples(ts REAL PRIMARY KEY, weekly REAL, session REAL, payload TEXT);
    CREATE TABLE IF NOT EXISTS legacy_days(day TEXT PRIMARY KEY, cost REAL, tokens INTEGER, messages INTEGER, quality TEXT);
    """,
    # A session's real start for sessions listed in a range that began before it.
    "CREATE INDEX IF NOT EXISTS rollup_day_session ON rollup_day(session_id, first_ms);",
]

# usage-daily.jsonl rows written before the dedupe fix (2026-06-03) counted
# every streamed line of a message, about 2.2x the real usage.
_PRE_DEDUPE_BEFORE = "2026-06-03"


def default_path() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "claude-usage", "usage.db")


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Open (and create or migrate) the store. Call once per thread."""
    path = path or default_path()
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for i in range(version, len(MIGRATIONS)):
        with conn:
            conn.executescript(MIGRATIONS[i])
            conn.execute(f"PRAGMA user_version={i + 1}")
            conn.execute("INSERT OR REPLACE INTO meta VALUES('schema_version', ?)", (str(i + 1),))


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    with conn:
        conn.execute("INSERT OR REPLACE INTO meta VALUES(?, ?)", (key, json.dumps(value)))


def add_sample(conn: sqlite3.Connection, ts: float, weekly: Optional[float], session: Optional[float],
               payload: Optional[dict] = None, min_gap: float = 300) -> bool:
    """Store one utilization sample; ``None`` means the limit was not reported (never 0).

    A sample that repeats the last one within ``min_gap`` seconds is skipped,
    so the table grows by a few hundred rows a day, not one per refresh.
    """
    last = conn.execute("SELECT ts, weekly, session FROM samples ORDER BY ts DESC LIMIT 1").fetchone()
    if last and last[1] == weekly and last[2] == session and 0 <= ts - last[0] < min_gap:
        return False
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO samples VALUES(?, ?, ?, ?)",
            (float(ts), weekly, session, json.dumps(payload) if payload is not None else None),
        )
    return True


# ---------------------------------------------------------------- rollup

def rollup(conn: sqlite3.Connection) -> int:
    """Rebuild the rollups for every day the ingest touched. Returns the number of days.

    ``rollup`` holds token sums per (local day, hour, session, agent, project,
    model, speed, prompt tier), about a tenth of the calls; ``rollup_day``
    drops hour and agent for the long ranges. Together with calls for a
    part-hour edge they give any window exactly. Each rebuild bumps meta
    'rollup_gen', which keys the snapshot caches.
    """
    n = conn.execute("SELECT COUNT(*) FROM dirty_days").fetchone()[0]
    if not n:
        return 0
    with conn:
        conn.execute("DELETE FROM rollup WHERE day IN (SELECT day FROM dirty_days)")
        conn.execute(
            "INSERT INTO rollup SELECT day, hour, session_id, agent_id, is_subagent, coalesce(project, 'Unknown'), "
            "model, coalesce(speed, ''), input + cache_read + cache_write_5m + cache_write_1h > ?, COUNT(*), "
            "SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), SUM(cache_write_1h), SUM(web_search), "
            "MIN(ts_ms), MAX(ts_ms) FROM calls WHERE day IN (SELECT day FROM dirty_days) GROUP BY 1, 2, 3, 4, 5, 6, 7, 8, 9",
            (pricing.LONG_PROMPT_TOKENS,))
        conn.execute("DELETE FROM rollup_day WHERE day IN (SELECT day FROM dirty_days)")
        conn.execute(
            "INSERT INTO rollup_day SELECT day, session_id, is_subagent, project, model, speed, long, SUM(calls), "
            "SUM(input), SUM(output), SUM(cache_read), SUM(cache_write_5m), SUM(cache_write_1h), SUM(web_search), "
            "MIN(first_ms), MAX(last_ms) FROM rollup WHERE day IN (SELECT day FROM dirty_days) "
            "GROUP BY 1, 2, 3, 4, 5, 6, 7")
        conn.execute("DELETE FROM dirty_days")
        conn.execute("INSERT INTO meta VALUES('rollup_gen', '1') "
                     "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")
    return n


# ---------------------------------------------------------------- legacy import

def import_legacy(conn: sqlite3.Connection, claude_dir: str, force: bool = False) -> dict:
    """Copy usage-daily.jsonl and usage-history.jsonl in once (read-only on both)."""
    if get_meta(conn, "legacy_imported") and not force:
        return {"skipped": True}
    days = _legacy_days(os.path.join(claude_dir, "usage-daily.jsonl"))
    samples = []
    for row in _jsonl(os.path.join(claude_dir, "usage-history.jsonl")):
        try:
            ts = float(row["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        weekly, session = _frac(row.get("weekly")), _frac(row.get("session"))
        # The old collector wrote 0.0 when the 5h limit was not reported.
        samples.append((ts, weekly, None if not session else session, None))
    with conn:
        conn.executemany("INSERT OR REPLACE INTO legacy_days VALUES(?, ?, ?, ?, ?)", days)
        conn.executemany("INSERT OR IGNORE INTO samples VALUES(?, ?, ?, ?)", samples)
    set_meta(conn, "legacy_imported", {"days": len(days), "samples": len(samples)})
    update_coverage(conn)
    return {"days": len(days), "samples": len(samples), "corrupt": sum(1 for d in days if d[4] == "corrupt")}


def _legacy_days(path: str) -> list:
    rows = sorted((r for r in _jsonl(path) if isinstance(r.get("date"), str)), key=lambda r: r["date"])
    out, prev = [], None
    for r in rows:
        t = r.get("tokens") if isinstance(r.get("tokens"), dict) else {}
        tokens = sum(int(t.get(k) or 0) for k in ("input", "output", "cache_read", "cache_creation"))
        cost, msgs = float(r.get("cost") or 0), int(r.get("messages") or 0)
        # The backfill flagged these rows too, so the date alone decides.
        pre_dedupe = r["date"] < _PRE_DEDUPE_BEFORE and cost > 0
        bad = pre_dedupe or (cost > 0 and prev is not None and _copied(cost, msgs, *prev))
        out.append((r["date"], cost, tokens, msgs, "corrupt" if bad else "legacy"))
        prev = (cost, msgs)
    return out


def _copied(cost: float, msgs: int, prev_cost: float, prev_msgs: int) -> bool:
    """The overnight copy bug: yesterday's totals written into today's row, then max()-merged with
    today's own, so a copy shares the cost or the message count with the row before, or is within 0.5%."""
    if cost == prev_cost or (msgs and msgs == prev_msgs):
        return True
    return prev_cost > 0 and prev_msgs > 0 and abs(cost / prev_cost - 1) < 0.005 and abs(msgs / prev_msgs - 1) < 0.005


def _jsonl(path: str):
    try:
        fh = open(path, encoding="utf-8-sig")
    except OSError:
        return
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _frac(v: Any) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if 0.0 <= f <= 1.5 else None


# ---------------------------------------------------------------- coverage

def update_coverage(conn: sqlite3.Connection) -> Optional[str]:
    """Recompute meta 'coverage_from': the first day from which the ledger can be trusted.

    A usable legacy day "fails" when the ledger has fewer than 90% of its
    main-thread messages (the old widget counted exactly those) or less than
    half its cost. Claude Code's cleanup deletes whole old sessions, so
    cleaned-up days fail densely; scattered failures are bad legacy rows
    (overnight copies the detector missed). Coverage starts the day after the
    latest failing day with at least 3 failures among the last 5 legacy days.
    Earlier days fall back to legacy rows.
    """
    ledger, main = {}, {}
    for day, model, speed, long, sub, n, i, o, r, w5, w1, ws in conn.execute(
        "SELECT day, model, speed, long, is_subagent, SUM(calls), SUM(input), SUM(output), SUM(cache_read), "
        "SUM(cache_write_5m), SUM(cache_write_1h), SUM(web_search) FROM rollup_day GROUP BY 1, 2, 3, 4, 5"
    ):
        ledger[day] = ledger.get(day, 0.0) + pricing.group_cost(model, speed, long, i, o, r, w5, w1, ws)
        if not sub:
            main[day] = main.get(day, 0) + n
    legacy = conn.execute(
        "SELECT day, cost, messages FROM legacy_days WHERE quality='legacy' AND cost > 0 ORDER BY day"
    ).fetchall()
    cov = None
    if ledger:
        cov = min(ledger)
        fails = [ledger.get(d, 0.0) < 0.5 * c or main.get(d, 0) < 0.9 * (m or 0) for d, c, m in legacy]
        for i in range(len(legacy) - 1, -1, -1):
            recent = fails[max(0, i - 4):i + 1]
            if fails[i] and sum(recent) >= min(3, len(recent)):
                cov = max(cov, _next_day(legacy[i][0]))
                break
    set_meta(conn, "coverage_from", cov)
    return cov


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()
