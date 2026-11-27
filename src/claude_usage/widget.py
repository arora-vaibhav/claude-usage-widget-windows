"""The controller: OSD, tray, the one menu, the app window and the refresh worker.

One worker thread does all collection (transcript ingest, the limits fetch,
utilization samples, the "now" dict), so two refreshes never overlap. A second
thread builds app snapshots, which take up to a second on a large store. Both
report to the GUI thread through Qt signals.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QToolTip

from claude_usage import __version__, autostart, collector, ingest, snapshot, store, tokens, updater
from claude_usage.auth_watchdog import AuthWatchdog, is_auth_error
from claude_usage.config import drop_bad_values, save_config, user_config_path
from claude_usage.logging_setup import get_logger
from claude_usage.notifier import UsageNotifier
from claude_usage.osd import NO_LIMITS, Osd, _clock, _font, _indexing, _parse, _pct, _rate, _resets, _span, _when
from claude_usage.winchrome import apply_win11_chrome

log = get_logger()

SERVER_NAME = "claude-usage-" + (os.environ.get("USERNAME") or os.environ.get("USER") or "user")
BACKOFF_S = (60, 120, 300)  # limits fetch retry after 1, 2, 3+ failures in a row
FORCE_GAP_S = 5  # Refresh now, clicked again and again, still fetches at most this often
NEWER_STORE = "Your usage store is from a newer version of Claude Usage. Update to see your usage."
ON_TOP_MS = 2000
SAVE_DELAY_MS = 400  # one write per burst of changes (a slider drag sends dozens)
SAVE_RETRY_MS = 60_000  # after a failed write; a locked file or a full disk can clear
OPEN_FAILED = "Couldn't open the Claude Usage window. Restart Claude Usage."
ICON = os.path.join(os.path.dirname(__file__), "icons", "claude-usage.ico")


def _rect_on_some_screen(x: int, y: int, w: int, h: int) -> bool:
    """True if a rect at (x, y) of size (w, h) is meaningfully on some screen.

    Uses each screen's full geometry, not availableGeometry: parking the OSD
    over the taskbar is allowed. Only a rect on no screen at all (an unplugged
    monitor) fails.
    """
    rect = QRect(x, y, max(w, 80), max(h, 24))
    for screen in QGuiApplication.screens():
        inter = screen.geometry().intersected(rect)
        if inter.width() >= 24 and inter.height() >= 4:
            return True
    return False


def ask_running_instance(timeout_ms: int = 1000) -> bool:
    """Ask the instance already running to open its app window. False if none answered."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.disconnectFromServer()  # the connection itself is the request
    return True


def _qss(dark: bool) -> str:
    p = tokens.palette(dark)
    # On Windows 11 DWM rounds the popup and draws its border and shadow.
    border = "none" if os.name == "nt" else f"1px solid {p['line_strong']}"
    return f"""
QMenu {{ background: {p['surface']}; color: {p['text']}; border: {border}; padding: 4px; }}
QMenu::item {{ padding: 7px 32px 7px 12px; border-radius: 4px; }}
QMenu::item:selected {{ background: {p['surface_alt']}; }}
QMenu::item:disabled {{ color: {p['text_muted']}; }}
QMenu::separator {{ height: 1px; background: {p['line']}; margin: 4px 8px; }}
QToolTip {{ background: {p['tooltip_bg']}; color: {p['tooltip_text']}; border: 1px solid {p['line_strong']};
            padding: 6px 8px; }}
"""


def _active_scoped(shown: Optional[dict]) -> list[dict]:
    """Scoped weekly limits in use this window (Fable on Ultra), from the limits fetch."""
    return [{"name": x.get("name") or str(x.get("kind") or "Limit").replace("_", " ").capitalize(),
             "pct": float(x["pct"]), "resets_at": x.get("resets_at")}
            for x in (shown or {}).get("limits") or []
            if x.get("kind") != "weekly_all" and x.get("active") and x.get("pct") is not None]


