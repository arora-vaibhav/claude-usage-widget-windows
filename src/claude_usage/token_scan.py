"""Incremental, memory-safe transcript token scanner.

A budget-capped *full* re-scan every refresh under-counts when the daily
transcript volume exceeds the budget. This scanner instead tracks a byte offset
per file and streams only the **new** lines appended since last time into
per-LOCAL-day token buckets ("today" follows the user's local day, matching the
rest of the app).

To stay responsive on huge histories it:
  * orders files newest-first and reads at most ``_PER_SCAN_BUDGET`` bytes per
    ``scan()`` call, so today's usage is captured first and a single call never
    blocks for minutes (the backlog catches up over a few refreshes);
  * persists offsets + day buckets to disk, so the one expensive full pass
    happens once ever — restarts resume incrementally.

``collect_tokens()`` is a drop-in replacement for the collector's old
``_collect_tokens_single_pass`` and returns the same result-dict shape.
"""

from __future__ import annotations

import glob
import json
import os
import tempfile
import time
from datetime import datetime, timedelta

_WINDOW_SECONDS = 8 * 86400           # today + 7-day week (+ slack)
_PER_SCAN_BUDGET = 250 * 1024 * 1024  # max bytes read per scan() call
_STATE_FILENAME = ".usage-token-scan.json"
# Schema 2: dedupe assistant turns by message.id (Claude Code writes one JSONL
# line per content/tool block, each repeating the SAME turn-level usage block,
# which previously inflated all token/cost totals ~3-4x). Bumping the schema
# discards any pre-dedup persisted state so totals self-correct on upgrade.
_STATE_SCHEMA = 2
_KEEP_DAYS = 14                       # prune persisted day buckets beyond this
# State persists offsets + 14 days of buckets + per-day seen-ids — a multi-MB
# JSON on heavy days. Dumping it every 30s scan is pointless churn; throttle.
# Crash-safe: everything is saved as one consistent snapshot, so a crash just
# re-reads the last <=5 min of appended lines and re-derives identical buckets.
_SAVE_INTERVAL = 300.0  # seconds between state saves (first save is immediate)


def _empty_day() -> dict:
    return {
        "messages": 0,
        "tokens": {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0},
        "by_model": {},
        "by_project": {},
    }


def local_day(ts: str) -> str:
    """A UTC ISO-8601 timestamp -> the user's LOCAL calendar date 'YYYY-MM-DD'.

    "Today" follows the user's local day-rollover (matching the rest of the app
    and what the user perceives as "today"); transcripts store UTC timestamps,
    so we convert before bucketing. Falls back to the date prefix on bad input.
    """
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return ts[:10]


