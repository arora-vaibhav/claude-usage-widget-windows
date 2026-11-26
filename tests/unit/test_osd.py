"""The OSD: every state x style x light/dark renders, reads (contrast measured
from pixels), never overlaps text and stays on the 4px / device-pixel grid."""

from __future__ import annotations

import itertools
import os
import sys
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # the offscreen platform finds no fonts without this
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage import tokens  # noqa: E402
from claude_usage.osd import MIN_OPACITY, Osd, _pct  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from snapshot_contract import NOW, check  # noqa: E402

_app = QApplication.instance() or QApplication([])

BACKDROPS = ("#202020", "#f3f3f3")
SECONDARY = {"label", "sub", "status", "cost", "tag", "title", "body", "action", "unit"}


def states(now: datetime | None = None) -> dict[str, dict]:
    """Synthetic "now" dicts, one per OSD state."""
    now = now or datetime.now().astimezone()

    def iso(td):
        return None if td is None else (now + td).isoformat()

    def meter(pct, reset, eta=None, forecast=None):
        return {"pct": pct, "resets_at": iso(reset), "level": tokens.level(pct), "forecast": forecast, "eta": iso(eta)}

    week = meter(0.46, timedelta(days=5, hours=17, minutes=20))
    five = meter(0.12, timedelta(hours=2, minutes=30, seconds=30))
    base = {
        "primary": "weekly", "weekly": week, "session": five, "today_cost": 82.4,
        "today_tokens": 1_234_567, "tokens_per_min": 0.0, "active_sessions": 1,
        "active_subagents": 0, "live": False, "state": "ok",
        "as_of": iso(timedelta(seconds=-30)), "message": None,
    }
    none = {"weekly": None, "session": None}
    return {
        "loading": {**base, **none, "state": "loading", "today_cost": 0.0, "as_of": None},
        "normal": base,
        "live": {**base, "live": True, "tokens_per_min": 4100.0, "active_sessions": 2, "active_subagents": 1},
        "caution": {**base, "weekly": meter(0.78, timedelta(days=2, hours=4), timedelta(days=1, hours=2),
                                            "At this pace the weekly limit is hit tomorrow"),
                    "session": meter(0.30, timedelta(hours=3, minutes=10))},
        "critical": {**base, "primary": "session",
                     "session": meter(0.93, timedelta(hours=1, minutes=52), timedelta(minutes=38)),
                     "weekly": meter(0.62, timedelta(days=3, hours=1))},
        "limited": {**base, "weekly": meter(1.0, timedelta(days=1, hours=3)),
                    "session": meter(0.41, timedelta(hours=4, minutes=5))},
        "limited_5h": {**base, "primary": "session", "session": meter(1.0, timedelta(minutes=48)),
                       "weekly": meter(0.71, timedelta(days=4, hours=2))},
        "no_5h": {**base, "session": None},
        "not_started": {**base, "session": meter(0.0, None)},
        "stale": {**base, "state": "stale", "as_of": iso(timedelta(minutes=-47)),
                  "message": "Rate limited by the usage API"},
        "signed_out": {**base, **none, "state": "signed_out", "as_of": None, "message": "No credentials found"},
        "error": {**base, **none, "state": "error", "as_of": None, "message": "Network unreachable"},
    }


def make(state: str, style: str = "card", dark: bool = True, opacity: float = 0.5, scale: float = 1.0) -> Osd:
    w = Osd(style=style, dark=dark, opacity=opacity, scale=scale)
    w.set_now(states()[state])
    return w


CASES = list(itertools.product(states(), ("pill", "card"), (True, False)))


def _lum(c: QColor) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def _ratio(a: QColor, b: QColor) -> float:
    la, lb = _lum(a), _lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _over(img: QImage, backdrop: str) -> QImage:
    out = QImage(img.size(), QImage.Format_RGB32)
    out.fill(QColor(backdrop))
    p = QPainter(out)
    p.drawImage(0, 0, img)  # both images in device px
    p.end()
    return out


def _peak_contrast(img: QImage, box: QRectF, k: float) -> float:
    """Best text-vs-background contrast inside a text box; background = the box's most common colour."""
    x0, y0 = max(0, int(box.left() * k) - 1), max(0, int(box.top() * k) - 1)
    x1, y1 = min(img.width(), int(box.right() * k) + 2), min(img.height(), int(box.bottom() * k) + 2)
    px = [img.pixel(x, y) for y in range(y0, y1) for x in range(x0, x1)]
    bg = QColor(max(set(px), key=px.count))
    return max(_ratio(QColor(v), bg) for v in set(px))


