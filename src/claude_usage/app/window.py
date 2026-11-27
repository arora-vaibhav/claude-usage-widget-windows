"""The app window: a Qt Quick ApplicationWindow fed with snapshot dicts.

The controller owns the data. It gives :class:`AppWindow` a provider
``(range_key, project_or_None) -> snapshot dict`` (shape:
tests/unit/snapshot_contract.py) and a settings callback
``on_setting(key, value)``; the window never reads files itself.
"""

from __future__ import annotations

import ctypes
import json
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import Property, QObject, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle

from .. import __version__, snapshot, tokens
from ..winchrome import apply_win11_chrome

log = logging.getLogger(__name__)

QML_MAIN = Path(__file__).with_name("qml") / "Main.qml"
RANGES = ("24h", "7d", "30d", "90d", "all")
PAGES = ("overview", "sessions", "settings")
VARIABLE_FONT = "Segoe UI Variable"

Provider = Callable[[str, Optional[str]], dict]


def animations_on() -> bool:
    """Windows' "Animation effects" setting (Accessibility, Visual effects)."""
    if sys.platform != "win32":
        return True
    on = ctypes.c_int(1)
    spi_getclientareaanimation = 0x1042
    if not ctypes.windll.user32.SystemParametersInfoW(spi_getclientareaanimation, 0, ctypes.byref(on), 0):
        return True
    return bool(on.value)

OnSetting = Callable[[str, Any], None]
OnAction = Callable[[str], None]  # "refresh", "reindex", "check_updates"


