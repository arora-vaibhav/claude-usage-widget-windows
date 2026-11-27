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
    "5h_not_started": lambda s: s["limits"].update(session={"pct": 0.0, "resets_at": None, "level": "normal"}),
    "ultra": lambda s: (s["status"].update(plan="Ultra"),
                        s["limits"].update(session=None, session_note="Ultra has a weekly limit and no 5-hour limit.")),
    "limits_failed": lambda s: (s["status"].update(state="error", message="Can't reach the usage API. Check your network."),
                                s["limits"].update(weekly=None, session=None, past_weeks=[], weeks_hit=0, weeks_counted=0)),
    "limits_pending": lambda s: (s["status"].update(state="loading"), s["limits"].update(weekly=None, session=None)),
    "empty": lambda s: (s.update(sessions=[], sessions_total=0, project="nothing-here"),
                        [g["values"][m].__setitem__(i, [0] * len(g["values"][m][i]))
                         for g in s["timeline"]["by"].values() for m in g["values"] for i in range(len(g["values"][m]))]),
    "5h_critical": lambda s: (s["limits"].update(session={"pct": 0.93, "resets_at": s["range"]["to"], "level": "critical"}),
                              s["now"].update(session={"pct": 0.93, "resets_at": s["range"]["to"], "level": "critical",
                                                       "forecast": "Limit 3:27 AM", "eta": s["range"]["to"]})),
    # a store from a newer version: the widget builds from an empty stand-in store
    "newer_store": lambda s: (STATES["limits_failed"](s), STATES["empty"](s), s.update(project=None),
                              s["status"].update(message="Your usage store is from a newer version of Claude Usage. "
                                                         "Update to see your usage.", transcripts=0),
                              s["now"].update(state="error", error_kind="store")),
    "first_week": lambda s: s["limits"].update(past_weeks=[dict(s["limits"]["past_weeks"][-1], current=True)],
                                               weeks_hit=0, weeks_counted=0),
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
        h.win.update_snapshot(h.snap)  # a build for the old range that was already running: not shown
        assert h.win.backend.busy and h.win.backend.rangeKey == "90d"
        h.win.update_snapshot(dict(h.snap, range=dict(h.snap["range"], key="90d")))
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
    # the table shows top_sessions, not the first rows of the capped sessions list
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


def test_all_projects_count_comes_from_the_snapshot(h):
    side = find(h.win, "Sidebar")[0]
    scoped = copy.deepcopy(h.snap)
    scoped.update(project="some-project", sessions_total=3)
    h.win.update_snapshot(scoped)
    pump(30)
    try:
        # every project's sessions, not the selected project's
        assert side.property("total") == f"{h.snap['sessions_total_all']:,}"
    finally:
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


def test_this_weeks_bar_stays_inside_the_past_weeks_chart(h):
    # the scale once came from ended weeks only, so a long week at the limit
    # ran off the top of the card and over the cards above it
    snap = copy.deepcopy(h.snap)
    weeks = snap["limits"]["past_weeks"]
    longest = max(w["hours_at_limit"] for w in weeks[:-1])
    weeks[-1].update(current=True, peak=1.0, hours_at_limit=longest * 3 + 40)
    snap["limits"]["weekly"]["pct"] = 1.0
    h.win.update_snapshot(snap)
    pump(60)
    try:
        card = find(h.win, "PastWeeksCard")[0]
        chart = [c for c in find_in(card) if c.property("yTop") is not None][0]
        bars = [b for b in find_in(chart) if b.property("wk") is not None]
        assert len(bars) == len(weeks)
        assert min(b.y() for b in bars) >= chart.property("yTop") - 0.5, [b.y() for b in bars]
        assert chart.property("maxH") >= weeks[-1]["hours_at_limit"]
        labels = js(chart.property("labels"))
        assert [x for x in labels if x["now"]] and min(x["y"] for x in labels) >= 0
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)


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


