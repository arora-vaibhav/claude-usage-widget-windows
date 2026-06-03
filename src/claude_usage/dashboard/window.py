"""The Dashboard window — a full-size, themed view of the usage history.

Reads the daily-rollup store (:mod:`claude_usage.daily`) and renders the
history dimensions the user asked for — a cost *trend* over time, tokens/day,
messages/day, and per-model / per-project breakdowns — with a 7/30/90/All-day
range selector. No live API calls: it visualises the recorded daily history,
so it works regardless of auth state.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from claude_usage import daily
from claude_usage.dashboard.charts import AreaChart, BarChart, StackedBar
from claude_usage.themes import get_theme

_RANGES: tuple[tuple[str, int | None], ...] = (("7d", 7), ("30d", 30), ("90d", 90), ("All", None))


def _fmt_tokens(v: float) -> str:
    v = float(v)
    if v >= 1e6:
        return f"{v / 1e6:.1f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}K"
    return f"{v:.0f}"


def _fmt_usd(v: float) -> str:
    return f"${v:,.0f}" if v >= 1 else f"${v:.2f}"


def _short_model(m: str) -> str:
    return m[len("claude-"):] if m.startswith("claude-") else m


def _short_project(p: str) -> str:
    if "Projects-" in p:
        p = p.rsplit("Projects-", 1)[1]
    elif "--" in p:
        p = p.rsplit("--", 1)[1]
    p = p.strip("-")
    return (p[:16] + "…") if len(p) > 17 else p


class DashboardWindow(QWidget):
    """Full-size usage-history dashboard (opened from the OSD widget / tray)."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        cfg = config or {}
        self._theme = get_theme(str(cfg.get("theme", "default")))
        claude_dir = cfg.get("claude_dir") or os.path.expanduser("~/.claude")
        self._daily_path = os.path.join(claude_dir, daily.DAILY_FILENAME)
        self._rows = daily.load_daily(self._daily_path)
        self._range_days: int | None = 30
        self._stats = None  # latest live UsageStats (for hourly/heatmap charts)

        self.setWindowTitle("Claude Usage — History")
        self.resize(960, 780)
        self.setStyleSheet(
            f"background:{self._theme['bg']}; color:{self._theme['text_primary']};"
        )
        self._build()

    def reload(self) -> None:
        """Re-read the daily store from disk and re-render the current range.

        Cheap (one small JSONL read); call when the History view becomes
        visible so newly-recorded days appear without reopening the window.
        """
        self._rows = daily.load_daily(self._daily_path)
        self._render(self._range_days)

    def set_stats(self, stats) -> None:
        """Store the latest live ``UsageStats`` so the History tab can chart the
        rich live-only series (hourly activity, etc.) alongside the daily store.

        Only re-renders if the panel is currently showing — the per-refresh
        snapshot is cheap to stash but we avoid rebuilding an unseen tab.
        """
        self._stats = stats
        if self.isVisible():
            self._render(self._range_days)

    # -- construction ------------------------------------------------------
    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._make_header())

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        self._body_layout = QVBoxLayout(body)
        self._body_layout.setContentsMargins(16, 8, 16, 16)
        self._body_layout.setSpacing(14)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        self._render(self._range_days)

    def _make_header(self) -> QWidget:
        th = self._theme
        box = QWidget()
        box.setStyleSheet(f"background:{th['bg']}; border-bottom:1px solid {th['separator']};")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(6)

        title = QLabel("Claude Usage — History")
        tf = QFont()
        tf.setPointSizeF(15.0)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color:{th['text_primary']};")
        lay.addWidget(title)

        self._summary = QLabel("")
        sf = QFont()
        sf.setPointSizeF(9.5)
        self._summary.setFont(sf)
        self._summary.setStyleSheet(f"color:{th['text_secondary']};")
        lay.addWidget(self._summary)

        rangerow = QHBoxLayout()
        rangerow.setSpacing(6)
        rangerow.setContentsMargins(0, 4, 0, 0)
        self._range_buttons: dict[int | None, QPushButton] = {}
        for label, days in _RANGES:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(24)
            b.clicked.connect(lambda _=False, d=days: self._render(d))
            self._range_buttons[days] = b
            rangerow.addWidget(b)
        rangerow.addStretch(1)
        lay.addLayout(rangerow)
        return box

    # -- rendering ---------------------------------------------------------
    def _render(self, days: int | None) -> None:
        self._range_days = days
        self._clear_body()
        th = self._theme
        rows = self._rows if days is None else self._rows[-days:]

        if not rows:
            empty = QLabel("No usage history yet — it builds as the widget runs.")
            empty.setStyleSheet(f"color:{th['text_secondary']};")
            empty.setAlignment(Qt.AlignCenter)
            self._body_layout.addWidget(empty)
            self._summary.setText("No history yet.")
            self._update_range_buttons()
            return

        self._body_layout.addWidget(AreaChart(
            "Cost per day  (API-equivalent USD)",
            [(r["date"][5:], r.get("cost", 0.0)) for r in rows],
            value_fmt=_fmt_usd, theme=th, accent_key="bar_blue"))
        self._body_layout.addWidget(BarChart(
            "Output tokens per day",
            [(r["date"][5:], r.get("tokens", {}).get("output", 0)) for r in rows],
            value_fmt=_fmt_tokens, theme=th, accent_key="bar_blue"))
        self._body_layout.addWidget(BarChart(
            "Messages per day",
            [(r["date"][5:], r.get("messages", 0)) for r in rows],
            value_fmt=lambda v: f"{v:,.0f}", theme=th, accent_key="live_indicator"))

        models: dict[str, float] = {}
        for r in rows:
            for m, b in (r.get("by_model") or {}).items():
                if m.startswith("<"):
                    continue
                models[m] = models.get(m, 0) + (b.get("output", 0) or 0)
        top_models = sorted(models.items(), key=lambda kv: kv[1], reverse=True)[:6]
        # Composition at a glance: one stacked bar of model share.
        if top_models:
            self._body_layout.addWidget(StackedBar(
                "Output share by model",
                [(_short_model(m), v) for m, v in top_models],
                value_fmt=_fmt_tokens, theme=th))
        self._body_layout.addWidget(BarChart(
            "Output tokens by model",
            [(_short_model(m), v) for m, v in top_models],
            value_fmt=_fmt_tokens, theme=th, accent_key="warn"))

        projects: dict[str, float] = {}
        for r in rows:
            for proj, v in (r.get("by_project") or {}).items():
                projects[proj] = projects.get(proj, 0) + (v or 0)
        top_proj = sorted(projects.items(), key=lambda kv: kv[1], reverse=True)[:8]
        self._body_layout.addWidget(BarChart(
            "Output tokens by project",
            [(_short_project(p), v) for p, v in top_proj],
            value_fmt=_fmt_tokens, theme=th, accent_key="text_link"))

        # Live-only series: "Activity by hour of day" from the 24-bucket hourly
        # histogram (computed each refresh, previously trapped in the text popup).
        # Values are normalized 0-1 intensities (relative activity), so render as
        # a percentage of the busiest hour rather than a raw count.
        hourly = list(getattr(self._stats, "hourly_histogram", []) or [])
        if len(hourly) == 24 and any(v > 0 for v in hourly):
            self._body_layout.addWidget(BarChart(
                "Activity by hour of day  (relative intensity, local)",
                [(f"{h:02d}", float(hourly[h]) * 100.0) for h in range(24)],
                value_fmt=lambda v: f"{v:.0f}%", theme=th, accent_key="warn"))

        self._body_layout.addStretch(1)

        total = sum(r.get("cost", 0.0) for r in rows)
        scope = "all time" if days is None else f"last {days}d"
        self._summary.setText(
            f"{len(rows)} days ({scope})  ·  {rows[0]['date']} → {rows[-1]['date']}  ·  "
            f"${total:,.0f} total  ·  today ${self._rows[-1].get('cost', 0.0):,.0f}"
        )
        self._update_range_buttons()

    def _clear_body(self) -> None:
        while self._body_layout.count():
            item = self._body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _update_range_buttons(self) -> None:
        th = self._theme
        for days, b in self._range_buttons.items():
            if days == self._range_days:
                b.setStyleSheet(
                    f"QPushButton{{background:{th['bar_blue']}; color:{th['bg']}; border:none;"
                    f" border-radius:5px; padding:3px 12px; font-weight:bold;}}"
                )
            else:
                b.setStyleSheet(
                    f"QPushButton{{background:{th['bar_track']}; color:{th['text_secondary']};"
                    f" border:none; border-radius:5px; padding:3px 12px;}}"
                    f"QPushButton:hover{{color:{th['text_primary']};}}"
                )
