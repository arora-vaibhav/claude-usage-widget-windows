"""Unified tabbed window — Overview (live) + History (charts) in one place.

The app historically had two separate windows: the live ``UsagePopup`` (plan
limits, today's cost, tips) and the ``DashboardWindow`` (recorded daily-history
charts). Users wanted them *together*, switchable by tab. This window hosts both
as tab pages so they share one frame, one position, and one close button — while
reusing the existing render code in each (no duplication).

The live "Overview" tab is fed by :meth:`update_stats` each refresh (same
``UsageStats`` the OSD/popup consume). The "History" tab reloads its daily store
from disk whenever it becomes visible, so newly-recorded days appear without
reopening.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from claude_usage.themes import get_theme


class UnifiedWindow(QWidget):
    """A single window with Overview (live) and History (charts) tabs."""

    def __init__(self, config: dict[str, Any], popup: QWidget, dashboard: QWidget) -> None:
        super().__init__()
        self._config = config
        self._theme = get_theme(str(config.get("theme", "default")))
        self._popup = popup
        self._dashboard = dashboard

        self.setWindowTitle("Claude Usage")
        self.resize(980, 800)
        self.setMinimumSize(520, 460)
        # Utility window: stay on top, keep a native close button, hidden from
        # the taskbar (exit is via the OSD/tray menu).
        self.setWindowFlags(Qt.Tool | Qt.WindowStaysOnTopHint | Qt.WindowCloseButtonHint)
        self.setAttribute(Qt.WA_X11NetWmWindowTypeUtility, True)

        self._tabs = QTabWidget(self)
        self._tabs.setDocumentMode(True)
        self._tabs.addTab(self._wrap(popup), "📊  Overview")
        self._tabs.addTab(self._wrap(dashboard), "📈  History")
        self._tabs.currentChanged.connect(self._on_tab_changed)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._tabs)

        self.setStyleSheet(self._build_qss())

    @staticmethod
    def _wrap(inner: QWidget) -> QWidget:
        """Host an embedded page widget in a fresh container with a tight layout.

        The embedded popup/dashboard were designed as top-level windows; wrapping
        them strips their window-chrome margins so they sit flush in the tab.
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        inner.setParent(page)
        # Embedded child must not act as its own window.
        inner.setWindowFlags(Qt.Widget)
        lay.addWidget(inner)
        inner.show()
        return page

    def _build_qss(self) -> str:
        t = self._theme
        return f"""
            QTabWidget::pane {{ border: 0; background: {t['bg']}; }}
            QTabBar {{ background: {t['bg']}; }}
            QTabBar::tab {{
                background: {t['bar_track']};
                color: {t['text_secondary']};
                padding: 7px 18px;
                margin-right: 2px;
                border: 0;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-size: 12px;
            }}
            QTabBar::tab:selected {{
                background: {t['bar_blue']};
                color: {t['bg']};
                font-weight: bold;
            }}
            QTabBar::tab:hover:!selected {{ color: {t['text_primary']}; }}
        """

    # -- live data + lifecycle --------------------------------------------
    def update_stats(self, stats: Any) -> None:
        """Forward a fresh stats snapshot to the embedded live Overview page.

        The standalone ``UsagePopup`` defers its (re)build until it is visible
        and flushes in ``showEvent``. Embedded in a tab it may already be
        "visible" (in the hidden Overview page) or not yet shown, so we drive
        the rebuild eagerly here to guarantee the Overview tab has content
        whether or not a ``showEvent`` has fired.
        """
        popup = self._popup
        if hasattr(popup, "update_stats"):
            popup.update_stats(stats)
        # Force a build if the deferred path left the layout empty.
        rebuild = getattr(popup, "_rebuild_from", None)
        layout = getattr(popup, "_layout", None)
        if callable(rebuild) and layout is not None and layout.count() == 0:
            try:
                rebuild(stats)
            except Exception:
                pass

    def show_tab(self, index: int) -> None:
        """Select a tab by index (0 = Overview, 1 = History) before showing."""
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def _on_tab_changed(self, index: int) -> None:
        # Refresh the History tab from disk when it becomes visible.
        if index == 1 and hasattr(self._dashboard, "reload"):
            try:
                self._dashboard.reload()
            except Exception:
                pass