def test_lists_colour_only_what_the_chart_draws(h):
    shown = js(h.win.window.property("snap"))
    g = shown["timeline"]["by"]["project"]
    drawn = dict(zip(g["keys"], g["colors"]))
    assert all(p["color"] == drawn.get(p["name"], -1) for p in shown["projects"])
    assert all(x["color"] == drawn.get(x["project"], -1) for x in shown["sessions"] + shown["top_sessions"])


def test_a_range_switch_with_a_project_selected_keeps_every_active_project(tmp_path):
    # A project's snapshot once listed only that project; the sidebar then kept a list
    # from the same range unfiltered, so another range showed everything else as idle.
    sys.path.insert(0, str(FIXTURES))
    import make_store
    from claude_usage import snapshot
    conn, limits = make_store.build_store(str(tmp_path))
    win = AppWindow(lambda r, p: snapshot.build(conn, r, p, make_store.NOW, limits), settings={"appearance": "light"})
    try:
        win.window.resize(1280, 820)
        win.show()
        win.update_snapshot(snapshot.build(conn, "7d", None, make_store.NOW, limits))
        pump(60)
        side = find(win, "Sidebar")[0]
        name = js(side.property("active"))[-1]["name"]
        win.backend.setProject(name)
        pump(120)
        for rng in ("90d", "24h", "all"):
            win.backend.setRange(rng)
            pump(120)
            want = snapshot.build(conn, rng, None, make_store.NOW, limits)
            assert js(win.window.property("snap"))["project"] == name
            active = sorted(p["name"] for p in want["projects"] if not p["idle"])
            assert len(active) > 1 and sorted(p["name"] for p in js(side.property("active"))) == active, rng
            assert side.property("total") == f"{want['sessions_total']:,}"
        assert win.warnings == []
    finally:
        win.prepare_quit()
        win.window.close()
        conn.close()


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
    # and the head of the timeline: its label and the range total
    tl = find_in(page, "TimelineCard")[0]
    total = [t for t in find_in(tl, "Txt") if t.property("display")][0]
    assert total.mapToItem(page, QPointF(0, total.height())).y() <= page.height()


def visible_texts(win, cls="OverviewPage"):
    return [t.property("text") for t in find_in(find(win, cls)[0]) if "Txt" in t.metaObject().className()
            and t.isVisible() and t.property("text")]


def show_state(h, state):
    snap = copy.deepcopy(h.snap)
    STATES[state](snap)
    h.win.update_snapshot(snap)
    h.win.backend.pageRequested.emit("overview")
    pump(60)
    return visible_texts(h.win)


