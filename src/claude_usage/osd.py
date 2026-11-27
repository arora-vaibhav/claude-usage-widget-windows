"""The always-on-top OSD: a 32px pill or a 296x136 card with the Claude limits.

Driven by the snapshot "now" dict (``NOW`` in tests/unit/snapshot_contract.py).
The binding limit (``now["primary"]``) always comes first. Layout is built as a
list of :class:`Item` in unscaled px so tests can check text boxes and pixels;
painting only draws those items.
"""

from __future__ import annotations

import ctypes
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta

from PySide6.QtCore import (
    QDate,
    QEasingCurve,
    QLocale,
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTime,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QWidget

from claude_usage import tokens

STYLES = ("pill", "card")
CARD_W, CARD_H, PILL_H = 296, 136, 32
RADIUS = 8
MIN_OPACITY = 0.85  # the plate never goes below this while it carries text
# Light stays opaque: thin muted digits ('1h 14m') measure under 4.5:1 even at 0.95, and a
# 5% see-through white plate is invisible anyway.
MIN_OPACITY_LIGHT = 1.0
DRAG_THRESHOLD = 5
EDGE_MARGIN = 16
# Family plus weight and the opsz axis picks the Text/Display cut on both
# DirectWrite and FreeType; the "Segoe UI Variable Text" family name does not.
FAMILIES = ["Segoe UI Variable", "Segoe UI"]
RANK = {"none": 0, "normal": 1, "caution": 2, "critical": 3, "limited": 4}
NAMES = {"weekly": ("7d", "Weekly"), "session": ("5h", "5-hour")}  # pill tag, and the name the menu, tray and alerts use
NO_LIMITS = "Claude didn't report a weekly or 5-hour limit."
# Each pill countdown keeps the room of the widest string its limit shows in normal use
# (weekly '6d 23h', '23h 59m', forecast '~23h 59m'; 5h '4h 59m', forecast '~4h 59m'), so the
# pill keeps its width and nothing in it moves as the numbers tick. Forecasts ('~') are semibold.
SLOTS = {"weekly": ("00h 00m", "~00h 00m"), "session": ("0h 00m", "~0h 00m")}

LOADING = {
    "primary": "weekly", "weekly": None, "session": None, "today_cost": 0.0,
    "today_tokens": 0, "tokens_per_min": 0.0, "active_sessions": 0,
    "active_subagents": 0, "live": False, "state": "loading", "as_of": None,
    "message": None, "error_kind": None,
}


@dataclass
class Item:
    kind: str                       # text, meter, dot, icon, skel, button, rule
    rect: QRectF                    # text: tight ink box; others: the shape
    color: QColor
    text: str = ""
    font: QFont | None = None
    origin: QPointF | None = None   # text baseline start
    role: str = ""                  # value, unit, tag, label, sub, status, cost, title, body, action
    frac: float = 0.0               # meter fill 0..1
    track: QColor | None = None     # meter track
    icon: str = ""                  # warn, alert, clock


# ---------------------------------------------------------------- formatting

def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone()
    except ValueError:
        return None


def _span(td: timedelta) -> str:
    """Compact countdown: '5d 17h', '2h 30m', '13h', '45m', '<1m'. Ui.span in the app matches it."""
    mins = max(0, int(td.total_seconds() // 60))
    d, mins = divmod(mins, 1440)
    h, m = divmod(mins, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{m}m" if m else "<1m"


def _clock(dt: datetime) -> str:
    return QLocale.system().toString(QTime(dt.hour, dt.minute), QLocale.ShortFormat)


def _when(dt: datetime, now: datetime) -> str:
    """'4:31 PM' today, 'Tue 8:00 PM' within the week, else 'Dec 2 8:00 PM'."""
    days = (dt.date() - now.date()).days
    if days <= 0:
        return _clock(dt)
    loc = QLocale.system()
    if days < 7:
        return f"{loc.dayName(dt.isoweekday(), QLocale.ShortFormat)} {_clock(dt)}"
    return f"{loc.toString(QDate(dt.year, dt.month, dt.day), 'MMM d')} {_clock(dt)}"


def _resets(reset: datetime, now: datetime) -> list[str]:
    """Reset phrase options, longest first."""
    if reset - now < timedelta(hours=24):
        s = _span(reset - now)
        return [f"Resets in {s}", f"In {s}"]
    w = _when(reset, now)
    return [f"Resets {w}", w]


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _rate(tpm: float) -> str:
    if round(tpm / 1000, 1) >= 1000:  # 999,960 would print as "1000.0k"
        return f"{tpm / 1e6:.1f}M tok/min"
    if tpm >= 1000:
        return f"{tpm / 1000:.1f}k tok/min"
    return f"{int(tpm)} tok/min"


def _retries(now: dict) -> bool:
    """True unless the store is the problem (one from a newer version): the worker keeps retrying a
    failed fetch by itself, but a store error needs the user, so the OSD shows its message alone,
    without "Can't reach Claude", "Retrying automatically" or the empty stand-in store's $0."""
    return now.get("error_kind") != "store"


def _indexing(ix: dict) -> str:
    """'Indexing 19%' for a first index or a re-index in progress (``{done, total}`` transcripts),
    floored as in the app; plain 'Indexing' below 1%, where '0%' reads as stuck."""
    pct = 100 * ix["done"] // ix["total"] if ix["total"] else 0
    return f"Indexing {pct}%" if pct else "Indexing"


def _not_started(m: dict) -> bool:
    return m["resets_at"] is None and m["pct"] == 0


def _pct(m: dict) -> str:
    """Whole percent, half up like the app's toFixed(0); never 100 before the limit is really hit."""
    if _not_started(m):
        return "--"
    v = math.floor(m["pct"] * 100 + 0.5)
    return str(v if m["pct"] >= 1 else min(v, 99))


def _font(px: float, weight: int = 400) -> QFont:
    f = QFont()
    f.setFamilies(FAMILIES)
    f.setPixelSize(round(px))
    f.setWeight(QFont.Weight(weight))
    f.setVariableAxis(QFont.Tag("opsz"), 36.0 if px >= 20 else 10.5)
    f.setFeature(QFont.Tag("tnum"), 1)
    return f


# ---------------------------------------------------------------- layout ops

class _Ops:
    """Collects Items, snapping every edge and baseline to device pixels."""

    def __init__(self, dpr: float) -> None:
        self.dpr = dpr
        self.items: list[Item] = []

    def snap(self, v: float) -> float:
        return round(v * self.dpr) / self.dpr

    def length(self, v: float) -> float:
        return max(1, round(v * self.dpr)) / self.dpr

    def text(self, x: float, baseline: float, text: str, font: QFont, color: QColor,
             role: str, right: bool = False, width: float | None = None) -> float:
        fm = QFontMetricsF(font)
        if width is not None and fm.horizontalAdvance(text) > width:
            while text and fm.horizontalAdvance(text + "...") > width:
                text = text[:-1]
            text = text.rstrip() + "..."
        adv = fm.horizontalAdvance(text)
        x, baseline = self.snap(x - adv if right else x), self.snap(baseline)
        box = fm.tightBoundingRect(text).translated(x, baseline)
        self.items.append(Item("text", box, color, text, font, QPointF(x, baseline), role))
        return adv

    def shape(self, kind: str, x: float, y: float, w: float, h: float, color: QColor, **kw) -> QRectF:
        r = QRectF(self.snap(x), self.snap(y), self.length(w), self.length(h))
        self.items.append(Item(kind, r, color, **kw))
        return r


def _fit(options: list[str], font: QFont, width: float) -> str:
    fm = QFontMetricsF(font)
    for t in options:
        if fm.horizontalAdvance(t) <= width:
            return t
    return options[-1] if options else ""


def _wrap(text: str, font: QFont, width: float, lines: int = 2) -> list[str]:
    """Word-wrap into at most `lines` lines; the last keeps the rest (the caller elides it)."""
    fm = QFontMetricsF(font)
    words, out = text.split(), []
    while words and len(out) < lines - 1:
        line = words.pop(0)
        while words and fm.horizontalAdvance(f"{line} {words[0]}") <= width:
            line += " " + words.pop(0)
        out.append(line)
    if words:
        out.append(" ".join(words))
    return out


# ---------------------------------------------------------------- the widget

class Osd(QWidget):
    """Frameless top-most OSD. Click opens the app, right-click the menu, drag moves it."""

    clicked = Signal()
    right_clicked = Signal(QPoint)
    moved = Signal(int, int)
    style_toggled = Signal(str)

    def __init__(self, style: str = "card", dark: bool = True, opacity: float = 1.0,
                 scale: float = 1.0) -> None:
        super().__init__()
        self._style = style if style in STYLES else "card"
        self._dark = bool(dark)
        self._opacity = 1.0
        self._scale = 1.0
        self._now: dict = dict(LOADING)
        self._ix: dict | None = None
        # Layout and paint both read this clock, so a countdown cannot change
        # width between them; it advances only on set_now.
        self._t = datetime.now().astimezone()
        self._saved: QPoint | None = None  # the spot last reported through moved (see _spot)
        self._hover = False
        self._press: QPoint | None = None
        self._press_pos = QPoint()
        self._dragging = False

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.Tool                       # no taskbar button
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus   # never steals typing focus
            | Qt.BypassWindowManagerHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_AlwaysShowToolTips)  # the window is never active, and Qt hides tips of inactive ones
        self.setAttribute(Qt.WA_X11NetWmWindowTypeNotification, True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName("Claude usage")

        # 150 ms meter tween and a single 600 ms stroke flash on a threshold
        # crossing. Both only run while visible; nothing animates at rest.
        self._from: dict[str, float] = {}
        self._tween = QVariantAnimation(self)
        self._tween.setDuration(150)
        self._tween.setStartValue(0.0)
        self._tween.setEndValue(1.0)
        self._tween.setEasingCurve(QEasingCurve.OutCubic)
        self._tween.valueChanged.connect(self.update)
        self._flash = QVariantAnimation(self)
        self._flash.setDuration(600)
        self._flash.setStartValue(1.0)
        self._flash.setEndValue(0.0)
        self._flash.valueChanged.connect(self.update)

        self.set_opacity(opacity)
        self.set_scale(scale)
        self._move_to_default_position()
        self._set_tip()

    # ------------------------------------------------------------------ API

    def set_now(self, now: dict, indexing: dict | None = None) -> None:
        """``indexing`` is the first index's (or a re-index's) ``{done, total}`` while it runs: today's
        cost is then partial, so the card and tooltip say so instead of showing it as final."""
        old = self._now
        self._ix = indexing
        self._from = {k: self._frac(k, old[k]["pct"]) for k in ("weekly", "session") if old[k]}
        self._now = dict(now)
        self._t = datetime.now().astimezone()
        first = old["state"] == "loading"
        if self.isVisible():
            if any(self._now[k] and self._from.get(k) != self._now[k]["pct"] for k in self._from):
                self._tween.start()
            if not first and self._worst() > max(self._worst(old), RANK["normal"]) and self._now["state"] == "ok":
                self._flash.start()
        self._set_tip()
        # The first data also pulls a restored spot fully onto a screen (a monitor may have shrunk).
        self._relayout(clamp=first)

    def set_dark(self, dark: bool) -> None:
        self._dark = bool(dark)
        self.update()

    def set_style(self, style: str) -> None:
        if style not in STYLES or style == self._style:
            return
        self._style = style
        self._relayout()
        self.style_toggled.emit(style)

    def toggle_style(self) -> None:
        self.set_style("card" if self._style == "pill" else "pill")

    def osd_style(self) -> str:
        return self._style

    def set_opacity(self, value: float) -> None:
        """User opacity, used only while idle and normal, and never below MIN_OPACITY (MIN_OPACITY_LIGHT in light)."""
        self._opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def set_scale(self, value: float) -> None:
        # Half steps keep every size on the 4px grid (all base sizes are multiples of 8).
        self._scale = min(2.0, max(1.0, round(float(value) * 2) / 2))
        self._relayout()

    def effective_opacity(self) -> float:
        alert = self._now["state"] in ("stale", "signed_out", "error") or self._worst() >= RANK["caution"]
        return 1.0 if self._hover or alert else max(MIN_OPACITY if self._dark else MIN_OPACITY_LIGHT, self._opacity)

    def keep_on_top(self) -> None:
        """Re-assert top-most without activating; Windows drops it when other top-most windows appear."""
        if not self.isVisible():
            return
        if sys.platform == "win32":
            try:
                fn = ctypes.WinDLL("user32").SetWindowPos
                fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
                # HWND_TOPMOST; SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER
                fn(int(self.winId()), ctypes.c_void_p(-1), 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010 | 0x0200)
                return
            except (OSError, AttributeError):
                pass
        self.raise_()

    def items(self, dpr: float = 1.0) -> list[Item]:
        """The items to paint, in unscaled px (multiply by the scale for widget px)."""
        return self._build(dpr)[1]

    def render_image(self, dpr: float = 1.0) -> QImage:
        """What the screen shows (opacity included) as a transparent image."""
        return self._image(dpr, self._draw)

    # ------------------------------------------------------------- state

    def _pal(self) -> dict[str, QColor]:
        return {k: QColor(v) for k, v in tokens.palette(self._dark).items()}

    def _meters(self, now: dict | None = None) -> list[tuple[str, dict]]:
        n = now or self._now
        order = ("session", "weekly") if n["primary"] == "session" else ("weekly", "session")
        return [(k, n[k]) for k in order if n[k]]

    def _worst(self, now: dict | None = None) -> int:
        return max((RANK[m["level"]] for _, m in self._meters(now)), default=0)

    def _eta(self, m: dict) -> datetime | None:
        """When the limit lands at this pace, if that is before the reset."""
        reset, eta = _parse(m["resets_at"]), _parse(m["eta"])
        if m["level"] != "limited" and reset and eta and self._t < reset and eta < reset:
            return eta
        return None

    def _frac(self, key: str, pct: float) -> float:
        t = self._tween.currentValue() if self._tween.state() == QVariantAnimation.Running else 1.0
        a = self._from.get(key, pct)
        return max(0.0, min(1.0, a + (pct - a) * float(t)))

    def _level_colors(self, level: str, pal: dict) -> tuple[QColor, QColor]:
        """(meter or icon fill, text) colours for a level."""
        if level in ("critical", "limited"):
            return pal["bad_fill"], pal["bad"]
        if level == "caution":
            return pal["warn_fill"], pal["warn"]
        return pal["accent"], pal["text_muted"]

    def _fill(self, m: dict, pal: dict) -> QColor:
        """Meter fill; grey with the numbers when they are last-known values (still 3:1 on the track)."""
        if self._now["state"] in ("stale", "error"):
            return pal["text_faint"]
        return self._level_colors(m["level"], pal)[0]

    def _value_color(self, pal: dict) -> QColor:
        """Values are dimmed while last-known; light faint text renders under 4.5:1, so light uses muted."""
        if self._now["state"] in ("stale", "error"):
            return pal["text_faint" if self._dark else "text_muted"]
        return pal["text"]

    def _status(self, pal: dict) -> tuple[str, QColor, str]:
        """(icon kind or '', icon colour, text) for the reserved status slot."""
        n = self._now
        as_of = _parse(n["as_of"])
        if n["state"] == "loading":
            return "", pal["text_faint"], "Loading usage"
        if n["state"] == "signed_out":
            return "", pal["text_faint"], "Signed out"
        if n["state"] in ("stale", "error"):
            # The face keeps the as-of time; the reason is in the tooltip.
            kind, color = ("alert", pal["bad_fill"]) if n["state"] == "error" else ("clock", pal["text_muted"])
            if as_of:
                return kind, color, f"Stale, as of {_clock(as_of)}"
            if n["state"] == "stale":
                return kind, color, "Stale"
            return kind, color, "Can't reach Claude" if _retries(n) else "Claude usage"
        if n["live"]:
            return "dot", pal["ok"], "Live " + _rate(n["tokens_per_min"])
        return "", pal["text_muted"], "Claude usage"

    def _forecast_ink(self, m: dict, pal: dict) -> QColor:
        """Colour of a time-to-limit forecast (always semibold), one rule for pill and card: plain
        text below caution (the '~' and 'Limit in' say what it is, and an all-day pill is not amber
        all week), the level colour from caution, muted like the values while they are last-known."""
        if self._now["state"] in ("stale", "error"):
            return pal["text_muted"]
        if RANK[m["level"]] < RANK["caution"]:
            return pal["text"]
        return self._level_colors(m["level"], pal)[1]

    def _sub(self, m: dict, pal: dict) -> tuple[list[str], QColor, bool]:
        """Card sub-line options (longest first), colour and semibold."""
        now, reset, lvl, muted = self._t, _parse(m["resets_at"]), m["level"], pal["text_muted"]
        if reset and reset <= now:  # last-known numbers from before the window reset
            return [f"Reset at {_when(reset, now)}"], muted, False
        if lvl == "limited":
            if not reset:
                return ["Limited"], pal["bad"], True
            w = _when(reset, now)
            short = [f"Limited for {_span(reset - now)}"] if reset - now < timedelta(hours=24) else []
            return [f"Limited until {w}", *short, f"Until {w}"], pal["bad"], True
        if reset is None:
            return (["Starts on next prompt", "Not started"] if _not_started(m) else []), muted, False
        eta = self._eta(m)
        if eta:
            left = eta - now
            return ([f"Limit in ~{_span(left)}"] if left >= timedelta(minutes=1) else ["Limit any minute"],
                    self._forecast_ink(m, pal), True)
        return _resets(reset, now), muted, False

    def _countdown(self, m: dict, pal: dict) -> tuple[str, QColor, bool]:
        """Pill sub (text, colour, semibold): '~1d 7h' to the limit when it lands before the reset,
        else the time to the reset."""
        eta, reset = self._eta(m), _parse(m["resets_at"])
        if eta:
            left = eta - self._t
            return ("~" + _span(left) if left >= timedelta(minutes=1) else "<1m"), self._forecast_ink(m, pal), True
        if reset and reset > self._t:
            return _span(reset - self._t), pal["text_muted"], False
        # Last-known numbers from before the window reset, as the card's "Reset at".
        return ("reset" if reset else ""), pal["text_muted"], False

    # ------------------------------------------------------------ layout

    def _build(self, dpr: float) -> tuple[tuple[int, int], list[Item]]:
        o = _Ops(dpr * self._scale)
        pal = self._pal()
        size = self._pill(o, pal) if self._style == "pill" else self._card(o, pal)
        return size, o.items

    def _icon(self, o: _Ops, kind: str, x: float, cy: float, color: QColor) -> float:
        """Place a status glyph left-aligned at x, centred on cy; return its advance."""
        if not kind:
            return 0.0
        d = 6 if kind == "dot" else 12
        o.shape("dot" if kind == "dot" else "icon", x, cy - d / 2, d, d, color, icon=kind)
        return d

    def _card(self, o: _Ops, pal: dict) -> tuple[int, int]:
        n = self._now
        W, H, P, GUT = CARD_W, CARD_H, 16, 16
        f12, f12b = _font(12), _font(12, 600)
        cap12 = QFontMetricsF(f12).capHeight()

        # Header: status slot left, today's cost right (baseline 28). The status
        # wins the space; the cost drops its "Today" before anything is cut.
        hb = 16 + 8 + cap12 / 2
        kind, kcol, label = self._status(pal)
        x = P + self._icon(o, kind, P, hb - cap12 / 2, kcol)
        x += 6 if kind else 0
        x += o.text(x, hb, label, f12b, pal["text_muted"], "status", width=W - P - x)
        # An error outside the fetch is the local store failing, so its $0 is not today's cost.
        if n["state"] != "loading" and (n["state"] != "error" or _retries(n)):
            cost = _money(n["today_cost"])
            options = [_indexing(self._ix), "Indexing"] if self._ix else [f"Today {cost}", cost]
            o.text(W - P, hb, _fit(options, f12, W - P - x - 12), f12, pal["text_muted"], "cost", right=True)

        meters = self._meters()
        if n["state"] == "signed_out" or (not meters and n["state"] != "loading"):
            self._card_message(o, pal, P, W - 2 * P)
            return W, H
        cols = meters or [("weekly", None)]  # loading: one skeleton column
        cw = (W - 2 * P - GUT * (len(cols) - 1)) / len(cols)
        f28 = _font(28, 600)
        f18 = _font(18, 600)
        cap28 = QFontMetricsF(f28).capHeight()
        for i, (key, m) in enumerate(cols):
            cx = P + i * (cw + GUT)
            lb = 40 + 8 + cap12 / 2
            sub = ""
            if m:
                options, scol, bold = self._sub(m, pal)
                # Risk lines are semibold too, so they never rely on colour alone.
                sf = f12b if bold else f12
                sub = _fit(options, sf, cw)
            lw = o.text(cx, lb, NAMES[key][1], f12, pal["text_muted"], "label")
            if m and RANK[m["level"]] >= RANK["caution"]:
                fill, ink = self._level_colors(m["level"], pal)
                self._icon(o, "warn", cx + lw + 6, lb - cap12 / 2, fill)
                # When the sub-line is too narrow to say "Limited", the label row does.
                if (m["level"] == "limited" and not sub.startswith("Limited")
                        and lw + 22 + QFontMetricsF(f12b).horizontalAdvance("Limited") <= cw):
                    o.text(cx + lw + 22, lb, "Limited", f12b, ink, "label")
            vb = 56 + (36 - cap28) / 2 + cap28
            if m is None:
                o.shape("skel", cx, vb - cap28, 56, cap28, pal["track"])
                o.shape("meter", cx, 96, cw, 4, pal["track"], track=pal["track"])
                o.shape("skel", cx, 104 + 4, 96, 8, pal["track"])
                continue
            vcol = self._value_color(pal)
            num = _pct(m)
            adv = o.text(cx, vb, num, f28, vcol, "value")
            if num != "--":
                o.text(cx + adv + 1, vb, "%", f18, vcol, "unit")
            o.shape("meter", cx, 96, cw, 4, self._fill(m, pal), frac=self._frac(key, m["pct"]), track=pal["track"])
            sb = 104 + 8 + cap12 / 2
            reset, eta = _parse(m["resets_at"]), self._eta(m)
            # One column (weekly only, as on Ultra) has room for a second fact: the reset beside a
            # forecast, or the countdown beside a reset date ("Resets in 2h 30m" already is one).
            if len(cols) == 1 and reset and reset > self._t and (
                    eta or m["level"] == "limited" or reset - self._t >= timedelta(hours=24)):
                o.text(cx + cw, sb, _resets(reset, self._t)[0] if eta else "in " + _span(reset - self._t), f12,
                       pal["text_muted"], "sub", right=True)
            if sub:
                o.text(cx, sb, sub, sf, scol, "sub", width=cw)
        return W, H

    def _card_message(self, o: _Ops, pal: dict, x: float, width: float) -> None:
        n = self._now
        f14b, f12 = _font(14, 600), _font(12)
        cap14, cap12 = QFontMetricsF(f14b).capHeight(), QFontMetricsF(f12).capHeight()
        if n["state"] == "signed_out":
            title, body = "Sign in to see your limits", "Claude Code is signed out on this PC."
        elif n["state"] == "ok":  # Claude answered but sent no limit headers
            title, body = "No limits reported", NO_LIMITS
        else:
            title, body = "Limits unavailable", n["message"] or "Claude didn't answer."
        o.text(x, 44 + (20 - cap14) / 2 + cap14, title, f14b, pal["text"], "title", width=width)
        # Wrapped, not cut: the end of an error message is often the part that says what to do.
        rows = 2
        if n["state"] == "signed_out":
            rows = 1
        elif n["state"] in ("stale", "error"):
            body, rows = (body.rstrip(".") + ". Retrying automatically." if _retries(n) else body), 3
        for i, line in enumerate(_wrap(body, f12, width, rows)):
            o.text(x, 68 + 16 * i + 8 + cap12 / 2, line, f12, pal["text_muted"], "body", width=width)
        if n["state"] == "signed_out":
            fb = _font(12, 600)
            bw = QFontMetricsF(fb).horizontalAdvance("Sign in") + 24
            o.shape("button", x, 92, bw, 28, pal["accent"])
            o.text(x + 12, 92 + 14 + QFontMetricsF(fb).capHeight() / 2, "Sign in", fb, pal["on_accent"], "action")

    def _pill(self, o: _Ops, pal: dict) -> tuple[int, int]:
        n = self._now
        P = 12
        f11, f12, f12b, f14b = _font(11), _font(12), _font(12, 600), _font(14, 600)
        fm12, fm12b, fm14 = QFontMetricsF(f12), QFontMetricsF(f12b), QFontMetricsF(f14b)
        cap12, cap14 = fm12.capHeight(), fm14.capHeight()
        top = (PILL_H - (cap14 + 4 + 3)) / 2   # value cap + gap + 3px meter, centred
        base = top + cap14
        mid = base - cap14 / 2
        meters = self._meters()

        kind, kcol, _ = self._status(pal)
        worst = self._worst()
        if n["state"] in ("ok", "stale") and worst >= RANK["caution"]:
            # Stale keeps its clock (the numbers are old) but takes the level colour.
            kind = "warn" if n["state"] == "ok" else kind
            kcol = self._level_colors("critical" if worst > RANK["caution"] else "caution", pal)[0]
        elif not kind:
            kind, kcol = "dot", pal["text_faint"]
        # A fixed 12px status slot (the dot centred in it) keeps the width across ok, live, stale and caution.
        self._icon(o, kind, P + 3 if kind == "dot" else P, mid, kcol)
        x = P + 12 + 8

        if n["state"] == "signed_out":
            x += o.text(x, base, "Claude", f11, pal["text_muted"], "tag") + 4
            x += o.text(x, base, "Sign in", f14b, pal["accent_text"], "action")
        elif not meters and n["state"] != "loading":
            msg = ("No limits reported" if n["state"] == "ok" else "Limits unavailable" if _retries(n)
                   else "Update Claude Usage")  # a store from a newer version
            x += o.text(x, base, msg, f12, pal["text_muted"], "status")
        else:
            for i, (key, m) in enumerate(meters or [("weekly", None)]):
                if i:  # hairline between the two limits so "5d 17h" never reads into "5h"
                    o.shape("rule", x + 8, PILL_H / 2 - 8, 1 / o.dpr, 16, pal["line_strong"])
                    x += 17
                x += o.text(x, base, NAMES[key][0], f11, pal["text_muted"], "tag") + 4
                # Fixed slots, left-aligned: '5%', '46%' and a not-started '--' share one width, as do
                # the countdowns; only '100%' or an unusually long countdown widen the pill.
                label = "00%" if m is None else _pct(m) + ("" if _not_started(m) else "%")
                vw = max(fm14.horizontalAdvance("00%"), fm14.horizontalAdvance(label))
                sw = max((fm12b if t[0] == "~" else fm12).horizontalAdvance(t) for t in SLOTS[key])
                if m is None:
                    o.shape("skel", x, base - cap14, vw, cap14, pal["track"])
                    o.shape("meter", x, base + 4, vw, 3, pal["track"], track=pal["track"])
                    o.shape("skel", x + vw + 6, base - cap12, sw, cap12, pal["track"])
                else:
                    o.text(x, base, label, f14b, self._value_color(pal), "value")
                    o.shape("meter", x, base + 4, vw, 3, self._fill(m, pal), frac=self._frac(key, m["pct"]),
                            track=pal["track"])
                    sub, scol, bold = self._countdown(m, pal)
                    if sub:
                        sw = max(sw, o.text(x + vw + 6, base, sub, f12b if bold else f12, scol, "sub"))
                x += vw + 6 + sw
        w = 8 * math.ceil((x + P) / 8)
        # Split the grid rounding between both ends. Everything sits at fixed offsets from the
        # left, so nothing moves as countdowns tick; a short countdown leaves room on its right.
        dx = o.snap((w - x - P) / 2)
        for it in o.items:
            it.rect.translate(dx, 0)
            if it.origin is not None:
                it.origin += QPointF(dx, 0)
        return w, PILL_H

    # ----------------------------------------------------------- painting

    def _draw(self, p: QPainter, dpr: float) -> None:
        op = self.effective_opacity()
        if op >= 1.0:
            self._paint(p, dpr)
            return
        # Group opacity, like a layered window's constant alpha: text and
        # plate fade together instead of text stacking on a faded plate.
        p.setOpacity(op)
        p.drawImage(QPointF(0, 0), self._image(dpr, self._paint))

    def _image(self, dpr: float, paint) -> QImage:
        s = self._scale
        w, h = self._build(dpr)[0]
        img = QImage(round(w * s * dpr), round(h * s * dpr), QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dpr)
        img.fill(Qt.transparent)
        q = QPainter(img)
        paint(q, dpr)
        q.end()
        return img

    def _paint(self, p: QPainter, dpr: float) -> None:
        (w, h), items = self._build(dpr)
        pal = self._pal()
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)
        p.scale(self._scale, self._scale)
        px = 1 / (dpr * self._scale)  # one device pixel in unscaled units

        plate = QRectF(px / 2, px / 2, w - px, h - px)
        edge = QLinearGradient(0, h - 3, 0, h)
        edge.setColorAt(0, pal["line_strong"])
        # A shadow would catch clicks around the plate, so a light plate gets Fluent's
        # elevation border instead: the bottom edge a step darker, to lift it off a light window.
        edge.setColorAt(1, pal["line_strong"] if self._dark else pal["line_strong"].darker(125))
        p.setPen(QPen(edge, px))
        p.setBrush(pal["osd_bg"])
        p.drawRoundedRect(plate, RADIUS, RADIUS)
        if self._flash.state() == QVariantAnimation.Running:
            flash = QColor(self._level_colors("critical" if self._worst() > RANK["caution"] else "caution", pal)[0])
            flash.setAlphaF(float(self._flash.currentValue()))
            p.setPen(QPen(flash, 2 * px))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(plate, RADIUS, RADIUS)

        for it in items:
            r = it.rect
            if it.kind == "text":
                p.setFont(it.font)
                p.setPen(it.color)
                p.drawText(it.origin, it.text)
                continue
            p.setPen(Qt.NoPen)
            if it.kind == "meter":
                rad = r.height() / 2
                p.setBrush(it.track)
                p.drawRoundedRect(r, rad, rad)
                if it.frac > 0:
                    p.setBrush(it.color)
                    p.drawRoundedRect(QRectF(r.x(), r.y(), max(r.height(), r.width() * it.frac), r.height()), rad, rad)
            elif it.kind == "rule":
                p.fillRect(r, it.color)
            elif it.kind in ("skel", "button"):
                p.setBrush(it.color)
                rad = 4 if it.kind == "button" else r.height() / 2
                p.drawRoundedRect(r, rad, rad)
            elif it.kind == "dot":
                p.setBrush(it.color)
                p.drawEllipse(r)
            elif it.kind == "icon":
                self._paint_icon(p, it, pal["osd_bg"])

    @staticmethod
    def _paint_icon(p: QPainter, it: Item, cut: QColor) -> None:
        r, c = it.rect, it.color
        cx = r.center().x()
        if it.icon == "warn":
            path = QPainterPath()
            path.moveTo(cx, r.top() + 0.5)
            path.lineTo(r.right() - 0.5, r.bottom() - 0.5)
            path.lineTo(r.left() + 0.5, r.bottom() - 0.5)
            path.closeSubpath()
            p.setPen(QPen(c, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(c)
            p.drawPath(path)
            p.setPen(Qt.NoPen)
            p.setBrush(cut)
            p.drawRoundedRect(QRectF(cx - 0.75, r.top() + 4, 1.5, 4), 0.75, 0.75)
            p.drawEllipse(QPointF(cx, r.bottom() - 2.25), 0.85, 0.85)
        elif it.icon == "alert":
            p.setBrush(c)
            p.drawEllipse(r)
            p.setBrush(cut)
            p.drawRoundedRect(QRectF(cx - 0.75, r.top() + 2.75, 1.5, 4.25), 0.75, 0.75)
            p.drawEllipse(QPointF(cx, r.bottom() - 3), 0.85, 0.85)
        elif it.icon == "clock":
            p.setPen(QPen(c, 1.25))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(r.adjusted(1, 1, -1, -1))
            p.setPen(QPen(c, 1.25, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(cx, r.center().y()), QPointF(cx, r.top() + 3.5))
            p.drawLine(QPointF(cx, r.center().y()), QPointF(cx + 2.5, r.center().y()))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        p = QPainter(self)
        p.setCompositionMode(QPainter.CompositionMode_Source)
        p.fillRect(self.rect(), Qt.transparent)
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
        self._draw(p, self.devicePixelRatioF())

    # ------------------------------------------------------------- geometry

    def _size(self) -> QSize:
        w, h = self._build(1.0)[0]
        return QSize(round(w * self._scale), round(h * self._scale))

    def _relayout(self, clamp: bool = False) -> None:
        self._resize(self._size(), clamp)
        self.update()

    @staticmethod
    def _screen(g: QRect) -> QRect:
        def overlap(s):
            r = s.geometry().intersected(g)
            return r.width() * r.height()
        return max(QApplication.screens(), key=overlap).geometry()

    def _anchored(self, g: QRect, size: QSize) -> QPoint:
        """Top-left of a window of `size` on the same edges as g: the ones nearest the screen border."""
        scr = self._screen(g)
        return QPoint(g.right() + 1 - size.width() if g.center().x() > scr.center().x() else g.x(),
                      g.bottom() + 1 - size.height() if g.center().y() > scr.center().y() else g.y())

    def _spot(self) -> QPoint:
        """What widget.py saves and restores: the top-left of the loading-size window on the
        same edges. The OSD starts at that size, so a pill parked by the tray comes back on
        its saved right edge whatever its width at launch, instead of drifting each restart."""
        now, self._now = self._now, LOADING
        try:
            size = self._size()
        finally:
            self._now = now
        return self._anchored(self.geometry(), size)

    def _report(self) -> None:
        spot = self._spot()
        if spot != self._saved:
            self._saved = spot
            self.moved.emit(spot.x(), spot.y())

    def _resize(self, size: QSize, clamp: bool = False) -> None:
        """Resize keeping the edges nearest the screen border fixed, so a pill parked
        by the tray grows leftwards and a card on the taskbar grows up. The result
        always fits the screen it overlaps most."""
        if size == self.size() and not clamp:
            return
        if not self.isVisible():
            self.resize(size)
            return
        g = self.geometry()
        p, scr = self._anchored(g, size), self._screen(g)
        # Full geometry, not availableGeometry: parking over the taskbar is allowed.
        x = max(scr.left(), min(p.x(), scr.right() + 1 - size.width()))
        y = max(scr.top(), min(p.y(), scr.bottom() + 1 - size.height()))
        self.setGeometry(x, y, size.width(), size.height())
        self._report()

    def _move_to_default_position(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.move(geo.right() + 1 - self.width() - EDGE_MARGIN, geo.y() + EDGE_MARGIN)

    def showEvent(self, event) -> None:  # noqa: N802
        if self._saved is None:  # shown at the restored (or default) spot, before any data
            self._saved = self._spot()
        super().showEvent(event)

    # --------------------------------------------------------------- events

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press = event.globalPosition().toPoint()
            self._press_pos = self.pos()
            self._dragging = False
        elif event.button() == Qt.RightButton:
            self.right_clicked.emit(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press is None:
            return
        d = event.globalPosition().toPoint() - self._press
        if not self._dragging and (abs(d.x()) > DRAG_THRESHOLD or abs(d.y()) > DRAG_THRESHOLD):
            self._dragging = True
        if self._dragging:
            self.move(self._press_pos + d)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._press is None:
            return
        dragged = self._dragging
        self._press, self._dragging = None, False
        if dragged:
            self._report()
        else:
            self.clicked.emit()

    # -------------------------------------------------------------- tooltip

    def _set_tip(self) -> None:
        tip = self._tooltip()
        self.setToolTip(tip)
        self.setAccessibleDescription(tip)  # Narrator reads nothing else from a painted window

    def _tooltip(self) -> str:
        n, now = self._now, self._t
        lines = []
        if n["state"] == "signed_out":
            lines.append("Signed out. Click to open Claude Usage and sign in.")
        elif n["state"] == "ok" and not self._meters():
            lines.append(NO_LIMITS)
        for key, m in self._meters():
            line = f"{NAMES[key][1]} {_pct(m)}{'' if _not_started(m) else '%'}"
            reset, eta = _parse(m["resets_at"]), self._eta(m)
            if reset and reset <= now:
                line += f", window reset at {_when(reset, now)}"
            elif m["level"] == "limited":  # the card's words: limited until, not resets
                line += f", limited until {_when(reset, now)}" if reset else ", limited"
            elif reset and reset - now < timedelta(hours=24):
                line += f", resets in {_span(reset - now)}"
            elif reset:
                line += f", resets {_when(reset, now)}"
            elif _not_started(m):
                line += ", starts on your next prompt"
            lines.append(line)
            if m["forecast"]:
                lines.append(m["forecast"])
            elif eta:
                lines.append("At this pace the limit is reached " + (_when(eta, now) if eta > now else "any minute"))
        if n["state"] != "loading" and (n["state"] != "error" or _retries(n)):
            ix = self._ix
            lines.append(f"Today {_money(n['today_cost'])} so far, indexing {ix['done']:,} of {ix['total']:,} transcripts"
                         if ix else f"Today {_money(n['today_cost'])} (API-equivalent)")
        if n["live"]:
            k, subs = n["active_sessions"], n["active_subagents"]
            lines.append(f"Live {_rate(n['tokens_per_min'])}, {k} active session{'s' if k != 1 else ''}"
                         + (f", {subs} subagent{'s' if subs != 1 else ''}" if subs else ""))
        if n["state"] in ("stale", "error", "signed_out"):
            # Signed out after a good fetch keeps that fetch's time, as stale does.
            as_of = _parse(n["as_of"])
            why = n["message"] or ("" if n["state"] == "signed_out" else "Can't reach Claude.")
            line = ("As of " + _clock(as_of) + ". " if as_of else "") + why
            if line:
                lines.append(line)
        return "\n".join(lines)
