"""Render + behaviour tests for the unified tabbed window."""

import json
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage import daily  # noqa: E402
from claude_usage.collector import UsageStats  # noqa: E402
from claude_usage.dashboard import DashboardWindow, UnifiedWindow  # noqa: E402
from claude_usage.widget import UsagePopup  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _seed_daily(tmp_path):
    rows = [
        {"date": "2026-06-02", "messages": 10, "sessions": 1,
         "tokens": {"input": 0, "output": 500, "cache_read": 0, "cache_creation": 0},
         "cost": 1.0, "by_model": {"claude-opus-4-8": {"input": 0, "output": 500}},
         "by_project": {"C--Projects-Demo": 500}, "schema": 1},
        {"date": "2026-06-03", "messages": 20, "sessions": 2,
         "tokens": {"input": 0, "output": 900, "cache_read": 0, "cache_creation": 0},
         "cost": 2.0, "by_model": {"claude-opus-4-8": {"input": 0, "output": 900}},
         "by_project": {"C--Projects-Demo": 900}, "schema": 1},
    ]
    (tmp_path / daily.DAILY_FILENAME).write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _make(tmp_path):
    cfg = {"theme": "default", "claude_dir": str(tmp_path)}
    return UnifiedWindow(cfg, UsagePopup(cfg), DashboardWindow(cfg))


def test_unified_has_two_tabs(tmp_path):
    _seed_daily(tmp_path)
    win = _make(tmp_path)
    assert win._tabs.count() == 2
    assert "Overview" in win._tabs.tabText(0)
    assert "History" in win._tabs.tabText(1)


def test_overview_tab_builds_on_update_stats(tmp_path):
    _seed_daily(tmp_path)
    win = _make(tmp_path)
    stats = UsageStats(session_utilization=0.22, weekly_utilization=0.09,
                       today_cost=2.0, subscription_type="max")
    win.update_stats(stats)
    # The embedded popup must have content after update_stats (eager build).
    assert win._popup._layout.count() > 0


def test_both_tabs_render_nonempty(tmp_path):
    _seed_daily(tmp_path)
    win = _make(tmp_path)
    win.update_stats(UsageStats(session_utilization=0.5, today_cost=3.0))
    win.resize(960, 800)
    win.show_tab(0)
    _app.processEvents()
    assert not win.grab().toImage().isNull()
    win.show_tab(1)  # triggers dashboard reload
    _app.processEvents()
    assert not win.grab().toImage().isNull()


def test_show_tab_switches(tmp_path):
    _seed_daily(tmp_path)
    win = _make(tmp_path)
    win.show_tab(1)
    assert win._tabs.currentIndex() == 1
    win.show_tab(0)
    assert win._tabs.currentIndex() == 0
