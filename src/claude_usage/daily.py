"""Daily usage-rollup store — one JSON row per UTC day.

The fine-grained utilization point-samples live in :mod:`history`; this module
keeps a compact per-day rollup of tokens / cost / messages / per-model /
per-project that the dashboard charts read. One row per day keeps the file
tiny (~365 rows/year), so an upsert just rewrites the whole file atomically.

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

import json
import os
import tempfile

DAILY_FILENAME = "usage-daily.jsonl"
SCHEMA_VERSION = 1


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


def upsert_day(path: str, row: dict) -> None:
    """Insert or replace the row for ``row['date']`` (idempotent), atomically.

    Reads existing rows, replaces/adds the one matching ``row['date']``, then
    rewrites the file sorted by date via a temp file + :func:`os.replace`, so a
    crash mid-write can never truncate the history.
    """
    date = str(row["date"])
    by_date = {str(r.get("date", "")): r for r in load_daily(path)}
    by_date[date] = row
    ordered = [by_date[d] for d in sorted(by_date)]

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
