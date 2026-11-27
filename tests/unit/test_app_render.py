"""The app window: every page in light and dark loads offscreen from the
synthetic fixtures with zero QML warnings, survives the data states, and
wires range, project and settings back to the controller."""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":  # the offscreen platform finds no fonts without this
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from claude_usage.app import AppWindow  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from snapshot_contract import check, check_consistency  # noqa: E402

_app = QApplication.instance() or QApplication([])

FIXTURES = Path(__file__).parent.parent / "fixtures"
SNAPS = [p for p in (FIXTURES / "app_fixture.json", FIXTURES / "snapshot_sample.json") if p.exists()]
PAGES = ("overview", "sessions", "settings")
OUT = Path(tempfile.gettempdir()) / "claude_usage_app_render"


def pump(ms: int = 250) -> None:
    end = time.perf_counter() + ms / 1000
    while time.perf_counter() < end:
        _app.processEvents()
        time.sleep(0.005)


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class Harness:
    def __init__(self, snap: dict) -> None:
        self.snap = snap
        self.calls: list[tuple] = []
        self.settings: dict = {}
        self.win = AppWindow(self.provide, settings={"appearance": "light"}, on_setting=self.settings.__setitem__)
        self.win.update_snapshot(snap)
        self.win.show()
        self.win.window.resize(1280, 820)
        pump()

    def provide(self, range_key, project):
        self.calls.append((range_key, project))
        out = copy.deepcopy(self.snap)
        out["range"]["key"] = range_key
        out["project"] = project
        return out


@pytest.fixture(scope="module", params=SNAPS, ids=[p.stem for p in SNAPS])
def h(request):
    harness = Harness(load(request.param))
    harness.name = request.param.stem
    yield harness
    harness.win.prepare_quit()
    harness.win.window.close()
    pump(50)


def test_fixtures_follow_the_contract():
    for p in SNAPS:
        snap = load(p)
        check(snap)
        check_consistency(snap)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_warnings(h, page, dark):
    h.win.set_dark(dark)
    h.win.backend.pageRequested.emit(page)
    pump()
    img = h.win.window.grabWindow()
    OUT.mkdir(exist_ok=True)
    img.save(str(OUT / f"{h.name}_{page}_{'dark' if dark else 'light'}.png"))
    assert img.width() >= 1280 and img.height() >= 800
    # A blank or half-loaded window has a handful of colours; a page has hundreds.
    colours = {img.pixel(x, y) for x in range(0, img.width(), 7) for y in range(0, img.height(), 7)}
    assert len(colours) > 40, f"{page} looks empty"
    assert h.win.warnings == []


STATES = {
    "signed_out": lambda s: (s["status"].update(state="signed_out"), s["now"].update(state="signed_out")),
    "indexing": lambda s: s["status"].update(indexing={"done": 120, "total": 900}),
    "stale": lambda s: s["status"].update(state="stale"),
    "error": lambda s: s["status"].update(state="error", message="Could not read the usage store."),
    "no_weekly": lambda s: s["limits"].update(weekly=None, past_weeks=[], weeks_hit=0, weeks_counted=0),
    "with_5h": lambda s: s["limits"].update(session={"pct": 0.4, "resets_at": s["range"]["to"], "level": "normal"}),
    "empty": lambda s: (s.update(sessions=[], sessions_total=0, project="nothing-here"),
                        [g["values"][m].__setitem__(i, [0] * len(g["values"][m][i]))
                         for g in s["timeline"]["by"].values() for m in g["values"] for i in range(len(g["values"][m]))]),
}


@pytest.mark.parametrize("state", list(STATES))
def test_states_render_without_warnings(h, state):
    snap = copy.deepcopy(h.snap)
    STATES[state](snap)
    for page in ("overview", "sessions"):
        h.win.update_snapshot(snap)
        h.win.backend.pageRequested.emit(page)
        pump(120)
    h.win.update_snapshot(h.snap)
    pump(60)
    assert h.win.warnings == []


def test_range_and_project_go_through_the_provider(h):
    h.calls.clear()
    h.win.backend.setRange("30d")
    pump(120)
    h.win.backend.setProject("some-project")
    pump(120)
    assert h.calls[0] == ("30d", None)
    assert h.calls[-1] == ("30d", "some-project")
    assert h.settings.get("app_range") == "30d"
    assert h.win.backend.rangeKey == "30d"
    h.win.backend.setProject("")
    h.win.backend.setRange("7d")
    pump(120)
    h.win.update_snapshot(h.snap)


