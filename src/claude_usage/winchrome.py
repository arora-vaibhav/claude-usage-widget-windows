"""Windows 11 native chrome (DWM) helpers. Silent no-ops elsewhere.

Qt windows on Windows 11 dark mode default to a WHITE title bar — the single
biggest "not a native app" tell. ``DwmSetWindowAttribute`` fixes that
(immersive dark mode) and lets us opt in to rounded window corners.
"""

from __future__ import annotations

import sys


def apply_win11_chrome(widget, dark: bool = True, corner: int = 2) -> None:
    """Dark immersive title bar + corner preference on a top-level window.

    ``corner``: 0=default, 1=square, 2=DWMWCP_ROUND, 3=DWMWCP_ROUNDSMALL.
    Must be called AFTER the last ``setWindowFlags`` on the widget — changing
    flags recreates the native window and drops these attributes. Safe under
    ``QT_QPA_PLATFORM=offscreen`` and on non-Windows (silent no-op; DWM errors
    are swallowed — Windows 10 ignores the corner attribute, for example).
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(widget.winId())  # forces native handle creation
        dwm = ctypes.windll.dwmapi
        val = ctypes.c_int(1 if dark else 0)
        # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (19 on builds < 19041)
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), ctypes.sizeof(val)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(val), ctypes.sizeof(val))
        pref = ctypes.c_int(corner)  # 33 = DWMWA_WINDOW_CORNER_PREFERENCE
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass
