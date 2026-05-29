"""Daily usage-rollup store — one JSON row per UTC day.

The fine-grained utilization point-samples live in :mod:`history`; this module
keeps a compact per-day rollup of tokens / cost / messages / per-model /
per-project that the dashboard charts read. One row per day keeps the file
tiny (~365 rows/year), so an upsert just rewrites the whole file atomically.

Days are bucketed in **UTC** to match the conversation-transcript timestamps
(ISO-8601 ``...Z``) that the token data comes from.

Row schema (v1), all values illustrative::

    {
      "date": "2026-05-29",            # ISO-8601 YYYY-MM-DD, UTC
      "messages": 142,
      "sessions": 7,
      "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
      "cost": 12.47,
      "cache_savings": 8.10,
      "by_model": {"claude-opus-4-8": {"input": 0, "output": 0}},
      "by_project": {"Claude Widget": 0},
      "peak_session_util": 0.62,
      "peak_weekly_util": 0.48,
      "schema": 1
    }
"""

from __future__ import annotations

import gc
import glob
import json
import os
import tempfile
from datetime import datetime, timezone

DAILY_FILENAME = "usage-daily.jsonl"
SCHEMA_VERSION = 1

# Backfill scan guards — streaming keeps per-file memory low, but cap total
# work so a pathological projects/ dir can't stall first launch.
_BACKFILL_BYTE_BUDGET = 400 * 1024 * 1024  # 400 MB of transcript bytes
_BACKFILL_MAX_FILES = 6000


def load_daily(path: str, since_date: str = "") -> list[dict]:
    """Return daily rows sorted ascending by date.

    ISO-8601 ``YYYY-MM-DD`` strings compare and sort lexicographically, so
    ``since_date`` filtering is a plain string comparison (rows with
    ``date >= since_date`` are kept). A missing file yields ``[]``; malformed
    or date-less lines are skipped so one bad row never poisons the series.
    """
    if not os.path.isfile(path):
        return []
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or "date" not in row:
                continue
            if since_date and str(row["date"]) < since_date:
                continue
            rows.append(row)
    rows.sort(key=lambda r: str(r.get("date", "")))
    return rows