def test_background_provider_keeps_the_page_dimmed_until_data_arrives(h):
    pending = []
    real, h.win.backend._provider = h.win.backend._provider, lambda r, p: pending.append((r, p))
    try:
        h.win.backend.setRange("90d")
        pump(80)
        assert pending == [("90d", None)] and h.win.backend.busy
        h.win.update_snapshot(h.snap)
        assert not h.win.backend.busy
    finally:
        h.win.backend._provider = real
        h.win.backend.setRange("7d")
        pump(80)
        h.win.update_snapshot(h.snap)


def test_settings_reach_the_controller_and_appearance_applies(h):
    h.win.backend.setSetting("appearance", "dark")
    pump(50)
    assert h.settings["appearance"] == "dark"
    assert h.win.backend.dark is True
    h.win.backend.setSetting("appearance", "light")
    pump(50)
    assert h.win.backend.dark is False


def find_in(root, cls=""):
    """Every item under *root* whose QML type name starts with *cls*."""
    out, stack = [], [root]
    while stack:
        it = stack.pop()
        if it.metaObject().className().startswith(cls):
            out.append(it)
        stack.extend(it.childItems())
    return out


def find(win, cls):
    return find_in(win.window.contentItem(), cls)


def js(v):
    return v.toVariant() if hasattr(v, "toVariant") else v


def test_first_snapshot_loads_fast():
    # The weekly chart once built a day tick for every day since 1970 on the
    # first load (8-10 s and 300 MB); a fresh window must take well under that.
    win = AppWindow(lambda r, p: None, settings={"appearance": "light"})
    try:
        t = time.perf_counter()
        win.update_snapshot(load(SNAPS[0]))
        _app.processEvents()
        assert time.perf_counter() - t < 1.5
        days = [r for r in find(win, "QQuickRepeater") if r.objectName() == "weekDays"]
        assert days and all(r.property("count") <= 8 for r in days)
    finally:
        win.prepare_quit()
        win.window.close()


def test_series_past_the_palette_get_their_own_colour(h):
    snap = copy.deepcopy(h.snap)
    g = snap["timeline"]["by"]["project"]
    named = [k for k, c in zip(g["keys"], g["colors"]) if c >= 0][:5]
    g["colors"] = [[2, 16, 4, 0, 15][named.index(k)] if k in named else -1 for k in g["keys"]]
    for p in snap["projects"]:
        if p["name"] == named[1]:
            p["color"] = 16
    h.win.update_snapshot(snap)
    pump(60)
    shown = js(h.win.window.property("snap"))
    colors = [c for c in shown["timeline"]["by"]["project"]["colors"] if c != -1]
    assert len(set(colors)) == len(colors) and all(0 <= c < 8 for c in colors)
    sidebar = {p["name"]: p["color"] for p in shown["projects"]}
    assert sidebar[named[1]] == colors[named.index(named[1])]
    h.win.update_snapshot(h.snap)
    pump(60)


