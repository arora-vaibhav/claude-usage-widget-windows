import time
from datetime import datetime, timezone

from claude_usage.trends import hourly_histogram


def test_hourly_histogram_local_vs_utc_bucketing():
    # A sample at a known epoch must land in the LOCAL hour by default, and in
    # the UTC hour when utc=True. Build the epoch from a fixed local wall-clock
    # so the test is timezone-robust on any machine.
    local_dt = datetime(2026, 6, 3, 15, 30, 0)  # 3:30pm local
    ts = local_dt.timestamp()
    now = ts + 3600  # 1h later, so the sample is within the 7-day window
    samples = [{"ts": ts, "session": 0.8}]

    local_hist = hourly_histogram(samples, now=now)          # local (default)
    assert local_hist[15] == 0.8                              # bucketed at local 15:00
    assert sum(1 for v in local_hist if v > 0) == 1

    utc_hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
    utc_hist = hourly_histogram(samples, now=now, utc=True)
    assert utc_hist[utc_hour] == 0.8


def test_hourly_histogram_averages_and_window():
    base = datetime(2026, 6, 3, 9, 0, 0).timestamp()
    now = base + 3600
    samples = [
        {"ts": base, "session": 0.4},
        {"ts": base + 60, "session": 0.6},          # same local hour -> avg 0.5
        {"ts": base - 8 * 86400, "session": 1.0},   # outside 7-day window -> ignored
    ]
    h = hourly_histogram(samples, now=now)
    assert abs(h[9] - 0.5) < 1e-9
    assert all(h[i] == 0.0 for i in range(24) if i != 9)


def test_hourly_histogram_empty():
    assert hourly_histogram([], now=time.time()) == [0.0] * 24
