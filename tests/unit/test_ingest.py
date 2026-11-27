import json
import os
import time
from datetime import datetime, timezone

import pytest

from claude_usage import ingest, pricing, store


def _iso(local_dt: datetime) -> str:
    """A naive local datetime as the UTC 'Z' timestamp Claude Code writes."""
    return local_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


T0 = datetime(2026, 11, 20, 14, 0, 0)


def _ts(minutes: float) -> str:
    return _iso(datetime.fromtimestamp(T0.timestamp() + minutes * 60))


def asst(mid, ts, out=10, inp=1, cr=0, w5=0, w1=0, model="claude-opus-5-5", sid="s1",
         cwd=r"C:\work\acme-api", **extra):
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr,
             "cache_creation_input_tokens": w5 + w1,
             "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
             "speed": "standard"}
    rec = {"parentUuid": None, "isSidechain": False, "type": "assistant",
           "message": {"id": mid, "model": model, "role": "assistant", "usage": usage, "content": []},
           "uuid": "u-" + mid + ts, "timestamp": ts, "sessionId": sid, "cwd": cwd, "entrypoint": "cli"}
    rec.update(extra)
    return json.dumps(rec, separators=(",", ":"))


def prompt(uuid, ts, sid="s1", cwd=r"C:\work\acme-api", text="do the thing"):
    return json.dumps({"parentUuid": None, "isSidechain": False, "type": "user",
                       "message": {"role": "user", "content": text}, "uuid": uuid, "timestamp": ts,
                       "sessionId": sid, "cwd": cwd, "entrypoint": "cli"}, separators=(",", ":"))


def turn_duration(uuid, ts, ms, sid="s1"):
    return json.dumps({"parentUuid": None, "isSidechain": False, "type": "system", "subtype": "turn_duration",
                       "durationMs": ms, "messageCount": 3, "timestamp": ts, "uuid": uuid, "sessionId": sid,
                       "cwd": r"C:\work\acme-api", "entrypoint": "cli"}, separators=(",", ":"))


def write(root, rel, lines, mode="w", newline=True):
    path = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8", newline="") as fh:
        fh.write("\n".join(lines) + ("\n" if newline else ""))
    return path


@pytest.fixture
def env(tmp_path):
    root = str(tmp_path / "projects")
    os.makedirs(root)
    conn = store.connect(str(tmp_path / "usage.db"))
    return root, conn


def rows(conn, sql="SELECT msg_id, output, ts_ms, day, session_id, is_subagent FROM calls ORDER BY msg_id"):
    return conn.execute(sql).fetchall()


def test_streamed_message_keeps_max_usage_and_earliest_time(env):
    root, conn = env
    # Claude Code writes one message on several lines; early lines carry a partial output count.
    write(root, "C--work-acme-api/s1.jsonl", [
        asst("m1", _ts(1), out=3, cr=100),
        asst("m1", _ts(0), out=120, cr=100),
        asst("m1", _ts(2), out=267, cr=90, w1=50),
    ])
    ingest.Ingestor(conn, root).index()
    (mid, out, ts, day, sid, sub), = rows(conn)
    assert (mid, out, sid, sub) == ("m1", 267, "s1", 0)
    assert ts == int(datetime.fromisoformat(_ts(0).replace("Z", "+00:00")).timestamp() * 1000)
    assert conn.execute("SELECT cache_read, cache_write_1h FROM calls").fetchone() == (100, 50)


def test_same_id_in_resumed_session_file_counted_once(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0), out=50)])
    write(root, "C--work-acme-api/s2.jsonl", [asst("m1", _ts(0), out=50, sid="s2"), asst("m2", _ts(5), sid="s2")])
    ingest.Ingestor(conn, root).index()
    assert [r[0] for r in rows(conn)] == ["m1", "m2"]
    assert rows(conn)[0][4] == "s1"  # the first copy keeps it on a timestamp tie