def test_legend_click_leaves_the_rest_of_the_chart_at_full_strength(h):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    h.win.backend.pageRequested.emit("overview")
    pump()
    tl = find(h.win, "TimelineCard")[0]
    page = find(h.win, "OverviewPage")[0]
    page.setProperty("contentY", tl.mapToItem(page.property("contentItem"), QPointF(0, 0)).y())
    pump()
    segs = find_in(tl, "Seg")
    chip = [c for c in find_in(tl, "QQuickAbstractButton")
            if c.property("on") is not None and not any(g.isAncestorOf(c) for g in segs)][0]
    p = chip.mapToScene(QPointF(chip.width() / 2, chip.height() / 2))
    QTest.mouseMove(h.win.window, QPoint(int(p.x()), int(p.y())))
    pump(60)
    assert tl.property("iso") != ""
    QTest.mouseClick(h.win.window, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
    pump(250)
    try:
        assert tl.property("iso") == ""
        assert all(s.opacity() == 1 for s in find(h.win, "QQuickShape") if tl.isAncestorOf(s))
    finally:
        QTest.mouseClick(h.win.window, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
        QTest.mouseMove(h.win.window, QPoint(5, 5))
        page.setProperty("contentY", 0)
        pump(60)


def test_pace_charts_always_label_this_week(h):
    for card in ("WeekChart", "PastWeeksCard"):
        labels = [js(c.property("labels")) for c in find_in(find(h.win, card)[0]) if c.property("labels") is not None]
        texts = [x["text"] for ls in labels for x in (ls.values() if isinstance(ls, dict) else ls)
                 if card == "WeekChart" or x["now"]]
        assert any(t.startswith("Now ") or card == "PastWeeksCard" and t.endswith("%") for t in texts), (card, texts)


def test_top_sessions_come_from_the_whole_range(h):
    # sessions is capped to the most recent; top_sessions ranks the whole range
    snap = copy.deepcopy(h.snap)
    top = copy.deepcopy(snap["sessions"][0])
    top.update(id="the-costliest", cost=10 ** 6)
    snap["top_sessions"] = [top]
    h.win.update_snapshot(snap)
    pump(60)
    page = find(h.win, "OverviewPage")[0]
    table = [t for t in find(h.win, "SessionTable") if page.isAncestorOf(t)][0]
    assert [r["id"] for r in js(table.property("rows"))] == ["the-costliest"]
    seen = len(h.win.warnings)
    for _ in range(3):  # and back: the rows once read the old top_sessions and threw
        h.win.update_snapshot(h.snap)
        pump(60)
        h.win.update_snapshot(snap)
        pump(60)
    h.win.update_snapshot(h.snap)
    pump(60)
    assert h.win.warnings[seen:] == []


def test_all_projects_count_is_not_carried_across_ranges(h):
    side = find(h.win, "Sidebar")[0]
    h.win.update_snapshot(h.snap)
    pump(30)
    assert side.property("total") == f"{h.snap['sessions_total']:,}"
    scoped = copy.deepcopy(h.snap)
    scoped.update(project="some-project", sessions_total=3)
    scoped["range"]["key"] = "all"
    h.win.update_snapshot(scoped)
    pump(30)
    assert side.property("total") == ""  # all time never seen unscoped
    h.win.update_snapshot(h.snap)
    pump(30)


def test_headline_numbers_fit_at_the_minimum_size(h):
    h.win.backend.pageRequested.emit("overview")
    h.win.window.resize(960, 640)
    pump()
    try:
        cut = [t.property("text") for k in find(h.win, "KpiCard") for t in find_in(k, "Txt") if t.property("truncated")]
        assert cut == []
    finally:
        h.win.window.resize(1280, 820)
        pump()


def qml_eval(h, expr, root="QtObject"):
    """Evaluate a JS expression with the Ui singleton (and *root*'s members) in scope."""
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent
    from claude_usage.app.window import QML_MAIN
    comp = QQmlComponent(h.win.engine)
    comp.setData(f"import QtQuick\n{root} {{ property var out: {expr} }}".encode(),
                 QUrl.fromLocalFile(str(QML_MAIN.with_name("Eval.qml"))))
    obj = comp.create()
    assert obj is not None, [e.toString() for e in comp.errors()]
    return js(obj.property("out"))


def test_money_axis_labels_keep_the_quarter_steps(h):
    assert qml_eval(h, "[Ui.moneyAxis(1250), Ui.moneyAxis(12.5), Ui.moneyAxis(2500), Ui.moneyAxis(600), Ui.moneyAxis(0.25)]") \
        == ["$1.25k", "$12.5", "$2.5k", "$600", "$0.25"]


def test_today_is_not_drawn_as_a_cleaned_up_day(h):
    # The data layer flags today partial because it is still filling; only
    # earlier days are partial because their transcripts were cleaned up.
    snap = copy.deepcopy(h.snap)
    for d in snap["activity"]["days"]:
        d["partial"] = False
    snap["activity"]["days"][-1].update(partial=True, level=4)
    h.win.update_snapshot(snap)
    pump(60)
    try:
        card = find(h.win, "ActivityCard")[0]
        assert card.property("anyPartial") is False
        paths = [js(i.property("paths")) for i in find_in(card) if i.property("paths") is not None][0]
        assert paths["ring"] == [""] * 5 and paths["today"] and paths["fill"][4]
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)


def test_past_weeks_quote_the_weekly_card_for_this_week(h):
    snap = copy.deepcopy(h.snap)
    snap["limits"]["weekly"]["pct"] = 0.42
    snap["limits"]["past_weeks"][-1].update(current=True, peak=0.63)
    h.win.update_snapshot(snap)
    pump(60)
    try:
        card = find(h.win, "PastWeeksCard")[0]
        labels = [js(c.property("labels")) for c in find_in(card) if c.property("labels") is not None][0]
        assert [x["text"].replace("Now ", "") for x in labels if x["now"]] == ["42%"]
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)


