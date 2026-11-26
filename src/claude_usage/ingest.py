"""Incremental transcript ingest: ~/.claude/projects/**/*.jsonl into the store.

Every API call is stored once per ``message.id``. Claude Code writes one
message on several lines while it streams, and early lines can carry a
partial ``output_tokens``, so each usage field keeps the maximum seen across
the message's lines (in any file) and the timestamp keeps the earliest; the
local day and hour follow that timestamp. Subagent and workflow transcripts
(``<session>/subagents/**``) are included and belong to their parent session.

Turns are human prompts in main transcripts. Agent time per turn is the
``system/turn_duration`` record when Claude Code wrote one, else the span from
the prompt to the last main-thread reply before the next prompt, capped at
30 minutes.

Use one :class:`Ingestor` per worker thread: :meth:`Ingestor.index` for the
first full pass (progress and cancel), then :meth:`Ingestor.tail` every
refresh. ``tail`` only stats recently active files and directories and does a
full walk every few minutes, so an idle tick costs a few milliseconds.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from claude_usage import store

MAX_LINE = 8 << 20  # lines longer than this are skipped (huge pasted payloads)
SPAN_CAP_MS = 30 * 60 * 1000
HOT_SECONDS = 6 * 3600
FULL_WALK_SECONDS = 300
_BATCH = 5000

HOME = os.path.expanduser("~")
TEMP = tempfile.gettempdir()
_COMMAND_ECHOES = ("<local-command", "<command-message>", "<bash-stdout", "<task-notification")

_UPSERT_CALL = """
INSERT INTO calls(msg_id, ts_ms, day, hour, session_id, agent_id, is_subagent, project, cwd, model, speed,
                  input, output, cache_read, cache_write_5m, cache_write_1h, web_search)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(msg_id) DO UPDATE SET
    input = max(input, excluded.input),
    output = max(output, excluded.output),
    cache_read = max(cache_read, excluded.cache_read),
    cache_write_5m = max(cache_write_5m, excluded.cache_write_5m),
    cache_write_1h = max(cache_write_1h, excluded.cache_write_1h),
    web_search = max(web_search, excluded.web_search),
    day = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.day ELSE day END,
    hour = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.hour ELSE hour END,
    session_id = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.session_id ELSE session_id END,
    agent_id = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.agent_id ELSE agent_id END,
    is_subagent = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.is_subagent ELSE is_subagent END,
    project = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.project ELSE project END,
    cwd = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.cwd ELSE cwd END,
    model = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.model ELSE model END,
    speed = CASE WHEN excluded.ts_ms < ts_ms THEN excluded.speed ELSE speed END,
    ts_ms = min(ts_ms, excluded.ts_ms)
"""

_UPSERT_SESSION = """
INSERT INTO sessions(session_id, entrypoint, cwd, project) VALUES (?,?,?,?)
ON CONFLICT(session_id) DO UPDATE SET
    entrypoint = coalesce(entrypoint, excluded.entrypoint),
    cwd = coalesce(cwd, excluded.cwd),
    project = coalesce(project, excluded.project)
