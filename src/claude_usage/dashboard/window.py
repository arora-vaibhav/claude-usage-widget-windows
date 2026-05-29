"""The Dashboard window — a full-size, themed view of the usage history.

Reads the daily-rollup store (:mod:`claude_usage.daily`) and renders the four
history dimensions the user asked for — usage cost over time, tokens/day,
messages/day, and per-model / per-project breakdowns — as custom-painted bar
charts. No live API calls: it visualises the recorded daily history, so it
works regardless of auth state.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from claude_usage import daily
from claude_usage.dashboard.charts import BarChart
from claude_usage.themes import get_theme


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
    # Project dirs flatten the full cwd to "-", e.g. "C--Projects-Job-Apps" or
    # "C--Users-V--claude". Show the distinctive tail: prefer what follows
    # "Projects-", else the last "--"-delimited segment.
    if "Projects-" in p:
        p = p.rsplit("Projects-", 1)[1]
    elif "--" in p:
        p = p.rsplit("--", 1)[1]
    p = p.strip("-")
    return (p[:16] + "…") if len(p) > 17 else p


class DashboardWindow(QWidget):
    """Full-size usage-history dashboard (opened from the OSD widget)."""

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        cfg = config or {}
        self._theme = get_theme(str(cfg.get("theme", "default")))
        claude_dir = cfg.get("claude_dir") or os.path.expanduser("~/.claude")
        self._daily_path = os.path.join(claude_dir, daily.DAILY_FILENAME)

        self.setWindowTitle("Claude Usage — History")
        self.resize(940, 760)
        self.setStyleSheet(
            f"background:{self._theme['bg']}; color:{self._theme['text_primary']};"
        )
        self._build()

    def _build(self) -> None:
        rows = daily.load_daily(self._daily_path)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._make_header(rows))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(16, 8, 16, 16)
        col.setSpacing(14)

        if not rows:
            empty = QLabel("No usage history yet — it builds as the widget runs.")
            empty.setStyleSheet(f"color:{self._theme['text_secondary']};")
            empty.setAlignment(Qt.AlignCenter)
            col.addWidget(empty)
        else:
            recent = rows[-30:]
            th = self._theme
            col.addWidget(BarChart(
                "Cost per day  (API-equivalent USD)",
                [(r["date"][5:], r.get("cost", 0.0)) for r in recent],
                value_fmt=_fmt_usd, theme=th, accent_key="bar_blue"))
            col.addWidget(BarChart(
                "Output tokens per day",
                [(r["date"][5:], r.get("tokens", {}).get("output", 0)) for r in recent],
                value_fmt=_fmt_tokens, theme=th, accent_key="bar_blue"))
            col.addWidget(BarChart(
                "Messages per day",
                [(r["date"][5:], r.get("messages", 0)) for r in recent],
                value_fmt=lambda v: f"{v:,.0f}", theme=th, accent_key="live_indicator"))

            models: dict[str, float] = {}
            for r in rows:
                for m, b in (r.get("by_model") or {}).items():
                    if m.startswith("<"):
                        continue  # skip Claude Code bookkeeping (<synthetic>)
                    models[m] = models.get(m, 0) + (b.get("output", 0) or 0)
            top_models = sorted(models.items(), key=lambda kv: kv[1], reverse=True)[:6]
            col.addWidget(BarChart(
                "Output tokens by model  (whole history)",
                [(_short_model(m), v) for m, v in top_models],
                value_fmt=_fmt_tokens, theme=th, accent_key="warn"))

            projects: dict[str, float] = {}
            for r in rows:
                for proj, v in (r.get("by_project") or {}).items():
                    projects[proj] = projects.get(proj, 0) + (v or 0)
            top_proj = sorted(projects.items(), key=lambda kv: kv[1], reverse=True)[:8]
            col.addWidget(BarChart(
                "Output tokens by project  (whole history)",
                [(_short_project(p), v) for p, v in top_proj],
                value_fmt=_fmt_tokens, theme=th, accent_key="text_link"))

        col.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    def _make_header(self, rows: list[dict]) -> QWidget:
        th = self._theme
        box = QWidget()
        box.setStyleSheet(f"background:{th['bg']}; border-bottom:1px solid {th['separator']};")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(2)

        title = QLabel("Claude Usage — History")
        tf = QFont()
        tf.setPointSizeF(15.0)
        tf.setBold(True)
        title.setFont(tf)
        title.setStyleSheet(f"color:{th['text_primary']};")
        lay.addWidget(title)

        if rows:
            total = sum(r.get("cost", 0.0) for r in rows)
            today = rows[-1].get("cost", 0.0)
            sub = QLabel(
                f"{len(rows)} days  ·  {rows[0]['date']} → {rows[-1]['date']}  ·  "
                f"${total:,.0f} total  ·  today ${today:,.0f}"
            )
        else:
            sub = QLabel("No history yet.")
        sf = QFont()
        sf.setPointSizeF(9.5)
        sub.setFont(sf)
        sub.setStyleSheet(f"color:{th['text_secondary']};")
        lay.addWidget(sub)
        return box