def test_fixtures_follow_the_contract():
    for name, now in states().items():
        check(now, NOW, f"now[{name}]")


@pytest.mark.parametrize("state,style,dark", CASES)
def test_renders_and_reads_at_half_opacity(state, style, dark, tmp_path):
    w = make(state, style, dark, opacity=0.5)
    img = w.render_image(1.0)
    assert not img.isNull()
    img.save(str(tmp_path / f"{state}_{style}_{'dark' if dark else 'light'}.png"))
    texts = [it for it in w.items() if it.kind == "text"]
    for backdrop in BACKDROPS:
        comp = _over(img, backdrop)
        for it in texts:
            need = 4.5 if it.role in SECONDARY | {"value"} else 0
            got = _peak_contrast(comp, it.rect, 1.0)
            assert got >= need, f"{it.role} {it.text!r} on {backdrop}: {got:.2f}:1"


@pytest.mark.parametrize("state,style,dark", CASES)
def test_no_overlap_and_inside_bounds(state, style, dark):
    w = make(state, style, dark)
    bounds = QRectF(0, 0, w.width(), w.height()).adjusted(4, 4, -4, -4)
    items = w.items()
    for it in items:
        assert bounds.contains(it.rect), f"{it.kind} {it.text!r} {it.rect} leaves {bounds}"
        if it.kind == "text":
            assert it.font.pixelSize() >= 11, f"{it.text!r} is {it.font.pixelSize()}px"
    for a, b in itertools.combinations(items, 2):
        if "text" not in (a.kind, b.kind) or {a.kind, b.kind} == {"text", "button"}:
            continue
        assert not a.rect.intersects(b.rect), f"{a.kind} {a.text!r} overlaps {b.kind} {b.text!r}"


@pytest.mark.parametrize("style", ["pill", "card"])
@pytest.mark.parametrize("scale", [1.0, 1.5, 2.0])
def test_sizes_on_grid_and_device_pixels(style, scale):
    for state in states():
        w = make(state, style, scale=scale)
        assert w.width() % 4 == 0 and w.height() % 4 == 0, (state, w.size())
        for dpr in (1.0, 1.25, 1.5, 1.75, 2.0):
            k = dpr * scale
            assert (w.width() * dpr).is_integer() and (w.height() * dpr).is_integer()
            for it in w.items(dpr):
                if it.kind in ("meter", "skel", "dot", "button", "rule"):
                    edges = (it.rect.x(), it.rect.y(), it.rect.width(), it.rect.height())
                    assert all(abs(v * k - round(v * k)) < 1e-6 for v in edges), (state, it.kind, edges, dpr)
            img = w.render_image(dpr)
            assert (img.width(), img.height()) == (round(w.width() * dpr), round(w.height() * dpr))


def test_meters_of_a_kind_have_equal_device_height():
    w = make("normal", "card")
    heights = {round(it.rect.height() * 1.25, 6) for it in w.items(1.25) if it.kind == "meter"}
    assert len(heights) == 1


def test_opacity_rule():
    assert make("normal", opacity=0.5).effective_opacity() == MIN_OPACITY
    assert make("normal", opacity=0.95).effective_opacity() == 0.95
    for state in ("caution", "critical", "limited", "stale", "signed_out", "error"):
        assert make(state, opacity=0.5).effective_opacity() == 1.0, state
    w = make("normal", opacity=0.5)
    w.enterEvent(None)
    assert w.effective_opacity() == 1.0


def test_signed_out_never_shows_a_percentage():
    for style in ("pill", "card"):
        texts = [it.text for it in make("signed_out", style).items() if it.kind == "text"]
        assert "Sign in" in texts
        assert not any("%" in t for t in texts)


def test_binding_limit_comes_first_and_missing_5h_is_omitted():
    first = [it.text for it in make("critical", "pill").items() if it.role == "tag"][0]
    assert first == "5h"
    assert [it.text for it in make("normal", "pill").items() if it.role == "tag"] == ["7d", "5h"]
    assert len([it for it in make("no_5h", "card").items() if it.role == "value"]) == 1
    assert [it.text for it in make("no_5h", "pill").items() if it.role == "tag"] == ["7d"]