def _summary(now: Optional[dict], shown: Optional[dict], indexing: Optional[dict] = None) -> list[str]:
    """What the menu header and the tray tooltip say: each limit the API reported, the binding
    one first as on the OSD, then the state, indexing progress and the live rate (always last).
    A limit it did not report (the 5-hour limit on Ultra) gets no part, never a 0%."""
    if now is None or now["state"] == "loading":
        return ["Loading usage"]
    if now["state"] == "signed_out":
        return ["Signed out"]
    t, parts = datetime.now().astimezone(), []
    meters = [("Weekly", now["weekly"], True), ("5-hour", now["session"], True)]
    if now["primary"] == "session":
        meters.reverse()
    for name, m, with_reset in meters + [(x["name"], x, False) for x in _active_scoped(shown)]:
        if not m:
            continue
        if _pct(m) == "--":
            parts.append(f"{name} not started")
            continue
        part, reset = f"{name} {_pct(m)}%", with_reset and _parse(m["resets_at"])
        eta = reset and _parse(m["eta"])
        if reset and reset > t:
            if m["level"] == "limited":
                part += f", limited until {_when(reset, t)}"
            elif eta and eta < reset:  # the time to the limit, as the pill shows it, says more than the reset
                part += f", limit in ~{_span(eta - t)}" if eta - t >= timedelta(minutes=1) else ", limit any minute"
            else:
                part += ", " + _resets(reset, t)[0].replace("Resets", "resets")
        parts.append(part)
    if now["state"] in ("stale", "error"):
        as_of = _parse(now["as_of"])
        parts.append(f"as of {_clock(as_of)}" if parts and as_of else now["message"] or "Can't reach Claude")
    elif not parts:
        parts.append(NO_LIMITS)
    if indexing:  # today's numbers are still partial
        parts.append(_indexing(indexing))
    if now["live"]:
        parts.append("Live " + _rate(now["tokens_per_min"]))
    return parts


def _tray_tip(now: dict, shown: Optional[dict], indexing: Optional[dict] = None) -> str:
    lines = ["Claude Usage", *_summary(now, shown, indexing)]
    # _summary ends with the indexing progress and the live rate: they give way first
    n = bool(indexing) + bool(now["live"]) if now["state"] not in ("loading", "signed_out") else 0
    lines, extra = lines[:len(lines) - n], lines[len(lines) - n:]
    why = now["message"] if now["state"] in ("stale", "error", "signed_out") and now["message"] not in lines else None
    out: list[str] = []
    for line in lines + [why] * bool(why) + extra:  # within the 127 characters of a Windows tray tip
        room = 127 - len("\n".join(out + [""]))
        if len(line) <= room:
            out.append(line)
        elif line == why and room >= 24:  # the reason is cut short rather than left out
            out.append(line[:room - 3].rstrip() + "...")
    return "\n".join(out)