"""


def project_name(cwd: Optional[str], home: str = HOME, temp: str = TEMP) -> Optional[str]:
    """Display name for a working directory; never a raw path.

    Worktrees fold into their repo folder, the home folder is "Home", anything
    under the temp folder is "Temporary folders".
    """
    if not cwd:
        return None
    path = os.path.normpath(cwd)
    key = os.path.normcase(path).rstrip("\\/")
    for marker in (os.sep + ".claude" + os.sep + "worktrees" + os.sep, os.sep + ".worktrees" + os.sep):
        i = (key + os.sep).find(marker)
        if i >= 0:
            path, key = path[:i], key[:i]
            break
    if key == os.path.normcase(os.path.normpath(home)).rstrip("\\/"):
        return "Home"
    tkey = os.path.normcase(os.path.normpath(temp)).rstrip("\\/")
    if key == tkey or key.startswith(tkey + os.sep):
        return "Temporary folders"
    return os.path.basename(path.rstrip("\\/")) or path


def _epoch_ms(ts: str) -> Optional[int]:
    try:
        return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000)
    except (AttributeError, TypeError, ValueError):
        return None


def _local(ms: int) -> tuple:
    d = datetime.fromtimestamp(ms / 1000)
    return d.strftime("%Y-%m-%d"), d.hour


def _next_day(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _is_prompt(e: dict) -> bool:
    """A human prompt: not meta, sidechain, compact summary, tool result or command echo."""
    if e.get("isSidechain") or e.get("isMeta") or e.get("isCompactSummary"):
        return False
    c = (e.get("message") or {}).get("content")
    if isinstance(c, list):
        kinds = {b.get("type") for b in c if isinstance(b, dict)}
        if "tool_result" in kinds or not ({"text", "image"} & kinds):
            return False
        c = next((b.get("text") or "" for b in c if isinstance(b, dict) and b.get("type") == "text"), "")
    if not isinstance(c, str):
        return False
    return not c.lstrip().startswith(_COMMAND_ECHOES)


class Ingestor:
    """Keeps the store in step with the transcript tree. Not thread-safe; one per worker."""

    def __init__(self, conn: sqlite3.Connection, projects_dir: Optional[str] = None,
                 clock: Callable[[], float] = time.time):
        self.conn = conn
        self.root = os.path.abspath(projects_dir or os.path.join(HOME, ".claude", "projects"))
        self.clock = clock
        self._files = {p: (s, m, o) for p, s, m, o in conn.execute("SELECT path, size, mtime, offset FROM files")}
        self._names = {}
        for (name,) in conn.execute("SELECT DISTINCT project FROM calls WHERE project IS NOT NULL"):
            self._names.setdefault(name.lower(), name)
        self._dirs = {}  # dir -> mtime_ns as of the last look
        self._hot_dirs = set()
        self._hot_files = set()
        self._last_full = None
        self._known_sessions = set()
        store.rollup(conn)  # days left dirty by an interrupted run

    # ------------------------------------------------------------ public

    def index(self, progress: Optional[Callable[[int, int], None]] = None,
              cancel: Optional[Callable[[], bool]] = None) -> dict:
        """Walk the whole tree and read every new or changed transcript."""
        t0 = time.perf_counter()
        found = self._walk()
        todo = sorted(p for p, st in found.items() if self._changed(p, st))
        calls = done = 0
        if progress:
            progress(0, len(todo))
        for rel in todo:
            if cancel and cancel():
                break
            calls += self._read(rel, *found[rel], cancel=cancel)
            done += 1
            if progress:
                progress(done, len(todo))
        self._last_full = self.clock()
        self._pick_hot(found)
        if store.rollup(self.conn):
            store.update_coverage(self.conn)
        return {"files": len(found), "read": done, "todo": len(todo), "calls": calls,
                "cancelled": done < len(todo), "ms": round((time.perf_counter() - t0) * 1000, 1), "full": True}

    def tail(self) -> dict:
        """Read what changed since the last call; cheap when nothing did."""
        if self._last_full is None or self.clock() - self._last_full >= FULL_WALK_SECONDS:
            return self.index()
        t0 = time.perf_counter()
        for d in list(self._hot_dirs):
            try:
                m = os.stat(d).st_mtime_ns
            except OSError:
                self._hot_dirs.discard(d)
                continue
            if m != self._dirs.get(d):
                self._dirs[d] = m
                self._scan_dir(d)
        calls = read = 0
        for rel in sorted(self._hot_files):
            try:
                st = os.stat(os.path.join(self.root, rel))
            except OSError:
                self._hot_files.discard(rel)
                continue
            if self._changed(rel, (st.st_size, st.st_mtime_ns)):
                calls += self._read(rel, st.st_size, st.st_mtime_ns)
                read += 1
        store.rollup(self.conn)
        return {"files": len(self._files), "read": read, "calls": calls,
                "ms": round((time.perf_counter() - t0) * 1000, 2), "full": False}

    # ------------------------------------------------------------ walking

    def _walk(self) -> dict:
        found = {}
        self._dirs.clear()
        stack = [self.root]
        while stack:
            d = stack.pop()
            try:
                self._dirs[d] = os.stat(d).st_mtime_ns
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.name.endswith(".jsonl"):
                            st = e.stat(follow_symlinks=False)
                            found[os.path.relpath(e.path, self.root)] = (st.st_size, st.st_mtime_ns)
                    except OSError:
                        continue
        return found

    def _pick_hot(self, found: dict) -> None:
        cutoff = (self.clock() - HOT_SECONDS) * 1e9
        self._hot_files = {p for p, (_s, m) in found.items() if m >= cutoff}
        # Live sessions add subagent files under <slug>/<session>/ at any time.
        live = {os.path.join(self.root, p[:-6]) for p in self._hot_files if p.count(os.sep) == 1}
        self._hot_dirs = set()
        for d, m in self._dirs.items():
            rel = os.path.relpath(d, self.root)
            if rel == "." or rel.count(os.sep) == 0 or m >= cutoff or any(
                    d == s or d.startswith(s + os.sep) for s in live):
                self._hot_dirs.add(d)

    def _scan_dir(self, d: str) -> None:
        """A hot directory changed: pick up new files and new subdirectories."""
        try:
            it = os.scandir(d)
        except OSError:
            return
        with it:
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.path not in self._dirs:
                            self._dirs[e.path] = e.stat(follow_symlinks=False).st_mtime_ns
                            self._hot_dirs.add(e.path)
                            self._scan_dir(e.path)
                    elif e.name.endswith(".jsonl"):
                        self._hot_files.add(os.path.relpath(e.path, self.root))
                except OSError:
                    continue

    def _changed(self, rel: str, st: tuple) -> bool:
        old = self._files.get(rel)
        return old is None or (old[0], old[1]) != st

    # ------------------------------------------------------------ reading

    def _read(self, rel: str, size: int, mtime: int, cancel: Optional[Callable[[], bool]] = None) -> int:
        old = self._files.get(rel)
        offset = old[2] if old else 0
        if old and (size < offset or mtime < old[1]):
            offset = 0  # shrank or rewritten: read again, the upsert makes that safe
        parts = rel.split(os.sep)
        is_sub = "subagents" in parts
        file_sid = parts[1] if is_sub else (parts[-1][:-6] if len(parts) == 2 else None)
        ctx = _FileCtx(self, rel, is_sub, file_sid)
        try:
            fh = open(os.path.join(self.root, rel), "rb")
        except OSError:
            return 0
        n_lines = 0
        with fh:
            fh.seek(offset)
            pos = offset
            while True:
                raw = fh.readline(MAX_LINE + 1)
                if not raw:
                    break
                if raw[-1:] != b"\n":
                    if len(raw) <= MAX_LINE:
                        break  # partial last line: wait for the rest
                    end = pos + len(raw)
                    while raw and raw[-1:] != b"\n":
                        raw = fh.readline(MAX_LINE)
                        end += len(raw)
                    if not raw:
                        break  # an over-long line still being written
                    pos = end
                    continue
                pos += len(raw)
                ctx.line(raw)
                n_lines += 1
                if len(ctx.calls) >= _BATCH:
                    ctx.flush(rel, size, mtime, pos, final=False)
                    if cancel and cancel():
                        break
        ctx.flush(rel, size, mtime, pos, final=True)
        self._files[rel] = (size, mtime, pos)
        return ctx.n_calls

    def _project(self, cwd: Optional[str]) -> Optional[str]:
        name = project_name(cwd)
        if name is None:
            return None
        return self._names.setdefault(name.lower(), name)


class _FileCtx:
    """Per-file parse state: pending rows and the open turn of a main transcript."""

    def __init__(self, ing: Ingestor, rel: str, is_sub: bool, file_sid: Optional[str]):
        self.ing = ing
        self.is_sub = is_sub
        self.file_sid = file_sid
        self.calls = []
        self.titles = []
        self.sessions = []
        self.n_calls = 0
        self.seen = set()
        self.turn = None
        if not is_sub and file_sid:
            row = ing.conn.execute(
                "SELECT uuid, ts_ms, day, hour, project, duration_ms, src, model FROM turns WHERE session_id=? "
                "ORDER BY ts_ms DESC LIMIT 1", (file_sid,)).fetchone()
            if row:
                self.turn = {"uuid": row[0], "start": row[1], "last": row[1] + (row[5] if row[6] == "span" else 0),
                             "rec": row[6] == "record", "dur": row[5], "dirty": False, "model": row[7],
                             "sid": file_sid, "day": row[2], "hour": row[3], "project": row[4]}
        self.turn_rows = []

    def line(self, raw: bytes) -> None:
        if b'"type":"assistant"' in raw and b'"usage"' in raw:
            self._assistant(raw)
        elif self.is_sub:
            return
        elif b'"type":"user"' in raw and b'"tool_use_id"' not in raw:
            self._user(raw)
        elif b'"turn_duration"' in raw:
            self._turn_duration(raw)
        elif raw.startswith((b'{"type":"custom-title"', b'{"type":"ai-title"')):
            self._title(raw)

    def _assistant(self, raw: bytes) -> None:
        try:
            e = json.loads(raw)
        except ValueError:
            return
        msg = e.get("message")
        if e.get("type") != "assistant" or not isinstance(msg, dict) or e.get("isApiErrorMessage"):
            return
        u, mid, model = msg.get("usage"), msg.get("id"), msg.get("model")
        if not isinstance(u, dict) or not mid or not model or model == "<synthetic>":
            return
        ts = _epoch_ms(e.get("timestamp") or "")
        if ts is None:
            return
        day, hour = _local(ts)
        sid = self.file_sid if self.is_sub else (e.get("sessionId") or self.file_sid)
        cwd = e.get("cwd")
        project = self.ing._project(cwd)
        cc = int(u.get("cache_creation_input_tokens") or 0)
        br = u.get("cache_creation")
        w1 = int(br.get("ephemeral_1h_input_tokens") or 0) if isinstance(br, dict) else 0
        w5 = max(cc - w1, 0)  # no breakdown means the default 5-minute TTL
        web = int((u.get("server_tool_use") or {}).get("web_search_requests") or 0)
        self.calls.append((mid, ts, day, hour, sid, e.get("agentId"), int(self.is_sub or bool(e.get("isSidechain"))),
                           project, cwd, model,
                           u.get("speed"), int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                           int(u.get("cache_read_input_tokens") or 0), w5, w1, web))
        main = not self.is_sub and not e.get("isSidechain")
        if sid and sid not in self.ing._known_sessions and sid not in self.seen:
            self.seen.add(sid)
            if main and cwd and e.get("entrypoint"):
                self.ing._known_sessions.add(sid)
            # Main-thread records name the session's entrypoint and folder; subagent ones only ensure the row.
            self.sessions.append((sid, e.get("entrypoint"), cwd, project) if main else (sid, None, None, None))
        t = self.turn
        if main and t and ts >= t["start"] and (ts > t["last"] or not t["model"]):
            t["last"], t["model"], t["dirty"] = max(ts, t["last"]), model, True

    def _user(self, raw: bytes) -> None:
        try:
            e = json.loads(raw)
        except ValueError:
            return
        if e.get("type") != "user" or not e.get("uuid") or not _is_prompt(e):
            return
        ts = _epoch_ms(e.get("timestamp") or "")
        if ts is None:
            return
        self._close_turn()
        day, hour = _local(ts)
        self.turn = {"uuid": e["uuid"], "start": ts, "last": ts, "rec": False, "dur": 0, "dirty": True, "model": None,
                     "sid": e.get("sessionId") or self.file_sid, "day": day, "hour": hour,
                     "project": self.ing._project(e.get("cwd"))}

    def _turn_duration(self, raw: bytes) -> None:
        try:
            e = json.loads(raw)
        except ValueError:
            return
        if e.get("type") != "system" or e.get("subtype") != "turn_duration":
            return
        dur = int(e.get("durationMs") or 0)
        t = self.turn
        if t is None or t["rec"]:
            # No prompt seen for this turn (or a second record): keep it as its own turn.
            ts = _epoch_ms(e.get("timestamp") or "")
            if ts is None or not e.get("uuid"):
                return
            self._close_turn()
            day, hour = _local(ts - dur)
            self.turn = {"uuid": e["uuid"], "start": ts - dur, "last": ts, "rec": True, "dur": dur, "dirty": True,
                         "model": None, "sid": e.get("sessionId") or self.file_sid, "day": day, "hour": hour,
                         "project": self.ing._project(e.get("cwd"))}
            return
        t["rec"], t["dur"], t["dirty"] = True, dur, True

    def _title(self, raw: bytes) -> None:
        try:
            e = json.loads(raw)
        except ValueError:
            return
        custom = e.get("type") == "custom-title"
        title = (e.get("customTitle") if custom else e.get("aiTitle")) or ""
        sid = e.get("sessionId") or self.file_sid
        if sid and title.strip():
            self.titles.append((sid, title.strip()[:120], "custom" if custom else "ai"))

    def _close_turn(self) -> None:
        t = self.turn
        if t and t["dirty"]:
            dur = t["dur"] if t["rec"] else min(t["last"] - t["start"], SPAN_CAP_MS)
            self.turn_rows.append((t["uuid"], t["sid"], t["start"], t["day"], t["hour"], t["project"], dur,
                                   "record" if t["rec"] else "span", t["model"]))
            t["dirty"] = False

    def flush(self, rel: str, size: int, mtime: int, offset: int, final: bool) -> None:
        if final:
            self._close_turn()
        conn = self.ing.conn
        with conn:
            if self.sessions:
                conn.executemany(_UPSERT_SESSION, self.sessions)
            if self.calls:
                conn.executemany(_UPSERT_CALL, self.calls)
                # An earlier copy can move a call back across midnight, so the next day is touched too.
                days = {c[2] for c in self.calls}
                days |= {_next_day(d) for d in days}
                conn.executemany("INSERT OR IGNORE INTO dirty_days VALUES (?)", [(d,) for d in days])
            if self.turn_rows:
                conn.executemany(
                    "INSERT INTO turns(uuid, session_id, ts_ms, day, hour, project, duration_ms, src, model) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET duration_ms=excluded.duration_ms, "
                    "src=excluded.src, model=coalesce(excluded.model, model)", self.turn_rows)
            for sid, title, src in self.titles:
                conn.execute(
                    "INSERT INTO sessions(session_id, title, title_src) VALUES (?,?,?) ON CONFLICT(session_id) DO "
                    "UPDATE SET title = CASE WHEN excluded.title_src = 'custom' OR coalesce(title_src, '') <> 'custom' "
                    "THEN excluded.title ELSE title END, title_src = CASE WHEN excluded.title_src = 'custom' "
                    "THEN 'custom' ELSE coalesce(title_src, 'ai') END", (sid, title, src))
            conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (rel, size, mtime, offset))
        self.n_calls += len(self.calls)
        self.calls, self.turn_rows, self.titles, self.sessions = [], [], [], []
