import json
import os
from datetime import datetime, timezone

import pytest

from claude_usage import ingest, store


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
    write(root, "C--work-acme-api/s1.jsonl", [asst("m1", _ts(0), cwd=r"C:\work\Acme-Api"),
                                              asst("m2", _ts(1), cwd="c:\\work\\acme-api\\")])
    ingest.Ingestor(conn, root).index()
    assert rows(conn, "SELECT DISTINCT project FROM calls") == [("Acme-Api",)]
