import json
import os

import pytest

from claude_usage import config
from claude_usage.config import DEFAULT_CONFIG, load_config, migrate, save_config


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(str(tmp_path / "does-not-exist.json"))
    assert cfg == DEFAULT_CONFIG
    assert cfg is not DEFAULT_CONFIG  # a copy, not the module-level default


def test_user_values_override_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"appearance": "light", "refresh_seconds": 5, "custom": 1}))
    cfg = load_config(str(p))
    assert (cfg["appearance"], cfg["refresh_seconds"], cfg["custom"]) == ("light", 5, 1)
    assert cfg["osd_style"] == DEFAULT_CONFIG["osd_style"]
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes())  # saved with a BOM, as PowerShell 5 writes UTF-8
    assert load_config(str(p))["appearance"] == "light" and not list(tmp_path.glob("*.bad"))


def test_bad_json_falls_back_to_defaults_and_is_kept(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not valid json")
    assert load_config(str(p)) == DEFAULT_CONFIG
    # moved aside, so the first settings save cannot overwrite it
    assert not p.exists() and (tmp_path / "config.json.bad").read_text() == "{not valid json"
    p.write_text("[]")  # a second broken file never overwrites the first copy
    assert load_config(str(p)) == DEFAULT_CONFIG
    assert (tmp_path / "config.json.bad").read_text() == "{not valid json" and len(list(tmp_path.glob("*.bad"))) == 2


def test_a_read_error_is_retried_and_never_moves_the_file(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"appearance": "dark"}))
    real_open, fails = open, [2]
    monkeypatch.setattr(config.time, "sleep", lambda s: None)

    def flaky_open(*a, **k):
        if fails[0]:
            fails[0] -= 1
            raise PermissionError(13, "sharing violation")
        return real_open(*a, **k)

    monkeypatch.setattr(config, "open", flaky_open, raising=False)
    assert load_config(str(p))["appearance"] == "dark"  # two blips, then the read works
    fails[0] = 99
    cfg = load_config(str(p))  # still unreadable: defaults, and the file stays
    assert cfg == dict(DEFAULT_CONFIG, **{config.UNREAD: True})
    assert json.loads(p.read_text()) == {"appearance": "dark"} and not list(tmp_path.glob("*.bad"))

    cfg["osd_style"] = "pill"  # a change made in this session (an OSD toggle)
    with pytest.raises(OSError):
        save_config(str(p), cfg)  # still locked: nothing is written, the caller retries
    fails[0] = 0
    save_config(str(p), cfg)  # the user's settings stay, under this session's change
    assert json.loads(p.read_text()) == {"appearance": "dark", "osd_style": "pill"}


def test_hand_edited_values_that_do_not_parse_use_the_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"refresh_seconds": "45", "osd_opacity": "half", "osd_scale": None, "osd_x": "left",
                             "osd_y": 12.0, "notify_thresholds": [0.5]}))
    cfg = load_config(str(p))
    assert (cfg["refresh_seconds"], cfg["osd_opacity"], cfg["osd_scale"]) == (45, 1.0, 1.0)
    assert "osd_x" not in cfg and cfg["osd_y"] == 12
    assert cfg["notify_thresholds"] == DEFAULT_CONFIG["notify_thresholds"]
    p.write_text(json.dumps({"notify_thresholds": ["0.6", 0.8]}))
    assert load_config(str(p))["notify_thresholds"] == [0.6, 0.8]
    p.write_text('{"notify_thresholds": [0.9, 0.5], "osd_opacity": NaN, "refresh_seconds": Infinity,'
                 ' "notifications_enabled": "false", "osd_always_on_top": 0, "claude_dir": null,'
                 ' "appearance": ["dark"], "osd_style": "big"}')
    cfg = load_config(str(p))
    for key in ("notify_thresholds", "osd_opacity", "refresh_seconds", "notifications_enabled",
                "osd_always_on_top", "claude_dir", "appearance", "osd_style"):
        assert cfg[key] == DEFAULT_CONFIG[key], key


