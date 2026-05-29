"""Render smoke tests for the dashboard. Skipped where PySide6 isn't installed.

These build the real widgets off-screen and grab them to a QImage, so they
catch import/layout/paint regressions without a display.
"""

import json
import os

import pytest

pytest.importorskip("PySide6")  # GUI test — skip in headless/pure-only envs
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage import daily  # noqa: E402
from claude_usage.dashboard import DashboardWindow  # noqa: E402
from claude_usage.dashboard.charts import AreaChart, BarChart  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_bar_chart_renders_nonempty():
    c = BarChart("Title", [("a", 1.0), ("b", 2.0), ("c", 0.0)])
    c.resize(320, 180)
    img = c.grab().toImage()
    assert img.width() == 320 and img.height() == 180
    assert not img.isNull()


def test_bar_chart_empty_state_does_not_crash():
    c = BarChart("Empty", [])
    c.resize(220, 170)
    assert not c.grab().toImage().isNull()


def test_dashboard_builds_and_renders(tmp_path):
    rows = [
        {"date": "2026-05-28", "messages": 10, "sessions": 1,
         "tokens": {"input": 0, "output": 500, "cache_read": 0, "cache_creation": 0},
         "cost": 1.0, "by_model": {"claude-opus-4-8": {"input": 0, "output": 500},
                                   "<synthetic>": {"input": 0, "output": 9}},
         "by_project": {"C--Projects-Demo": 500}, "schema": 1},
        {"date": "2026-05-29", "messages": 20, "sessions": 2,
         "tokens": {"input": 0, "output": 900, "cache_read": 0, "cache_creation": 0},
         "cost": 2.0, "by_model": {"claude-opus-4-8": {"input": 0, "output": 900}},
         "by_project": {"C--Projects-Demo": 900}, "schema": 1},
    ]
    p = tmp_path / daily.DAILY_FILENAME
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    win = DashboardWindow({"theme": "default", "claude_dir": str(tmp_path)})
    win.resize(900, 1000)
    img = win.grab().toImage()
    assert not img.isNull()
    assert img.width() >= 900  # window may clamp up to its min width (header/buttons)


def test_dashboard_empty_store_renders(tmp_path):
    win = DashboardWindow({"theme": "default", "claude_dir": str(tmp_path)})
    win.resize(700, 500)
    assert not win.grab().toImage().isNull()


def test_area_chart_renders_nonempty():
    c = AreaChart("Trend", [("a", 1.0), ("b", 3.0), ("c", 2.0)])
    c.resize(320, 180)
    img = c.grab().toImage()
    assert img.width() == 320 and not img.isNull()


def test_area_chart_single_point_does_not_crash():
    c = AreaChart("One", [("a", 5.0)])
    c.resize(300, 170)
    assert not c.grab().toImage().isNull()


def test_dashboard_range_switch(tmp_path):
    rows = [
        {"date": f"2026-05-{d:02d}", "messages": d, "sessions": 1,
         "tokens": {"input": 0, "output": d * 100, "cache_read": 0, "cache_creation": 0},
         "cost": float(d), "by_model": {"claude-opus-4-8": {"input": 0, "output": d * 100}},
         "by_project": {"C--Projects-Demo": d * 100}, "schema": 1}
        for d in range(1, 21)
    ]
    p = tmp_path / daily.DAILY_FILENAME
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    win = DashboardWindow({"theme": "default", "claude_dir": str(tmp_path)})
    win.resize(960, 1000)
    for rng in (7, 90, None):
        win._render(rng)
        assert not win.grab().toImage().isNull()