def test_nested_subagent_file_belongs_to_parent_session(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    write(root, "C--work-acme-api/s1/subagents/workflows/wf_1/agent-a1.jsonl",
          [asst("m2", _ts(1), sid="s1", agentId="a1", isSidechain=True)])
    ingest.Ingestor(conn, root).index()
    assert conn.execute("SELECT session_id, is_subagent, agent_id FROM calls WHERE msg_id='m2'").fetchone() == \
        ("s1", 1, "a1")


def test_partial_last_line_waits_for_the_rest(env):
    root, conn = env
    line = asst("m1", _ts(0), out=40)
    path = write(root, "C--work-acme-api/s1.jsonl", [asst("m0", _ts(0)), line[:50]], newline=False)
    ing = ingest.Ingestor(conn, root)
    ing.index()
    assert [r[0] for r in rows(conn)] == ["m0"]
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(line[50:] + "\n")
    ing.tail()
    assert [r[0] for r in rows(conn)] == ["m0", "m1"]


def test_file_that_shrank_is_read_again_and_history_is_kept(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0)), asst("m2", _ts(1)), asst("m3", _ts(2))])
    ing = ingest.Ingestor(conn, root)
    ing.index()
    write(root, "C--work-acme-api/s1.jsonl", [asst("m9", _ts(3))])
    ing.index()
    assert [r[0] for r in rows(conn)] == ["m1", "m2", "m3", "m9"]


def test_day_and_hour_come_from_the_call_not_from_now(env):
    root, conn = env
    before = _iso(datetime(2026, 11, 20, 23, 59, 30))
    after = _iso(datetime(2026, 11, 21, 0, 0, 30))
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", before), asst("m2", after)])
    later = datetime(2026, 11, 23, 9, 0).timestamp()  # indexed days later
    ingest.Ingestor(conn, root, clock=lambda: later).index()
    assert rows(conn, "SELECT day, hour FROM calls ORDER BY msg_id") == [("2026-11-20", 23), ("2026-11-21", 0)]


def test_earlier_copy_moves_the_call_to_its_day(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _iso(datetime(2026, 11, 21, 0, 0, 5)))])
    write(root, "C--work-acme-api/s2.jsonl", [asst("m1", _iso(datetime(2026, 11, 20, 23, 59, 59)), sid="s2")])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT day, hour, session_id FROM calls") == [("2026-11-20", 23, "s2")]


def test_skips_synthetic_and_api_error_rows(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [
        asst("m1", _ts(0), model="<synthetic>"),
        asst("m2", _ts(0), isApiErrorMessage=True),
        asst("m3", _ts(0)),
    ])
    ingest.Ingestor(conn, root).index()
    assert [r[0] for r in rows(conn)] == ["m3"]


def test_overlong_line_is_skipped(env, monkeypatch):
    root, conn = env
    monkeypatch.setattr(ingest, "MAX_LINE", 1000)
    big = asst("big", _ts(0), padding="x" * 5000)
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0)), big, asst("m2", _ts(1))])
    ingest.Ingestor(conn, root).index()
    assert [r[0] for r in rows(conn)] == ["m1", "m2"]


def test_turns_use_turn_duration_else_capped_span(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [
        prompt("p1", _ts(0)),
        asst("m1", _ts(1)),
        asst("m2", _ts(4)),
        turn_duration("d1", _ts(5), 300_000),
        prompt("p2", _ts(10)),
        asst("m3", _ts(12)),
        prompt("p3", _ts(100)),
        asst("m4", _ts(180)),  # 80 minutes later: capped at 30
        json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}, "uuid": "tr", "timestamp": _ts(181),
            "sessionId": "s1"}, separators=(",", ":")),
    ])
    ingest.Ingestor(conn, root).index()
    got = rows(conn, "SELECT uuid, duration_ms, src FROM turns ORDER BY ts_ms")
    assert got == [("p1", 300_000, "record"), ("p2", 120_000, "span"), ("p3", 30 * 60_000, "span")]


def test_open_turn_continues_on_the_next_tick(env):
    root, conn = env
    path = write(root, "C--work-acme-api/s1.jsonl", [prompt("p1", _ts(0)), asst("m1", _ts(1))])
    ing = ingest.Ingestor(conn, root)
    ing.index()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(asst("m2", _ts(6)) + "\n")
    ing.tail()
    assert rows(conn, "SELECT uuid, duration_ms FROM turns") == [("p1", 6 * 60_000)]


def test_custom_title_wins_over_ai_title(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [
        asst("m1", _ts(0)),
        json.dumps({"type": "ai-title", "aiTitle": "Fix login", "sessionId": "s1"}, separators=(",", ":")),
        json.dumps({"type": "custom-title", "customTitle": "Login work", "sessionId": "s1"}, separators=(",", ":")),
        json.dumps({"type": "ai-title", "aiTitle": "Later ai title", "sessionId": "s1"}, separators=(",", ":")),
    ])
    ingest.Ingestor(conn, root).index()
    assert conn.execute("SELECT title, title_src, entrypoint, project FROM sessions").fetchone() == \
        ("Login work", "custom", "cli", "acme-api")


