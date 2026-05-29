import json
import os

from claude_usage.config import DEFAULT_CONFIG, load_config


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(str(tmp_path / "does-not-exist.json"))
    assert cfg == DEFAULT_CONFIG
    # Returned dict must be a copy, not the module-level default.
    assert cfg is not DEFAULT_CONFIG


def test_user_values_override_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"theme": "gruvbox-dark", "refresh_seconds": 5}))
    cfg = load_config(str(p))
    assert cfg["theme"] == "gruvbox-dark"
    assert cfg["refresh_seconds"] == 5
    # Untouched keys keep their defaults.
    assert cfg["weekly_token_limit"] == DEFAULT_CONFIG["weekly_token_limit"]


def test_bad_json_falls_back_to_defaults(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{not valid json")
    cfg = load_config(str(p))
    assert cfg["theme"] == DEFAULT_CONFIG["theme"]


def test_claude_dir_expanded_and_normalized(tmp_path):
    # A user-supplied claude_dir must be expanduser'd and normpath'd so
    # downstream os.path.join calls get a clean, single-separator path.
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"claude_dir": "~/foo/../bar"}))
    cfg = load_config(str(p))
    assert cfg["claude_dir"] == os.path.normpath(os.path.expanduser("~/foo/../bar"))
    assert "~" not in cfg["claude_dir"]


def test_default_claude_dir_has_consistent_separators():
    # Regression: DEFAULT_CONFIG['claude_dir'] used to be expanduser('~/.claude')
    # which yields a mixed-separator path on Windows (C:\\Users\\V/.claude).
    cd = DEFAULT_CONFIG["claude_dir"]
    assert cd == os.path.normpath(cd)
    if os.sep == "\\":
        assert "/" not in cd