class Backend(QObject):
    """What QML sees as ``backend``: the snapshot, theme tokens, settings and actions."""

    snapshotChanged = Signal()
    themeChanged = Signal()
    settingsChanged = Signal()
    busyChanged = Signal()
    updatedChanged = Signal()
    pageRequested = Signal(str)
    appearanceChanged = Signal()

    def __init__(self, provider: Provider, settings: Optional[dict] = None,
                 on_setting: Optional[OnSetting] = None,
                 on_action: Optional[OnAction] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._provider = provider
        self._on_setting = on_setting
        self._on_action = on_action
        self._settings = dict(settings or {})
        self._range = self._settings.get("app_range") if self._settings.get("app_range") in RANGES else "7d"
        self._project = ""
        self._json = "null"
        self._last: Optional[dict] = None
        self._updated = ""
        self._dark = False
        self._busy = False
        self._quitting = False

    # -- properties -------------------------------------------------------
    def _snapshot_json(self) -> str:
        return self._json

    def _range_key(self) -> str:
        return self._range

    def _project_name(self) -> str:
        return self._project

    def _updated_at(self) -> str:
        return self._updated

    def _is_dark(self) -> bool:
        return self._dark

    def _is_busy(self) -> bool:
        return self._busy

    def _is_quitting(self) -> bool:
        return self._quitting

    def _settings_map(self) -> dict:
        return self._settings

    def _version(self) -> str:
        return __version__

    def _cost_note(self) -> str:
        return snapshot.cost_note()

    def _theme(self) -> dict:
        dark = self._dark
        # shortcut: the offscreen platform renders Segoe UI Variable through
        # FreeType with the wrong axes, so test renders use static Segoe UI.
        variable = QGuiApplication.platformName() != "offscreen"
        th = self._settings.get("notify_thresholds") or [tokens.CAUTION, tokens.CRITICAL]
        return {
            "dark": dark,
            "p": tokens.palette(dark),
            "s": tokens.series(dark),
            "other": tokens.OTHER_DARK if dark else tokens.OTHER_LIGHT,
            # The "Segoe UI Variable Text" and "Display" families only list
            # Regular and Bold, so 600 rendered Bold; QML picks the size with
            # the opsz axis instead (Ui.axes).
            "fontUi": VARIABLE_FONT if variable else "Segoe UI",
            "fontDisplay": VARIABLE_FONT if variable else "Segoe UI",
            "opsz": variable,
            "motion": animations_on(),
            "caution": float(th[0]),
            "critical": float(th[1]),
        }

    snapshotJson = Property(str, _snapshot_json, notify=snapshotChanged)
    rangeKey = Property(str, _range_key, notify=snapshotChanged)
    project = Property(str, _project_name, notify=snapshotChanged)
    updatedAt = Property(str, _updated_at, notify=updatedChanged)
    dark = Property(bool, _is_dark, notify=themeChanged)
    theme = Property("QVariantMap", _theme, notify=themeChanged)
    settings = Property("QVariantMap", _settings_map, notify=settingsChanged)
    busy = Property(bool, _is_busy, notify=busyChanged)
    quitting = Property(bool, _is_quitting)
    version = Property(str, _version, constant=True)
    costNote = Property(str, _cost_note, constant=True)

    # -- slots called from QML --------------------------------------------
    @Slot(str)
    def setRange(self, key: str) -> None:
        if key not in RANGES or key == self._range:
            return
        self._range = key
        self._report("app_range", key)
        self._reload_soon()

    @Slot(str)
    def setProject(self, name: str) -> None:
        if name == self._project:
            return
        self._project = name
        self._reload_soon()

    @Slot()
    def refresh(self) -> None:
        if self._on_action:
            self._action("refresh")
        else:
            self._reload_soon()

    @Slot()
    def reindex(self) -> None:
        self._action("reindex")

    @Slot()
    def checkUpdates(self) -> None:
        self._action("check_updates")

    @Slot(str, "QVariant")
    def setSetting(self, key: str, value: Any) -> None:
        if self._settings.get(key) == value:
            return
        self._settings[key] = value
        self.settingsChanged.emit()
        self._report(key, value)
        if key == "appearance":
            self.appearanceChanged.emit()
        elif key == "notify_thresholds":
            self.themeChanged.emit()

    # -- Python side ------------------------------------------------------
    def reload(self) -> None:
        """Ask the provider for the current range and project and show it.

        A provider that returns None is building in the background; the page
        stays dimmed until the controller calls :meth:`update_snapshot`.
        """
        try:
            snap = self._provider(self._range, self._project or None)
        except Exception:  # keep the last good frame on screen
            log.exception("snapshot provider failed")
            self._set_busy(False)
            return
        if snap is not None:
            self.update_snapshot(snap)

    def update_snapshot(self, snap: dict) -> None:
        key = (snap.get("range") or {}).get("key", self._range)
        if self._busy and (key, snap.get("project") or "") != (self._range, self._project):
            return  # a build for the previous choice landed after a new one was picked; that one is on its way
        status = snap.get("status") or {}
        if status.get("updated_at") != (self._updated or None):
            self._updated = status.get("updated_at") or ""
            self.updatedChanged.emit()
        # A refresh where only the clock moved skips the QML rebuild.
        same = dict(snap, generated_at=None, status=dict(status, updated_at=None))
        if same == self._last and key == self._range and (snap.get("project") or "") == self._project:
            self._set_busy(False)
            return
        self._last = same
        # The header controls show what the data actually covers.
        self._range = key
        self._project = snap.get("project") or ""
        self._json = json.dumps(snap, separators=(",", ":"))
        self.snapshotChanged.emit()
        self._set_busy(False)

    def set_dark(self, dark: bool) -> None:
        if dark != self._dark:
            self._dark = dark
            self.themeChanged.emit()

    def set_settings(self, settings: dict) -> None:
        self._settings = dict(settings)
        self.settingsChanged.emit()
        self.themeChanged.emit()
        self.appearanceChanged.emit()

    def _action(self, name: str) -> None:
        if self._on_action:
            try:
                self._on_action(name)
            except Exception:
                log.exception("action callback failed for %s", name)

    def _report(self, key: str, value: Any) -> None:
        if self._on_setting:
            try:
                self._on_setting(key, value)
            except Exception:
                log.exception("settings callback failed for %s", key)

    def _reload_soon(self) -> None:
        # One frame first, so the control shows the new choice and the old
        # numbers dim while the provider works.
        self._set_busy(True)
        QTimer.singleShot(16, self.reload)

    def _set_busy(self, busy: bool) -> None:
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


class AppWindow(QObject):
    """The app window. Hidden, not destroyed, on close; created once."""

    def __init__(self, provider: Provider, settings: Optional[dict] = None,
                 on_setting: Optional[OnSetting] = None,
                 on_action: Optional[OnAction] = None,
                 parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        if QQuickStyle.name() != "FluentWinUI3":  # set once per process
            QQuickStyle.setStyle("FluentWinUI3")
        self.warnings: list[str] = []
        # Children are deleted in creation order: the engine (and every
        # binding that reads backend) must go before the backend does.
        self.engine = QQmlApplicationEngine(self)
        self.backend = Backend(provider, settings, on_setting, on_action, self)
        self.engine.warnings.connect(self._on_warnings)
        self.engine.rootContext().setContextProperty("backend", self.backend)
        self.engine.load(QUrl.fromLocalFile(str(QML_MAIN)))
        roots = self.engine.rootObjects()
        if not roots:
            raise RuntimeError("app window failed to load: " + "; ".join(self.warnings[:5]))
        self.window = roots[0]
        self._restore_geometry((settings or {}).get("app_geometry"))
        self.window.visibleChanged.connect(self._on_visible)
        self.backend.appearanceChanged.connect(self._resolve_dark)
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._resolve_dark)
        self._resolve_dark()

    # -- controller API -----------------------------------------------------
    def show(self, page: Optional[str] = None) -> None:
        if page in PAGES:
            self.backend.pageRequested.emit(page)
        if not self.window.isVisible():
            self.backend.reload()
        self.window.show()
        if self.window.windowState() & Qt.WindowMinimized:
            self.window.showNormal()
        self.window.raise_()
        self.window.requestActivate()
        apply_win11_chrome(self.window, self.backend.dark)

    def hide(self) -> None:
        self.window.hide()

    def is_visible(self) -> bool:
        return self.window.isVisible()

    def reload(self) -> None:
        """Rebuild the snapshot for the current range and project."""
        self.backend.reload()

    def update_snapshot(self, snap: dict) -> None:
        self.backend.update_snapshot(snap)

    def set_settings(self, settings: dict) -> None:
        self.backend.set_settings(settings)

    def set_dark(self, dark: bool) -> None:
        """Force a mode (tests, or a controller that resolves System itself)."""
        self.backend.set_dark(dark)
        if self.window.isVisible():
            apply_win11_chrome(self.window, dark)

    def prepare_quit(self) -> None:
        """Let the next close really close (call before QApplication.quit())."""
        self.backend._quitting = True

    # -- internals ----------------------------------------------------------
    def _resolve_dark(self) -> None:
        choice = self.backend.settings.get("appearance", "system")
        if choice in ("light", "dark"):
            dark = choice == "dark"
        else:
            dark = QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self.set_dark(dark)

    def _on_visible(self, visible: bool) -> None:
        if not visible:
            g = self.window.geometry()
            self.backend._report("app_geometry", [g.x(), g.y(), g.width(), g.height()])

    def _restore_geometry(self, geo: Any) -> None:
        if not (isinstance(geo, (list, tuple)) and len(geo) == 4):
            return
        rect = QRect(*(int(v) for v in geo))
        if rect.width() < 960 or rect.height() < 640:
            return
        # A window saved on a monitor that is gone would open out of reach.
        if any(s.availableGeometry().intersects(rect) for s in QGuiApplication.screens()):
            self.window.setGeometry(rect)

    def _on_warnings(self, warnings) -> None:
        for w in warnings:
            self.warnings.append(w.toString())
            log.warning("qml: %s", w.toString())