def test_a_failed_limits_fetch_says_what_failed_not_plan_facts(h):
    try:
        texts = show_state(h, "limits_failed")
        assert "Couldn't load your plan limits" in texts
        assert any(t.startswith("Can't reach the usage API. Check your network.") for t in texts), texts
        assert not any("has not reported" in t or "does not report" in t for t in texts), texts
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_pending_limits_show_placeholders_not_missing_limits(h):
    try:
        texts = show_state(h, "limits_pending")
        assert "Checking your plan limits." in texts
        assert not any("has not reported" in t or "Not reported" in t for t in texts), texts
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_a_5_hour_window_that_has_not_started_is_not_0_percent(h):
    try:
        texts = show_state(h, "5h_not_started")
        card = find(h.win, "OtherLimitsCard")[0]
        rows = js(card.property("rows"))
        assert rows[0]["pill"] == "Not started" and "value" not in rows[0], rows[0]
        assert "Starts on your next prompt." in texts
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_ultra_has_no_5_hour_limit_and_weekly_leads(h):
    try:
        texts = show_state(h, "ultra")
        rows = js(find(h.win, "OtherLimitsCard")[0].property("rows"))
        assert rows[0]["pill"] == "None on Ultra" and rows[0]["sub"] == "Ultra has a weekly limit and no 5-hour limit."
        assert any(t.startswith("Ultra plan, account-wide") for t in texts), texts
        assert not any(t.endswith("0%") and "5-hour" in t for t in texts)
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_a_store_from_a_newer_version_does_not_promise_history(h):
    try:
        texts = show_state(h, "newer_store")
        assert "Usage shows again after you update Claude Usage." in texts, texts
        assert not any("still works" in t or t.startswith("No activity") or "sessions in the" in t for t in texts), texts
        page = find(h.win, "OverviewPage")[0]
        shown = [b.property("text") for b in find_in(page, "Btn") if b.isVisible()]
        assert "Refresh" not in shown and "Show all time" not in shown, shown
        assert not [t for t in visible_texts(h.win, "Sidebar") if "local transcripts" in t]
        assert not [t for t in texts if "It is not a bill." in t], texts  # no cost note over no usage
        # its counts are unknown, not 0
        assert not [t for t in visible_texts(h.win, "Sidebar") if t == "0" or t.endswith(" active")]
        h.win.backend.pageRequested.emit("sessions")
        pump(60)
        assert "Session history unavailable. It shows again after you update Claude Usage." in visible_texts(h.win, "SessionsPage")
        assert find(h.win, "SessionsPage")[0].property("subtitle") == ""
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_the_sessions_page_says_the_first_index_is_still_reading(h):
    snap = copy.deepcopy(h.snap)
    STATES["empty"](snap)
    snap["project"] = None
    snap["status"]["indexing"] = {"done": 412, "total": 12512}
    try:
        h.win.update_snapshot(snap)
        h.win.backend.pageRequested.emit("sessions")
        pump(60)
        texts = visible_texts(h.win, "SessionsPage")
        assert find(h.win, "SessionsPage")[0].property("subtitle").startswith("Counting sessions in the last 7 days")
        assert any(t.startswith("Still reading your transcripts (412 of 12,512).") for t in texts), texts
        assert not [t for t in texts if t.startswith(("Showing ", "No sessions"))], texts
    finally:
        h.win.update_snapshot(h.snap)
        h.win.backend.pageRequested.emit("overview")
        pump(60)


def test_kpis_read_plainly_at_the_edges(h):
    # no use in the prior stretch, a many-fold jump, and a cache hit rate with nothing to rate
    snap = copy.deepcopy(h.snap)
    k = {x["key"]: x for x in snap["kpis"]}
    assert k["tokens"]["note"].startswith("vs prior ")
    k["tokens"].update(prev=0, delta=None)
    k["sessions"].update(value=87, prev=1, delta=86.0)
    k["cache_hit"].update(value=None, delta=None)
    try:
        h.win.update_snapshot(snap)
        h.win.backend.pageRequested.emit("overview")
        pump(400)  # past the count-up
        cards = {js(c.property("k"))["key"]: c for c in find(h.win, "KpiCard") if c.property("k")}
        assert cards["tokens"].property("note") == k["tokens"]["note"].replace("vs prior", "none in the prior")
        d = js(cards["sessions"].property("delta"))
        assert (d["text"], d["spoken"]) == ("↑87x", "up 87 times"), d
        assert "n/a" in [t.property("text") for t in find_in(cards["cache_hit"], "Txt") if t.isVisible()]
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)


def test_header_limits_say_what_they_leave_out(h):
    snap = copy.deepcopy(h.snap)
    snap["limits"].update(source="headers", scoped=[], surfaces=[], extra_usage=None)
    try:
        h.win.update_snapshot(snap)
        h.win.backend.pageRequested.emit("overview")
        pump(60)
        assert [t for t in visible_texts(h.win) if t.startswith("Read from response headers, so Fable")]
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)
    assert not [t for t in visible_texts(h.win) if t.startswith("Read from response headers")]


def test_the_cost_note_folds_behind_about_this_data(h):
    h.win.backend.pageRequested.emit("overview")
    pump(60)
    page = find(h.win, "OverviewPage")[0]
    foot = [c for c in find_in(page) if c.property("cut") is not None][0]
    texts = visible_texts(h.win)
    assert [t for t in texts if t.startswith("Cost is API-equivalent") and t.endswith("It is not a bill.")], texts
    assert "About this data" in texts and not [t for t in texts if "cleanup period" in t]
    foot.setProperty("open", True)
    pump(30)
    try:
        assert [t for t in visible_texts(h.win) if "cleanup period" in t]
    finally:
        foot.setProperty("open", False)


