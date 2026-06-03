import datetime as _dt
import json

from claude_usage.daily import DAILY_FILENAME, backfill, load_daily, upsert_day


def _row(date, output=0, cost=0.0):
    return {
        "date": date,
        "messages": 0,
        "sessions": 0,
        "tokens": {"input": 0, "output": output, "cache_read": 0, "cache_creation": 0},
        "cost": cost,
        "cache_savings": 0.0,
        "by_model": {},
        "by_project": {},
        "peak_session_util": 0.0,
        "peak_weekly_util": 0.0,
        "schema": 1,
    }


def test_upsert_then_load_roundtrip(tmp_path):
    path = str(tmp_path / DAILY_FILENAME)
    upsert_day(path, _row("2026-05-27", output=100, cost=1.5))
    rows = load_daily(path)
    assert len(rows) == 1
    assert rows[0]["date"] == "2026-05-27"
    assert rows[0]["tokens"]["output"] == 100
    assert rows[0]["cost"] == 1.5


def test_upsert_same_date_replaces_idempotent(tmp_path):
    path = str(tmp_path / DAILY_FILENAME)
    upsert_day(path, _row("2026-05-27", output=100))
    upsert_day(path, _row("2026-05-27", output=250))  # same day -> replace, not append
    rows = load_daily(path)
    assert len(rows) == 1
    assert rows[0]["tokens"]["output"] == 250


def test_rows_sorted_by_date(tmp_path):
    path = str(tmp_path / DAILY_FILENAME)
    upsert_day(path, _row("2026-05-28"))
    upsert_day(path, _row("2026-05-26"))
    upsert_day(path, _row("2026-05-27"))
    assert [r["date"] for r in load_daily(path)] == ["2026-05-26", "2026-05-27", "2026-05-28"]


def test_load_daily_since_filter(tmp_path):
    path = str(tmp_path / DAILY_FILENAME)
    for d in ("2026-05-25", "2026-05-27", "2026-05-29"):
        upsert_day(path, _row(d))
    got = [r["date"] for r in load_daily(path, since_date="2026-05-27")]
    assert got == ["2026-05-27", "2026-05-29"]


def test_load_missing_file_returns_empty(tmp_path):
    assert load_daily(str(tmp_path / "nope.jsonl")) == []


def test_malformed_line_skipped(tmp_path):
    path = tmp_path / DAILY_FILENAME
    path.write_text(
        '{"date":"2026-05-27","schema":1}\nnot json\n{"date":"2026-05-28","schema":1}\n'
    )
    assert [r["date"] for r in load_daily(str(path))] == ["2026-05-27", "2026-05-28"]


# ---- backfill ----------------------------------------------------------

def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _ms(iso_utc):
    return int(_dt.datetime.fromisoformat(iso_utc).replace(tzinfo=_dt.timezone.utc).timestamp() * 1000)


def test_upsert_merged_keeps_peak_util(tmp_path):
    from claude_usage.daily import upsert_day_merged
    path = str(tmp_path / DAILY_FILENAME)
    upsert_day_merged(path, {**_row("2026-06-03"), "peak_session_util": 0.95})
    # A later refresh at low utilization must NOT lower the recorded peak.
    upsert_day_merged(path, {**_row("2026-06-03"), "peak_session_util": 0.05})
    r = load_daily(path)[0]
    assert r["peak_session_util"] == 0.95


def test_upsert_merged_totals_never_regress(tmp_path):
    from claude_usage.daily import upsert_day_merged
    path = str(tmp_path / DAILY_FILENAME)
    upsert_day_merged(path, _row("2026-06-03", output=900, cost=12.0))
    # A transient smaller recompute (e.g. scanner state reset) must not shrink.
    upsert_day_merged(path, _row("2026-06-03", output=100, cost=2.0))
    r = load_daily(path)[0]
    assert r["tokens"]["output"] == 900
    assert r["cost"] == 12.0
    # But a larger value (normal accumulation) does advance.
    upsert_day_merged(path, _row("2026-06-03", output=1500, cost=20.0))
    r = load_daily(path)[0]
    assert r["tokens"]["output"] == 1500
    assert r["cost"] == 20.0


def _seed_transcripts_and_history(cd):
    tx = cd / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, "\n".join([
        json.dumps({"type": "assistant", "timestamp": "2026-05-20T10:00:00.000Z",
                    "message": {"id": "m1", "model": "claude-opus-4-8",
                                "usage": {"input_tokens": 100, "output_tokens": 50,
                                          "cache_read_input_tokens": 10,
                                          "cache_creation_input_tokens": 5}}}),
        json.dumps({"type": "assistant", "timestamp": "2026-05-20T11:00:00.000Z",
                    "message": {"id": "m2", "model": "claude-opus-4-8",
                                "usage": {"input_tokens": 200, "output_tokens": 80}}}),
        json.dumps({"type": "assistant", "timestamp": "2026-05-21T09:00:00.000Z",
                    "message": {"id": "m3", "model": "claude-sonnet-4-6",
                                "usage": {"input_tokens": 300, "output_tokens": 120}}}),
        json.dumps({"type": "user", "timestamp": "2026-05-20T10:00:00.000Z"}),  # ignored
    ]) + "\n")
    _write(cd / "history.jsonl", "\n".join([
        json.dumps({"timestamp": _ms("2026-05-20T10:00:00"), "sessionId": "A"}),
        json.dumps({"timestamp": _ms("2026-05-20T12:00:00"), "sessionId": "A"}),
        json.dumps({"timestamp": _ms("2026-05-21T09:00:00"), "sessionId": "B"}),
    ]) + "\n")


def test_backfill_buckets_by_day(tmp_path):
    _seed_transcripts_and_history(tmp_path)
    n = backfill(str(tmp_path))
    rows = {r["date"]: r for r in load_daily(str(tmp_path / DAILY_FILENAME))}
    assert n == 2
    assert set(rows) == {"2026-05-20", "2026-05-21"}

    d20 = rows["2026-05-20"]
    assert d20["tokens"]["output"] == 130          # 50 + 80
    assert d20["tokens"]["input"] == 300           # 100 + 200
    assert d20["by_model"]["claude-opus-4-8"]["output"] == 130
    assert d20["by_project"]["proj-a"] == 130
    assert d20["messages"] == 2
    assert d20["sessions"] == 1                     # session "A" deduped
    assert d20["cost"] > 0                          # opus output costed (not zero)
    assert d20["backfilled"] is True

    d21 = rows["2026-05-21"]
    assert d21["tokens"]["output"] == 120
    assert d21["sessions"] == 1                     # session "B"


def test_backfill_is_idempotent(tmp_path):
    _seed_transcripts_and_history(tmp_path)
    assert backfill(str(tmp_path)) == 2
    assert backfill(str(tmp_path)) == 0             # re-run writes nothing new


def test_backfill_preserves_existing_rows(tmp_path):
    _seed_transcripts_and_history(tmp_path)
    daily_path = str(tmp_path / DAILY_FILENAME)
    # Simulate a live-collector row already present for 2026-05-20.
    upsert_day(daily_path, {"date": "2026-05-20", "messages": 999, "schema": 1, "live": True})
    written = backfill(str(tmp_path))
    rows = {r["date"]: r for r in load_daily(daily_path)}
    assert written == 1                              # only 2026-05-21 added
    assert rows["2026-05-20"]["messages"] == 999     # existing row untouched
    assert rows["2026-05-20"].get("live") is True
