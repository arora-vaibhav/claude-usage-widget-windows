from datetime import datetime

from claude_usage.analytics import _daily_peaks, detect_anomaly


def _ts(y, mo, d, h=12):
    return datetime(y, mo, d, h, 0, 0).timestamp()


def test_daily_peaks_local_day_grouping_and_max():
    # Two samples on the same local day -> one peak (the max); separate days
    # -> separate peaks, ordered by date.
    samples = [
        {"ts": _ts(2026, 6, 1, 9), "session": 0.3},
        {"ts": _ts(2026, 6, 1, 20), "session": 0.7},   # same local day -> max 0.7
        {"ts": _ts(2026, 6, 2, 10), "session": 0.5},
    ]
    assert _daily_peaks(samples) == [0.7, 0.5]


def test_daily_peaks_skips_bad_ts():
    samples = [{"ts": 0, "session": 0.9}, {"ts": -5, "session": 0.9},
               {"ts": _ts(2026, 6, 1), "session": 0.4}]
    assert _daily_peaks(samples) == [0.4]


def test_detect_anomaly_needs_min_samples():
    # Fewer than MIN_SAMPLES distinct prior days -> not flagged.
    samples = [{"ts": _ts(2026, 6, d), "session": 0.2} for d in range(1, 4)]
    rep = detect_anomaly(samples, today_usage=0.9)
    assert rep.is_anomaly is False


def test_detect_anomaly_flags_spike():
    # A week+ of low days, then a big spike today -> flagged anomalous.
    samples = [{"ts": _ts(2026, 6, d), "session": 0.1} for d in range(1, 12)]
    rep = detect_anomaly(samples, today_usage=0.95)
    assert rep.is_anomaly is True
    assert rep.message  # has a human-readable note
