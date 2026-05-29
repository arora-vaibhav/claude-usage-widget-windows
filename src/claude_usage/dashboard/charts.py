"""Custom-painted chart widgets for the dashboard.

Hand-painted with ``QPainter`` (no QtCharts dependency, so the app stays on
PySide6-Essentials) and themed from :mod:`claude_usage.themes`. Each chart is a
self-contained ``QWidget`` that renders deterministically, so it can be grabbed
to a ``QImage`` in tests.
"""

from __future__ import annotations

from typing import Callable, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from claude_usage.themes import get_theme


def _q(hex_str: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_str)
    if alpha < 1.0:
        c.setAlphaF(alpha)
    return c


class _ChartBase(QWidget):
    """Shared scaffolding for the charts: title, axis frame, empty state."""

    def __init__(
        self,
        title: str,
        items: Sequence[tuple[str, float]],
        value_fmt: Callable[[float], str] | None = None,
        theme: dict | None = None,
        accent_key: str = "bar_blue",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._title = title
        self._items = list(items)
        self._fmt = value_fmt or (lambda v: f"{v:,.0f}")
        self._theme = theme or get_theme("default")
        self._accent_key = accent_key
        self.setMinimumHeight(170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_items(self, items: Sequence[tuple[str, float]]) -> None:
        self._items = list(items)
        self.update()

    def _frame(self, p: QPainter):
        """Paint bg + title, return geometry + parsed series, or None if empty."""
        w, h = self.width(), self.height()
        th = self._theme
        pad = 14
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.fillRect(self.rect(), _q(th["bg"]))

        tf = QFont()
        tf.setPointSizeF(11.0)
        tf.setBold(True)
        p.setFont(tf)
        p.setPen(_q(th["text_primary"]))
        p.drawText(QRectF(pad, pad - 2, w - 2 * pad, 20),
                   Qt.AlignLeft | Qt.AlignVCenter, self._title)

        values = [max(0.0, float(v)) for _, v in self._items]
        labels = [str(lbl) for lbl, _ in self._items]
        peak = max(values) if values else 0.0

        geo = {
            "top": pad + 24, "xlabel_h": 16, "left": pad, "right": w - pad,
            "bottom": h - pad - 16, "pad": pad, "w": w, "h": h,
        }
        geo["chart_h"] = max(1.0, geo["bottom"] - geo["top"])
        geo["chart_w"] = max(1.0, geo["right"] - geo["left"])

        if not values or peak <= 0:
            p.setFont(QFont())
            p.setPen(_q(th["text_dim"]))
            p.drawText(QRectF(0, geo["top"], w, geo["chart_h"]),
                       Qt.AlignCenter, "no data yet")
            return None

        sf = QFont()
        sf.setPointSizeF(8.0)
        p.setFont(sf)
        if values[-1] < peak:  # peak ref (skipped when last bar is the peak)
            p.setPen(_q(th["text_dim"]))
            p.drawText(QRectF(pad, geo["top"] - 14, w - 2 * pad, 12),
                       Qt.AlignRight | Qt.AlignVCenter, f"peak {self._fmt(peak)}")
        p.setPen(_q(th["separator"]))
        p.drawLine(int(geo["left"]), int(geo["top"]), int(geo["right"]), int(geo["top"]))
        p.drawLine(int(geo["left"]), int(geo["bottom"]), int(geo["right"]), int(geo["bottom"]))
        return values, labels, peak, geo


class BarChart(_ChartBase):
    """A titled vertical bar chart over ``(label, value)`` pairs."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        parsed = self._frame(p)
        if parsed is None:
            return
        values, labels, peak, geo = parsed
        th = self._theme
        chart_top, chart_bottom = geo["top"], geo["bottom"]
        chart_left, chart_h, chart_w = geo["left"], geo["chart_h"], geo["chart_w"]
        xlabel_h = geo["xlabel_h"]

        n = len(values)
        slot = chart_w / n
        bar_w = max(2.0, min(slot * 0.66, 40.0))
        accent = _q(th.get(self._accent_key, th["bar_blue"]))
        track = _q(th["bar_track"], 0.5)
        label_every = max(1, int(n / 10) + 1)

        sf = QFont()
        sf.setPointSizeF(8.0)
        for i, (val, lbl) in enumerate(zip(values, labels)):
            cx = chart_left + slot * (i + 0.5)
            bx = cx - bar_w / 2
            bh = (val / peak) * chart_h
            by = chart_bottom - bh
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(QRectF(bx, chart_top, bar_w, chart_h), 3, 3)
            p.setBrush(accent)
            p.drawRoundedRect(QRectF(bx, by, bar_w, max(2.0, bh)), 3, 3)
            if i % label_every == 0 or i == n - 1:
                p.setFont(sf)
                p.setPen(_q(th["text_secondary"]))
                p.drawText(QRectF(cx - slot / 2, chart_bottom + 2, slot, xlabel_h),
                           Qt.AlignCenter, lbl)

        last_val = values[-1]
        last_cx = chart_left + slot * (n - 0.5)
        last_by = chart_bottom - (last_val / peak) * chart_h
        vf = QFont()
        vf.setPointSizeF(8.5)
        vf.setBold(True)
        p.setFont(vf)
        p.setPen(_q(th["text_primary"]))
        p.drawText(QRectF(last_cx - slot, last_by - 16, slot * 2, 14),
                   Qt.AlignHCenter | Qt.AlignBottom, self._fmt(last_val))


class AreaChart(_ChartBase):
    """A titled filled-area line chart over ``(label, value)`` pairs (a trend)."""

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        parsed = self._frame(p)
        if parsed is None:
            return
        values, labels, peak, geo = parsed
        th = self._theme
        chart_top, chart_bottom = geo["top"], geo["bottom"]
        chart_left, chart_right = geo["left"], geo["right"]
        chart_h, chart_w, xlabel_h = geo["chart_h"], geo["chart_w"], geo["xlabel_h"]

        accent = _q(th.get(self._accent_key, th["bar_blue"]))
        n = len(values)
        if n == 1:
            xs = [(chart_left + chart_right) / 2]
        else:
            xs = [chart_left + chart_w * i / (n - 1) for i in range(n)]
        ys = [chart_bottom - (v / peak) * chart_h for v in values]
        pts = [QPointF(x, y) for x, y in zip(xs, ys)]

        # Filled area under the line.
        fill = _q(th.get(self._accent_key, th["bar_blue"]), 0.22)
        poly = QPolygonF([QPointF(xs[0], chart_bottom)] + pts + [QPointF(xs[-1], chart_bottom)])
        p.setPen(Qt.NoPen)
        p.setBrush(fill)
        p.drawPolygon(poly)

        # The line itself.
        pen = QPen(accent)
        pen.setWidthF(2.0)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        if n == 1:
            p.setBrush(accent)
            p.drawEllipse(pts[0], 3.0, 3.0)
        else:
            p.drawPolyline(QPolygonF(pts))

        # Latest-point dot.
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawEllipse(pts[-1], 3.2, 3.2)

        # X labels (thinned) + latest value.
        sf = QFont()
        sf.setPointSizeF(8.0)
        p.setFont(sf)
        p.setPen(_q(th["text_secondary"]))
        label_every = max(1, int(n / 10) + 1)
        for i, (x, lbl) in enumerate(zip(xs, labels)):
            if i % label_every == 0 or i == n - 1:
                p.drawText(QRectF(x - 24, chart_bottom + 2, 48, xlabel_h),
                           Qt.AlignCenter, lbl)
        vf = QFont()
        vf.setPointSizeF(8.5)
        vf.setBold(True)
        p.setFont(vf)
        p.setPen(_q(th["text_primary"]))
        p.drawText(QRectF(xs[-1] - 64, ys[-1] - 18, 64, 14),
                   Qt.AlignRight | Qt.AlignBottom, self._fmt(values[-1]))
