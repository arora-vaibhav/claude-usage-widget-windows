"""Persistence of the OSD's parked state (position is covered elsewhere;
these cover the minimized flag restore + change signal)."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage.overlay import MINIMIZED_HEIGHT, UsageOverlay  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_starts_expanded_by_default():
    ov = UsageOverlay({"theme": "default"})
    assert ov._minimized is False
    assert ov.height() > MINIMIZED_HEIGHT


def test_restores_minimized_from_config():
    ov = UsageOverlay({"theme": "default", "osd_minimized": True})
    assert ov._minimized is True
    assert ov.height() == MINIMIZED_HEIGHT  # collapsed strip from the first paint


def test_toggle_emits_state_for_persistence():
    ov = UsageOverlay({"theme": "default"})
    seen: list[bool] = []
    ov.minimizedChanged.connect(seen.append)
    ov.toggle_minimized()
    ov.toggle_minimized()
    assert seen == [True, False]
    assert ov._minimized is False


def test_saved_position_accepts_taskbar_band():
    # Regression: the restore guard used availableGeometry (which excludes the
    # taskbar), so an OSD parked over the taskbar snapped back to the default
    # position on every restart. The check must use the FULL screen geometry —
    # only positions with no presence on ANY screen (unplugged monitor) fail.
    from PySide6.QtGui import QGuiApplication

    from claude_usage.widget import _rect_on_some_screen

    geo = QGuiApplication.primaryScreen().geometry()
    # Centre of the screen — trivially visible.
    assert _rect_on_some_screen(geo.center().x(), geo.center().y(), 260, 100)
    # Bottom edge / taskbar band (like a minimized strip parked on the taskbar).
    assert _rect_on_some_screen(geo.x() + 36, geo.bottom() - 20, 260, 6)
    # Far beyond every connected screen — the unplugged-monitor case.
    assert not _rect_on_some_screen(geo.right() + 10_000, geo.bottom() + 10_000, 260, 100)
    assert not _rect_on_some_screen(-20_000, -20_000, 260, 100)