def test_colours_past_the_palette_stick_across_ranges(h):
    base = copy.deepcopy(h.snap)
    g = base["timeline"]["by"]["project"]
    named = [k for k, c in zip(g["keys"], g["colors"]) if c >= 0][:5]

    def shown(colors, key):
        snap = copy.deepcopy(base)
        snap["range"]["key"] = key
        gg = snap["timeline"]["by"]["project"]
        gg["colors"] = [colors[named.index(k)] if k in named else -1 for k in gg["keys"]]
        h.win.update_snapshot(snap)
        pump(30)
        out = js(h.win.window.property("snap"))["timeline"]["by"]["project"]
        return dict(zip(out["keys"], out["colors"]))

    # Real ranks are all-time, so they do not move between snapshots like
    # this; forget what the earlier tests' made-up ranks left behind.
    qml_eval(h, "(Ui.slots = ({}), 0)")
    try:
        first = shown([0, 1, 12, 2, 3], "7d")[named[2]]
        # a higher-ranked newcomer must not take the slot named[2] already has
        again = shown([0, 9, 12, 2, 3], "24h")
        assert again[named[2]] == first and again[named[1]] not in (first, 0, 2, 3)
        assert json.loads(h.settings["series_slots"])["p:" + named[2]] == first
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_a_refresh_that_changed_nothing_skips_the_rebuild(h):
    count = []

    def counted():
        count.append(1)

    h.win.update_snapshot(h.snap)
    h.win.backend.snapshotChanged.connect(counted)
    try:
        same = copy.deepcopy(h.snap)
        same["generated_at"] = "2026-11-26T14:21:00-05:00"
        same["status"]["updated_at"] = "2026-11-26T14:21:00-05:00"
        h.win.update_snapshot(same)
        assert count == [] and h.win.backend.updatedAt == same["status"]["updated_at"]
        same["now"]["today_cost"] += 1
        h.win.update_snapshot(same)
        assert count == [1]
    finally:
        h.win.backend.snapshotChanged.disconnect(counted)
        h.win.update_snapshot(h.snap)
        pump(30)


def test_tab_reaches_the_projects_before_show_more(h):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    snap = copy.deepcopy(h.snap)
    # enough active projects to fold some under "Show N more"
    snap["projects"] = [dict(name=f"p{i}", sessions=1, cost=10 - i * 0.1, color=-1, idle=False) for i in range(12)]
    h.win.update_snapshot(snap)
    pump(60)
    try:
        start = [n for n in find(h.win, "NavItem") if n.property("text") == "Settings"][0]
        start.forceActiveFocus(Qt.TabFocusReason)
        seen = []
        for _ in range(14):
            QTest.keyClick(h.win.window, Qt.Key_Tab)
            seen.append(h.win.window.activeFocusItem().property("text"))
        assert seen.index("All projects") < seen.index("p0") < seen.index("p7") < seen.index("Show 4 more"), seen
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


@pytest.mark.skipif(sys.platform != "win32", reason="reads Windows private bytes")
def test_switching_data_does_not_grow_memory():
    import ctypes
    import ctypes.wintypes as wt
    from PySide6.QtCore import QCoreApplication, QEvent

    class PMC(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("faults", wt.DWORD)] + [(n, ctypes.c_size_t) for n in
                    ("peak_ws", "ws", "a", "b", "c", "d", "private", "peak_private")]

    k32, psapi = ctypes.windll.kernel32, ctypes.windll.psapi
    k32.GetCurrentProcess.restype = ctypes.c_void_p
    psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(PMC), wt.DWORD]

    def private_mb():
        p = PMC()
        p.cb = ctypes.sizeof(PMC)
        psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(p), p.cb)
        return p.private / 2 ** 20

    snaps = [load(p) for p in SNAPS]
    other = copy.deepcopy(snaps[0])
    other["range"]["key"] = "30d"
    snaps.append(other)
    # never shown: the delegates are the question here, not the renderer
    win = AppWindow(lambda r, p: None, settings={"appearance": "dark"})
    win.window.resize(1280, 820)

    def switch(n):
        for i in range(n):
            win.update_snapshot(snaps[i % len(snaps)])
            _app.processEvents()
            # what the running event loop does between refreshes; a script
            # that never enters it keeps every replaced delegate alive
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        win.engine.collectGarbage()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    try:
        switch(6)
        before = private_mb()
        switch(24)
        assert private_mb() - before < 40
    finally:
        win.prepare_quit()
        win.window.close()


