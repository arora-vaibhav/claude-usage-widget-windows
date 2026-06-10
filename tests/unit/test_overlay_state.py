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
