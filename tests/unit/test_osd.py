"""The OSD: every state x style x light/dark renders, reads (contrast of the
composited colours over any backdrop), never overlaps text and stays on the
4px / device-pixel grid."""

from __future__ import annotations

import itertools
import math
import os
import sys
from datetime import datetime, timedelta
from functools import lru_cache

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # the offscreen platform finds no fonts without this
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt, QVariantAnimation  # noqa: E402
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage import osd, tokens  # noqa: E402
from claude_usage.osd import EDGE_MARGIN, MIN_OPACITY, MIN_OPACITY_LIGHT, Osd, _clock, _pct, _when  # noqa: E402
from claude_usage.widget import NEWER_STORE as NEWER  # noqa: E402  (the one error today that is not a fetch's)

sys.path.insert(0, os.path.dirname(__file__))
from snapshot_contract import NOW, check  # noqa: E402

_app = QApplication.instance() or QApplication([])

# A busy wallpaper is some mix of these; group opacity blends each channel linearly.
BACKDROPS = ("#202020", "#f3f3f3", "#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff")


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
        "as_of": iso(timedelta(seconds=-30)), "message": None, "error_kind": None,
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
                  "message": "Rate limited by the usage API", "error_kind": "fetch"},
        "signed_out": {**base, **none, "state": "signed_out", "as_of": None, "message": "No credentials found",
                       "error_kind": "fetch"},
        "error": {**base, **none, "state": "error", "as_of": None, "message": "Network unreachable", "error_kind": "fetch"},
        "error_with_data": {**base, "state": "error", "as_of": iso(timedelta(minutes=-47)),
                            "message": "Network unreachable", "error_kind": "fetch"},
        "normal_eta_fast": {**base, "weekly": meter(0.46, timedelta(days=5, hours=17), timedelta(days=2, hours=3))},
        "wk_only_critical": {**base, "session": None,
                             "weekly": meter(0.93, timedelta(days=2, hours=4), timedelta(hours=9))},
        "eta_now": {**base, "session": None, "weekly": meter(0.97, timedelta(days=1), timedelta(minutes=-5))},
        "limited_far": {**base, "weekly": meter(1.0, timedelta(days=7)),
                        "session": meter(0.41, timedelta(hours=4, minutes=5))},
        "both_limited": {**base, "primary": "session", "weekly": meter(1.0, timedelta(days=4, hours=6)),
                         "session": meter(1.0, timedelta(hours=4, minutes=59))},
        "wk_only_eta_normal": {**base, "session": None,  # the common weekly-only case: forecast at normal level
                               "weekly": meter(0.58, timedelta(days=4, hours=19), timedelta(days=1, hours=7))},
        "no_limits": {**base, **none},  # Claude answered without limit headers
        "stale_no_limits": {**base, **none, "state": "stale", "message": None},
        "stale_past_reset": {**base, "state": "stale", "as_of": iso(timedelta(hours=-6)),
                             "session": meter(0.55, timedelta(hours=-1))},
        "stale_caution": {**base, "state": "stale", "as_of": iso(timedelta(minutes=-47)), "message": None,
                          "weekly": meter(0.78, timedelta(days=2, hours=4), timedelta(days=1, hours=2))},
        "long_error": {**base, **none, "state": "error", "as_of": None, "error_kind": "fetch",
                       "message": "OAuth token expired; run claude login in a terminal, then wait for the next refresh"},
        "wk_only_limited": {**base, "session": None, "weekly": meter(1.0, timedelta(days=6, hours=23, minutes=50))},
        # Not a fetch failure: nothing retries, and the empty stand-in store's $0 is not today's cost.
        "newer_store": {**base, **none, "state": "error", "as_of": None, "today_cost": 0.0, "message": NEWER,
                        "error_kind": "store"},
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


def _on(c: QColor, backdrop: str, op: float) -> QColor:
    """A colour of the OSD as seen through group opacity op over a backdrop."""
    b = QColor(backdrop)
    return QColor(*(round(op * x + (1 - op) * y) for x, y in zip(c.getRgb()[:3], b.getRgb()[:3])))