def test_claude_dir_expanded_and_normalized(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"claude_dir": "~/foo/../bar"}))
    cfg = load_config(str(p))
    assert cfg["claude_dir"] == os.path.normpath(os.path.expanduser("~/foo/../bar"))
    assert "~" not in cfg["claude_dir"]


def test_default_claude_dir_has_consistent_separators():
    cd = DEFAULT_CONFIG["claude_dir"]
    assert cd == os.path.normpath(cd)
    if os.sep == "\\":
        assert "/" not in cd


@pytest.mark.parametrize("theme, appearance", [
    ("dracula", "dark"), ("nord", "dark"), ("catppuccin-mocha", "dark"), ("gruvbox-dark", "dark"),
    ("default", "dark"), ("dashboard", "dark"), ("hud", "dark"), ("terminal", "dark"), ("strip", "dark"),
    ("receipt", "light"), ("brutalist", "light"), ("solarized", "system"),
])
def test_theme_becomes_appearance(theme, appearance):
    assert migrate({"theme": theme}) == {"appearance": appearance}


def test_a_theme_that_is_not_a_name_is_dropped():
    assert migrate({"theme": ["dracula"]}) == {}  # a hand edit; a set lookup would raise TypeError


def test_an_old_config_migrates_and_saves_without_dropped_keys(tmp_path):
    old = {"theme": "dracula", "osd_minimized": True, "osd_opacity": 0.5, "osd_x": 22, "osd_y": 1400,
           "show_ticker": False, "osd_view_mode": "bars", "webhooks": {}, "api_server_enabled": False,
           "api_server_host": "127.0.0.1", "api_server_port": 8765, "notify_thresholds": [0.75, 0.9],
           "daily_token_limit": 5_000_000}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(old))
    cfg = load_config(str(p))
    assert cfg["appearance"] == "dark" and cfg["osd_style"] == "pill"
    assert cfg["osd_opacity"] == 0.5  # an existing opacity is kept; only the default became 1.0
    assert (cfg["osd_x"], cfg["osd_y"]) == (22, 1400)
    assert "daily_token_limit" not in cfg  # an old limit nothing reads any more
    save_config(str(p), cfg)
    saved = json.loads(p.read_text())
    gone = {"theme", "osd_minimized", "show_ticker", "osd_view_mode", "webhooks",
            "api_server_enabled", "api_server_host", "api_server_port", "daily_token_limit"}
    assert not gone & set(saved)
    # only what differs from the defaults is saved, so claude_dir is never pinned
    assert saved == {"appearance": "dark", "osd_style": "pill", "osd_opacity": 0.5, "osd_x": 22, "osd_y": 1400}
    assert load_config(str(p)) == cfg  # migrating again changes nothing


def test_a_value_json_cannot_hold_leaves_the_file_alone(tmp_path):
    p = tmp_path / "config.json"
    save_config(str(p), {"osd_opacity": 0.5})
    with pytest.raises(TypeError):
        save_config(str(p), {"osd_opacity": object()})
    assert json.loads(p.read_text()) == {"osd_opacity": 0.5} and not (tmp_path / "config.json.tmp").exists()


def test_a_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    def locked(src, dst):
        raise PermissionError(13, "locked")

    p = tmp_path / "config.json"
    monkeypatch.setattr(config.os, "replace", locked)
    with pytest.raises(PermissionError):
        save_config(str(p), {"osd_opacity": 0.5})
    assert not list(tmp_path.iterdir())


def test_minimized_false_is_card_and_new_keys_win():
    assert migrate({"osd_minimized": False}) == {"osd_style": "card"}
    assert migrate({"theme": "receipt", "appearance": "dark", "osd_minimized": True, "osd_style": "card"}) == {
        "appearance": "dark", "osd_style": "card"}
    assert DEFAULT_CONFIG["osd_opacity"] == 1.0 and DEFAULT_CONFIG["appearance"] == "system"