def test_a_5_hour_limit_close_to_binding_leads_the_plan_limits(h):
    try:
        texts = show_state(h, "5h_critical")
        assert any(t.startswith("5-hour 93%, resets ") for t in texts), texts
        rows = js(find(h.win, "OtherLimitsCard")[0].property("rows"))
        assert rows[0]["sub"].startswith("At this pace you reach 100% at "), rows[0]
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_a_5_hour_limit_that_runs_out_before_its_reset_says_when(h):
    # as the menu and tray do: the time to the limit, not the reset, when the limit comes first
    from datetime import datetime, timedelta
    now = datetime.now().astimezone()
    # 45 s over the minute: the pill floors, as the menu does (rounding said 2h 11m)
    eta, reset = (now + timedelta(hours=2, minutes=10, seconds=45)).isoformat(), (now + timedelta(hours=4)).isoformat()
    try:
        snap = copy.deepcopy(h.snap)
        STATES["5h_critical"](snap)
        snap["limits"]["session"]["resets_at"] = reset
        snap["now"]["session"].update(resets_at=reset, eta=eta)
        h.win.update_snapshot(snap)
        h.win.backend.pageRequested.emit("overview")
        pump(60)
        texts = visible_texts(h.win)
        assert "5-hour 93%, limit in ~2h 10m" in texts, texts  # the menu's words
        snap = copy.deepcopy(snap)
        snap["now"]["session"]["eta"] = (now + timedelta(hours=3, seconds=30)).isoformat()
        h.win.update_snapshot(snap)
        pump(60)
        assert "5-hour 93%, limit in ~3h" in visible_texts(h.win)  # no "0m", as in the menu
        soon = (now + timedelta(seconds=40)).isoformat()
        snap = copy.deepcopy(snap)
        snap["now"]["session"]["eta"] = soon
        h.win.update_snapshot(snap)
        pump(60)
        assert "5-hour 93%, limit any minute" in visible_texts(h.win)
        snap = copy.deepcopy(snap)
        snap["limits"]["session"]["level"] = snap["now"]["session"]["level"] = "limited"
        h.win.update_snapshot(snap)
        pump(60)
        assert any(t.startswith("5-hour limit reached, resets ") for t in visible_texts(h.win))
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


