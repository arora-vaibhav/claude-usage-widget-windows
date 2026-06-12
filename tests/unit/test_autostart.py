"""Launch-at-startup management — file lifecycle + VBS content safety."""

from claude_usage import autostart


def test_round_trip_enable_disable(tmp_path, monkeypatch):
    sd = str(tmp_path)
    monkeypatch.setattr(autostart, "_exe_path", lambda: r"C:\fake\claude-usage.exe")
    assert autostart.is_enabled(sd) is False
    assert autostart.enable(sd) is True
    assert autostart.is_enabled(sd) is True
    assert autostart.disable(sd) is True
    assert autostart.is_enabled(sd) is False
    # Disabling when already absent is still success (idempotent).
    assert autostart.disable(sd) is True


def test_set_enabled_dispatch(tmp_path, monkeypatch):
    sd = str(tmp_path)
    monkeypatch.setattr(autostart, "_exe_path", lambda: r"C:\fake\claude-usage.exe")
    assert autostart.set_enabled(True, sd) is True
    assert autostart.is_enabled(sd) is True
    assert autostart.set_enabled(False, sd) is True
    assert autostart.is_enabled(sd) is False


def test_vbs_is_ascii_no_bom_and_hidden_launch(tmp_path, monkeypatch):
    sd = str(tmp_path)
    monkeypatch.setattr(autostart, "_exe_path", lambda: r"C:\fake\claude-usage.exe")
    assert autostart.enable(sd) is True
    raw = (tmp_path / "claude-usage-widget.vbs").read_bytes()
    # A UTF-8 BOM breaks the Windows script host — must be plain ASCII.
    assert not raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("ascii")  # raises if any non-ASCII slipped in
    assert '"""C:\\fake\\claude-usage.exe"""' in text  # quoted exe path
    assert ", 0, False" in text                         # hidden window, no wait


def test_enable_fails_without_exe(tmp_path, monkeypatch):
    sd = str(tmp_path)
    monkeypatch.setattr(autostart, "_exe_path", lambda: None)
    assert autostart.enable(sd) is False
    assert autostart.is_enabled(sd) is False