def _atomic_write_rows(path: str, rows: list[dict]) -> None:
    """Write *rows* (sorted by date) to *path* via temp file + os.replace."""
    ordered = sorted(rows, key=lambda r: str(r.get("date", "")))
    dirname = os.path.dirname(path) or "."
    os.makedirs(dirname, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirname, prefix=".usage-daily-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for r in ordered:
                f.write(json.dumps(r) + "\n")
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def upsert_day(path: str, row: dict) -> None:
    """Insert or replace the row for ``row['date']`` (idempotent), atomically.

    Reads existing rows, replaces/adds the one matching ``row['date']``, then
    rewrites the file sorted by date, so a crash mid-write can never truncate
    the history.
    """
    date = str(row["date"])
    by_date = {str(r.get("date", "")): r for r in load_daily(path)}
    by_date[date] = row
    _atomic_write_rows(path, list(by_date.values()))


# --------------------------------------------------------------------------
# Backfill — reconstruct historical daily rows from existing local data so the
# dashboard has real depth on first run, instead of starting empty.
# --------------------------------------------------------------------------

def _empty_tokens() -> dict:
    return {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}


def _bucket_transcripts_by_day(claude_dir: str) -> dict[str, dict]:
    """Bucket per-message tokens/model/project by UTC day from transcripts.

    Streams ``~/.claude/projects/*/*.jsonl`` line by line (low memory), newest
    files first, stopping at the byte/file budget. Returns
    ``{day: {"tokens": {...}, "by_model": {model: {...}}, "by_project": {name: output}}}``.
    """
    days: dict[str, dict] = {}
    projects_dir = os.path.join(claude_dir, "projects")
    if not os.path.isdir(projects_dir):
        return days
    projects_dir = os.path.realpath(projects_dir)

    candidates: list[tuple[float, int, str]] = []
    for jp in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
        if "subagents" in jp.split(os.sep):
            continue
        try:
            candidates.append((os.path.getmtime(jp), os.path.getsize(jp), jp))
        except OSError:
            continue
    candidates.sort(reverse=True)  # newest first

    read = 0
    for i, (_mtime, fsize, jp) in enumerate(candidates[:_BACKFILL_MAX_FILES]):
        if read > _BACKFILL_BYTE_BUDGET:
            break
        project = os.path.basename(os.path.dirname(jp))
        try:
            with open(jp, encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    ts = entry.get("timestamp", "")
                    if not isinstance(ts, str) or len(ts) < 10:
                        continue
                    day = ts[:10]  # ISO-8601 UTC -> YYYY-MM-DD
                    msg = entry.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    usage = msg.get("usage", {}) or {}
                    inp = usage.get("input_tokens", 0) or 0
                    out = usage.get("output_tokens", 0) or 0
                    cr = usage.get("cache_read_input_tokens", 0) or 0
                    cc = usage.get("cache_creation_input_tokens", 0) or 0
                    model = msg.get("model", "unknown")

                    d = days.setdefault(
                        day,
                        {"messages": 0, "tokens": _empty_tokens(),
                         "by_model": {}, "by_project": {}},
                    )
                    d["messages"] += 1  # one assistant response = one turn
                    t = d["tokens"]
                    t["input"] += inp
                    t["output"] += out
                    t["cache_read"] += cr
                    t["cache_creation"] += cc
                    bm = d["by_model"].setdefault(model, _empty_tokens())
                    bm["input"] += inp
                    bm["output"] += out
                    bm["cache_read"] += cr
                    bm["cache_creation"] += cc
                    d["by_project"][project] = d["by_project"].get(project, 0) + out
        except (OSError, MemoryError):
            gc.collect()
            continue
        read += fsize
        if i and i % 25 == 0:
            gc.collect()
    return days


def _bucket_history_by_day(claude_dir: str) -> dict[str, dict]:
    """Bucket message counts + unique sessions by UTC day from history.jsonl."""
    out: dict[str, dict] = {}
    path = os.path.join(claude_dir, "history.jsonl")
    if not os.path.isfile(path):
        return out
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts_ms = entry.get("timestamp", 0)
            if not ts_ms:
                continue
            try:
                day = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            except (OverflowError, OSError, ValueError):
                continue
            d = out.setdefault(day, {"messages": 0, "sessions": set()})
            d["messages"] += 1
            sid = entry.get("sessionId", "")
            if sid:
                d["sessions"].add(sid)
    return out


def _bucket_peak_util_by_day(claude_dir: str) -> dict[str, dict]:
    """Per-UTC-day peak session/weekly utilization from the point-sample file."""
    from claude_usage.history import load_samples

    out: dict[str, dict] = {}
    path = os.path.join(claude_dir, "usage-history.jsonl")
    for s in load_samples(path):
        ts = s.get("ts", 0)
        if not ts:
            continue
        try:
            day = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            continue
        d = out.setdefault(day, {"session": 0.0, "weekly": 0.0})
        d["session"] = max(d["session"], float(s.get("session", 0.0) or 0.0))
        d["weekly"] = max(d["weekly"], float(s.get("weekly", 0.0) or 0.0))
    return out


def backfill(claude_dir: str, daily_path: str | None = None) -> int:
    """Fill missing daily rows from existing local data. Returns rows written.

    Idempotent: only days **not already present** in the store are written, so
    re-running adds nothing and the live collector's rows (esp. today) are
    never clobbered. ``peak_*_util`` is only available for days we already had
    point-samples for (the rate-limit API can't be backfilled); other days get
    0.0 there. Backfilled rows carry ``"backfilled": true``.
    """
    from claude_usage import pricing

    daily_path = daily_path or os.path.join(claude_dir, DAILY_FILENAME)
    existing = {str(r.get("date", "")) for r in load_daily(daily_path)}

    tok_by_day = _bucket_transcripts_by_day(claude_dir)
    hist_by_day = _bucket_history_by_day(claude_dir)
    util_by_day = _bucket_peak_util_by_day(claude_dir)

    all_days = sorted(set(tok_by_day) | set(hist_by_day) | set(util_by_day))
    new_rows: list[dict] = []
    for day in all_days:
        if day in existing:
            continue
        tok = tok_by_day.get(day, {})
        bm_detailed = tok.get("by_model", {})
        cost = pricing.calculate_stats_cost(bm_detailed)
        hist = hist_by_day.get(day, {})
        util = util_by_day.get(day, {})
        new_rows.append({
            "date": day,
            # Prefer whichever source has more coverage: history.jsonl can stop
            # updating, while transcripts (one assistant entry = one turn) keep
            # going, so a day is never spuriously shown as zero-activity.
            "messages": max(int(tok.get("messages", 0)), int(hist.get("messages", 0))),
            "sessions": len(hist.get("sessions", set())),
            "tokens": tok.get("tokens", _empty_tokens()),
            "cost": round(float(cost.get("total", 0.0)), 4),
            "cache_savings": round(float(cost.get("cache_savings", 0.0)), 4),
            "by_model": {m: {"input": b["input"], "output": b["output"]}
                         for m, b in bm_detailed.items()},
            "by_project": tok.get("by_project", {}),
            "peak_session_util": round(float(util.get("session", 0.0)), 4),
            "peak_weekly_util": round(float(util.get("weekly", 0.0)), 4),
            "schema": SCHEMA_VERSION,
            "backfilled": True,
        })

    if new_rows:
        merged = load_daily(daily_path) + new_rows
        _atomic_write_rows(daily_path, merged)
    return len(new_rows)