class IncrementalTokenScanner:
    """Streams new transcript lines into per-local-day token buckets across refreshes."""

    def __init__(self, claude_dir: str) -> None:
        self._claude_dir = claude_dir
        self._state_path = os.path.join(claude_dir, _STATE_FILENAME)
        self._offsets: dict[str, int] = {}   # file path -> bytes consumed
        self._days: dict[str, dict] = {}     # "YYYY-MM-DD" (local) -> bucket
        # message.id values already counted, keyed by local day. Claude Code
        # repeats a turn's usage block across multiple JSONL lines, so we count
        # each id once. Persisted so duplicates spanning scan/restart boundaries
        # stay deduped — and stored PER DAY so the seen-set prunes in lockstep
        # with its day bucket (a flat set would keep ids forever, which under-
        # counts if a >14-day-old file is ever rewritten and re-scanned).
        self._seen_by_day: dict[str, set[str]] = {}
        self._last_save: float = 0.0  # 0 -> first save fires immediately
        self._load_state()

    # -- persistence -------------------------------------------------------
    def _load_state(self) -> None:
        try:
            with open(self._state_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(data, dict) or data.get("schema") != _STATE_SCHEMA:
            return
        offs = data.get("offsets")
        days = data.get("days")
        if isinstance(offs, dict):
            self._offsets = {str(k): int(v) for k, v in offs.items()}
        if isinstance(days, dict):
            self._days = days
        seen = data.get("seen")
        if isinstance(seen, dict):
            self._seen_by_day = {
                str(day): {str(i) for i in ids}
                for day, ids in seen.items() if isinstance(ids, list)
            }

    def _save_state(self) -> None:
        cutoff = (datetime.now() - timedelta(days=_KEEP_DAYS)).strftime("%Y-%m-%d")
        days = {d: b for d, b in self._days.items() if d >= cutoff}
        offsets = {p: o for p, o in self._offsets.items() if os.path.exists(p)}
        # Prune seen-ids on the SAME day cutoff as the buckets, so the two stay
        # in lockstep: an id is remembered exactly as long as its day bucket is.
        seen = {
            day: list(ids) for day, ids in self._seen_by_day.items() if day >= cutoff
        }
        payload = {"schema": _STATE_SCHEMA, "offsets": offsets, "days": days, "seen": seen}
        try:
            dirname = os.path.dirname(self._state_path) or "."
            fd, tmp = tempfile.mkstemp(dir=dirname, prefix=".uts-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, self._state_path)
        except OSError:
            pass

    # -- scanning ----------------------------------------------------------
    def scan(self) -> dict[str, dict]:
        """Read newly-appended lines (newest files first, budget-bounded)."""
        projects_dir = os.path.join(self._claude_dir, "projects")
        if not os.path.isdir(projects_dir):
            return self._days
        projects_dir = os.path.realpath(projects_dir)
        cutoff = time.time() - _WINDOW_SECONDS

        candidates: list[tuple[float, str, int]] = []
        for path in glob.glob(os.path.join(projects_dir, "*", "*.jsonl")):
            if "subagents" in path.split(os.sep):
                continue
            try:
                mtime = os.path.getmtime(path)
                size = os.path.getsize(path)
            except OSError:
                continue
            if mtime < cutoff and path not in self._offsets:
                continue
            off = self._offsets.get(path, 0)
            if size < off:        # truncated / replaced -> re-read from start
                off = 0
                self._offsets[path] = 0
            if size <= off:
                continue          # nothing new appended
            candidates.append((mtime, path, off))

        if not candidates:
            return self._days

        candidates.sort(reverse=True)  # newest first -> today captured first
        budget = _PER_SCAN_BUDGET
        for _mtime, path, off in candidates:
            if budget <= 0:
                break
            budget -= self._scan_file(path, off, budget)
        if time.time() - self._last_save >= _SAVE_INTERVAL:
            self._save_state()
            self._last_save = time.time()
        return self._days

    def _scan_file(self, path: str, off: int, budget: int) -> int:
        """Stream new lines from *path* (from *off*), up to ~*budget* bytes. Returns bytes read."""
        project = os.path.basename(os.path.dirname(path))
        consumed = off
        read = 0
        try:
            with open(path, "rb") as f:
                f.seek(off)
                for raw in f:                 # streams one line at a time (memory-safe)
                    if raw.endswith(b"\n"):
                        self._consume(raw[:-1], project)
                        consumed += len(raw)
                        read += len(raw)
                        if read >= budget:
                            break             # hit the per-scan budget; resume next call
                    else:
                        break                 # partial trailing line -> re-read next time
        except (OSError, MemoryError):
            return read
        self._offsets[path] = consumed
        return read

    def _consume(self, raw_line: bytes, project: str) -> None:
        line = raw_line.strip()
        if not line:
            return
        try:
            entry = json.loads(line.decode("utf-8", "replace"))
        except (json.JSONDecodeError, ValueError):
            return
        if entry.get("type") != "assistant":
            return
        ts = entry.get("timestamp", "")
        if not isinstance(ts, str) or len(ts) < 10:
            return
        msg = entry.get("message", {})
        if not isinstance(msg, dict):
            return
        model = msg.get("model", "unknown")
        # Skip Claude Code's internal bookkeeping turns (compact summaries,
        # sidechain context). Pricing already zero-rates them, but they also
        # padded token-volume and message counts — exclude them outright.
        if model in ("<synthetic>", "unknown"):
            return
        # Dedupe by message.id: Claude Code writes one JSONL line per content/
        # tool block within a turn, each repeating the SAME turn-level usage
        # block. Counting every line inflated totals ~3-4x. Count each id once
        # PER DAY (so the seen-set prunes with its day bucket); drop entries with
        # no id (we can't safely dedupe those replays).
        msg_id = str(msg.get("id") or "")
        if not msg_id:
            return
        day = local_day(ts)
        seen = self._seen_by_day.setdefault(day, set())
        if msg_id in seen:
            return
        seen.add(msg_id)

        usage = msg.get("usage", {}) or {}
        inp = usage.get("input_tokens", 0) or 0
        out = usage.get("output_tokens", 0) or 0
        cr = usage.get("cache_read_input_tokens", 0) or 0
        cc = usage.get("cache_creation_input_tokens", 0) or 0

        d = self._days.setdefault(day, _empty_day())
        d["messages"] += 1
        t = d["tokens"]
        t["input"] += inp
        t["output"] += out
        t["cache_read"] += cr
        t["cache_creation"] += cc
        bm = d["by_model"].setdefault(
            model, {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
        bm["input"] += inp
        bm["output"] += out
        bm["cache_read"] += cr
        bm["cache_creation"] += cc
        d["by_project"][project] = d["by_project"].get(project, 0) + out


_SCANNERS: dict[str, IncrementalTokenScanner] = {}


def get_scanner(claude_dir: str) -> IncrementalTokenScanner:
    """Return a process-lifetime scanner for *claude_dir* (created on first use)."""
    s = _SCANNERS.get(claude_dir)
    if s is None:
        s = IncrementalTokenScanner(claude_dir)
        _SCANNERS[claude_dir] = s
    return s


def collect_tokens(
    claude_dir: str,
    today_prefix: str,
    week_prefixes: list[str],
) -> dict:
    """Drop-in for ``_collect_tokens_single_pass`` backed by the incremental scanner.

    Returns the same result dict: today/week output, per-model (+ detailed),
    and today's per-project breakdown. ``today_prefix`` / ``week_prefixes`` are
    local YYYY-MM-DD dates (matching the scanner's local-day buckets).
    """
    days = get_scanner(claude_dir).scan()
    result: dict = {
        "today_output": 0,
        "today_messages": 0,
        "week_output": 0,
        "today_by_model": {},
        "today_by_model_detailed": {},
        "week_by_model_detailed": {},
        "today_by_project": {},
    }
    week_set = set(week_prefixes) | {today_prefix}
    for date, d in days.items():
        if date not in week_set:
            continue
        result["week_output"] += d["tokens"]["output"]
        for model, b in d["by_model"].items():
            wb = result["week_by_model_detailed"].setdefault(
                model, {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
            for k in ("input", "output", "cache_read", "cache_creation"):
                wb[k] += b.get(k, 0)
        if date == today_prefix:
            result["today_output"] = d["tokens"]["output"]
            result["today_messages"] = d["messages"]
            result["today_by_project"] = dict(d["by_project"])
            for model, b in d["by_model"].items():
                result["today_by_model"][model] = b.get("output", 0)
                result["today_by_model_detailed"][model] = dict(b)
    return result