def test_tail_finds_a_new_subagent_file_without_a_full_walk(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    ing = ingest.Ingestor(conn, root)
    ing.index()
    write(root, "C--work-acme-api/s1/subagents/agent-a1.jsonl", [asst("m2", _ts(1), agentId="a1")])
    result = ing.tail()
    assert result["full"] is False and result["calls"] == 1


def test_idle_tail_reads_nothing(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    ing = ingest.Ingestor(conn, root)
    ing.index()
    assert ing.tail()["read"] == 0


def test_index_reports_progress_and_can_be_cancelled(env):
    root, conn = env
    for i in range(3):
        write(root, f"C--work-acme-api/s{i}.jsonl", [asst(f"m{i}", _ts(i), sid=f"s{i}")])
    seen = []
    result = ingest.Ingestor(conn, root).index(progress=lambda d, t: seen.append((d, t)), cancel=lambda: len(seen) > 2)
    assert seen[0] == (0, 3) and result["cancelled"] and result["read"] == 2
    assert ingest.Ingestor(conn, root).index()["read"] == 1  # picks up where it stopped


@pytest.mark.parametrize("cwd,name", [
    (r"C:\Projects\acme-api", "acme-api"),
    ("C:/Projects/acme-api/", "acme-api"),
    (r"C:\Projects\acme-api\.worktrees\feature-x", "acme-api"),
    (r"C:\Projects\acme-api\.claude\worktrees\quirky-bohr", "acme-api"),
    (r"C:\Users\dev", "Home"),
    (r"C:\Users\dev\AppData\Local\Temp\scratch\x", "Temporary folders"),
    (None, None),
])
def test_project_name(cwd, name):
    assert ingest.project_name(cwd, home=r"C:\Users\dev", temp=r"C:\Users\dev\AppData\Local\Temp") == name


def test_project_names_do_not_split_by_case(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0), cwd=r"C:\work\Acme-Api")])
    write(root, "c--work-acme-api/s2.jsonl", [asst("m2", _ts(1), sid="s2", cwd="c:\\work\\acme-api\\")])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT DISTINCT project FROM calls") == [("Acme-Api",)]


def test_a_session_keeps_the_project_it_started_in(env):
    root, conn = env
    # The shell moves into a subfolder mid-session; a subagent works in a worktree of another repo.
    write(root, "C--work-acme-api/s1.jsonl", [
        prompt("p1", _ts(0)),
        asst("m1", _ts(1)),
        asst("m2", _ts(2), cwd=r"C:\work\acme-api\src\billing"),
    ])
    write(root, "C--work-acme-api/s1/subagents/agent-a1.jsonl",
          [asst("m3", _ts(3), agentId="a1", cwd=r"C:\work\web-console\.claude\worktrees\x")])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT DISTINCT project FROM calls") == [("acme-api",)]
    assert rows(conn, "SELECT project FROM turns") == [("acme-api",)]
    assert rows(conn, "SELECT project FROM sessions") == [("acme-api",)]


def test_cancel_inside_a_file_resumes_where_it_stopped(env, monkeypatch):
    root, conn = env
    monkeypatch.setattr(ingest, "_BATCH", 2)
    write(root, "C--work-acme-api/s1.jsonl", [asst(f"m{i}", _ts(i)) for i in range(6)])
    first = ingest.Ingestor(conn, root).index(cancel=lambda: len(rows(conn)) >= 2)
    assert first["cancelled"] and len(rows(conn)) == 2
    ingest.Ingestor(conn, root).index()  # a fresh start (as after a restart) finds the unread rest
    assert len(rows(conn)) == 6


def test_memory_error_keeps_what_was_read_and_retries(env, monkeypatch):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst(f"m{i}", _ts(i)) for i in range(4)])
    real, calls = ingest._FileCtx.line, []

    def flaky(self, raw):
        calls.append(1)
        if len(calls) == 3:
            raise MemoryError
        real(self, raw)

    monkeypatch.setattr(ingest._FileCtx, "line", flaky)
    ing = ingest.Ingestor(conn, root)
    ing.index()
    assert [r[0] for r in rows(conn)] == ["m0", "m1"]
    ing.index()
    assert [r[0] for r in rows(conn)] == ["m0", "m1", "m2", "m3"]