@pytest.mark.parametrize("width", [960, 1024])
def test_the_usage_scope_line_is_whole_at_narrow_widths(h, width):
    # it once held 200 px back for a pill that is not there, cutting what the deltas compare against
    h.win.backend.pageRequested.emit("overview")
    h.win.window.resize(width, 820)
    pump(120)
    try:
        brow = [e for e in find(h.win, "Eyebrow") if e.property("title") == "USAGE"][0]
        sub = [t for t in find_in(brow, "TipTxt") if "Changes compare" in t.property("text")][0]
        assert sub.width() >= sub.implicitWidth(), (sub.width(), sub.implicitWidth())
    finally:
        h.win.window.resize(1280, 820)
        pump(60)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_every_heatmap_level_stands_out_from_the_card(h, dark):
    from claude_usage import tokens

    def lum(c):
        c = c.lstrip("#")
        ch = [int(c[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        ch = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

    h.win.set_dark(dark)
    pump(30)
    try:
        ramp = js(find(h.win, "ActivityCard")[0].property("ramp"))
        card = tokens.palette(dark)["surface"]
        ratios = [(max(lum(c), lum(card)) + 0.05) / (min(lum(c), lum(card)) + 0.05) for c in ramp]
        assert min(ratios) >= 3.0 and ratios == sorted(ratios), list(zip(ramp, ratios))  # WCAG 1.4.11, in ramp order
        steps = [(max(lum(a), lum(b)) + 0.05) / (min(lum(a), lum(b)) + 0.05) for a, b in zip(ramp, ramp[1:])]
        assert dark or min(steps) >= 1.4, steps  # light levels apart in lightness too (dark differs by hue)
    finally:
        h.win.set_dark(False)
        pump(30)


def test_first_week_has_no_empty_past_weeks_chart(h):
    try:
        texts = show_state(h, "first_week")
        assert "Weeks appear here once a weekly window has ended." in texts
        card = find(h.win, "PastWeeksCard")[0]
        assert not [c for c in find_in(card) if c.property("ticks") is not None and c.isVisible()]
    finally:
        h.win.update_snapshot(h.snap)
        pump(30)


def test_the_first_index_counts_no_sessions_yet_and_activity_says_its_scope(h):
    snap = copy.deepcopy(h.snap)
    STATES["empty"](snap)
    snap["status"]["indexing"] = {"done": 412, "total": 12512}
    h.win.update_snapshot(snap)
    pump(60)
    try:
        assert find(h.win, "OverviewPage")[0].property("subtitle").startswith("Counting sessions in the last 7 days")
    finally:
        h.win.update_snapshot(h.snap)
        pump(60)
    assert "In the last 7 days" in visible_texts(h.win)  # above the facts that follow the range


def test_kpi_changes_are_spoken_as_words(h):
    deltas = [js(k.property("delta")) for k in find(h.win, "KpiCard") if k.property("delta") is not None]
    deltas = [d for d in deltas if d]
    assert deltas and all(d["spoken"].startswith(("up ", "down ")) and "↑" not in d["spoken"] and "↓" not in d["spoken"]
                          for d in deltas if d["text"][0] in "↑↓"), deltas


def test_messages_read_as_sentences(h):
    got = qml_eval(h, '[Ui.sentence("The usage API sent no limits"), Ui.sentence("Rate limited."), Ui.sentence("")]')
    assert got == ["The usage API sent no limits.", "Rate limited.", ""]


def test_settings_show_the_stored_values(h):
    # an older config: thresholds under the old slider minimums, opacity under the overlay's floor
    h.win.set_settings({"appearance": "dark", "notify_thresholds": [0.4, 0.6], "osd_opacity": 0.5})
    h.win.backend.pageRequested.emit("settings")
    pump(80)
    try:
        page = find(h.win, "SettingsPage")[0]
        values = [round(s.property("value"), 2) for s in find_in(page, "SliderX")]
        froms = [round(s.property("from"), 2) for s in find_in(page, "SliderX")]
        texts = visible_texts(h.win, "SettingsPage")
        assert "40%" in texts and "60%" in texts and "85%" in texts, texts
        assert 0.4 in values and 0.6 in values and 0.85 in values, values
        assert 0.85 in froms and min(froms) <= 0.1, froms
    finally:
        h.win.set_settings({"appearance": "light"})
        h.win.backend.pageRequested.emit("overview")
        pump(60)


def test_hero_numbers_keep_tabular_figures(h):
    from PySide6.QtGui import QFont
    h.win.backend.pageRequested.emit("overview")
    pump(30)
    heroes = [t for c in ("KpiCard", "TimelineCard", "WeekCard") for k in find(h.win, c) for t in find_in(k, "Txt")
              if t.property("display")]
    assert heroes and all(t.property("font").featureValue(QFont.Tag("tnum")) == 1 for t in heroes)


def test_quitting_leaves_no_binding_errors():
    from PySide6.QtCore import QObject, qInstallMessageHandler
    seen = []
    old = qInstallMessageHandler(lambda kind, ctx, msg: seen.append(msg))
    try:
        holder = QObject()
        win = AppWindow(lambda r, p: None, settings={"appearance": "light"}, parent=holder)
        win.update_snapshot(load(SNAPS[0]))
        win.show()
        pump(100)
        win.prepare_quit()
        win.window.close()
        del win
        import shiboken6
        shiboken6.delete(holder)
        pump(30)
    finally:
        qInstallMessageHandler(old)
    assert not [m for m in seen if "of null" in m or "TypeError" in m], seen


def test_series_palette_is_distinct_and_readable():
    from claude_usage import tokens

    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, b = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    def contrast(a, b):
        x, y = sorted((lum(a), lum(b)))
        return (y + 0.05) / (x + 0.05)

    for series, pal in ((tokens.SERIES_LIGHT, tokens.LIGHT), (tokens.SERIES_DARK, tokens.DARK)):
        assert len(series) == len(set(series)) == 8
        assert pal["bad_fill"] not in series and pal["warn_fill"] not in series
        assert all(contrast(c, pal["surface"]) >= 3 for c in series), [(c, round(contrast(c, pal["surface"]), 2)) for c in series]
    assert contrast(tokens.OTHER_LIGHT, tokens.LIGHT["surface"]) >= 3


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
    env["QT_ENABLE_HIGHDPI_SCALING"] = "0"  # one image pixel per item pixel on every backend
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


# The real GPU path: the timeline drawn through QQuickRenderControl on
# Direct3D 11 (never shown on screen) must match the software scene graph
# across the whole chart, apart from antialiased edges. The curve renderer
# once dropped whole bands of real data on the GPU while the software render
# was complete.
TIMELINE_CHILD = r"""
import json, sys, time
from pathlib import Path
src, api, snap_path, cases_path, out = sys.argv[1:6]
sys.path.insert(0, src)
from PySide6.QtCore import QPointF, QSize, QUrl
from PySide6.QtGui import (QGuiApplication, QImage, QRhiColorAttachment, QRhiReadbackDescription,
                           QRhiReadbackResult, QRhiRenderBuffer, QRhiTexture, QRhiTextureRenderTargetDescription)
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickRenderControl, QQuickRenderTarget, QQuickWindow, QSGRendererInterface
from PySide6.QtQuickControls2 import QQuickStyle
app = QGuiApplication([])
from claude_usage.app.window import QML_MAIN, Backend
gpu = api == "d3d11"
QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Direct3D11 if gpu else QSGRendererInterface.GraphicsApi.Software)
QQuickStyle.setStyle("FluentWinUI3")
W, H = 1280, 1300
text = Path(snap_path).read_text(encoding="utf-8")
cases = json.loads(Path(cases_path).read_text(encoding="utf-8"))
be = Backend(lambda r, p: None, {})
be.update_snapshot(json.loads(text))
rc = QQuickRenderControl() if gpu else None
win = QQuickWindow(rc) if gpu else QQuickWindow()
eng = QQmlEngine(); eng.rootContext().setContextProperty("backend", be)
comp = QQmlComponent(eng)
comp.setData(b'import QtQuick\nItem { property var snap: JSON.parse(backend.snapshotJson)\n'
             b'Connections { target: backend; function onSnapshotChanged() { snap = JSON.parse(backend.snapshotJson) } }\n'
             b'Rectangle { anchors.fill: parent; color: Ui.p.bg }\n'
             b'OverviewPage { anchors.fill: parent; snap: parent.snap; viewH: 820 } }',
             QUrl.fromLocalFile(str(QML_MAIN.with_name("GpuHost.qml"))))
root = comp.create()
if root is None:
    print(json.dumps({"error": [e.toString() for e in comp.errors()]})); sys.exit(1)
root.setParentItem(win.contentItem()); root.setWidth(W); root.setHeight(H); win.setGeometry(0, 0, W, H)
if gpu:
    if not rc.initialize():
        sys.exit(77)
    rhi = rc.rhi()
    tex = rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(W, H), 1, QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource); tex.create()
    ds = rhi.newRenderBuffer(QRhiRenderBuffer.Type.DepthStencil, QSize(W, H), 1); ds.create()
    desc = QRhiTextureRenderTargetDescription(QRhiColorAttachment(tex)); desc.setDepthStencilBuffer(ds)
    rt = rhi.newTextureRenderTarget(desc); rp = rt.newCompatibleRenderPassDescriptor(); rt.setRenderPassDescriptor(rp); rt.create()
    win.setRenderTarget(QQuickRenderTarget.fromRhiRenderTarget(rt))
def frame():
    for _ in range(5):  # past the 300 ms entrance and count-up animations
        end = time.perf_counter() + 0.1
        while time.perf_counter() < end: app.processEvents(); time.sleep(0.005)
        if gpu:
            rc.polishItems(); rc.beginFrame(); rc.sync(); rc.render()
            res = QRhiReadbackResult(); b = rhi.nextResourceUpdateBatch(); b.readBackTexture(QRhiReadbackDescription(tex), res)
            rc.commandBuffer().resourceUpdate(b); rc.endFrame()
    if gpu:
        return QImage(bytes(res.data), res.pixelSize.width(), res.pixelSize.height(), QImage.Format.Format_RGBA8888).copy()
    return win.grabWindow()  # a hidden window renders offscreen
def find(pred):
    st = [root]
    while st:
        it = st.pop()
        if pred(it): return it
        st.extend(it.childItems())
card = find(lambda it: it.metaObject().className().startswith("TimelineCard"))
chart = find(lambda it: it.property("bands") is not None and it.property("xL") is not None)
js = lambda v: v.toVariant() if hasattr(v, "toVariant") else v
meta = {}
for name, c in cases.items():
    snap = json.loads(text)
    if c.get("rows") is not None:
        snap["timeline"]["by"][c["group"]]["values"][c["metric"]] = c["rows"]
    be.set_dark(c["dark"]); be.update_snapshot(snap)
    card.setProperty("metric", c["metric"]); card.setProperty("group", c["group"])
    img = frame()
    o = chart.mapToScene(QPointF(0, 0))
    png = str(Path(out) / (name + "_" + api + ".png"))
    img.copy(int(o.x()), int(o.y()), int(chart.width()), int(chart.height())).save(png)
    meta[name] = {"png": png, "xL": chart.property("xL"), "n": card.property("n"),
                  "labels": [[d["x"], d["y"], d["x"] + d["w"], d["y"] + 22] for d in js(chart.property("direct"))]}
print(json.dumps(meta))
"""


def ink_rows(path, ref_x):
    """Per row, a bitmask of the pixels that differ from the card behind the plot (column *ref_x*)."""
    from PySide6.QtGui import QImage, QPainter
    img = QImage(path).convertToFormat(QImage.Format.Format_RGB32)
    w, h = img.width(), img.height()
    diff = img.copy(ref_x, 0, 1, h).scaled(w, h)
    p = QPainter(diff)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Difference)
    p.drawImage(0, 0, img)
    p.end()
    g = diff.convertToFormat(QImage.Format.Format_Grayscale8)
    bpl, data = g.bytesPerLine(), bytes(g.constBits())
    table = bytes(48 + (v > 12) for v in range(256))  # "0" or "1"
    return [int(data[y * bpl:y * bpl + w].translate(table)[::-1], 2) for y in range(h)], w


def missing_pixels(sw, gpu, ref_x, boxes):
    """(holes, spikes): ink only in the software render, and ink only on the
    GPU, away from antialiased edges and the direct labels."""
    a, w = ink_rows(sw, ref_x)
    b, _ = ink_rows(gpu, ref_x)
    full = (1 << w) - 1

    def grow(rows):  # 2 px each way
        hz = [(m | m << 1 | m >> 1 | m << 2 | m >> 2) & full for m in rows]
        return [hz[max(0, y - 2)] | hz[max(0, y - 1)] | hz[y] | hz[min(len(hz) - 1, y + 1)] | hz[min(len(hz) - 1, y + 2)]
                for y in range(len(hz))]

    skip = [p & q for p, q in zip(grow(a), grow([~m & full for m in a]))]
    for x0, y0, x1, y1 in boxes:
        cols = ((1 << int(x1) + 4) - 1) ^ ((1 << max(0, int(x0) - 3)) - 1)
        for y in range(max(0, int(y0) - 3), min(len(a), int(y1) + 4)):
            skip[y] |= cols

    def tall(m):  # a 1.5 px rim can land a row apart; a missing band is taller
        return sum((m[y] & m[y - 1] & m[y + 1]).bit_count() for y in range(1, len(m) - 1))
    return (tall([a[y] & ~b[y] & ~skip[y] & full for y in range(len(a))]),
            tall([b[y] & ~a[y] & ~skip[y] & full for y in range(len(a))]))


def test_the_gpu_comparison_sees_a_missing_band(tmp_path):
    # the check itself: a band painted over with the card colour must count
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(400, 200, QImage.Format.Format_RGB32)
    img.fill(QColor("#1A1A19"))
    p = QPainter(img)
    p.fillRect(60, 120, 300, 60, QColor("#466EDD"))
    p.fillRect(60, 80, 300, 40, QColor("#E27100"))
    p.end()
    img.save(str(tmp_path / "a.png"))
    p = QPainter(img)
    p.fillRect(100, 85, 200, 30, QColor("#1A1A19"))
    p.end()
    img.save(str(tmp_path / "b.png"))
    assert missing_pixels(str(tmp_path / "a.png"), str(tmp_path / "a.png"), 40, []) == (0, 0)
    holes, spikes = missing_pixels(str(tmp_path / "a.png"), str(tmp_path / "b.png"), 40, [])
    assert holes > 4000 and spikes == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Direct3D 11 is Windows only")
def test_timeline_on_the_gpu_matches_the_software_render(tmp_path):
    import random
    snap = load(SNAPS[0])
    n, k = len(snap["timeline"]["starts"]), len(snap["timeline"]["by"]["project"]["keys"])
    data = {
        # Spikes that alternate between series: band edges interpolated separately
        # cross between buckets, and the curve renderer filled them as shards.
        "spikes": [[40.0 if (j + i) % 3 == 0 else 0.0 for j in range(n)] for i in range(k)],
    }
    for seed in range(4):
        # Like real use: one large project, the rest a few cents in most buckets
        # with the odd burst. Sub-pixel slivers made whole bands drop out.
        rnd = random.Random(seed)
        rows = [[rnd.uniform(50, 400) for _ in range(n)]]
        rows += [[rnd.uniform(5, 150) if rnd.random() < 0.15 else rnd.uniform(0.02, 1.5) if rnd.random() < 0.8 else 0.0
                  for _ in range(n)] for _ in range(k - 1)]
        rnd.shuffle(rows)
        data[f"slivers{seed}"] = rows
    cases = {f"{name}_{mode}": {"dark": mode == "dark", "metric": "cost", "group": "project", "rows": rows}
             for name, rows in data.items() for mode in ("dark", "light")}
    spec = tmp_path / "cases.json"
    spec.write_text(json.dumps(cases), encoding="utf-8")
    soft, hard = (run_child(TIMELINE_CHILD, api, str(SNAPS[0]), str(spec), str(tmp_path)) for api in ("software", "d3d11"))
    found = {}
    for name in cases:
        s, g = soft[name], hard[name]
        assert s["n"] == n and Path(s["png"]).exists() and Path(g["png"]).exists()
        found[name] = missing_pixels(s["png"], g["png"], int(s["xL"]) - 6, s["labels"] + g["labels"])
    assert {k: v for k, v in found.items() if v != (0, 0)} == {}, found


def test_kpi_and_heatmap_readouts_work_from_the_keyboard(h):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    h.win.backend.pageRequested.emit("overview")
    pump(30)

    def tips():
        return [t for t in find(h.win, "ChartTip") if t.isVisible()]

    kpi = [k for k in find(h.win, "KpiCard") if k.property("activeFocusOnTab")][0]
    kpi.forceActiveFocus(Qt.TabFocusReason)
    pump(30)
    assert len(tips()) == 1 and tips()[0].property("title") == js(kpi.property("k"))["label"]
    grid = [g for g in find_in(find(h.win, "ActivityCard")[0]) if g.property("cells") is not None][0]
    grid.forceActiveFocus(Qt.TabFocusReason)
    QTest.keyClick(h.win.window, Qt.Key_Left)
    pump(30)
    assert grid.property("kb") == len(js(grid.property("cells"))) - 1 and len(tips()) == 1
    find(h.win, "OverviewPage")[0].forceActiveFocus()
    pump(30)
    assert tips() == []
