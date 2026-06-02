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
