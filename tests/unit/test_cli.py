"""The one-off CLI commands run without Qt against the store in %LOCALAPPDATA%."""

from __future__ import annotations

import json
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "fixtures"))
sys.path.insert(0, HERE)

import make_store  # noqa: E402
from snapshot_contract import check, check_consistency  # noqa: E402

from claude_usage import __version__, cli, collector  # noqa: E402


@pytest.fixture
def home(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    make_store.write_transcripts(str(claude / "projects"))
    make_store.write_legacy(str(claude))
    (tmp_path / "cfg" / "claude-usage").mkdir(parents=True)
    (tmp_path / "cfg" / "claude-usage" / "config.json").write_text(json.dumps({"claude_dir": str(claude)}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setattr(collector, "fetch_rate_limits", lambda d: {"error": "OAuth usage request failed"})
    return tmp_path


def test_version(capsys):
    assert cli.run_cli(["--version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_no_flag_means_gui():
    assert cli.run_cli([]) == -1


def test_json_prints_a_contract_snapshot(home, capsys):
    assert cli.run_cli(["--json", "--range", "30d"]) == 0
    snap = json.loads(capsys.readouterr().out)
    check(snap)
    check_consistency(snap)
    assert snap["range"]["key"] == "30d"
    assert snap["now"]["state"] == "error" and snap["now"]["message"] == "OAuth usage request failed"
    assert os.path.isfile(home / "local" / "claude-usage" / "usage.db")


def test_reindex_reads_everything_again_without_double_counting(home, capsys):
    assert cli.run_cli(["--json"]) == 0
    db = home / "local" / "claude-usage" / "usage.db"
    with sqlite3.connect(db) as c:
        before = c.execute("SELECT COUNT(*), SUM(output) FROM calls").fetchone()
        files = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    capsys.readouterr()
    assert cli.run_cli(["--reindex"]) == 0
    assert f"Indexed {files} of {files} transcripts" in capsys.readouterr().err
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*), SUM(output) FROM calls").fetchone() == before


def test_a_bad_legacy_file_does_not_block_the_store(home, capsys, monkeypatch):
    from claude_usage import store

    def boom(conn, claude_dir):
        raise ValueError("invalid literal for int(): 'lots'")  # a hand-edited usage-daily.jsonl row

    monkeypatch.setattr(store, "import_legacy", boom)
    assert cli.run_cli(["--json"]) == 0
    out = capsys.readouterr()
    check(json.loads(out.out))
    assert "could not import the old usage files" in out.err


def test_a_store_from_a_newer_version_is_left_alone(home):
    db = home / "local" / "claude-usage" / "usage.db"
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as c:
        c.execute("PRAGMA user_version=99")
    with pytest.raises(SystemExit, match="newer Claude Usage"):
        cli.run_cli(["--json"])
    with sqlite3.connect(db) as c:
        assert c.execute("PRAGMA user_version").fetchone() == (99,)