def test_odd_shapes_are_skipped_not_fatal(env):
    root, conn = env
    odd = json.loads(asst("m1", _ts(0)))
    odd["message"]["usage"]["output_tokens_details"] = ["not", "a", "dict"]
    odd["message"]["usage"]["server_tool_use"] = 7
    write(root, "C--work-acme-api/s1.jsonl", [json.dumps(odd, separators=(",", ":")), asst("m2", _ts(1)),
                                              '{"type":"custom-title","customTitle":5,"sessionId":"s1"}'])
    ingest.Ingestor(conn, root).index()
    assert [r[0] for r in rows(conn)] == ["m2"]


def test_interrupted_message_counts_its_thinking_as_output(env):
    root, conn = env
    rec = json.loads(asst("m1", _ts(0), out=0))
    rec["message"]["usage"]["output_tokens_details"] = {"thinking_tokens": 900}
    write(root, "C--work-acme-api/s1.jsonl", [json.dumps(rec, separators=(",", ":"))])
    ingest.Ingestor(conn, root).index()
    assert rows(conn)[0][1] == 900


def test_file_deleted_between_walk_and_read_is_skipped(env, monkeypatch):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    gone = write(root, "C--work-acme-api/s2.jsonl", [asst("m2", _ts(1), sid="s2")])
    ing = ingest.Ingestor(conn, root)
    walk = ing._walk

    def walk_then_cleanup():
        found = walk()
        os.remove(gone)  # Claude Code's cleanup can delete old transcripts during the first long index
        return found

    monkeypatch.setattr(ing, "_walk", walk_then_cleanup)
    result = ing.index(cancel=lambda: False)
    assert not result["cancelled"] and [r[0] for r in rows(conn)] == ["m1"]


def test_streamed_message_across_midnight_read_in_two_ticks(env):
    root, conn = env
    path = write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _iso(datetime(2026, 11, 20, 23, 59, 58)), out=5)])
    ing = ingest.Ingestor(conn, root, clock=lambda: datetime(2026, 11, 21, 0, 0, 10).timestamp())
    ing.index()
    with open(path, "a", encoding="utf-8", newline="") as fh:
        fh.write(asst("m1", _iso(datetime(2026, 11, 21, 0, 0, 4)), out=500) + "\n")
    ing.tail()
    assert rows(conn, "SELECT day, output FROM calls") == [("2026-11-20", 500)]
    assert rows(conn, "SELECT day, SUM(output) FROM rollup_day GROUP BY day") == [("2026-11-20", 500)]


def test_subagent_copy_under_another_session_folder_keeps_its_session(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    agent = [asst("m2", _ts(1), agentId="a1", isSidechain=True), asst("m3", _ts(2), agentId="a1", isSidechain=True)]
    write(root, "C--work-acme-api/s1/subagents/agent-a1.jsonl", agent)
    write(root, "C--work-acme-api/s2/subagents/agent-a1.jsonl", agent)  # the same file copied under s2
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT session_id, COUNT(*) FROM calls GROUP BY 1") == [("s1", 3)]
    assert rows(conn, "SELECT session_id FROM sessions") == [("s1",)]


def test_every_attempt_of_a_fallback_response_is_billed(env):
    root, conn = env
    rec = json.loads(asst("m1", _ts(0), out=264, inp=412, model="claude-opus-4-8"))
    rec["message"]["usage"]["iterations"] = [
        {"type": "message", "model": "claude-fable-5-2", "input_tokens": 535, "output_tokens": 40},  # declined mid-stream
        {"type": "message", "model": "claude-fable-5-2", "input_tokens": 535, "output_tokens": 0},  # declined at once
        {"type": "fallback_message", "model": "claude-opus-4-8", "input_tokens": 412, "output_tokens": 264},
    ]
    write(root, "C--work-acme-api/s1.jsonl", [json.dumps(rec, separators=(",", ":"))])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT msg_id, model, input, output FROM calls ORDER BY msg_id") == [
        ("m1", "claude-opus-4-8", 412, 264), ("m1#0", "claude-fable-5-2", 535, 40)]