class Controller(QObject):
    """Owns the config, the OSD, tray, menu and app window, and the two worker threads."""

    _refreshed = Signal(object, object, object)  # now, limits shown, indexing progress
    _built = Signal(object, object)                       # (range, project), snapshot or None
    _notice = Signal(str, str)                            # a balloon from any thread
    _update_checked = Signal(object, bool, bool)          # tag, newer, asked by the user

    def __init__(self, config: dict[str, Any], *, store_path: Optional[str] = None,
                 config_path: Optional[str] = None,
                 fetch: Callable[[str], dict] = collector.fetch_rate_limits) -> None:
        super().__init__()
        self.config = config
        self.store_path = store_path or store.default_path()
        self.config_path = config_path or user_config_path()
        self.claude_dir = config["claude_dir"]
        self._fetch = fetch
        self.app = None  # AppWindow, created on first open
        self._shown: Optional[dict] = None
        self._now: Optional[dict] = None
        self._indexing: Optional[dict] = None
        self._updated_at: Optional[float] = None
        self._want: Optional[tuple] = None
        self._building = False
        self._dirty = False  # a refresh or a new choice came in while a build ran: build once more after it
        self._repairing = False
        self._newer = False  # the store is from a newer version: never read or written
        self._stop, self._wake, self._reindex, self._ready = (threading.Event() for _ in range(4))
        self._bconn = None  # the snapshot thread's own connection

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DELAY_MS)
        self._save_timer.timeout.connect(self._save)

        self.notifier = UsageNotifier(self._thresholds(), self._balloon, bool(config.get("notifications_enabled", True)))
        self.watchdog = AuthWatchdog(self.claude_dir, on_repair_attempt=lambda m: self._notice.emit("Claude Usage", m))

        self.osd = Osd(config.get("osd_style", "card"), False, float(config.get("osd_opacity", 1.0)),
                       float(config.get("osd_scale", 1.0)))
        x, y = config.get("osd_x"), config.get("osd_y")
        if x is not None and y is not None:
            if _rect_on_some_screen(int(x), int(y), self.osd.width(), self.osd.height()):
                self.osd.move(int(x), int(y))
            else:
                log.info("saved OSD position (%s, %s) is off-screen; using the default", x, y)
        self.osd.clicked.connect(lambda: self.open_app())
        self.osd.right_clicked.connect(self._popup_menu)
        self.osd.moved.connect(self._on_osd_moved)
        self.osd.style_toggled.connect(self._on_style_toggled)

        self.menu = self._build_menu()
        self.tray = self._build_tray()
        QToolTip.setFont(_font(12))
        QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self._on_scheme())
        self._apply_appearance()

        self._refreshed.connect(self._on_refreshed)
        self._built.connect(self._on_built)
        self._notice.connect(self._balloon)
        self._update_checked.connect(self._on_update_checked)

        self._on_top = QTimer(self)
        self._on_top.setInterval(ON_TOP_MS)
        self._on_top.timeout.connect(self._keep_on_top)
        self._builder = ThreadPoolExecutor(1, thread_name_prefix="claude-usage-snapshot")
        self._worker = threading.Thread(target=self._run, name="claude-usage-refresh", daemon=True)
        self._server: Optional[QLocalServer] = None

    # ----------------------------------------------------------- lifecycle

    def start(self, check_updates: bool = True) -> None:
        self._apply_on_top()  # also shows the OSD
        self._on_top.start()
        self._worker.start()
        self._server = QLocalServer(self)
        QLocalServer.removeServer(SERVER_NAME)  # a stale socket file after a crash (not Windows)
        if self._server.listen(SERVER_NAME):
            self._server.newConnection.connect(self._on_peer)
        else:
            log.warning("single-instance server failed: %s", self._server.errorString())
        if check_updates:
            threading.Thread(target=self._check_update, args=(False,), daemon=True).start()

    def quit(self) -> None:
        self._stop.set()
        self._wake.set()
        self._ready.set()  # a build still waiting for the store must not hold the process at exit
        try:
            self._on_top.stop()
            if self.app is not None:
                self.app.prepare_quit()
                self.app.hide()  # reports app_geometry
            if self._save_timer.isActive():
                self._save_timer.stop()
                self._save()
            if self.tray is not None:
                self.tray.hide()
            self._builder.shutdown(wait=False, cancel_futures=True)
            if self._worker.is_alive():
                self._worker.join(2)  # it stops between files; a stuck fetch is left to die with the process
        except Exception:
            log.exception("error while quitting")
        QApplication.quit()  # always, or Quit would leave the process running

    def refresh_now(self) -> None:
        self._wake.set()

    def reindex(self) -> None:
        self._reindex.set()
        self._wake.set()

    def open_app(self, page: Optional[str] = None) -> None:
        try:
            if self.app is None:
                # Imported here so the QML engine and its libraries load only when the window is first opened.
                from claude_usage.app import AppWindow
                self.app = AppWindow(self._provider, self._app_settings(), self._on_setting, self._on_action)
            self.app.show(page)
        except Exception:  # called from clicks and menus: say so instead of failing silently
            log.exception("could not open the app window")
            self._balloon("Claude Usage", OPEN_FAILED)

    # ----------------------------------------------------------- worker thread

    def _open(self) -> tuple:
        """The worker's store connection and ingestor, retried with backoff: a locked
        store or a full disk can clear later. (None, None) once quit, or for a newer store."""
        tries = 0
        while not self._stop.is_set():
            conn = None
            try:
                conn = store.connect(self.store_path)
                if conn.execute("PRAGMA user_version").fetchone()[0] > store.SCHEMA_VERSION:
                    # Writing into a layout this version does not know could corrupt it; a retry cannot help.
                    log.error("%s was made by a newer Claude Usage; not using it", self.store_path)
                    self._notice.emit("Claude Usage", NEWER_STORE)
                    conn.close()
                    self._newer = True
                    return None, None
                try:
                    store.import_legacy(conn, self.claude_dir)
                except Exception:  # the old daily totals are optional; a bad row must not keep the store closed
                    log.exception("could not import the old usage files; going on without them")
                return conn, ingest.Ingestor(conn, os.path.join(self.claude_dir, "projects"))
            except Exception:
                log.exception("could not open the store at %s", self.store_path)
                if conn is not None:
                    conn.close()
                if not tries:
                    self._notice.emit("Claude Usage", "Could not open the usage store. Retrying; see claude-usage.log.")
                tries += 1
                self._stop.wait(BACKOFF_S[min(tries, len(BACKOFF_S)) - 1])
        return None, None

    def _run(self) -> None:
        conn, ing = self._open()
        self._ready.set()
        if conn is None:
            if self._newer:  # say why on the OSD, tray, menu and app, from an empty in-memory store
                lim, mem = {"error": NEWER_STORE, "error_kind": "store"}, store.connect(":memory:")
                self._refreshed.emit(snapshot.now_state(mem, lim, time.time(), self._thresholds()), lim, None)
                mem.close()
            return
        last_good: Optional[dict] = None
        shown: Optional[dict] = None
        errors, next_fetch, last_fetch, full = 0, 0.0, float("-inf"), True
        ix: dict = {}

        def progress(done: int, total: int) -> None:
            ix.update(done=done, total=total)

        force, interval = True, 30  # the first pass always fetches
        while not self._stop.is_set():
            try:  # one failure must not end the thread and freeze the OSD
                interval = max(5, int(self.config.get("refresh_seconds") or 30))
                if self._reindex.is_set():
                    self._reindex.clear()
                    self._wake.clear()  # reindex() woke us; left set, it would cancel the pass below at once
                    with conn:  # forget every read offset; re-reading is safe, calls are upserted
                        conn.execute("DELETE FROM files")
                    ing, full, force = ingest.Ingestor(conn, os.path.join(self.claude_dir, "projects")), True, True
                try:
                    if full:
                        # A short first pass gets the OSD its first numbers within seconds on a first run;
                        # later passes stay short so the app's indexing progress keeps moving.
                        deadline = time.monotonic() + (min(interval, 10) if shown is not None else 3)
                        r = ing.index(progress, cancel=lambda: (self._stop.is_set() or self._wake.is_set()
                                                                or time.monotonic() > deadline))
                        full = r["cancelled"]
                    else:
                        ing.tail(cancel=lambda: self._stop.is_set() or self._wake.is_set())
                except Exception:
                    log.exception("ingest failed")
                    full = False  # tail() retries the full walk; never spin on a broken pass
                if self._stop.is_set():
                    break
                if self._wake.is_set():  # Refresh now or Re-index; it also cuts an indexing pass short
                    self._wake.clear()
                    force = time.monotonic() - last_fetch >= FORCE_GAP_S  # else only "now" is worked out again
                if force or time.monotonic() >= next_fetch:
                    force, last_fetch = False, time.monotonic()
                    try:
                        lim = self._fetch(self.claude_dir)
                    except Exception as exc:
                        log.exception("limits fetch failed")
                        lim = {"error": f"Collection error: {type(exc).__name__}"}
                    error = lim.get("error")
                    if error:
                        errors += 1
                        # The plan is in the credentials, not the reply: a first fetch that fails still names it.
                        shown = dict(last_good or {}, error=error, plan=lim.get("plan") or collector._plan(self.claude_dir))
                        next_fetch = time.monotonic() + BACKOFF_S[min(errors, len(BACKOFF_S)) - 1]
                    else:
                        errors = 0
                        last_good = shown = lim
                        next_fetch = time.monotonic() + interval
                        w, f = lim.get("weekly"), lim.get("five_hour")
                        try:
                            store.add_sample(conn, lim["fetched_at"], w and w["pct"], f and f["pct"],
                                             {"resets_at": (w or {}).get("resets_at"), "source": lim.get("source")})
                        except Exception:
                            log.exception("could not store the utilization sample")
                    self._heal(error)
                now = snapshot.now_state(conn, shown, time.time(), self._thresholds())
                self._refreshed.emit(now, shown, dict(ix) if full else None)
            except Exception:
                log.exception("refresh failed")
                full = False
            if not full:
                self._wake.wait(interval)  # not cleared here, so a wake that lands mid-refresh is kept
        conn.close()

    def _heal(self, error: Optional[str]) -> None:
        """Feed a fetch outcome to the auth watchdog. A repair runs `claude setup-token`
        (up to minutes), so it gets its own thread and the refreshes go on."""
        if self._repairing:
            return
        if not is_auth_error(error):
            self.watchdog.note_result(error)  # success resets the streak; 429 and network errors don't count
            return
        self._repairing = True

        def run() -> None:
            try:
                self.watchdog.note_result(error)
            finally:
                self._repairing = False

        threading.Thread(target=run, name="claude-usage-auth", daemon=True).start()

    # ----------------------------------------------------------- GUI thread

    def _on_refreshed(self, now: dict, shown: Optional[dict], indexing: Optional[dict]) -> None:
        self._now, self._shown, self._indexing, self._updated_at = now, shown, indexing, time.time()
        self.osd.set_now(now, indexing)
        if self.tray is not None:
            self.tray.setToolTip(_tray_tip(now, shown, indexing))
        self.notifier.check_now(now, _active_scoped(shown))
        if self.app is not None and self.app.is_visible() and not self.app.window.windowState() & Qt.WindowMinimized:
            self._kick_build()

    def _provider(self, range_key: str, project: Optional[str]) -> None:
        """AppWindow's provider: queue a build and return None; the result arrives via update_snapshot."""
        self._want = (range_key, project)
        self._kick_build()
        return None

    def _kick_build(self) -> None:
        if self._want is None or self._stop.is_set():
            return
        if self._building:
            self._dirty = True
            return
        self._building, self._dirty = True, False
        self._builder.submit(self._build_job, self._want, self._shown, self._indexing, self._updated_at,
                             self._thresholds())

    def _build_job(self, want: tuple, limits: Optional[dict], indexing: Optional[dict],
                   updated_at: Optional[float], thresholds: tuple) -> None:
        snap = None
        try:
            self._ready.wait()  # the worker creates or migrates the store first
            if self._stop.is_set():
                return
            if self._bconn is None:
                self._bconn = store.connect(":memory:" if self._newer else self.store_path)
            snap = snapshot.build(self._bconn, want[0], want[1], time.time(), limits, indexing=indexing,
                                  updated_at=updated_at, thresholds=thresholds)
        except Exception:
            log.exception("snapshot build failed for %s", want)
        try:
            self._built.emit(want, snap)
        except RuntimeError:  # the controller was deleted at exit while this build ran
            pass

    def _on_built(self, want: tuple, snap: Optional[dict]) -> None:
        self._building = False
        if want == self._want and snap is not None and self.app is not None:
            self.app.update_snapshot(snap)
        if self._dirty or want != self._want:
            self._kick_build()  # newer limits, or another range or project, came in while this one built

    # ----------------------------------------------------------- settings

    def _thresholds(self) -> tuple:
        t = self.config.get("notify_thresholds") or [tokens.CAUTION, tokens.CRITICAL]
        return float(t[0]), float(t[1])

    def _app_settings(self) -> dict:
        return dict(self.config, autostart=autostart.is_enabled(), store_path=self.store_path)

    def _set(self, key: str, value: Any) -> None:
        """The one config writer: update in memory, save shortly after."""
        if self.config.get(key) == value:
            return
        self.config[key] = value
        self._save_timer.start(SAVE_DELAY_MS)

    def _save(self) -> None:
        try:
            save_config(self.config_path, self.config)
        except OSError as exc:
            log.warning("could not save the config to %s: %s; retrying", self.config_path, exc)
            self._save_timer.start(SAVE_RETRY_MS)  # quit() also saves while a retry is pending
        except (TypeError, ValueError) as exc:  # a value JSON cannot hold
            log.warning("could not save the config to %s: %s", self.config_path, exc)

    def _on_setting(self, key: str, value: Any) -> None:
        raw_js = hasattr(value, "toVariant")  # a QJSValue: QML passed a JS array (the alert thresholds)
        if raw_js:
            value = value.toVariant()
        if key == "autostart":  # the Startup-folder file is the truth, not the config
            if not autostart.set_enabled(bool(value)):
                self._balloon("Claude Usage", "Couldn't change the launch-at-startup setting.")
            if self.app is not None:
                self.app.set_settings(self._app_settings())
            return
        clean = drop_bad_values({key: value})
        if key not in clean:  # e.g. thresholds that do not parse would fail every refresh
            log.warning("ignored a bad value for %s: %r", key, value)
            if self.app is not None:
                self.app.set_settings(self._app_settings())
            return
        value = clean[key]
        self._set(key, value)
        if key == "appearance":
            self._apply_appearance()
        elif key == "osd_style":
            self.osd.set_style(value)
        elif key == "osd_opacity":
            self.osd.set_opacity(float(value))
        elif key == "osd_always_on_top":
            self._apply_on_top()
        elif key == "notifications_enabled":
            self.notifier.enabled = bool(value)
        elif key == "notify_thresholds":
            self.notifier.set_thresholds(self._thresholds())
            self.refresh_now()  # the meter levels come from the worker; recolour now, not at the next tick
        if raw_js and self.app is not None:
            self.app.set_settings(self._app_settings())  # the window kept the QJSValue; give it the plain value

    def _on_action(self, name: str) -> None:
        if name == "refresh":
            self.refresh_now()
        elif name == "reindex":
            self.reindex()
        elif name == "check_updates":
            threading.Thread(target=self._check_update, args=(True,), daemon=True).start()

    def _on_osd_moved(self, x: int, y: int) -> None:
        self._set("osd_x", x)
        self._set("osd_y", y)

    def _on_style_toggled(self, style: str) -> None:
        self._set("osd_style", style)
        if self.app is not None:
            self.app.set_settings(self._app_settings())

    def _apply_on_top(self) -> None:
        on = bool(self.config.get("osd_always_on_top", True))
        if bool(self.osd.windowFlags() & Qt.WindowStaysOnTopHint) != on:
            self.osd.setWindowFlag(Qt.WindowStaysOnTopHint, on)  # recreates the native window and hides it
        self.osd.show()

    def _keep_on_top(self) -> None:
        if self.config.get("osd_always_on_top", True):
            self.osd.keep_on_top()

    def _dark(self) -> bool:
        choice = self.config.get("appearance", "system")
        if choice in ("light", "dark"):
            return choice == "dark"
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark

    def _apply_appearance(self) -> None:
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "setColorScheme"):  # Qt 6.8+: the app window's Fluent controls follow it too
            scheme = {"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}
            hints.setColorScheme(scheme.get(self.config.get("appearance"), Qt.ColorScheme.Unknown))
        self._on_scheme()

    def _on_scheme(self) -> None:
        dark = self._dark()
        self.osd.set_dark(dark)
        QApplication.instance().setStyleSheet(_qss(dark))

    # ----------------------------------------------------------- menu, tray, peers

    def _build_menu(self) -> QMenu:
        menu = QMenu()
        menu.setFont(_font(14))
        self._act_head = menu.addAction("Loading usage")
        self._act_head.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Open Claude Usage").triggered.connect(lambda: self.open_app())
        self._act_refresh = menu.addAction("Refresh now")
        self._act_refresh.triggered.connect(self.refresh_now)
        self._act_style = menu.addAction("")
        self._act_style.triggered.connect(lambda: self.osd.toggle_style())
        menu.addAction("Settings...").triggered.connect(lambda: self.open_app("settings"))
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(self.quit)
        menu.aboutToShow.connect(self._on_menu_show)
        return menu

    def _popup_menu(self, pos: QPoint) -> None:
        # Open above (or below) the overlay, never over it: the overlay is top-most and would cover the menu.
        self._on_menu_show()
        r, h = self.osd.frameGeometry(), self.menu.sizeHint().height()
        screen = self.osd.screen() or QGuiApplication.primaryScreen()
        y = r.top() - h if r.top() - h >= screen.availableGeometry().top() else r.bottom() + 1
        self.menu.popup(QPoint(pos.x(), y))

    def _on_menu_show(self) -> None:
        self._act_head.setText("  ·  ".join(_summary(self._now, self._shown, self._indexing)))
        self._act_refresh.setVisible(not self._newer)  # a store from a newer version: refreshing can't help
        self._act_style.setText("Show as card" if self.osd.osd_style() == "pill" else "Show as pill")
        apply_win11_chrome(self.menu, self._dark())

    def _build_tray(self) -> Optional[QSystemTrayIcon]:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(QIcon(ICON), self)
        tray.setToolTip("Claude Usage")
        tray.setContextMenu(self.menu)
        tray.activated.connect(lambda reason: self.open_app() if reason in (
            QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        tray.show()
        return tray

    def _balloon(self, title: str, body: str) -> None:
        log.info("notice: %s: %s", title, body)
        if self.tray is not None:
            self.tray.showMessage(title, body, QSystemTrayIcon.MessageIcon.Information, 6000)

    def _on_peer(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            sock.disconnectFromServer()
            sock.deleteLater()
        self.open_app()

    def _check_update(self, asked: bool) -> None:
        tag, newer = updater.check_latest_version(__version__)
        self._update_checked.emit(tag, newer, asked)

    def _on_update_checked(self, tag: Optional[str], newer: bool, asked: bool) -> None:
        if newer and (asked or tag != self.config.get("update_notified")):
            self._set("update_notified", tag)  # the startup check tells about a release once
            self._balloon(f"Claude Usage {tag} is available", updater.UPGRADE_HINT)
        elif asked:
            self._balloon("Claude Usage", f"You have the latest version ({__version__})." if tag
                          else "Couldn't reach GitHub to check for updates.")
