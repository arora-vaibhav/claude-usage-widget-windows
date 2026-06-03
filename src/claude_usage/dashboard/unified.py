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

    def __init__(self, config: dict[str, Any], popup: QWidget, dashboard: QWidget,
                 on_refresh=None) -> None:
        super().__init__()
        self._config = config
        self._theme = get_theme(str(config.get("theme", "default")))
        self._popup = popup
        self._dashboard = dashboard
        self._on_refresh = on_refresh  # callable() to trigger a live refresh

        self.setWindowTitle("Claude Usage")
        self.resize(980, 820)
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
        root.addWidget(self._make_toolbar())
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

    def _make_toolbar(self) -> QWidget:
        """A slim top strip: title + Refresh + Export, right-aligned."""
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton

        t = self._theme
        bar = QWidget()
        bar.setObjectName("uniToolbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 12, 8)
        lay.setSpacing(8)

        title = QLabel("Claude Usage")
        title.setObjectName("uniTitle")
        lay.addWidget(title)
        lay.addStretch(1)

        refresh = QPushButton("↻  Refresh")
        refresh.setCursor(Qt.PointingHandCursor)
        refresh.setObjectName("uniBtn")
        refresh.clicked.connect(self._do_refresh)
        lay.addWidget(refresh)

        export = QPushButton("⤓  Export CSV")
        export.setCursor(Qt.PointingHandCursor)
        export.setObjectName("uniBtn")
        export.clicked.connect(self._do_export)
        lay.addWidget(export)
        return bar

    def _do_refresh(self) -> None:
        if callable(self._on_refresh):
            try:
                self._on_refresh()
            except Exception:
                pass
        if hasattr(self._dashboard, "reload"):
            try:
                self._dashboard.reload()
            except Exception:
                pass

    def _do_export(self) -> None:
        """Export the daily-history store to a CSV the user picks."""
        import csv
        import os

        from PySide6.QtWidgets import QFileDialog

        from claude_usage import daily

        claude_dir = self._config.get("claude_dir") or os.path.expanduser("~/.claude")
        rows = daily.load_daily(os.path.join(claude_dir, daily.DAILY_FILENAME))
        path, _ = QFileDialog.getSaveFileName(
            self, "Export usage history", "claude-usage-history.csv", "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                wtr = csv.writer(f)
                wtr.writerow(["date", "cost_usd", "messages", "input", "output",
                              "cache_read", "cache_creation"])
                for r in rows:
                    tok = r.get("tokens", {})
                    wtr.writerow([
                        r.get("date", ""), f"{r.get('cost', 0):.4f}",
                        r.get("messages", 0), tok.get("input", 0), tok.get("output", 0),
                        tok.get("cache_read", 0), tok.get("cache_creation", 0),
                    ])
        except OSError:
            pass

    def _build_qss(self) -> str:
        t = self._theme
        return f"""
            QWidget#uniToolbar {{ background: {t['bg']}; border-bottom: 1px solid {t['separator']}; }}
            QLabel#uniTitle {{ color: {t['text_primary']}; font-size: 13px; font-weight: bold; }}
            QPushButton#uniBtn {{
                background: {t['bar_track']}; color: {t['text_secondary']};
                border: 0; border-radius: 5px; padding: 5px 12px; font-size: 11px;
            }}
            QPushButton#uniBtn:hover {{ color: {t['text_primary']}; background: {t['separator']}; }}
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
