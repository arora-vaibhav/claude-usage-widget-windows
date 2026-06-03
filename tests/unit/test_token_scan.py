import itertools
import json

from claude_usage.token_scan import IncrementalTokenScanner, collect_tokens

_id_counter = itertools.count(1)


def _entry(ts, out, model="claude-opus-4-8", inp=0, cr=0, cc=0, msg_id=None):
    # Each turn gets a unique message.id by default so it counts as distinct;
    # pass an explicit msg_id to simulate Claude Code's repeated usage lines.
    if msg_id is None:
        msg_id = f"msg_{next(_id_counter)}"
    return json.dumps({
        "type": "assistant", "timestamp": ts,
        "message": {"id": msg_id, "model": model, "usage": {
            "input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": cr, "cache_creation_input_tokens": cc}},
    })


def _write(p, lines, trailing_nl=True):
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if lines and trailing_nl:
        text += "\n"
    p.write_text(text, encoding="utf-8")


def test_cold_start_buckets_by_day(tmp_path):
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [
        _entry("2026-05-28T10:00:00Z", 100, inp=10, cr=5),
        _entry("2026-05-29T09:00:00Z", 50, model="claude-sonnet-4-6"),
        json.dumps({"type": "user", "timestamp": "2026-05-29T09:00:00Z"}),  # ignored
    ])
    days = IncrementalTokenScanner(str(tmp_path)).scan()
    assert days["2026-05-28"]["tokens"]["output"] == 100
    assert days["2026-05-28"]["tokens"]["input"] == 10
    assert days["2026-05-28"]["messages"] == 1
    assert days["2026-05-28"]["by_project"]["proj-a"] == 100
    assert days["2026-05-29"]["tokens"]["output"] == 50
    assert days["2026-05-29"]["by_model"]["claude-sonnet-4-6"]["output"] == 50


def test_rescan_no_double_count_then_increment(tmp_path):
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [_entry("2026-05-29T10:00:00Z", 100)])
    s = IncrementalTokenScanner(str(tmp_path))
    s.scan()
    s.scan()  # nothing new -> must not double-count
    assert s._days["2026-05-29"]["tokens"]["output"] == 100
    with open(tx, "a", encoding="utf-8") as f:
        f.write(_entry("2026-05-29T11:00:00Z", 40) + "\n")
    days = s.scan()
    assert days["2026-05-29"]["tokens"]["output"] == 140  # 100 + 40, not 240


def test_duplicate_message_id_counted_once(tmp_path):
    # Claude Code writes one JSONL line per content/tool block within a turn,
    # each repeating the SAME message.id + usage. We must count the turn once.
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [
        _entry("2026-05-29T10:00:00Z", 100, inp=20, msg_id="msg_dup"),
        _entry("2026-05-29T10:00:00Z", 100, inp=20, msg_id="msg_dup"),  # replay
        _entry("2026-05-29T10:00:00Z", 100, inp=20, msg_id="msg_dup"),  # replay
    ])
    days = IncrementalTokenScanner(str(tmp_path)).scan()
    assert days["2026-05-29"]["tokens"]["output"] == 100  # NOT 300
    assert days["2026-05-29"]["tokens"]["input"] == 20     # NOT 60
    assert days["2026-05-29"]["messages"] == 1             # NOT 3


def test_duplicate_id_dedup_survives_rescan(tmp_path):
    # A turn's replay lines can straddle scan boundaries; the persisted seen-set
    # must keep dedup working across scans/restarts.
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [_entry("2026-05-29T10:00:00Z", 100, msg_id="msg_x")])
    s = IncrementalTokenScanner(str(tmp_path))
    s.scan()
    with open(tx, "a", encoding="utf-8") as f:
        f.write(_entry("2026-05-29T10:00:00Z", 100, msg_id="msg_x") + "\n")  # later replay
    days = s.scan()
    assert days["2026-05-29"]["tokens"]["output"] == 100  # still once


def test_synthetic_and_no_id_entries_skipped(tmp_path):
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [
        _entry("2026-05-29T10:00:00Z", 100),                       # real (unique id)
        _entry("2026-05-29T11:00:00Z", 999, model="<synthetic>"),  # bookkeeping
        # entry with no message.id -> can't dedupe -> dropped
        json.dumps({"type": "assistant", "timestamp": "2026-05-29T12:00:00Z",
                    "message": {"model": "claude-opus-4-8",
                                "usage": {"output_tokens": 777}}}),
    ])
    days = IncrementalTokenScanner(str(tmp_path)).scan()
    assert days["2026-05-29"]["tokens"]["output"] == 100  # only the real turn
    assert "<synthetic>" not in days["2026-05-29"]["by_model"]


def test_partial_line_deferred_until_complete(tmp_path):
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [_entry("2026-05-29T10:00:00Z", 100)], trailing_nl=True)
    with open(tx, "a", encoding="utf-8") as f:
        f.write(_entry("2026-05-29T11:00:00Z", 40))  # partial — no trailing newline
    s = IncrementalTokenScanner(str(tmp_path))
    assert s.scan()["2026-05-29"]["tokens"]["output"] == 100  # partial not counted yet
    with open(tx, "a", encoding="utf-8") as f:
        f.write("\n")
    assert s.scan()["2026-05-29"]["tokens"]["output"] == 140  # now complete -> counted


def test_collect_tokens_result_shape(tmp_path):
    tx = tmp_path / "projects" / "proj-a" / "s1.jsonl"
    _write(tx, [
        _entry("2026-05-29T10:00:00Z", 100, inp=10),
        _entry("2026-05-28T10:00:00Z", 70),
        _entry("2026-05-29T12:00:00Z", 30, model="claude-sonnet-4-6"),
    ])
    today = "2026-05-29"
    week = ["2026-05-23", "2026-05-24", "2026-05-25", "2026-05-26",
            "2026-05-27", "2026-05-28", "2026-05-29"]
    r = collect_tokens(str(tmp_path), today, week)
    assert r["today_output"] == 130            # 100 + 30 today
    assert r["today_messages"] == 2
    assert r["week_output"] == 200             # 130 today + 70 (28th)
    assert r["today_by_model"]["claude-opus-4-8"] == 100
    assert r["today_by_model"]["claude-sonnet-4-6"] == 30
    assert r["today_by_model_detailed"]["claude-opus-4-8"]["input"] == 10
    assert r["today_by_project"]["proj-a"] == 130
    assert r["week_by_model_detailed"]["claude-opus-4-8"]["output"] == 170  # 100 + 70