def test_single_attempt_fills_a_zeroed_top_level_usage(env):
    root, conn = env
    rec = json.loads(asst("m1", _ts(0), out=0, inp=0))
    rec["message"]["usage"]["iterations"] = [{"type": "message", "input_tokens": 7, "output_tokens": 90,
                                              "cache_read_input_tokens": 300, "cache_creation_input_tokens": 0}]
    write(root, "C--work-acme-api/s1.jsonl", [json.dumps(rec, separators=(",", ":"))])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT input, output, cache_read FROM calls") == [(7, 90, 300)]


def test_us_only_inference_is_kept_as_a_price_tier(env):
    root, conn = env
    rec = json.loads(asst("m1", _ts(0)))
    rec["message"]["usage"]["inference_geo"] = "us"
    write(root, "C--work-acme-api/s1.jsonl", [json.dumps(rec, separators=(",", ":")), asst("m2", _ts(1))])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT msg_id, speed FROM calls ORDER BY msg_id") == [("m1", "standard+us"), ("m2", "standard")]


def test_fork_copy_keeps_the_session_that_made_the_call(tmp_path):
    # A resumed or forked session starts with a copy of its parent's records, timestamps and all.
    orig = [asst("m1", _ts(0), sid="zz-orig")]
    fork = [asst("m1", _ts(0), sid="aa-fork"), asst("m2", _iso(datetime(2026, 11, 21, 10, 0)), sid="aa-fork")]
    got = []
    for live in (False, True):
        root = str(tmp_path / f"projects-{live}")
        conn = store.connect(str(tmp_path / f"usage-{live}.db"))
        ing = ingest.Ingestor(conn, root)
        write(root, "C--work-acme-api/zz-orig.jsonl", orig)
        if live:
            ing.index()  # the live app read the original before the fork existed
        time.sleep(0.05)  # the fork's file is created later, though its name sorts first
        write(root, "C--work-acme-api/aa-fork.jsonl", fork)
        ing.index()
        got.append(rows(conn, "SELECT day, session_id FROM rollup_day ORDER BY day"))
    assert got[0] == got[1] == [("2026-11-20", "zz-orig"), ("2026-11-21", "aa-fork")]


class _Unreadable:
    def __init__(self, fh):
        self.fh = fh

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fh.close()

    def seek(self, n):
        self.fh.seek(n)

    def readline(self, n=-1):
        raise OSError(22, "Invalid argument")

    read = readline


def test_a_read_error_does_not_hold_up_later_files(env, monkeypatch):
    root, conn = env
    for i in (1, 2, 3):
        write(root, f"C--work-acme-api/s{i}.jsonl", [asst(f"m{i}", _ts(i), sid=f"s{i}")])
    real_open = open
    monkeypatch.setattr(ingest, "open", lambda path, *a, **k: _Unreadable(real_open(path, *a, **k))
                        if path.endswith("s2.jsonl") else real_open(path, *a, **k), raising=False)
    ing = ingest.Ingestor(conn, root)
    for _ in range(3):  # what the worker does every tick
        ing.tail()
    assert [r[0] for r in rows(conn)] == ["m1", "m3"]
    monkeypatch.undo()
    ing.index()
    assert [r[0] for r in rows(conn)] == ["m1", "m2", "m3"]


def test_absurd_numbers_and_nesting_are_skipped(env):
    root, conn = env
    huge = asst("m1", _ts(0)).replace('"output_tokens":10', '"output_tokens":1e999')
    deep = '{"type":"assistant","usage":' + "[" * 100_000 + "]" * 100_000 + "}"
    write(root, "C--work-acme-api/s1.jsonl", [huge, deep, asst("m2", _ts(1))])
    ingest.Ingestor(conn, root).index()
    assert [r[0] for r in rows(conn)] == ["m2"]


def _with(line, usage=None, content=None):
    rec = json.loads(line)
    if usage is not None:
        rec["message"]["usage"] = usage
    if content is not None:
        rec["message"]["content"] = content
    return json.dumps(rec, separators=(",", ":"))


def _it(kind, inp, out, read=0, model=None):
    x = {"type": kind, "input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
         "cache_creation_input_tokens": 0}
    return dict(x, model=model) if model else x


