from claude_usage.history import aggregate, append_sample, load_samples, prune


def test_append_and_load_roundtrip(tmp_path):
    path = str(tmp_path / "h.jsonl")
    append_sample(path, ts=100.0, session_util=0.5, weekly_util=0.4)
    append_sample(path, ts=200.0, session_util=0.6, weekly_util=0.45)
    rows = load_samples(path)
    assert [r["ts"] for r in rows] == [100.0, 200.0]
    assert rows[1]["session"] == 0.6


def test_load_samples_since_filter(tmp_path):
    path = str(tmp_path / "h.jsonl")
    append_sample(path, 100.0, 0.1, 0.1)
    append_sample(path, 300.0, 0.2, 0.2)
    assert [r["ts"] for r in load_samples(path, since_ts=200.0)] == [300.0]


def test_aggregate_takes_max_per_bucket():
    # window 100s into 10 buckets (10s each), ending at now=1000 (start=900).
    pts = [
        {"ts": 905.0, "session": 0.3, "weekly": 0.1},
        {"ts": 908.0, "session": 0.7, "weekly": 0.1},   # same bucket 0 -> max 0.7
        {"ts": 995.0, "session": 0.4, "weekly": 0.2},   # bucket 9
    ]
    out = aggregate(pts, "session", now=1000.0, window_seconds=100.0, n_buckets=10)
    assert len(out) == 10
    assert out[0] == 0.7
    assert out[9] == 0.4
    assert out[5] == 0.0  # empty bucket


def test_prune_drops_old(tmp_path):
    path = str(tmp_path / "h.jsonl")
    append_sample(path, 100.0, 0.1, 0.1)
    append_sample(path, 1000.0, 0.2, 0.2)
    kept = prune(path, keep_seconds=500.0, now=1000.0)  # cutoff=500 -> drop ts=100
    assert kept == 1
    assert [r["ts"] for r in load_samples(path)] == [1000.0]
