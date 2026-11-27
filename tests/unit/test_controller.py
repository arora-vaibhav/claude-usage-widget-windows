"""The controller wiring: the worker's results reach the OSD and the app, settings are
saved once through the one writer, alerts fire once per crossing, and a second
launch reaches the running instance."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "fixtures"))
sys.path.insert(0, HERE)

import make_store  # noqa: E402
from snapshot_contract import check  # noqa: E402

from claude_usage import autostart, snapshot, store, widget  # noqa: E402
from claude_usage.config import DEFAULT_CONFIG  # noqa: E402

_app = QApplication.instance() or QApplication([])


def pump_until(cond, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        _app.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def fake_fetch(_claude_dir):
    t = int(time.time())
    return {"weekly": {"pct": 0.42, "resets_at": t + 3 * 86400, "severity": "normal"}, "five_hour": None,
            "fetched_at": t, "source": "oauth"}


def now_dict(weekly: float, state: str = "ok") -> dict:
    reset = (datetime.now().astimezone() + timedelta(days=2)).isoformat()
    meter = {"pct": weekly, "resets_at": reset, "level": "normal", "forecast": None, "eta": None}
    return {"primary": "weekly", "weekly": meter, "session": None, "today_cost": 1.5, "today_tokens": 10,
            "tokens_per_min": 0.0, "active_sessions": 0, "active_subagents": 0, "live": False, "state": state,
            "as_of": None, "message": None}


@pytest.fixture
def make(tmp_path, monkeypatch):
    """A controller on a temp store and config; the caller decides whether to start it."""
    class _App:  # quit() would end the test session's event loop
        instance = staticmethod(QApplication.instance)
        quit = staticmethod(lambda: None)

    monkeypatch.setattr(widget, "QApplication", _App)
    monkeypatch.setattr(widget, "SERVER_NAME", f"claude-usage-test-{os.getpid()}-{tmp_path.name}")
    made = []

    def _make(**over):
        cfg = dict(DEFAULT_CONFIG, claude_dir=str(tmp_path / "claude"), refresh_seconds=5, **over)
        ctl = widget.Controller(cfg, store_path=str(tmp_path / "usage.db"), config_path=str(tmp_path / "config.json"),
                                fetch=fake_fetch)
        made.append(ctl)
        return ctl

    yield _make
    for ctl in made:
        ctl.quit()
        ctl.osd.hide()
        if ctl.app is not None and ctl.app.window is not None:
            ctl.app.window.close()
    QGuiApplication.styleHints().setColorScheme(widget.Qt.ColorScheme.Unknown)
    _app.setStyleSheet("")


def test_saved_position_accepts_taskbar_band():
    # Uses full screen geometry: an OSD parked over the taskbar is a valid spot.
    geo = QGuiApplication.primaryScreen().geometry()
    assert widget._rect_on_some_screen(geo.center().x(), geo.center().y(), 260, 100)
    assert widget._rect_on_some_screen(geo.x() + 36, geo.bottom() - 20, 260, 6)
    assert not widget._rect_on_some_screen(geo.right() + 10_000, geo.bottom() + 10_000, 260, 100)
    assert not widget._rect_on_some_screen(-20_000, -20_000, 260, 100)


def test_worker_result_reaches_the_osd_and_the_app(make, tmp_path):
    claude = tmp_path / "claude"
    make_store.write_transcripts(str(claude / "projects"))
    make_store.write_legacy(str(claude))
    ctl = make()
    progress = []
    ctl._refreshed.connect(lambda now, shown, indexing: progress.append(indexing))
    ctl.start(check_updates=False)
    assert pump_until(lambda: "Weekly 42%" in ctl.osd.toolTip()), ctl.osd.toolTip()

    ctl.open_app()
    backend = ctl.app.backend
    assert pump_until(lambda: backend.snapshotJson != "null")
    snap = json.loads(backend.snapshotJson)
    check(snap)
    assert snap["range"]["key"] == "7d" and snap["now"]["weekly"]["pct"] == 0.42

    backend.setRange("30d")
    assert pump_until(lambda: json.loads(backend.snapshotJson)["range"]["key"] == "30d" and not backend.busy)
    assert ctl.app.warnings == []

    assert pump_until(lambda: None in progress, timeout=90)  # the first index has finished (slow under load)
    ctl.quit()
    assert not ctl._worker.is_alive()
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["app_range"] == "30d" and len(saved["app_geometry"]) == 4
    with sqlite3.connect(tmp_path / "usage.db") as db:
        assert db.execute("SELECT weekly FROM samples ORDER BY ts DESC LIMIT 1").fetchone() == (0.42,)
        assert db.execute("SELECT COUNT(*) FROM files").fetchone()[0] > 100


def test_settings_apply_live_and_persist_once(make, tmp_path, monkeypatch):
    writes = []
    monkeypatch.setattr(widget, "save_config", lambda path, cfg: writes.append(dict(cfg)))
    monkeypatch.setattr(autostart, "set_enabled", lambda on: True)
    ctl = make()
    ctl._on_setting("osd_style", "pill")  # the OSD echoes style_toggled back; still one change
    for v in (0.9, 0.95, 1.0, 0.85):  # a slider drag
        ctl._on_setting("osd_opacity", v)
    ctl._on_setting("autostart", True)  # lives in the Startup folder, not the config
    assert ctl.osd.osd_style() == "pill"
    assert pump_until(lambda: writes, timeout=3)
    pump_until(lambda: False, timeout=0.6)
    assert len(writes) == 1
    assert writes[0]["osd_style"] == "pill" and writes[0]["osd_opacity"] == 0.85 and "autostart" not in writes[0]

    ctl.osd.toggle_style()  # the menu's Pill/Card item
    assert pump_until(lambda: len(writes) == 2, timeout=3)
    assert writes[1]["osd_style"] == "card"


def test_bad_values_from_the_settings_are_ignored(make, monkeypatch):
    writes = []
    monkeypatch.setattr(widget, "save_config", lambda path, cfg: writes.append(dict(cfg)))
    ctl = make()
    before = dict(ctl.config)
    for key, bad in (("notify_thresholds", [0.5]), ("notify_thresholds", None), ("notify_thresholds", ["a", "b"]),
                     ("osd_opacity", "half"), ("refresh_seconds", None)):
        ctl._on_setting(key, bad)  # each would fail every refresh if it were stored
    assert ctl.config == before and ctl._thresholds() == (0.75, 0.90)
    pump_until(lambda: False, timeout=0.6)
    assert writes == []


def test_a_failed_save_is_retried_and_saved_at_quit(make, monkeypatch):
    writes, fails = [], [1]

    def flaky_save(path, cfg):
        if fails[0]:
            fails[0] -= 1
            raise PermissionError(13, "locked")
        writes.append(dict(cfg))

    monkeypatch.setattr(widget, "save_config", flaky_save)
    ctl = make()
    ctl._on_setting("osd_style", "pill")
    assert pump_until(lambda: not fails[0], timeout=3)
    assert writes == [] and ctl._save_timer.isActive()  # the retry is pending
    ctl.quit()
    assert len(writes) == 1 and writes[0]["osd_style"] == "pill"


def plan_limits(weekly: float, fable: float | None = None, five: float | None = None) -> dict:
    """A fetch_rate_limits() result shaped like the live Ultra payload: five_hour null unless
    *five* is given (Pro and Max plans), and a scoped Fable limit, active only when *fable* is."""
    t = int(time.time())
    week = {"pct": weekly, "resets_at": t + 3 * 86400, "severity": "normal"}
    return {"weekly": week, "fetched_at": t, "source": "oauth",
            "five_hour": None if five is None else {"pct": five, "resets_at": t + 2 * 3600, "severity": "normal"},
            "limits": [dict(week, kind="weekly_all", name=None, active=True),
                       dict(week, kind="weekly_scoped", name="Fable", pct=fable or 0.0, active=fable is not None)]}


def test_menu_and_tray_show_only_the_limits_the_plan_reports(make, tmp_path):
    ctl = make()
    conn = store.connect(str(tmp_path / "s.db"))

    def show(lim):
        now = snapshot.now_state(conn, lim, time.time())
        ctl._on_refreshed(now, lim, None)
        ctl._on_menu_show()
        return ctl._act_head.text().split("  ·  "), widget._tray_tip(now, lim).splitlines()

    head, tip = show(plan_limits(0.67))  # Ultra: weekly only, Fable not in use; on pace to hit it before the reset
    assert len(head) == 1 and head[0].startswith("Weekly 67%, limit in ~1d ") and not ctl._act_head.isEnabled()
    assert tip == ["Claude Usage", head[0]]
    head, tip = show(plan_limits(0.67, fable=0.12))
    assert head[1:] == ["Fable 12%"] and tip[1:] == head
    head, tip = show(plan_limits(0.67, five=0.2))  # the 5-hour limit is back: shown again, after weekly
    assert head[0].startswith("Weekly 67%, ") and head[1].startswith("5-hour 20%, resets in ") and tip[1:] == head
    for line in head + tip:
        assert "Session" not in line and " 0%" not in line
    head, tip = show(plan_limits(0.5, five=0.96))  # the binding 5-hour limit leads, as on the OSD
    assert head[0].startswith("5-hour 96%, limit in ~") and head[1].startswith("Weekly 50%, resets ")
    head, tip = show(dict(plan_limits(0.67), error="Rate limited"))  # stale: the tooltip says why
    assert head[0].startswith("Weekly 67%, ") and head[1].startswith("as of ") and "Rate limited" not in head
    assert tip[1:] == head + ["Rate limited"]
    head, tip = show({"error": "No credentials found. Run claude to sign in."})  # signed out: why, and what to do
    assert head == ["Signed out"] and tip == ["Claude Usage", "Signed out", "No credentials found. Run claude to sign in."]


def test_the_tray_tip_keeps_whole_lines_within_its_limit(make, tmp_path):
    conn = store.connect(str(tmp_path / "s.db"))
    why = "Can't reach the usage API. Check your network."
    ix = {"done": 2300, "total": 12571}
    for five in (None, 0.2):  # Ultra during an outage while Fable is in use, then with a 5-hour limit too
        lim = dict(plan_limits(0.67, five=five, fable=0.2), error=why)
        now = dict(snapshot.now_state(conn, lim, time.time()), live=True, tokens_per_min=12300.0)
        tip = widget._tray_tip(now, lim, ix)
        full = ["Claude Usage", *widget._summary(now, lim, ix)]
        lines = tip.splitlines()
        r = next(i for i, line in enumerate(lines) if line.startswith("Can't reach"))
        assert len(tip) <= 127 and set(lines[:r] + lines[r + 1:]) <= set(full), tip  # whole lines but the reason
        # the indexing progress and the live rate give way to the reason, which is cut short only when it must be
        assert not [x for x in lines[:r] if x.startswith(("Indexing", "Live"))] and why.startswith(lines[r].rstrip(".")), tip
        assert five or lines[r] == why, tip
    assert "Live" not in tip  # the 5-hour plan's tip has no room left for it
    assert widget._tray_tip(dict(now, state="ok", message=None), lim).endswith("Live 12.3k tok/min")


def test_the_first_index_says_today_is_partial(make, tmp_path):
    ctl = make()
    conn = store.connect(str(tmp_path / "s.db"))
    lim = plan_limits(0.67)
    now = snapshot.now_state(conn, lim, time.time())
    ctl._on_refreshed(now, lim, {"done": 2371, "total": 12571})
    ctl._on_menu_show()
    assert ctl._act_head.text().endswith("Indexing 18%")
    assert "Indexing 18%" in widget._tray_tip(now, lim, {"done": 2371, "total": 12571})
    # floored like the app's progress card, and no "0%" that reads as stuck
    assert [widget._indexing({"done": d, "total": 12582}) for d in (105, 12581)] == ["Indexing", "Indexing 99%"]
    assert "so far, indexing 2,371 of 12,571 transcripts" in ctl.osd.toolTip()
    ctl._on_refreshed(now, lim, None)  # done: today's cost is final again
    assert "Today $0.00 (API-equivalent)" in ctl.osd.toolTip()


def test_a_failed_legacy_import_still_opens_the_store(make, monkeypatch):
    def boom(conn, claude_dir):
        raise ValueError("invalid literal for int(): 'lots'")  # a hand-edited usage-daily.jsonl row

    monkeypatch.setattr(widget.store, "import_legacy", boom)
    ctl = make()
    ctl.start(check_updates=False)
    assert pump_until(lambda: "Weekly 42%" in ctl.osd.toolTip(), timeout=10)


def test_a_store_from_a_newer_version_is_left_alone(make, tmp_path):
    db = tmp_path / "usage.db"
    with sqlite3.connect(db) as c:
        c.execute("PRAGMA user_version=99")
    ctl = make()
    notices = []
    ctl._notice.connect(lambda title, body: notices.append(body))
    ctl.start(check_updates=False)
    assert pump_until(lambda: notices and not ctl._worker.is_alive(), timeout=10)
    assert len(notices) == 1 and "newer version" in notices[0]
    assert pump_until(lambda: "newer version" in ctl.osd.toolTip(), timeout=5)  # not stuck on "Loading usage"
    ctl._on_menu_show()
    assert "newer version" in ctl._act_head.text() and not ctl._act_refresh.isVisible()
    ctl.open_app()  # the app says why too, built from an empty store, never from the newer one
    assert pump_until(lambda: ctl.app.backend.snapshotJson != "null")
    status = json.loads(ctl.app.backend.snapshotJson)["status"]
    assert status["state"] == "error" and "newer version" in status["message"]
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT name FROM sqlite_master").fetchall() == []  # nothing written into it


def test_alerts_follow_the_limits_the_plan_reports(make, tmp_path):
    ctl = make(notify_thresholds=[0.75, 0.90])
    sent = []
    ctl.notifier._send = lambda title, body: sent.append(title)
    conn = store.connect(str(tmp_path / "s.db"))
    for lim in (plan_limits(0.5, fable=0.5), plan_limits(0.8, fable=0.8), plan_limits(0.8, fable=0.95),
                plan_limits(0.8), plan_limits(0.8, five=0.5), plan_limits(0.8, five=0.8)):
        ctl._on_refreshed(snapshot.now_state(conn, lim, time.time()), lim, None)
    # Ultra: weekly and the active Fable limit alert and nothing reports a 5-hour limit;
    # once the 5-hour limit is reported again its first value is the baseline.
    assert sent == ["Weekly limit at 80%", "Fable limit at 80%", "Fable limit at 95%", "5-hour limit at 80%"]


def test_alert_fires_once_per_crossing(make):
    ctl = make(notify_thresholds=[0.75, 0.90])
    sent = []
    ctl.notifier._send = lambda title, body: sent.append(title)
    # 0.74, 0.76: jitter across 75% is not a new crossing; 0.05: the window reset
    for pct in (0.70, 0.76, 0.74, 0.76, 0.80, 0.80, 0.92, 0.95, 0.05, 0.78):
        ctl._on_refreshed(now_dict(pct), None, None)
    assert sent == ["Weekly limit at 76%", "Weekly limit at 92%", "Weekly limit at 78%"]

    ctl._on_setting("notify_thresholds", [0.5, 0.8])  # the baseline (78%) survives the change
    ctl._on_refreshed(now_dict(0.82), None, None)
    assert sent[-1] == "Weekly limit at 82%"

    ctl._on_setting("notifications_enabled", False)
    ctl._on_refreshed(now_dict(0.95), None, None)
    assert len(sent) == 4


def test_thresholds_from_qml_are_saved_and_refreshes_go_on(make, tmp_path):
    from PySide6.QtQml import QQmlExpression

    ctl = make()
    ctl.start(check_updates=False)
    assert pump_until(lambda: "Weekly 42%" in ctl.osd.toolTip())
    ctl.open_app("settings")
    # The real QML call: a JS array reaches Python as a QJSValue.
    QQmlExpression(ctl.app.engine.rootContext(), None,
                   "backend.setSetting('notify_thresholds', [0.5, 0.9])").evaluate()
    assert ctl.config["notify_thresholds"] == [0.5, 0.9] and ctl.notifier.detector.thresholds == [0.5, 0.9]
    assert ctl.app.backend.theme["caution"] == 0.5
    cfg_file = tmp_path / "config.json"
    assert pump_until(lambda: cfg_file.exists() and json.loads(cfg_file.read_text()).get("notify_thresholds")
                      == [0.5, 0.9], timeout=3)

    refreshed = []
    ctl._refreshed.connect(lambda *a: refreshed.append(a))
    ctl.refresh_now()
    assert pump_until(lambda: refreshed, timeout=10)
    assert ctl.app.warnings == []


def test_refresh_now_is_throttled_and_a_threshold_change_recolours_at_once(make):
    ctl = make()
    fetches, refreshed = [], []
    ctl._fetch = lambda d: fetches.append(d) or fake_fetch(d)
    ctl._refreshed.connect(lambda now, shown, indexing: refreshed.append(now))
    ctl.start(check_updates=False)
    assert pump_until(lambda: refreshed)
    for _ in range(3):  # clicked again and again: "now" is worked out again, the endpoint is asked once
        n = len(refreshed)
        ctl.refresh_now()
        assert pump_until(lambda: len(refreshed) > n, timeout=10)
    n = len(refreshed)
    ctl._on_setting("notify_thresholds", [0.4, 0.9])  # 42% is past the new caution level
    assert pump_until(lambda: len(refreshed) > n, timeout=10)
    assert refreshed[-1]["weekly"]["level"] == "caution" and len(fetches) == 1


def test_worker_survives_a_failed_store_open_and_a_failed_refresh(make, monkeypatch):
    monkeypatch.setattr(widget, "BACKOFF_S", (0.05, 0.05, 0.05))
    real_connect, real_now = widget.store.connect, widget.snapshot.now_state
    calls = {"connect": 0, "now": 0}

    def flaky_connect(*a, **k):
        calls["connect"] += 1
        if calls["connect"] == 1:
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(*a, **k)

    def flaky_now(*a, **k):
        calls["now"] += 1
        if calls["now"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_now(*a, **k)

    monkeypatch.setattr(widget.store, "connect", flaky_connect)
    monkeypatch.setattr(widget.snapshot, "now_state", flaky_now)
    ctl = make()
    notices = []
    ctl._notice.connect(lambda title, body: notices.append(body))
    ctl.start(check_updates=False)
    assert pump_until(lambda: calls["now"] == 1, timeout=10)
    ctl.refresh_now()  # the thread is still alive and answers
    assert pump_until(lambda: "Weekly 42%" in ctl.osd.toolTip(), timeout=10)
    assert calls["connect"] == 2 and len(notices) == 1 and ctl._worker.is_alive()


def test_a_window_that_fails_to_open_says_so_and_can_try_again(make, monkeypatch):
    import claude_usage.app as app_pkg
    ctl = make()
    notices = []
    monkeypatch.setattr(ctl, "_balloon", lambda title, body: notices.append(body))
    real = app_pkg.AppWindow

    def broken(*_a, **_k):
        raise RuntimeError("app window failed to load: no QtQuick")

    monkeypatch.setattr(app_pkg, "AppWindow", broken)
    ctl.open_app()  # from a click: no exception escapes
    assert ctl.app is None and notices == [widget.OPEN_FAILED]
    monkeypatch.setattr(app_pkg, "AppWindow", real)
    ctl.open_app()
    assert ctl.app is not None and ctl.app.is_visible()


def test_a_first_fetch_that_fails_still_names_the_plan(make, tmp_path):
    # Ultra has no 5-hour limit; without the plan the app said "not reported" instead of saying so.
    ctl = make()
    shown = []
    ctl._fetch = lambda d: {"error": "Can't reach the usage API. Check your network.", "plan": "Ultra"}
    ctl._refreshed.connect(lambda now, lim, ix: shown.append(lim))
    ctl.start(check_updates=False)
    assert pump_until(lambda: shown, timeout=10)
    assert shown[-1]["plan"] == "Ultra"
    snap = snapshot.build(store.connect(str(tmp_path / "s.db")), "7d", None, time.time(), shown[-1])
    assert snap["status"]["plan"] == "Ultra" and snap["limits"]["session_note"] == snapshot.ULTRA_NOTE
    # a fetch that raised has no plan of its own: it comes from the credentials
    (tmp_path / "claude").mkdir(exist_ok=True)
    (tmp_path / "claude" / ".credentials.json").write_text(json.dumps(
        {"claudeAiOauth": {"accessToken": "synthetic", "rateLimitTier": "default_claude_max_20x"}}), encoding="utf-8")
    ctl._fetch = lambda d: 1 / 0
    n = len(shown)
    widget.FORCE_GAP_S, gap = 0, widget.FORCE_GAP_S
    try:
        ctl.refresh_now()
        assert pump_until(lambda: len(shown) > n and "Collection error" in (shown[-1].get("error") or ""), timeout=10)
    finally:
        widget.FORCE_GAP_S = gap
    assert shown[-1]["plan"] == "Ultra"


def test_a_refresh_during_a_build_reaches_the_app(make, monkeypatch):
    import threading
    monkeypatch.setattr(widget.Controller, "_run", lambda self: self._ready.set())  # no worker: refreshes by hand
    gate, builds, real = threading.Event(), [], widget.snapshot.build

    def slow(conn, range_key, project, now, limits, **kw):
        builds.append(limits)
        if len(builds) == 1:
            gate.wait(10)
        return real(conn, range_key, project, now, limits, **kw)

    monkeypatch.setattr(widget.snapshot, "build", slow)
    ctl = make()
    ctl.start(check_updates=False)
    ctl.open_app()
    assert pump_until(lambda: builds, timeout=10)  # the first build is running
    lim = fake_fetch(None)
    ctl._on_refreshed(snapshot.now_state(store.connect(":memory:"), lim, time.time()), lim, None)
    gate.set()
    assert pump_until(lambda: len(builds) == 2 and not ctl._building, timeout=10)
    assert builds == [None, lim]
    assert pump_until(lambda: json.loads(ctl.app.backend.snapshotJson)["limits"]["weekly"] is not None, timeout=10)


def test_reindex_reads_on_its_first_pass(make, tmp_path, monkeypatch):
    # reindex() wakes the worker; that wake once cancelled the re-index pass before it read a file.
    make_store.write_transcripts(str(tmp_path / "claude" / "projects"))
    passes, real = [], widget.ingest.Ingestor.index
    monkeypatch.setattr(widget.ingest.Ingestor, "index", lambda self, *a, **k: passes.append(real(self, *a, **k)) or passes[-1])
    ctl = make()
    fetches = []
    ctl._fetch = lambda d: fetches.append(d) or fake_fetch(d)
    ctl.start(check_updates=False)
    assert pump_until(lambda: passes and not passes[-1]["cancelled"], timeout=90)  # a full first pass; slow under load
    n_fetch = len(fetches)
    passes.clear()
    ctl.reindex()
    assert pump_until(lambda: passes, timeout=10)
    assert passes[0]["read"] > 0
    assert pump_until(lambda: len(fetches) > n_fetch, timeout=10)  # and the limits are fetched again


def test_a_second_launch_opens_the_running_app(make, monkeypatch):
    monkeypatch.setattr(widget.Controller, "_run", lambda self: None)  # no worker needed
    ctl = make()
    opened = []
    monkeypatch.setattr(ctl, "open_app", lambda page=None: opened.append(page))
    assert not widget.ask_running_instance(200)  # nothing listens yet
    ctl.start(check_updates=False)
    assert widget.ask_running_instance()
    assert pump_until(lambda: opened, timeout=5)
    assert opened == [None]


def test_the_overlay_menu_never_opens_over_the_overlay(make, monkeypatch):
    # the overlay is top-most, so a menu drawn over it was hidden under the card (seen on a real screen)
    ctl = make()
    shown = []
    monkeypatch.setattr(ctl.menu, "popup", lambda p: shown.append(QRect(p, ctl.menu.sizeHint())))
    avail = QGuiApplication.primaryScreen().availableGeometry()
    for style in ("card", "pill"):
        ctl.osd.set_style(style)
        for y in (avail.bottom() - ctl.osd.height(), avail.top()):  # parked at the bottom, then at the top
            ctl.osd.move(avail.left() + 20, y)
            ctl._popup_menu(ctl.osd.frameGeometry().center())
            assert not shown[-1].intersects(ctl.osd.frameGeometry()), (style, y, shown[-1], ctl.osd.frameGeometry())