def test_every_metric_and_group_switch_is_quick(h):
    # A switch once read the new metric's gridline step with the old metric's
    # top (cost after tokens) and froze in a ten-million-step loop.
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    h.win.backend.pageRequested.emit("overview")
    pump()
    tl = find(h.win, "TimelineCard")[0]
    page = find(h.win, "OverviewPage")[0]
    page.setProperty("contentY", tl.mapToItem(page.property("contentItem"), QPointF(0, 0)).y())
    pump()
    buttons = {b.property("text"): b for s in find_in(tl, "Seg") for b in find_in(s, "QQuickAbstractButton")}
    chart = [c for c in find_in(tl) if c.property("ticks") is not None][0]
    metrics = ["Cost", "Tokens", "Messages", "Agent time"]
    clicks = [x for a in metrics for b in metrics if a != b for x in (a, b)] + ["Model", "Tokens", "Cost", "Project"]
    slow = []
    try:
        for name in clicks:
            b = buttons[name]
            p = b.mapToScene(QPointF(b.width() / 2, b.height() / 2))
            t = time.perf_counter()
            QTest.mouseClick(h.win.window, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
            _app.processEvents()
            dt, ticks = time.perf_counter() - t, len(js(chart.property("ticks")))
            if dt > 1 or not 0 < ticks < 10:
                slow.append((name, round(dt, 2), ticks))
        assert slow == []
        assert (tl.property("metric"), tl.property("group")) == ("cost", "project")
    finally:
        page.setProperty("contentY", 0)
        pump(60)


def test_keys_scroll_the_page_and_keep_focus_in_view(h):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    h.win.backend.pageRequested.emit("overview")
    pump()
    page = find(h.win, "OverviewPage")[0]
    page.setProperty("contentY", 0)
    page.forceActiveFocus()
    try:
        QTest.keyClick(h.win.window, Qt.Key_PageDown)
        pump(300)
        assert page.property("contentY") > 0.8 * page.height()
        QTest.keyClick(h.win.window, Qt.Key_End)
        pump(300)
        assert abs(page.property("contentY") - (page.property("contentHeight") - page.height())) < 1
        QTest.keyClick(h.win.window, Qt.Key_Home)
        pump(300)
        assert page.property("contentY") == 0
        # a control far down the page that takes focus is scrolled into view
        btn = [b for b in find_in(page, "Btn") if b.property("text") == "All sessions"][0]
        btn.forceActiveFocus(Qt.TabFocusReason)
        pump(300)
        y = btn.mapToItem(page, QPointF(0, 0)).y()
        assert 0 <= y and y + btn.height() <= page.height(), y
    finally:
        page.setProperty("contentY", 0)
        pump(60)


def test_selecting_a_project_keeps_the_project_list(h):
    side = find(h.win, "Sidebar")[0]
    h.win.update_snapshot(h.snap)
    pump(30)
    before = [p["name"] for p in js(side.property("listed"))]
    # Lists colour only what the chart draws; the rest are in "Other".
    shown = js(h.win.window.property("snap"))
    g = shown["timeline"]["by"]["project"]
    drawn = dict(zip(g["keys"], g["colors"]))
    assert all(p["color"] == drawn.get(p["name"], -1) for p in shown["projects"])
    # a scoped snapshot only knows its own project
    scoped = copy.deepcopy(h.snap)
    name = before[1]
    scoped["project"] = name
    scoped["projects"] = [dict(p, idle=p["name"] != name) for p in scoped["projects"]]
    h.win.update_snapshot(scoped)
    pump(30)
    try:
        assert [p["name"] for p in js(side.property("listed"))] == before
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_close_hides_and_remembers_geometry(h):
    h.win.window.close()
    pump(50)
    assert not h.win.is_visible()
    assert len(h.settings.get("app_geometry", [])) == 4
    h.win.show()
    pump(50)
    assert h.win.is_visible()


def test_before_the_first_snapshot_the_page_shows_its_shape():
    win = AppWindow(lambda r, p: None, settings={"appearance": "light"})
    try:
        win.window.resize(1280, 820)
        win.show()
        pump(100)
        page = find(win, "OverviewPage")[0]
        assert len([g for g in find_in(page, "Ghost") if g.isVisible()]) >= 3
        # nothing that only makes sense with data: scope lines, the cost footnote
        texts = [t.property("text") for t in find_in(page, "Txt") if t.isVisible()]
        assert not any("Account-wide" in t or "API-equivalent" in t for t in texts), texts
        assert win.warnings == []
    finally:
        win.prepare_quit()
        win.window.close()


def test_first_index_says_it_is_still_reading(h):
    snap = copy.deepcopy(h.snap)
    STATES["empty"](snap)
    snap["project"] = None
    snap["status"]["indexing"] = {"done": 412, "total": 12512}
    h.win.update_snapshot(snap)
    h.win.backend.pageRequested.emit("overview")
    pump(60)
    try:
        page = find(h.win, "OverviewPage")[0]
        texts = [t.property("text") for t in find_in(page, "Txt") if t.isVisible()]
        assert "Still reading your transcripts" in texts and not any(t.startswith("No activity") for t in texts), texts
        assert not [b for b in find_in(page, "Btn") if b.isVisible() and b.property("text") == "Show all time"]
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)


