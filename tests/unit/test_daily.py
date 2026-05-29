from claude_usage.daily import DAILY_FILENAME, load_daily, upsert_day


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
