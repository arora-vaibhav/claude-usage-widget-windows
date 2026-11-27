"""Incremental transcript ingest: ~/.claude/projects/**/*.jsonl into the store.

Every API call is stored once per ``message.id``. Claude Code writes one
message on several lines while it streams, and early lines can carry a
partial ``output_tokens``, so each usage field keeps the maximum seen across
the message's lines (in any file) and the timestamp keeps the earliest; the
local day and hour follow that timestamp. Subagent and workflow transcripts
(``<session>/subagents/**``) are included and belong to the session their
records name (a subagent file can be copied under another session's folder).
A resumed or forked session starts with a copy of its parent's records, same
timestamps and all; on such a tie the file created first keeps the call, so
files are read in creation order (main transcripts before subagent files) and
a fresh index matches what the live app built. A session's calls and turns
all count toward the project of its first working directory (records follow
the shell into subfolders).

``usage.iterations`` is the per-attempt bill. After a server-side fallback the
top-level usage is only the attempt that produced the message (the last
entry); otherwise it is the sum of the ``message`` entries, and advisor or
compaction entries come on top. Every entry the top-level usage does not cover
gets its own row (``msg_id#n``) at its own model's rates. A refusal before any
output is billed only in some categories (``pricing.BILLED_REFUSALS``); in the
others the call is kept with zero tokens.

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
from datetime import datetime
from typing import Callable, Optional

from claude_usage import pricing, store

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
    -- early streamed lines can lack usage.speed; the fullest line names the price tier
    speed = CASE WHEN speed IS NULL OR excluded.output > output THEN coalesce(excluded.speed, speed) ELSE speed END,
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


def _born(st: os.stat_result) -> float:
    return getattr(st, "st_birthtime", st.st_ctime)  # st_ctime is the creation time on Windows before 3.12


def _order(rel: str, born: float) -> tuple:
    """Read order: main transcripts first (they name a session's project; a rewritten one can be newer than
    its subagent files), each kind in creation order so a forked session's copied calls stay with the
    session that made them (the upsert keeps the first reader on a timestamp tie)."""
    return "subagents" in rel.split(os.sep), born, rel


def _local(ms: int) -> tuple:
    d = datetime.fromtimestamp(ms / 1000)
    return d.strftime("%Y-%m-%d"), d.hour


def _tokens(u: dict) -> tuple:
    """(input, output, cache read, cache write total, 1-hour write) of a usage dict or one of its iterations."""
    br = u.get("cache_creation")
    # Thinking is part of output_tokens; an interrupted stream can leave output at 0 with thinking counted.
    out = max(int(u.get("output_tokens") or 0), int((u.get("output_tokens_details") or {}).get("thinking_tokens") or 0))
    return (int(u.get("input_tokens") or 0), out, int(u.get("cache_read_input_tokens") or 0),
            int(u.get("cache_creation_input_tokens") or 0),
            int(br.get("ephemeral_1h_input_tokens") or 0) if isinstance(br, dict) else 0)


def _split(t: tuple) -> tuple:
    """_tokens() as stored: input, output, cache read, 5-minute write, 1-hour write."""
    i, o, r, cc, w1 = t
    return i, o, r, max(cc - w1, 0), w1  # no breakdown means the default 5-minute TTL


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
        self._cwd_names = {}
        self._sess_proj = {}
        self._dirs = {}  # dir -> mtime_ns as of the last look
        self._hot_dirs = set()
        self._hot_files = set()
        self._last_full = None
        self._known_sessions = set()
        self._open_turn = {}  # main transcript -> its open turn, so a turn read over two ticks stays one turn
        self._looked_ns = 0  # wall clock of the last directory look
        store.rollup(conn)  # days left dirty by an interrupted run
        for (name,) in conn.execute("SELECT DISTINCT project FROM rollup_day"):
            self._names.setdefault(name.lower(), name)

    # ------------------------------------------------------------ public

    def index(self, progress: Optional[Callable[[int, int], None]] = None,
              cancel: Optional[Callable[[], bool]] = None) -> dict:
        """Walk the whole tree and read every new or changed transcript."""
        t0 = time.perf_counter()
        self._looked_ns = time.time_ns()
        found = self._walk()
        todo = sorted((p for p, st in found.items() if self._changed(p, st[:2])), key=lambda p: _order(p, found[p][2]))
        calls = done = 0
        cancelled = False
        if progress:
            progress(0, len(todo))
        for rel in todo:
            if cancel and cancel():
                cancelled = True
                break
            calls += self._read(rel, *found[rel][:2], cancel=cancel)
            done += 1
            if progress:
                progress(done, len(todo))
            # A file that could not be opened (deleted by cleanup, locked) is not recorded; retried next pass.
            if self._files.get(rel, found[rel])[0] != found[rel][0] and cancel and cancel():
                cancelled = True  # stopped inside this file
                break
        self._last_full = self.clock()
        self._pick_hot(found)
        if store.rollup(self.conn):
            store.update_coverage(self.conn)
        return {"files": len(found), "read": done, "todo": len(todo), "calls": calls,
                "cancelled": cancelled, "ms": round((time.perf_counter() - t0) * 1000, 1), "full": True}

    def tail(self) -> dict:
        """Read what changed since the last call; cheap when nothing did."""
        if self._last_full is None or self.clock() - self._last_full >= FULL_WALK_SECONDS:
            return self.index()
        t0 = time.perf_counter()
        # Directory mtimes move in clock ticks, so a file created just after our last look can leave the mtime
        # unchanged: a directory that changed shortly before (or since) that look is scanned again.
        recent = self._looked_ns - 2_000_000_000
        self._looked_ns = time.time_ns()
        for d in list(self._hot_dirs):
            try:
                m = os.stat(d).st_mtime_ns
            except OSError:
                self._hot_dirs.discard(d)
                continue
            if m != self._dirs.get(d) or m > recent:
                self._dirs[d] = m
                self._scan_dir(d)
        changed = []
        for rel in list(self._hot_files):
            try:
                st = os.stat(os.path.join(self.root, rel))
            except OSError:
                self._hot_files.discard(rel)
                continue
            if self._changed(rel, (st.st_size, st.st_mtime_ns)):
                changed.append((_order(rel, _born(st)), rel, st.st_size, st.st_mtime_ns))
        calls = 0
        for _o, rel, size, mtime in sorted(changed):
            calls += self._read(rel, size, mtime)
        store.rollup(self.conn)
        return {"files": len(self._files), "read": len(changed), "calls": calls,
                "ms": round((time.perf_counter() - t0) * 1000, 2), "full": False}

    # ------------------------------------------------------------ walking

    def _walk(self) -> dict:
        found = {}
        self._dirs.clear()
        cut = len(self.root) + 1
        try:
            self._dirs[self.root] = os.stat(self.root).st_mtime_ns
        except OSError:
            return found
        stack = [self.root]
        while stack:
            try:
                it = os.scandir(stack.pop())
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        # On Windows the scandir entry carries size and mtime, so these are not extra syscalls.
                        if e.is_dir(follow_symlinks=False):
                            self._dirs[e.path] = e.stat(follow_symlinks=False).st_mtime_ns
                            stack.append(e.path)
                        elif e.name.endswith(".jsonl"):
                            st = e.stat(follow_symlinks=False)
                            found[e.path[cut:]] = (st.st_size, st.st_mtime_ns, _born(st))
                    except OSError:
                        continue
        return found

    def _pick_hot(self, found: dict) -> None:
        cutoff = (self.clock() - HOT_SECONDS) * 1e9
        self._hot_files = {p for p, (_s, m, _b) in found.items() if m >= cutoff}
        # Live sessions add subagent files under <slug>/<session>/ at any time.
        live = {p[:-6] for p in self._hot_files if p.count(os.sep) == 1}
        cut = len(self.root) + 1
        self._hot_dirs = set()
        for d, m in self._dirs.items():
            parts = d[cut:].split(os.sep, 2)
            if len(parts) == 1 or m >= cutoff or os.sep.join(parts[:2]) in live:
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
                        self._hot_files.add(e.path[len(self.root) + 1:])
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
        done = True  # False when we stop before the end, so the next pass resumes at pos
        pos = offset
        with fh:
            try:
                if offset:
                    # A stored offset always ends a line, so anything else means the file was rewritten (a bulk
                    # import) and is read again. shortcut: a rewrite with a line end at that byte is missed; store a
                    # fingerprint of the last line if that ever matters.
                    fh.seek(offset - 1)
                    if fh.read(1) != b"\n":
                        pos = offset = 0
                if offset and rel in self._open_turn:
                    ctx.turn = self._open_turn[rel]  # the stored turns miss one whose copy belongs to another session
                fh.seek(offset)
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
                    ctx.line(raw)
                    pos += len(raw)
                    if len(ctx.calls) >= _BATCH:
                        ctx.flush(rel, pos, mtime, pos, final=False)
                        if cancel and cancel():
                            done = False
                            break
            except (MemoryError, OSError):
                # Low memory or a failed read (a locked or vanished file): keep what was read, retry from pos.
                done = False
        seen = size if done else pos
        ctx.flush(rel, seen, mtime, pos, final=True)
        self._files[rel] = (seen, mtime, pos)
        if not is_sub:
            self._open_turn[rel] = ctx.turn
        return ctx.n_calls

    def _project(self, cwd: Optional[str]) -> Optional[str]:
        name = self._cwd_names.get(cwd)
        if name is None and cwd:
            name = project_name(cwd)
            name = self._cwd_names[cwd] = self._names.setdefault(name.lower(), name)
        return name

    def _session_project(self, sid: str) -> Optional[str]:
        name = self._sess_proj.get(sid)
        if name is None:
            row = self.conn.execute("SELECT project FROM sessions WHERE session_id=?", (sid,)).fetchone()
            if row and row[0]:
                name = self._sess_proj[sid] = row[0]
        return name


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
        self.project = None
        self.looked = set()
        self.billed = {}  # msg id -> models whose decline before any output is billed
        self.unbilled = []  # msg ids refused before any output in a category that is not billed
        if not is_sub and file_sid:
            row = ing.conn.execute(
                "SELECT uuid, ts_ms, day, hour, project, duration_ms, src, model FROM turns WHERE session_id=? "
                "ORDER BY ts_ms DESC LIMIT 1", (file_sid,)).fetchone()
            if row:
                self.turn = {"uuid": row[0], "start": row[1], "last": row[1] + (row[5] if row[6] == "span" else 0),
                             "rec": row[6] == "record", "dur": row[5], "dirty": False, "model": row[7],
                             "sid": file_sid, "day": row[2], "hour": row[3], "project": row[4]}
        self.turn_rows = []
        self.copied_turns = []  # turns of another session copied into this file: inserted, never updated

    def _proj(self, cwd: Optional[str], sid: Optional[str]) -> Optional[str]:
        # Records follow the shell into subfolders; a session (with its subagents) belongs where it started.
        if self.project is None and sid and sid not in self.looked:
            self.looked.add(sid)
            self.project = self.ing._session_project(sid)
        if self.project is None and cwd:
            self.project = self.ing._project(cwd)
            if sid:
                self.ing._sess_proj.setdefault(sid, self.project)
        return self.project

    def line(self, raw: bytes) -> None:
        try:
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
        except (AttributeError, TypeError, ValueError, OverflowError, RecursionError):
            pass  # a record of an unexpected shape, a 1e999 count or absurd nesting must not stall the file

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
        sid = e.get("sessionId") or self.file_sid
        cwd = e.get("cwd")
        project = self._proj(cwd, sid)
        web = int((u.get("server_tool_use") or {}).get("web_search_requests") or 0)
        if b'"type":"fallback"' in raw:
            # shortcut: kept per file read; a billed decline whose fallback block was read on an earlier tick
            # than its final usage line is missed. Upgrade if a real one shows up (none so far).
            for b in msg.get("content") or ():
                if isinstance(b, dict) and b.get("type") == "fallback" and (
                        (b.get("trigger") or {}).get("category") in pricing.BILLED_REFUSALS):
                    self.billed.setdefault(mid, set()).add(pricing.normalize((b.get("from") or {}).get("model")))
        its = [x for x in u.get("iterations") or () if isinstance(x, dict)]
        fallback = any(x.get("type") == "fallback_message" for x in its)
        own = {len(its) - 1} if fallback else {n for n, x in enumerate(its) if x.get("type") in (None, "message")}
        tok = _tokens(u)
        if own:
            # Some streamed lines carry zeroed top-level usage while their attempts hold the counts.
            tok = tuple(map(max, tok, map(sum, zip(*(_tokens(its[n]) for n in own)))))
        speed = u.get("speed")
        if u.get("inference_geo") == "us":
            speed = (speed or "standard") + "+us"  # billed at 1.1x; pricing reads the suffix
        row = (ts, day, hour, sid, e.get("agentId"), int(self.is_sub or bool(e.get("isSidechain"))), project, cwd)
        self.calls.append((mid, *row, model, speed, *_split(tok), web))
        if msg.get("stop_reason") == "refusal" and not fallback and not tok[1] and (
                (msg.get("stop_details") or {}).get("category") not in pricing.BILLED_REFUSALS):
            self.unbilled.append(mid)
        for n, x in enumerate(its):
            t, xm = _tokens(x), x.get("model") or model
            # A fallback attempt declined before any output is billed only in some refusal categories.
            unbilled = fallback and not t[1] and pricing.normalize(xm) not in self.billed.get(mid, ())
            if n in own or not any(t) or unbilled:
                continue
            self.calls.append((f"{mid}#{n}", *row, xm, speed, *_split(t), 0))
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
                     "project": self._proj(e.get("cwd"), e.get("sessionId") or self.file_sid)}

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
                         "project": self._proj(e.get("cwd"), e.get("sessionId") or self.file_sid)}
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
            rows = self.turn_rows if t["sid"] == self.file_sid else self.copied_turns
            rows.append((t["uuid"], t["sid"], t["start"], t["day"], t["hour"], t["project"], dur,
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
                # A line can raise or move a call already stored on another day (its earliest line or an
                # earlier copy set the day, maybe across midnight), so that day is rebuilt too.
                days = {c[2] for c in self.calls}
                ids = list({c[0] for c in self.calls})
                for i in range(0, len(ids), 500):
                    chunk = ids[i:i + 500]
                    days.update(d for (d,) in conn.execute(
                        f"SELECT day FROM calls WHERE msg_id IN ({','.join('?' * len(chunk))})", chunk))
                conn.executemany(_UPSERT_CALL, self.calls)
                # Earlier streamed lines already stored the prompt's tokens; the max upsert cannot lower them.
                conn.executemany("UPDATE calls SET input=0, output=0, cache_read=0, cache_write_5m=0, cache_write_1h=0, "
                                 "web_search=0 WHERE msg_id=? AND output=0", [(m,) for m in self.unbilled])
                conn.executemany("INSERT OR IGNORE INTO dirty_days VALUES (?)", [(d,) for d in days])
            # Only a session's own main transcript sets a turn's duration: a resumed or forked file holds
            # copies of earlier turns (under its own session id or the original's) that end sooner.
            if self.turn_rows:
                conn.executemany(
                    "INSERT INTO turns(uuid, session_id, ts_ms, day, hour, project, duration_ms, src, model) "
                    "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(uuid) DO UPDATE SET duration_ms=excluded.duration_ms, "
                    "src=excluded.src, model=coalesce(excluded.model, model) "
                    "WHERE session_id IS excluded.session_id", self.turn_rows)
            if self.copied_turns:
                conn.executemany("INSERT OR IGNORE INTO turns(uuid, session_id, ts_ms, day, hour, project, duration_ms, "
                                 "src, model) VALUES (?,?,?,?,?,?,?,?,?)", self.copied_turns)
            for sid, title, src in self.titles:
                conn.execute(
                    "INSERT INTO sessions(session_id, title, title_src) VALUES (?,?,?) ON CONFLICT(session_id) DO "
                    "UPDATE SET title = CASE WHEN excluded.title_src = 'custom' OR coalesce(title_src, '') <> 'custom' "
                    "THEN excluded.title ELSE title END, title_src = CASE WHEN excluded.title_src = 'custom' "
                    "THEN 'custom' ELSE coalesce(title_src, 'ai') END", (sid, title, src))
            conn.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?)", (rel, size, mtime, offset))
        self.n_calls += len(self.calls)
        self.calls, self.turn_rows, self.copied_turns, self.titles, self.sessions, self.unbilled = [], [], [], [], [], []