def test_advisor_calls_are_billed_on_top_of_the_executor(env):
    # The advisor tool docs' example: top-level usage is the sum of the executor's 'message' entries.
    root, conn = env
    final = {"input_tokens": 1760, "output_tokens": 531, "cache_read_input_tokens": 412,
             "cache_creation_input_tokens": 0, "iterations": [
                 _it("message", 412, 89), _it("advisor_message", 823, 1612, model="claude-opus-5-5"),
                 _it("message", 1348, 442, 412)]}
    early = {"input_tokens": 412, "output_tokens": 5, "cache_read_input_tokens": 0}  # an earlier streamed line
    line = asst("m1", _ts(0), model="claude-sonnet-5-5")
    write(root, "C--work-acme-api/s1.jsonl", [_with(line, early), _with(line, final)])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT model, SUM(input), SUM(output), SUM(cache_read) FROM calls GROUP BY 1 ORDER BY 1") == [
        ("claude-opus-5-5", 823, 1612, 0), ("claude-sonnet-5-5", 412 + 1348, 89 + 442, 412)]


@pytest.mark.parametrize("its,top,want", [
    ([_it("compaction", 180_000, 3500), _it("message", 23_000, 1000)], (23_000, 1000), (203_000, 4500)),
    ([_it("message", 5000, 200), _it("compaction", 180_000, 3500), _it("message", 23_000, 1000)], (28_000, 1200),
     (208_000, 4700)),
])
def test_compaction_inside_a_response_is_billed_once(env, its, top, want):
    root, conn = env
    usage = {"input_tokens": top[0], "output_tokens": top[1], "iterations": its}
    write(root, "C--work-acme-api/s1.jsonl", [_with(asst("m1", _ts(0)), usage)])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT SUM(input), SUM(output) FROM calls") == [want]


def test_a_decline_before_any_output_is_billed_only_in_billed_categories(env):
    root, conn = env
    lines = []
    for mid, category in (("m1", "bio"), ("m2", "cyber")):
        line = asst(mid, _ts(0), model="claude-opus-4-8")
        block = {"type": "fallback", "from": {"model": "claude-fable-5"}, "to": {"model": "claude-opus-4-8"},
                 "trigger": {"type": "refusal", "category": category}}
        final = {"input_tokens": 412, "output_tokens": 264, "iterations": [
            _it("message", 535, 0, model="claude-fable-5"), _it("fallback_message", 412, 264, model="claude-opus-4-8")]}
        lines += [_with(line, {"input_tokens": 412, "output_tokens": 1}, [block]),
                  _with(line, final, [{"type": "text", "text": "ok"}])]
    write(root, "C--work-acme-api/s1.jsonl", lines)
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT msg_id, model, input, output FROM calls ORDER BY msg_id") == [
        ("m1", "claude-opus-4-8", 412, 264), ("m1#0", "claude-fable-5", 535, 0), ("m2", "claude-opus-4-8", 412, 264)]


def test_a_main_transcript_newer_than_its_subagent_file_still_names_the_project(env):
    root, conn = env
    write(root, "C--work-acme-api/s1/subagents/agent-a1.jsonl",
          [asst("m2", _ts(1), agentId="a1", isSidechain=True, cwd=r"C:\work\acme-api\site")])
    time.sleep(0.05)  # the main file was rewritten later (a copy, an import), so it is the newer file
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT DISTINCT project FROM calls") == [("acme-api",)]


@pytest.mark.parametrize("copy_sid", ["aa-fork", "zz-orig"])  # copies carry the fork's id, or keep the original's
def test_a_copied_turn_does_not_rewrite_the_original(env, copy_sid):
    root, conn = env
    write(root, "C--work-acme-api/zz-orig.jsonl", [prompt("p1", _ts(0), sid="zz-orig"), asst("m1", _ts(1), sid="zz-orig"),
                                                   asst("m2", _ts(5), sid="zz-orig")])
    time.sleep(0.05)  # a later file copied the turn before its last reply, then had a turn of its own
    write(root, "C--work-acme-api/aa-fork.jsonl", [prompt("p1", _ts(0), sid=copy_sid), asst("m1", _ts(1), sid=copy_sid),
                                                   prompt("p2", _ts(60), sid="aa-fork"), asst("m3", _ts(61), sid="aa-fork")])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT uuid, session_id, duration_ms FROM turns ORDER BY uuid") == [
        ("p1", "zz-orig", 5 * 60_000), ("p2", "aa-fork", 60_000)]


