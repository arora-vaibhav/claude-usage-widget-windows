import json
import os
import threading

import pytest

from claude_usage import store


@pytest.fixture
def conn(tmp_path):
    c = store.connect(str(tmp_path / "usage.db"))
    yield c
    c.close()


def test_schema_version_and_wal(tmp_path):
    path = str(tmp_path / "sub" / "usage.db")
    c = store.connect(path)
    assert c.execute("PRAGMA user_version").fetchone()[0] == store.SCHEMA_VERSION == len(store.MIGRATIONS)
    assert store.get_meta(c, "schema_version") == store.SCHEMA_VERSION
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"meta", "files", "calls", "turns", "sessions", "samples", "legacy_days", "rollup", "rollup_day"} <= tables
    c.close()
    store.connect(path).close()  # reopening runs no migration twice


def test_migrations_only_add():
    # Every migration step must be additive so an older store keeps its rows.
    for script in store.MIGRATIONS:
        for stmt in script.upper().split(";"):
            assert "DROP " not in stmt and "DELETE " not in stmt
            assert "ALTER TABLE" not in stmt or "ADD COLUMN" in stmt


def test_samples_keep_none(conn):
    store.add_sample(conn, 100.0, 0.42, None, {"source": "oauth"})
    store.add_sample(conn, 130.0, None, None)
    assert conn.execute("SELECT ts, weekly, session FROM samples ORDER BY ts").fetchall() == [
        (100.0, 0.42, None), (130.0, None, None)]


def test_repeated_samples_are_thinned(conn):
    assert store.add_sample(conn, 0.0, 0.40, None)
    assert not store.add_sample(conn, 30.0, 0.40, None)  # same values within 5 minutes
    assert store.add_sample(conn, 60.0, 0.41, None)  # a change is always kept
    assert store.add_sample(conn, 360.0, 0.41, None)  # and one row every 5 minutes
    assert [r[0] for r in conn.execute("SELECT ts FROM samples ORDER BY ts")] == [0.0, 60.0, 360.0]


def test_each_thread_gets_its_own_connection(tmp_path):
    path = str(tmp_path / "usage.db")
    store.connect(path).close()

    def worker():
        c = store.connect(path)
        store.add_sample(c, 1.0, 0.5, None)
        c.close()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    reader = store.connect(path)
    assert reader.execute("SELECT weekly FROM samples").fetchone() == (0.5,)
    reader.close()


def _legacy_files(d, rows, samples):
    with open(os.path.join(d, "usage-daily.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    with open(os.path.join(d, "usage-history.jsonl"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(json.dumps(s) for s in samples) + "\n")


def _day(date, cost, msgs, backfilled=False):
    return {"date": date, "cost": cost, "messages": msgs, "backfilled": backfilled,
            "tokens": {"input": 1, "output": 2, "cache_read": 3, "cache_creation": 4}}


def test_legacy_import_flags_copies_and_pre_dedupe_rows(conn, tmp_path):
    d = str(tmp_path)
    _legacy_files(d, [
        _day("2026-05-30", 90.0, 300, backfilled=True),  # before the dedupe fix: about 2.2x too high
        _day("2026-07-01", 100.0, 500),
        _day("2026-07-02", 100.0, 500),  # yesterday's totals copied overnight
        _day("2026-07-03", 80.0, 400),
        _day("2026-07-04", 120.0, 400),  # max-merged copy: today's cost, yesterday's message count
        _day("2026-07-05", 120.3, 401),  # within 0.5% of the row before
        _day("2026-07-06", 0.0, 0),
    ], [{"ts": 10, "weekly": 0.3, "session": 0.0}, {"ts": 40, "weekly": 0.31, "session": 0.2},
        {"ts": 70, "weekly": "bad"}])
    before = {f: open(os.path.join(d, f), "rb").read() for f in ("usage-daily.jsonl", "usage-history.jsonl")}
    res = store.import_legacy(conn, d)
    assert res == {"days": 7, "samples": 3, "corrupt": 4}
    q = dict(conn.execute("SELECT day, quality FROM legacy_days"))
    assert [q[k] for k in sorted(q)] == ["corrupt", "legacy", "corrupt", "legacy", "corrupt", "corrupt", "legacy"]
    assert conn.execute("SELECT ts, weekly, session FROM samples ORDER BY ts").fetchall() == [
        (10.0, 0.3, None), (40.0, 0.31, 0.2), (70.0, None, None)]  # the old 0.0 meant "not reported"
    assert store.import_legacy(conn, d) == {"skipped": True}
    assert before == {f: open(os.path.join(d, f), "rb").read() for f in before}  # read-only


def _ledger_day(conn, day, cost_usd):
    # Opus 5.5 output at $20/MTok makes the token count easy to pick.
    out = int(cost_usd / 20 * 1e6)
    conn.execute("INSERT INTO rollup_day VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (day, "s-" + day, 0, "acme-api", "claude-opus-5-5", "", 0, 1, 0, out, 0, 0, 0, 0, 0, 0))


def _coverage(conn, legacy, ledger):
    conn.executemany("INSERT INTO legacy_days VALUES (?,?,?,?,?)",
                     [(x[0], x[1], 0, x[2] if len(x) > 2 else 1, "legacy") for x in legacy])
    for d, c in ledger:
        _ledger_day(conn, d, c)
    conn.commit()
    return store.update_coverage(conn)


def test_coverage_starts_after_cleaned_up_days(conn):
    legacy = [("2026-08-01", 100), ("2026-08-02", 100), ("2026-08-03", 100), ("2026-08-04", 100)]
    ledger = [("2026-08-01", 10), ("2026-08-02", 20), ("2026-08-03", 90), ("2026-08-04", 130), ("2026-08-05", 50)]
    assert _coverage(conn, legacy, ledger) == "2026-08-03"
    assert store.get_meta(conn, "coverage_from") == "2026-08-03"


def test_missing_main_thread_messages_fail_even_when_subagents_fill_the_cost(conn):
    # The old widget counted main-thread messages only; a cleaned-up day keeps subagent cost but loses messages.
    legacy = [("2026-08-01", 100, 50), ("2026-08-02", 100, 50), ("2026-08-03", 100, 50), ("2026-08-04", 100, 1)]
    ledger = [("2026-08-01", 150), ("2026-08-02", 150), ("2026-08-03", 150), ("2026-08-04", 150)]
    assert _coverage(conn, legacy, ledger) == "2026-08-04"


def test_a_lone_bad_legacy_day_does_not_move_coverage(conn):
    legacy = [("2026-08-01", 100), ("2026-08-02", 100), ("2026-08-03", 100), ("2026-08-04", 900), ("2026-08-05", 100)]
    ledger = [("2026-08-01", 10), ("2026-08-02", 70), ("2026-08-03", 80), ("2026-08-04", 90), ("2026-08-05", 95)]
    assert _coverage(conn, legacy, ledger) == "2026-08-02"


def test_coverage_without_legacy_is_the_first_ledger_day(conn):
    assert _coverage(conn, [], [("2026-09-03", 5), ("2026-09-04", 5)]) == "2026-09-03"