def test_percent_never_rounds_up_to_the_limit():
    m = {"pct": 0.996, "resets_at": "2026-11-27T10:00:00+00:00", "level": "critical", "forecast": None, "eta": None}
    assert _pct(m) == "99"
    assert _pct({**m, "pct": 1.0, "level": "limited"}) == "100"
    assert _pct({**m, "pct": 0.0, "resets_at": None}) == "--"


def test_states_show_their_copy():
    def texts(state, style="card"):
        return " ".join(it.text for it in make(state, style).items() if it.kind == "text")
    assert "Live 4.1k tok/min" in texts("live")
    assert "Stale, as of" in texts("stale")
    assert "as of" in texts("stale", "pill")
    assert "Limited until" in texts("limited") or "Until" in texts("limited")
    assert "Limit in ~" in texts("critical")
    assert "Starts on next prompt" in texts("not_started") or "Not started" in texts("not_started")
    assert "Today $82.40" in texts("normal")
    assert not [it for it in make("loading").items() if it.role == "value"]
    tip = make("caution").toolTip()
    assert "At this pace the weekly limit is hit tomorrow" in tip and "Today $82.40" in tip
    assert "2 active sessions, 1 subagent" in make("live").toolTip()


def test_stale_numbers_from_before_a_reset_say_so():
    now = datetime.now().astimezone()
    st = states(now)["stale"]
    st["session"] = {**st["session"], "resets_at": (now - timedelta(minutes=5)).isoformat()}
    card, pill = Osd(style="card"), Osd(style="pill")
    card.set_now(st)
    pill.set_now(st)
    assert any(it.text.startswith("Reset at") for it in card.items())
    assert [it.text for it in pill.items() if it.role == "sub"] == ["5d 17h"]
    assert "window reset at" in card.toolTip()


def _mouse(kind, button, pos):
    return QMouseEvent(kind, QPointF(pos), QPointF(pos), button, button, Qt.NoModifier)


def test_click_drag_right_click_and_style_signals():
    w = make("normal", "pill")
    seen = []
    w.clicked.connect(lambda: seen.append("click"))
    w.right_clicked.connect(lambda p: seen.append(("menu", p.x(), p.y())))
    w.moved.connect(lambda x, y: seen.append(("moved", x, y)))
    w.style_toggled.connect(lambda s: seen.append(("style", s)))

    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, Qt.LeftButton, QPoint(10, 10)))
    w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, Qt.LeftButton, QPoint(11, 10)))
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, Qt.RightButton, QPoint(20, 30)))
    start = w.pos()
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, Qt.LeftButton, QPoint(10, 10)))
    w.mouseMoveEvent(_mouse(QEvent.MouseMove, Qt.LeftButton, QPoint(60, 40)))
    w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, Qt.LeftButton, QPoint(60, 40)))
    w.toggle_style()
    w.set_style("card")  # already card: no second signal

    assert seen[0] == "click"
    assert seen[1] == ("menu", 20, 30)
    assert seen[2] == ("moved", start.x() + 50, start.y() + 30)
    assert seen[3] == ("style", "card")
    assert len(seen) == 4
    assert w.osd_style() == "card" and w.size().width() == 296


def test_scale_snaps_to_half_steps_and_never_shrinks_text():
    w = make("normal", "card", scale=1.2)
    assert w.width() == 296
    w.set_scale(0.6)
    assert w.width() == 296
    w.set_scale(1.5)
    assert (w.width(), w.height()) == (444, 204)


@pytest.mark.skipif(QApplication.platformName() != "offscreen", reason="shows a window")
def test_resize_keeps_the_edge_nearest_the_screen_border():
    w = make("normal", "card")
    scr = w.screen().geometry()
    w.move(scr.right() + 1 - w.width() - 20, scr.bottom() + 1 - w.height() - 10)  # bottom-right quadrant
    w.show()
    right, bottom = w.geometry().right(), w.geometry().bottom()
    seen = []
    w.moved.connect(lambda x, y: seen.append((x, y)))
    w.set_style("pill")
    assert (w.geometry().right(), w.geometry().bottom()) == (right, bottom)
    assert seen == [(w.x(), w.y())]
    w.hide()


def test_widget_paint_matches_render():
    w = make("live", "card", opacity=1.0)
    grabbed = w.grab().toImage()
    assert (grabbed.width(), grabbed.height()) == (296, 136)
    assert QColor(grabbed.pixel(148, 3)) == QColor(tokens.DARK["osd_bg"])