@pytest.mark.parametrize("fast_first", [False, True])
def test_the_fullest_line_names_the_price_tier(env, fast_first):
    # Early streamed lines can lack usage.speed; the final line says the call ran in fast mode.
    root, conn = env
    line = asst("m1", _ts(0), model="claude-opus-5-5")
    early = _with(line, {"input_tokens": 100, "output_tokens": 3})
    final = _with(line, {"input_tokens": 100, "output_tokens": 4000, "speed": "fast"})
    write(root, "C--work-acme-api/s1.jsonl", [final, early] if fast_first else [early, final])
    ingest.Ingestor(conn, root).index()
    (model, speed, i, o), = rows(conn, "SELECT model, speed, input, output FROM calls")
    assert (speed, i, o) == ("fast", 100, 4000)
    assert pricing.call_cost(model, i, o, speed=speed) == pytest.approx(2 * pricing.call_cost(model, i, o))


def test_a_refusal_before_any_output_is_billed_only_in_billed_categories(env):
    root, conn = env
    lines = []
    for mid, category in (("m1", "cyber"), ("m2", None), ("m3", "bio")):
        line = asst(mid, _ts(0))
        refused = json.loads(_with(line, {"input_tokens": 900, "output_tokens": 0, "cache_read_input_tokens": 5000}))
        refused["message"]["stop_reason"] = "refusal"
        refused["message"]["stop_details"] = {"type": "refusal", "category": category}
        # an earlier streamed line already stored the prompt's tokens
        lines += [_with(line, {"input_tokens": 900, "output_tokens": 0, "cache_read_input_tokens": 5000}),
                  json.dumps(refused, separators=(",", ":"))]
    write(root, "C--work-acme-api/s1.jsonl", lines)
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT msg_id, input, cache_read FROM calls ORDER BY msg_id") == [
        ("m1", 0, 0), ("m2", 0, 0), ("m3", 900, 5000)]
    assert rows(conn, "SELECT SUM(calls), SUM(input) FROM rollup_day") == [(3, 900)]


@pytest.mark.parametrize("copy_sid", ["aa-fork", "zz-orig"])
def test_a_fork_copy_read_over_two_ticks_adds_no_turn(env, copy_sid):
    root, conn = env
    orig = [prompt("p1", _ts(0), sid="zz-orig"), asst("m1", _ts(1), sid="zz-orig"),
            turn_duration("d1", _ts(2), 120_000, sid="zz-orig")]
    write(root, "C--work-acme-api/zz-orig.jsonl", orig)
    ing = ingest.Ingestor(conn, root)
    ing.index()
    time.sleep(0.05)
    copied = [ln.replace('"sessionId":"zz-orig"', f'"sessionId":"{copy_sid}"') for ln in orig]
    fork = write(root, "C--work-acme-api/aa-fork.jsonl", copied[:2])  # a tick lands inside the copied history
    ing.tail()
    with open(fork, "a", encoding="utf-8", newline="") as fh:
        fh.write(copied[2] + "\n")
    ing.tail()
    fresh = store.connect(":memory:")
    ingest.Ingestor(fresh, root).index()
    sql = "SELECT uuid, session_id, duration_ms FROM turns ORDER BY uuid"
    assert rows(conn, sql) == rows(fresh, sql) == [("p1", "zz-orig", 120_000)]


def test_a_transcript_rewritten_to_a_larger_size_is_read_again(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(1)), asst("m2", _ts(2))])
    ing = ingest.Ingestor(conn, root)
    ing.index()
    # A bulk import rewrote it with earlier history in front; lines of uneven length, so the old end is mid-line.
    write(root, "C--work-acme-api/s1.jsonl", [asst("m0", _ts(0), out=12345), asst("m1", _ts(1)), asst("m2", _ts(2)),
                                              asst("m3", _ts(3))])
    ing.index()
    assert [r[0] for r in rows(conn)] == ["m0", "m1", "m2", "m3"]


def test_a_file_created_in_the_clock_tick_of_the_last_look_is_found(env):
    root, conn = env
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0))])
    ing = ingest.Ingestor(conn, root)
    ing.index()
    write(root, "C--work-acme-api/s1/subagents/agent-a1.jsonl", [asst("m2", _ts(1), agentId="a1")])
    ing.tail()
    d = os.path.join(root, "C--work-acme-api", "s1", "subagents")
    write(root, "C--work-acme-api/s1/subagents/agent-a2.jsonl", [asst("m3", _ts(2), agentId="a2")])
    os.utime(d, ns=(ing._dirs[d], ing._dirs[d]))  # parallel subagents: the directory mtime did not move
    ing.tail()
    assert [r[0] for r in rows(conn)] == ["m1", "m2", "m3"]
