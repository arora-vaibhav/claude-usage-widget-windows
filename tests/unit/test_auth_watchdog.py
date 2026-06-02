from claude_usage.auth_watchdog import AuthWatchdog, is_auth_error


def test_is_auth_error_classification():
    assert is_auth_error("Credentials expired -- re-authenticate with 'claude'")
    assert is_auth_error("invalid_grant")
    assert not is_auth_error("Rate limited -- try again shortly")
    assert not is_auth_error("API request failed -- check network")
    assert not is_auth_error(None)


def _watchdog(tmp_path, **kw):
    wd = AuthWatchdog(str(tmp_path), **kw)
    calls = {"n": 0}
    wd._attempt_repair = lambda: calls.__setitem__("n", calls["n"] + 1) or True  # stub
    return wd, calls


def test_triggers_after_threshold(tmp_path):
    wd, calls = _watchdog(tmp_path, fail_threshold=3, cooldown_s=0)
    for i in range(2):
        assert wd.note_result("Credentials expired", now=float(i)) is False
    assert calls["n"] == 0
    assert wd.note_result("Credentials expired", now=2.0) is True  # 3rd -> repair
    assert calls["n"] == 1


def test_transient_errors_do_not_count(tmp_path):
    wd, calls = _watchdog(tmp_path, fail_threshold=2, cooldown_s=0)
    for i in range(10):
        wd.note_result("Rate limited -- try again shortly", now=float(i))
    assert calls["n"] == 0  # 429s never trigger repair


def test_success_resets_streak(tmp_path):
    wd, calls = _watchdog(tmp_path, fail_threshold=3, cooldown_s=0)
    wd.note_result("invalid", now=0.0)
    wd.note_result("invalid", now=1.0)
    wd.note_result(None, now=2.0)            # success resets the counter
    wd.note_result("invalid", now=3.0)
    wd.note_result("invalid", now=4.0)
    assert calls["n"] == 0                   # only 2 in a row since reset
    wd.note_result("invalid", now=5.0)       # now 3 in a row
    assert calls["n"] == 1


def test_cooldown_blocks_rapid_repairs(tmp_path):
    wd, calls = _watchdog(tmp_path, fail_threshold=1, cooldown_s=900)
    assert wd.note_result("invalid", now=100.0) is True   # first repair
    assert calls["n"] == 1
    wd.note_result("invalid", now=200.0)                  # within cooldown -> blocked
    assert calls["n"] == 1
    wd.note_result("invalid", now=100.0 + 901)            # cooldown elapsed -> repair
    assert calls["n"] == 2


def test_extract_token_from_cli_output():
    out = "Your token:\n  sk-ant-oat01-AbCd_1234-EFGH5678ijklMNOP90qrST\nKeep it secret."
    assert AuthWatchdog._extract_token(out) == "sk-ant-oat01-AbCd_1234-EFGH5678ijklMNOP90qrST"
    assert AuthWatchdog._extract_token("no token here") is None
    assert AuthWatchdog._extract_token(None) is None


def test_write_token_roundtrip(tmp_path):
    wd = AuthWatchdog(str(tmp_path))
    assert wd._write_token("sk-ant-oat01-XYZ") is True
    assert (tmp_path / "claude-usage-token").read_text(encoding="utf-8") == "sk-ant-oat01-XYZ"


def _wd_with_repair(tmp_path, token_each_time, **kw):
    """Watchdog whose _attempt_repair simulates a real outcome (token or not)."""
    wd = AuthWatchdog(str(tmp_path), **kw)
    state = {"calls": 0}

    def fake_repair():
        state["calls"] += 1
        tok = token_each_time(state["calls"])
        if tok and wd._write_token(tok):
            wd._succeeded = True
            return True
        wd._failed_repairs += 1
        if wd._failed_repairs >= wd._max_attempts:
            wd._gave_up = True
        return True

    wd._attempt_repair = fake_repair
    return wd, state


def test_failed_repair_long_backoff_then_gives_up(tmp_path):
    # setup-token never yields a token (always awaiting browser).
    wd, st = _wd_with_repair(
        tmp_path, token_each_time=lambda n: None,
        fail_threshold=2, cooldown_s=60,
    )
    wd._failed_backoff_s = 600  # 10-min backoff so the cap is reachable in-window
    # Simulate ~1 day of failing cycles, one per minute.
    t = 0.0
    for _ in range(1440):
        wd.note_result("invalid", now=t)
        t += 60.0
    # Capped at _MAX_REPAIR_ATTEMPTS (default 3), NOT one-per-threshold (~720).
    assert st["calls"] <= 3
    assert wd._gave_up is True


def test_does_not_storm_within_backoff(tmp_path):
    wd, st = _wd_with_repair(
        tmp_path, token_each_time=lambda n: None,
        fail_threshold=1, cooldown_s=60,
    )
    wd._failed_backoff_s = 3600  # 1h backoff after a no-token attempt
    assert wd.note_result("invalid", now=100.0) is True   # attempt #1
    assert st["calls"] == 1
    # Many failures over the next ~50 min: must NOT spawn again (within backoff).
    for m in range(1, 50):
        wd.note_result("invalid", now=100.0 + m * 60)
    assert st["calls"] == 1
    # After backoff elapses, exactly one more attempt.
    wd.note_result("invalid", now=100.0 + 3601)
    assert st["calls"] == 2


def test_success_stops_all_future_repairs(tmp_path):
    # First attempt yields a token → healed → never tries again.
    wd, st = _wd_with_repair(
        tmp_path, token_each_time=lambda n: "sk-ant-oat01-GOOD",
        fail_threshold=1, cooldown_s=0,
    )
    assert wd.note_result("invalid", now=0.0) is True
    assert st["calls"] == 1 and wd._succeeded is True
    for i in range(20):
        wd.note_result("invalid", now=float(i + 1))
    assert st["calls"] == 1  # no further attempts after success


def test_force_repair_bypasses_giveup(tmp_path):
    wd, st = _wd_with_repair(
        tmp_path, token_each_time=lambda n: None,
        fail_threshold=1, cooldown_s=0,
    )
    wd._gave_up = True  # simulate having given up
    wd.note_result("invalid", now=0.0)
    assert st["calls"] == 0           # gave_up blocks auto-repair
    wd.force_repair()                 # manual override
    assert st["calls"] == 1