def test_the_kpi_row_is_in_the_first_screen(h):
    # Plan limits come first; at the default size they must leave room for
    # the whole KPI row.
    from PySide6.QtCore import QPointF
    h.win.backend.pageRequested.emit("overview")
    page = find(h.win, "OverviewPage")[0]
    page.setProperty("contentY", 0)
    pump()
    bottoms = [k.mapToItem(page, QPointF(0, k.height())).y() for k in find_in(page, "KpiCard") if k.isVisible()]
    assert len(bottoms) == 5 and max(bottoms) <= page.height(), (bottoms, page.height())


def test_turns_without_records_read_as_unknown(h):
    cells = "[cell({turns: 0, agent_ms: 0}, 'turns'), cell({turns: 0, agent_ms: 5000}, 'turns'), cell({turns: 3, agent_ms: 0}, 'turns')]"
    assert qml_eval(h, cells, root="SessionTable") == ["-", "0", "3"]


def test_legend_keeps_whole_names_when_the_row_has_room(h):
    snap = copy.deepcopy(h.snap)
    long_name = "a-rather-long-project-folder-name"
    snap["timeline"]["by"]["project"]["keys"][0] = long_name
    h.win.backend.pageRequested.emit("overview")
    h.win.window.resize(1600, 1000)
    h.win.update_snapshot(snap)
    pump()
    try:
        tl = find(h.win, "TimelineCard")[0]
        names = [t for t in find_in(tl, "TipTxt") if t.property("text") == long_name]
        assert names and not names[0].property("truncated")
    finally:
        h.win.window.resize(1280, 820)
        h.win.update_snapshot(h.snap)
        pump()


def test_windows_animation_setting_turns_motion_off(h, monkeypatch):
    from claude_usage.app import window
    assert isinstance(window.animations_on(), bool)
    monkeypatch.setattr(window, "animations_on", lambda: False)
    h.win.backend.themeChanged.emit()
    try:
        assert qml_eval(h, "[Ui.fast, Ui.ms(300)]") == [0, 0]
    finally:
        monkeypatch.undo()
        h.win.backend.themeChanged.emit()
    assert qml_eval(h, "[Ui.fast, Ui.ms(300)]") == ([140, 300] if window.animations_on() else [0, 0])


# The tests below run in a child process on the real Windows platform
# (DirectWrite fonts, Direct3D 11), which the offscreen platform hides.
def run_child(code, *args):
    import subprocess
    env = {k: v for k, v in os.environ.items() if k != "QT_QPA_PLATFORM"}
    src = str(Path(__file__).parents[2] / "src")
    r = subprocess.run([sys.executable, "-c", code, src, *args], env=env, capture_output=True, text=True, timeout=120)
    if r.returncode == 77:
        pytest.skip("no Direct3D 11 device")
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