def _seen_ratio(a: QColor, b: QColor, w: Osd) -> float:
    op = w.effective_opacity()
    return min(_ratio(_on(a, bd, op), _on(b, bd, op)) for bd in BACKDROPS)


@lru_cache(maxsize=None)
def _lum_rgb(rgb: int) -> float:
    return _lum(QColor(rgb))


def _flat(img: QImage, backdrop: str) -> QImage:
    out = QImage(img.size(), QImage.Format_RGB32)
    out.fill(QColor(backdrop))
    src = QImage(img)
    src.setDevicePixelRatio(1.0)
    p = QPainter(out)
    p.drawImage(0, 0, src)
    p.end()
    return out


def _measured(w: Osd, dpr: float, backdrop: str) -> list[tuple[osd.Item, float]]:
    """Each text's contrast as rendered: the median ratio over its glyph cores (pixels
    within 15% of the strongest change from a render with the text hidden), over a backdrop."""
    real = _flat(w.render_image(dpr), backdrop)
    build = w._build

    def hidden(d):
        size, items = build(d)
        for it in items:
            if it.kind == "text":
                it.color = QColor(0, 0, 0, 0)
        return size, items
    w._build = hidden
    try:
        base = _flat(w.render_image(dpr), backdrop)
    finally:
        del w._build
    k = dpr * w._scale
    out = []
    for it in w.items(dpr):
        if it.kind != "text":
            continue
        r = it.rect
        px = [(real.pixel(x, y) & 0xFFFFFF, base.pixel(x, y) & 0xFFFFFF)
              for y in range(max(0, math.floor(r.top() * k)), min(real.height(), math.ceil(r.bottom() * k)))
              for x in range(max(0, math.floor(r.left() * k)), min(real.width(), math.ceil(r.right() * k)))]
        diff = [abs(_lum_rgb(a) - _lum_rgb(b)) for a, b in px]
        top = max(diff)
        core = sorted((max(_lum_rgb(a), _lum_rgb(b)) + 0.05) / (min(_lum_rgb(a), _lum_rgb(b)) + 0.05)
                      for (a, b), d in zip(px, diff) if d >= 0.85 * top)
        out.append((it, core[len(core) // 2]))
    return out


def test_fixtures_follow_the_contract():
    for name, now in states().items():
        check(now, NOW, f"now[{name}]")


@pytest.mark.parametrize("state,style,dark", CASES)
def test_renders_and_reads_at_half_opacity(state, style, dark, tmp_path):
    """Text 4.5:1 and meters 3:1 on their track, as composited over any backdrop."""
    w = make(state, style, dark, opacity=0.5)
    img = w.render_image(1.0)
    assert not img.isNull()
    img.save(str(tmp_path / f"{state}_{style}_{'dark' if dark else 'light'}.png"))
    items = w.items()
    plate = QColor(tokens.palette(dark)["osd_bg"])
    for it in items:
        if it.kind == "text":
            bg = next((b.color for b in items if b.kind == "button" and b.rect.contains(it.rect)), plate)
            got = _seen_ratio(it.color, bg, w)
            assert got >= 4.5, f"{it.role} {it.text!r}: {got:.2f}:1"
        elif it.kind == "meter" and it.frac > 0:
            got = _seen_ratio(it.color, it.track, w)
            assert got >= 3, f"meter {it.color.name()} on {it.track.name()}: {got:.2f}:1"


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


@pytest.mark.parametrize("dark", [True, False])
def test_text_reads_as_rendered(dark):
    """4.5:1 measured from pixels (glyph cores at DPR 1.25), at full opacity and at the floor,
    over a dark taskbar and a light window."""
    worst = []
    for state, style in itertools.product(states(), ("pill", "card")):
        for opacity in (1.0, 0.5):
            w = make(state, style, dark, opacity=opacity)
            if opacity < 1 and w.effective_opacity() == 1.0:
                continue  # alert states ignore the user opacity
            for bd in ("#202020", "#f3f3f3"):
                worst += [(round(m, 2), state, style, opacity, bd, it.text) for it, m in _measured(w, 1.25, bd)
                          if m < 4.5]
    assert not worst, sorted(worst)[:10]


def test_thin_digits_read_at_the_opacity_floor():
    """'1' and '4' have the thinnest stems; the fixtures' '2h 30m' would hide a floor set too low."""
    now = datetime.now().astimezone()
    st = states(now)["normal"]
    for dark in (True, False):
        w = Osd(style="pill", dark=dark, opacity=0.5)
        w.set_now({**st, "weekly": {**st["weekly"], "resets_at": (now + timedelta(days=1, hours=14, minutes=5)).isoformat()},
                   "session": {**st["session"], "resets_at": (now + timedelta(hours=1, minutes=14, seconds=50)).isoformat()}})
        for bd in ("#202020", "#f3f3f3"):
            low = [(it.text, round(m, 2)) for it, m in _measured(w, 1.25, bd) if m < 4.5]
            assert not low, (dark, bd, low)


def test_meters_of_a_kind_have_equal_device_height():
    w = make("normal", "card")
    heights = {round(it.rect.height() * 1.25, 6) for it in w.items(1.25) if it.kind == "meter"}
    assert len(heights) == 1


def test_opacity_rule():
    assert make("normal", opacity=0.5).effective_opacity() == MIN_OPACITY
    assert make("normal", dark=False, opacity=0.5).effective_opacity() == MIN_OPACITY_LIGHT
    assert make("normal", opacity=0.95).effective_opacity() == 0.95
    for state in ("caution", "critical", "limited", "stale", "signed_out", "error"):
        assert make(state, opacity=0.5).effective_opacity() == 1.0, state
    # A forecast at normal level is plain text on both surfaces, so both keep the user's opacity.
    for style in ("pill", "card"):
        assert make("normal_eta_fast", style, opacity=0.5).effective_opacity() == MIN_OPACITY
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
    # Ultra (five_hour null): weekly only on every surface, never a 5-hour 0%, and a 5h limit
    # that comes back shows again on the next refresh.
    for style in ("pill", "card"):
        w = make("no_5h", style)
        said = [it.text for it in w.items() if it.kind == "text"] + [w.toolTip()]
        assert not any(k in t for t in said for k in ("5h", "5-hour", "Session")), (style, said)
        w.set_now(states()["normal"])
        assert ("5h" if style == "pill" else "5-hour") in [it.text for it in w.items()], style


def test_percent_never_rounds_up_to_the_limit():
    m = {"pct": 0.996, "resets_at": "2026-11-27T10:00:00+00:00", "level": "critical", "forecast": None, "eta": None}
    assert _pct(m) == "99"
    assert _pct({**m, "pct": 1.0, "level": "limited"}) == "100"
    assert _pct({**m, "pct": 0.0, "resets_at": None}) == "--"
    # Half up, like the app's toFixed(0), so both surfaces show the same number.
    assert [_pct({**m, "pct": v}) for v in (0.125, 0.135, 0.004, 0.005)] == ["13", "14", "0", "1"]


def test_no_limits_says_so_instead_of_loading_forever():
    for state, msg in (("no_limits", "No limits reported"), ("stale_no_limits", "Limits unavailable")):
        for style in ("pill", "card"):
            items = make(state, style).items()
            assert msg in [it.text for it in items], (state, style)
            assert not [it for it in items if it.kind == "skel"], (state, style)
    assert osd.NO_LIMITS in make("no_limits").toolTip()


def test_every_limited_column_says_limited():
    for state in ("limited", "limited_5h", "both_limited", "limited_far", "wk_only_limited"):
        w = make(state)
        texts = [it.text for it in w.items() if it.kind == "text"]
        limited = [m for _, m in w._meters() if m["level"] == "limited"]
        assert sum("Limited" in t for t in texts) == len(limited), (state, texts)


def test_states_show_their_copy():
    def texts(state, style="card"):
        return " ".join(it.text for it in make(state, style).items() if it.kind == "text")
    assert "Live 4.1k tok/min" in texts("live")
    assert "Stale, as of" in texts("stale")
    assert "as of" not in texts("stale", "pill")  # the clock icon and dimmed values say it; keeps the pill short
    assert "Stale, as of" in texts("error_with_data")
    assert "Limited until" in texts("limited") or "Until" in texts("limited")
    assert "Limit in ~" in texts("critical")
    assert "Limit any minute" in texts("eta_now")
    one = texts("wk_only_critical")  # one column: the forecast and the reset side by side
    assert "Limit in ~" in one and "Resets " in one
    # Weekly only without a forecast (Ultra's usual card): the reset date and the countdown.
    subs = [it.text for it in sorted(make("no_5h").items(), key=lambda it: it.rect.left()) if it.role == "sub"]
    assert subs[0].startswith("Resets ") and subs[1] == "in 5d 17h", subs
    assert "Starts on next prompt" in texts("not_started") or "Not started" in texts("not_started")
    assert "Today $82.40" in texts("normal")
    assert not [it for it in make("loading").items() if it.role == "value"]
    tip = make("caution").toolTip()
    assert "At this pace the weekly limit is hit tomorrow" in tip and "Today $82.40" in tip
    assert "2 active sessions, 1 subagent" in make("live").toolTip()
    # The tooltip uses the card's words for a limited window and never puts a passed forecast in the future.
    assert "Weekly 100%, limited until " in make("limited").toolTip()
    assert "resets" not in make("limited").toolTip().split("\n")[0]
    assert "At this pace the limit is reached any minute" in make("eta_now").toolTip()
    assert osd._rate(999_960) == "1.0M tok/min" and osd._rate(999_940) == "999.9k tok/min"
    # The 5-hour limit has one name on the card, the tooltip, the menu, the tray and the alerts.
    assert [it.text for it in make("normal").items() if it.role == "label"] == ["Weekly", "5-hour"]
    assert "5-hour 12%, resets in 2h 30m" in make("normal").toolTip()


def test_signed_out_tooltip_says_why_and_as_of_when():
    st = states()["signed_out"]
    assert make("signed_out").toolTip().splitlines()[-1] == "No credentials found"
    as_of = datetime.now().astimezone() - timedelta(minutes=47)
    w = Osd()
    w.set_now({**st, "as_of": as_of.isoformat()})  # signed out after a good fetch
    assert w.toolTip().splitlines()[-1] == f"As of {_clock(as_of)}. No credentials found"


def test_only_fetch_errors_say_they_retry():
    def texts(state, style="card"):
        return [it.text for it in make(state, style).items() if it.kind == "text"]
    net = texts("error")
    assert "Can't reach Claude" in net and "Today $82.40" in net
    assert " ".join(texts("error")).endswith("Network unreachable. Retrying automatically.")
    newer = texts("newer_store")
    assert not [t for t in newer if "reach" in t or "Retrying" in t or "$" in t], newer
    assert " ".join(it.text for it in make("newer_store").items() if it.role == "body") == NEWER
    assert make("newer_store").toolTip() == NEWER
    assert "Update Claude Usage" in texts("newer_store", "pill") and "Limits unavailable" in texts("error", "pill")


def test_while_indexing_the_card_does_not_show_a_partial_cost_as_today(monkeypatch):
    w = Osd(style="card")
    w.set_now(states()["normal"], {"done": 2371, "total": 12571})
    texts = [it.text for it in w.items() if it.kind == "text"]
    assert "Indexing 18%" in texts and not [t for t in texts if t.startswith("Today $")], texts
    assert "Today $82.40 so far, indexing 2,371 of 12,571 transcripts" in w.toolTip()
    w.set_now(states()["normal"])
    assert "Today $82.40" in [it.text for it in w.items() if it.kind == "text"]


def test_long_error_wraps_and_keeps_its_end():
    body = [it.text for it in make("long_error").items() if it.role == "body"]
    assert len(body) > 1 and " ".join(body) == states()["long_error"]["message"] + ". Retrying automatically.", body


def test_stale_numbers_from_before_a_reset_say_so():
    now = datetime.now().astimezone()
    st = states(now)["stale"]
    st["session"] = {**st["session"], "resets_at": (now - timedelta(minutes=5)).isoformat()}
    card, pill = Osd(style="card"), Osd(style="pill")
    card.set_now(st)
    pill.set_now(st)
    assert any(it.text.startswith("Reset at") for it in card.items())
    assert [it.text for it in pill.items() if it.role == "sub"] == ["5d 17h", "reset"]
    assert "window reset at" in card.toolTip()


def test_nothing_is_cut_short():
    for state, style in itertools.product(states(), ("pill", "card")):
        cut = [it.text for it in make(state, style).items() if it.kind == "text" and it.text.endswith("...")]
        assert not cut, (state, style, cut)


def test_far_dates_keep_their_time():
    now = datetime.now().astimezone()
    far = now + timedelta(days=7)
    assert _when(far, now).endswith(_clock(far))
    assert any(it.text.endswith(_clock(far)) for it in make("limited_far").items() if it.role == "sub")


def test_pill_shows_the_time_to_the_limit_when_it_comes_first():
    def subs(state):
        return [it for it in make(state, "pill").items() if it.role == "sub"]
    assert subs("critical")[0].text.startswith("~")
    assert [it.text for it in subs("eta_now")] == ["<1m"]
    # Below caution it still shows, so the pill never hides a lockout the card shows.
    assert subs("normal_eta_fast")[0].text.startswith("~2d")
    assert subs("normal_eta_fast")[1].color == QColor(tokens.DARK["text_muted"])  # a reset countdown stays muted


@pytest.mark.parametrize("state,ink", [
    ("normal_eta_fast", "text"),      # below caution: plain text; the words say it is a forecast
    ("wk_only_eta_normal", "text"),
    ("caution", "warn"),
    ("wk_only_critical", "bad"),
    ("stale_caution", "text_muted"),  # last-known: dimmed like the values
])
def test_forecast_colour_is_one_rule_for_pill_and_card(state, ink):
    pill = next(it for it in make(state, "pill").items() if it.role == "sub")
    card = next(it for it in make(state, "card").items() if it.role == "sub" and "Limit" in it.text)
    assert pill.text.startswith("~") and card.text.startswith("Limit in ~")
    assert pill.color == card.color == QColor(tokens.DARK[ink]), state
    assert pill.font.weight() == card.font.weight() == 600, state


def test_stale_pill_keeps_its_clock_at_caution():
    icon = next(it for it in make("stale_caution", "pill").items() if it.kind == "icon")
    assert icon.icon == "clock" and icon.color == QColor(tokens.DARK["warn_fill"])
    assert next(it for it in make("caution", "pill").items() if it.kind == "icon").icon == "warn"


def test_layout_and_paint_read_the_same_clock(monkeypatch):
    now = datetime.now().astimezone()
    st = states(now)["no_5h"]
    st["weekly"] = {**st["weekly"], "resets_at": (now + timedelta(days=1, seconds=20)).isoformat()}
    w = Osd(style="pill")
    w.set_now(st)
    real = osd.datetime

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(seconds=40)

    monkeypatch.setattr(osd, "datetime", Later)
    assert w._build(1.0)[0] == (w.width(), w.height())  # "1d 0h" would be "23h 59m" by paint time


def test_pill_width_holds_while_countdowns_tick():
    now = datetime.now().astimezone()
    st = states(now)["normal"]
    w = Osd(style="pill")
    w.set_now(st)
    width = w.width()
    for mins in (59, 9, 0):
        w.set_now({**st, "session": {**st["session"], "resets_at": (now + timedelta(minutes=mins)).isoformat()}})
        assert w.width() == width, mins
    # Nor does a state change, or the 5h window starting, resize it (the status and value slots are fixed).
    assert {make(s, "pill").width() for s in ("live", "stale", "caution", "critical", "error_with_data",
                                              "stale_caution", "not_started")} == {width}
    # Nor a forecast coming and going on a weekly-only pill.
    wk = states(now)["no_5h"]
    widths = set()
    for eta in (None, timedelta(days=1, hours=10, minutes=30), timedelta(days=1, hours=9, minutes=30)):
        w.set_now({**wk, "weekly": {**wk["weekly"], "eta": eta and (now + eta).isoformat()}})
        widths.add(w.width())
    assert len(widths) == 1, widths


def test_accessible_name_and_description():
    w = make("caution")
    assert w.accessibleName() == "Claude usage"
    assert w.accessibleDescription() == w.toolTip() != ""
    assert w.testAttribute(Qt.WA_AlwaysShowToolTips)  # the OSD is never the active window


@pytest.mark.parametrize("dpr", [1.0, 1.25])
def test_pill_pads(dpr):
    """12px plus half the grid rounding on the left; at least that on the right (plus countdown room)."""
    for state in states():
        w = make(state, "pill")
        items = w.items(dpr)
        status = items[0]
        left = status.rect.left() - (3 if status.kind == "dot" else 0)  # the dot is centred in a 12px slot
        right = w.width() - max(it.rect.right() for it in items)
        assert 12 <= left < 16 + 1e-6 and right >= left - 1 / dpr - 1e-6, (state, left, right)


def test_nothing_in_the_pill_moves_while_countdowns_tick():
    now = datetime.now().astimezone()
    st = states(now)["normal"]
    w = Osd(style="pill")

    def at(weekly=None, session=None, eta=None):
        wk, s5 = dict(st["weekly"]), dict(st["session"])
        if weekly is not None:
            wk["resets_at"] = (now + weekly).isoformat()
        if session is not None:
            s5["resets_at"] = (now + session).isoformat()
        wk["eta"] = eta and (now + eta).isoformat()
        w.set_now({**st, "weekly": wk, "session": s5})
        # Countdowns may change their own text; everything else, and where each countdown starts, stays.
        return [(it.kind, it.role, "" if it.role == "sub" else it.text,
                 it.origin.x() if it.origin else it.rect.left(), it.rect.width() * (it.role != "sub"))
                for it in w.items(1.25)]

    m = timedelta(minutes=1, seconds=30)
    views = [at(session=timedelta(minutes=t) + m) for t in (130, 129, 70, 59, 10, 9, 0)]
    views += [at(weekly=d) for d in (timedelta(days=6, hours=23), timedelta(days=1, minutes=2), timedelta(hours=23, minutes=59),
                                     timedelta(hours=9, minutes=5))]
    views += [at(eta=timedelta(days=1, hours=10)), at(eta=timedelta(days=2, hours=3))]
    assert all(v == views[0] for v in views), [v for v in views if v != views[0]][:1]


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
    start, width = w.pos(), w.width()
    w.mousePressEvent(_mouse(QEvent.MouseButtonPress, Qt.LeftButton, QPoint(10, 10)))
    w.mouseMoveEvent(_mouse(QEvent.MouseMove, Qt.LeftButton, QPoint(60, 40)))
    w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, Qt.LeftButton, QPoint(60, 40)))
    w.toggle_style()
    w.set_style("card")  # already card: no second signal

    assert seen[0] == "click"
    assert seen[1] == ("menu", 20, 30)
    # The saved spot is the loading-size window on the same edges; this pill sits right of centre.
    assert seen[2] == ("moved", start.x() + 50 + width - Osd(style="pill").width(), start.y() + 30)
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
    assert seen == [(right + 1 - Osd(style="pill").width(), w.y())]  # the pill's spot at its loading size
    w.hide()


@pytest.mark.skipif(QApplication.platformName() != "offscreen", reason="shows a window")
def test_first_data_keeps_the_restored_edge():
    w = Osd(style="pill")
    scr = w.screen().geometry()
    saved = QPoint(scr.right() - 300, scr.bottom() - 40)  # bottom-right quadrant, as parked by the tray
    w.move(saved)
    w.show()
    right = w.geometry().right()
    seen = []
    w.moved.connect(lambda x, y: seen.append((x, y)))
    w.set_now(states()["normal"])
    assert w.width() > 200 and w.geometry().right() == right and seen == []
    w.set_style("card")  # the card's spot differs from the pill's, so it is reported
    assert w.geometry().right() == right and seen == [(w.x(), w.y())]
    w.hide()


@pytest.mark.skipif(QApplication.platformName() != "offscreen", reason="shows a window")
def test_restarts_keep_a_parked_pill_on_its_edge():
    """As widget.py does it: restore the spot moved last reported, show, then data arrives. The
    pill's width at launch differs from its width when saved, which used to walk it left daily."""
    st = states()
    scr = QApplication.primaryScreen().geometry()
    saved, edge = None, None
    for launch in (None, "not_started", "no_5h", "wk_only_eta_normal", "not_started", "no_5h"):
        w = Osd(style="pill")
        moves = []
        w.moved.connect(lambda x, y: moves.append(QPoint(x, y)))
        if saved:
            w.move(saved)
        w.show()
        if launch:
            w.set_now(st[launch])
        w.set_now(st["normal"])
        if saved is None:  # park it by the tray with a drag
            d = QPoint(scr.right() + 1 - 16 - w.width(), scr.bottom() + 1 - 40) - w.pos()
            w.mousePressEvent(_mouse(QEvent.MouseButtonPress, Qt.LeftButton, QPoint(0, 0)))
            w.mouseMoveEvent(_mouse(QEvent.MouseMove, Qt.LeftButton, d))
            w.mouseReleaseEvent(_mouse(QEvent.MouseButtonRelease, Qt.LeftButton, d))
            saved, edge = moves[-1], (w.geometry().right(), w.geometry().bottom())
        else:
            assert moves == [], launch
        assert (w.geometry().right(), w.geometry().bottom()) == edge, launch
        w.hide()


@pytest.mark.skipif(QApplication.platformName() != "offscreen", reason="shows a window")
def test_first_data_pulls_a_restored_spot_onto_the_screen():
    w = Osd(style="pill")
    scr = w.screen().geometry()
    w.move(scr.right() - 30, scr.top() + 100)  # mostly off the right edge (a monitor that shrank)
    w.show()
    w.set_now(states()["no_5h"])  # weekly only: the loaded size equals the loading size
    assert scr.contains(w.geometry()), (w.geometry(), scr)
    w.hide()


@pytest.mark.skipif(QApplication.platformName() != "offscreen", reason="shows a window")
def test_default_spot_keeps_its_margin_after_loading():
    w = Osd(style="pill")
    w.show()
    w.set_now(states()["normal"])
    assert w.geometry().right() == w.screen().availableGeometry().right() - EDGE_MARGIN
    w.hide()


@pytest.mark.skipif(QApplication.platformName() != "offscreen", reason="shows a window")
def test_flash_only_on_a_crossing_not_on_first_data():
    w = Osd(style="pill")
    w.show()
    w.set_now(states()["caution"])
    assert w._flash.state() != QVariantAnimation.Running
    w.set_now(states()["normal"])
    w.set_now(states()["caution"])
    assert w._flash.state() == QVariantAnimation.Running
    w.hide()


def test_widget_paint_matches_render():
    w = make("live", "card", opacity=1.0)
    grabbed = w.grab().toImage()
    assert (grabbed.width(), grabbed.height()) == (296, 136)
    assert QColor(grabbed.pixel(148, 3)) == QColor(tokens.DARK["osd_bg"])
