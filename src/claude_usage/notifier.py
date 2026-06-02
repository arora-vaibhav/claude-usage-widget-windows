"""Threshold-crossing notifications for usage utilization.

Pure crossing logic lives in :class:`CrossingDetector` (testable).
:class:`UsageNotifier` binds the detector to a platform-appropriate desktop
notification sender.

The sender uses plain subprocess calls so the widget has no runtime
dependency on PyGObject (libnotify) or rumps:

    Linux : ``notify-send`` (from ``libnotify-bin`` / ``libnotify``)
    macOS : ``osascript`` (AppleScript, ships with macOS)
    Other : silent no-op
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Callable, Optional


class CrossingDetector:
    """Detects upward threshold crossings in a stream of values per scope.

    A threshold fires only on the transition that crosses it (prev < t <= cur),
    so a reset (cur < prev) silently re-arms it for the next cycle.
    """

    def __init__(self, thresholds):
        self.thresholds = sorted(t for t in thresholds if 0.0 < t <= 1.0)
        self._last: dict[str, float] = {}

    def check(self, scope: str, util: float) -> list[float]:
        """Update *scope*'s last value and return thresholds it just crossed.

        First-ever call for a scope returns ``[]`` — we need a baseline before
        a "crossing" can be defined; otherwise the first observation would
        always fire every threshold ≤ the current value.
        """
        prev = self._last.get(scope)
        self._last[scope] = util
        if prev is None:
            return []
        return [t for t in self.thresholds if prev < t <= util]


def _send_linux(title: str, body: str) -> None:
    """Fire a desktop notification via ``notify-send`` (libnotify)."""
    if shutil.which("notify-send") is None:
        return
    try:
        subprocess.run(
            ["notify-send", "--icon=dialog-warning", title, body],
            check=False, timeout=5,
        )
    except Exception:
        pass


def _send_macos(title: str, body: str) -> None:
    """Fire a desktop notification via ``osascript`` (AppleScript)."""
    # AppleScript string literals escape quotes by DOUBLING (`""`), not via
    # backslash. Newlines also break the single-line `-e` script so we
    # collapse them to spaces. This guards against arbitrary content
    # (project paths, error messages) breaking the script or worse,
    # injecting AppleScript via crafted strings.
    def _escape(s: str) -> str:
        return s.replace('"', '""').replace("\n", " ").replace("\r", " ")
    t = _escape(title)
    b = _escape(body)
    script = f'display notification "{b}" with title "{t}"'
    try:
        subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            check=False, timeout=5,
        )
    except Exception:
        pass


def _send_windows(title: str, body: str) -> None:
    """Fire a desktop notification via a Windows tray balloon (PowerShell)."""
    # Single-quoted PowerShell strings escape a quote by doubling it; collapse
    # newlines so the one-line command stays intact and content can't break out.
    def _escape(s: str) -> str:
        return s.replace("'", "''").replace("\n", " ").replace("\r", " ")
    t = _escape(title)
    b = _escape(body)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$n=New-Object System.Windows.Forms.NotifyIcon;"
        "$n.Icon=[System.Drawing.SystemIcons]::Information;"
        "$n.Visible=$true;"
        f"$n.ShowBalloonTip(6000,'{t}','{b}',"
        "[System.Windows.Forms.ToolTipIcon]::Info);"
        "Start-Sleep -Milliseconds 6500;$n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive",
             "-WindowStyle", "Hidden", "-Command", script],
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        pass


def _default_sender() -> Callable[[str, str], None]:
    if sys.platform == "darwin":
        return _send_macos
    if sys.platform.startswith("linux"):
        return _send_linux
    if sys.platform.startswith("win"):
        return _send_windows

    def _noop(title: str, body: str) -> None:
        return

    return _noop


def notify(title: str, body: str) -> None:
    """Fire a one-off desktop notification on the current platform (best effort)."""
    try:
        _default_sender()(title, body)
    except Exception:
        pass


class UsageNotifier:
    """Fires desktop notifications when session/weekly utilization crosses a threshold."""

    SCOPES = (
        ("session", "Session", "session_utilization"),
        ("weekly", "Weekly", "weekly_utilization"),
    )

    def __init__(
        self,
        config: dict,
        sender: Optional[Callable[[str, str], None]] = None,
        on_threshold: Optional[Callable[[str, float], None]] = None,
    ):
        self.enabled = bool(config.get("notifications_enabled", True))
        thresholds = config.get("notify_thresholds", [0.75, 0.90])
        self.detector = CrossingDetector(thresholds)
        self._send = sender or _default_sender()
        self._on_threshold = on_threshold

    def check_stats(self, stats) -> None:
        """Run the detector for each scope and dispatch notifications +
        the optional ``on_threshold`` callback for any new crossings."""
        for scope, label, attr in self.SCOPES:
            util = getattr(stats, attr, 0.0) or 0.0
            for t in self.detector.check(scope, util):
                if self.enabled:
                    pct_t = int(round(t * 100))
                    pct_now = int(round(util * 100))
                    self._send(
                        f"Claude {label} usage at {pct_now}%",
                        f"Crossed the {pct_t}% threshold.",
                    )
                if self._on_threshold is not None:
                    try:
                        self._on_threshold(scope, t)
                    except Exception:
                        pass