FONT_CHILD = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QUrl
from PySide6.QtGui import QFont, QFontInfo, QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
app = QGuiApplication([])
from claude_usage.app.window import QML_MAIN, Backend
be = Backend(lambda r, p: None, {})
eng = QQmlEngine(); eng.rootContext().setContextProperty("backend", be)
comp = QQmlComponent(eng)
comp.setData(b'import QtQuick\nItem { Txt { font.weight: 600 }\nTxt { display: true; font.weight: 600; font.pixelSize: 28 } }',
             QUrl.fromLocalFile(str(QML_MAIN.with_name("FontHost.qml"))))
root, out = comp.create(), []
for t in root.childItems():
    f = t.property("font")
    out.append([QFontInfo(f).styleName(), int(QFontInfo(f).weight()), f.variableAxisValue(QFont.Tag("opsz"))])
print(json.dumps(out))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Segoe UI Variable is a Windows font")
def test_semibold_renders_semibold_on_windows():
    # "Segoe UI Variable Text" and "Display" only list Regular and Bold, so
    # weight 600 used to come out Bold on the real platform.
    assert run_child(FONT_CHILD) == [["Semibold", 600, 10.5], ["Semibold", 600, 36.0]]


# Every other test renders on the software scene graph. This one draws the
# Overview through QQuickRenderControl on Direct3D 11 (the real GPU path,
# never shown on screen), dark, and checks the stacked timeline column by
# column: no background showing inside the stack, no fill above it.
GPU_CHILD = r"""
import json, random, re, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QPointF, QSize, QUrl
from PySide6.QtGui import (QGuiApplication, QImage, QRhiColorAttachment, QRhiReadbackDescription,
                           QRhiReadbackResult, QRhiRenderBuffer, QRhiTexture, QRhiTextureRenderTargetDescription)
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickRenderControl, QQuickRenderTarget, QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle
app = QGuiApplication([])
from claude_usage.app.window import QML_MAIN, Backend
QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Direct3D11)
QQuickStyle.setStyle("FluentWinUI3")
W, H = 1280, 2400
text = Path(sys.argv[2]).read_text(encoding="utf-8")
snap = json.loads(text)
n, K = len(snap["timeline"]["starts"]), len(snap["timeline"]["by"]["project"]["keys"])
cases = {
    # Spikes that alternate between series: band edges interpolated separately
    # cross between buckets, and the curve renderer filled them as shards.
    "spikes": [[40.0 if (k + i) % 3 == 0 else 0.0 for k in range(n)] for i in range(K)],
}
for seed in range(4):
    # Like real use: one large project, the rest a few cents in most buckets
    # with the odd burst. Sub-pixel slivers made whole bands drop out.
    rnd = random.Random(seed)
    rows = [[rnd.uniform(50, 400) for _ in range(n)]]
    rows += [[rnd.uniform(5, 150) if rnd.random() < 0.15 else rnd.uniform(0.02, 1.5) if rnd.random() < 0.8 else 0.0
              for _ in range(n)] for _ in range(K - 1)]
    rnd.shuffle(rows)
    cases["slivers%d" % seed] = rows
be = Backend(lambda r, p: None, {})
be.set_dark(True)
be.update_snapshot(snap)
rc = QQuickRenderControl(); win = QQuickWindow(rc)
eng = QQmlEngine(); eng.rootContext().setContextProperty("backend", be)
comp = QQmlComponent(eng)
comp.setData(b'import QtQuick\nItem { property var snap: Ui.fixColors(JSON.parse(backend.snapshotJson))\n'
             b'Rectangle { anchors.fill: parent; color: Ui.p.bg }\n'
             b'OverviewPage { anchors.fill: parent; snap: parent.snap; viewH: 820 } }',
             QUrl.fromLocalFile(str(QML_MAIN.with_name("GpuHost.qml"))))
root = comp.create()
if root is None:
    print(json.dumps({"error": [e.toString() for e in comp.errors()]})); sys.exit(1)
root.setParentItem(win.contentItem()); root.setWidth(W); root.setHeight(H); win.setGeometry(0, 0, W, H)
if not rc.initialize():
    sys.exit(77)
rhi = rc.rhi()
tex = rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(W, H), 1, QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource); tex.create()
ds = rhi.newRenderBuffer(QRhiRenderBuffer.Type.DepthStencil, QSize(W, H), 1); ds.create()
desc = QRhiTextureRenderTargetDescription(QRhiColorAttachment(tex)); desc.setDepthStencilBuffer(ds)
rt = rhi.newTextureRenderTarget(desc); rp = rt.newCompatibleRenderPassDescriptor(); rt.setRenderPassDescriptor(rp); rt.create()
win.setRenderTarget(QQuickRenderTarget.fromRhiRenderTarget(rt))
js = lambda v: v.toVariant() if hasattr(v, "toVariant") else v
def frame():
    for _ in range(5):  # past the 300 ms entrance and count-up animations
        end = time.perf_counter() + 0.1
        while time.perf_counter() < end: app.processEvents(); time.sleep(0.005)
        rc.polishItems(); rc.beginFrame(); rc.sync(); rc.render()
        res = QRhiReadbackResult(); b = rhi.nextResourceUpdateBatch(); b.readBackTexture(QRhiReadbackDescription(tex), res)
        rc.commandBuffer().resourceUpdate(b); rc.endFrame()
    return QImage(bytes(res.data), res.pixelSize.width(), res.pixelSize.height(), QImage.Format.Format_RGBA8888).copy()
def check(img):
    chart, stack = None, [root]
    while stack:
        it = stack.pop()
        if it.property("bands") is not None and it.property("xL") is not None:
            chart = it
        stack.extend(it.childItems())
    card = chart.parentItem().parentItem()
    xL, xR, base, ytop = (chart.property(x) for x in ("xL", "xR", "base", "yTop"))
    last, cnt = card.property("lastFull"), card.property("n")
    top = [(float(a), float(b)) for a, b in re.findall(r"(-?[\d.]+),(-?[\d.]+)", js(chart.property("stack"))["top"])]
    labels = [(d["x"] - 3, d["y"] - 3, d["x"] + d["w"] + 3, d["y"] + 25) for d in js(chart.property("direct"))]
    o = chart.mapToScene(QPointF(0, 0))
    rgb = lambda x, y: img.pixelColor(int(o.x() + x), int(o.y() + y)).getRgb()[:3]
    dist = lambda a, b: sum(abs(p - q) for p, q in zip(a, b))
    def edge(x):
        j = max(0, min(len(top) - 2, next((i for i in range(len(top) - 1) if top[i + 1][0] >= x), len(top) - 2)))
        (ax, ay), (bx, by) = top[j], top[j + 1]
        return ay + (by - ay) * (x - ax) / max(1e-6, bx - ax)
    holes = spikes = cols = 0
    for x in range(int(xL) + 2, int(xL + (xR - xL) * last / (cnt - 1)) - 2):
        # the outline stroke spans more than one row on a steep slope
        near = [edge(x + dx) for dx in (-2, -1, 0, 1, 2)]
        lo, hi = min(near) - 3, max(near) + 3
        cols += 1
        for y in range(int(ytop), int(base) - 3):
            if lo <= y <= hi or any(l[0] <= x <= l[2] and l[1] <= y <= l[3] for l in labels):
                continue
            d = dist(rgb(x, y), rgb(xL - 6, y))  # the card behind, left of the plot
            holes += y > hi and d < 30
            spikes += y < lo and d > 150
    return {"holes": holes, "spikes": spikes, "cols": cols}
out = {}
for name, rows in cases.items():
    snap = json.loads(text)
    snap["timeline"]["by"]["project"]["values"]["cost"] = rows
    be.update_snapshot(snap)
    out[name] = check(frame())
print(json.dumps(out))
"""


@pytest.mark.skipif(sys.platform != "win32", reason="Direct3D 11 is Windows only")
def test_timeline_bands_fill_cleanly_on_the_gpu_path():
    r = run_child(GPU_CHILD, str(SNAPS[0]))
    assert all(c["cols"] > 500 for c in r.values()), r
    assert {k: (c["holes"], c["spikes"]) for k, c in r.items() if c["holes"] or c["spikes"]} == {}, r
