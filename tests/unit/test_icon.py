import os
import struct

from PySide6.QtGui import QGuiApplication, QIcon

from claude_usage import widget

_app = QGuiApplication.instance() or QGuiApplication([])
ICONS = os.path.join(os.path.dirname(widget.__file__), "icons")


def test_the_app_icon_has_every_windows_size():
    data = open(os.path.join(ICONS, "claude-usage.ico"), "rb").read()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sides = {data[6 + 16 * i] or 256 for i in range(count)}  # 0 in an ICO entry means 256
    assert {16, 20, 24, 32, 48, 256} <= sides
    assert {s.width() for s in QIcon(widget.ICON).availableSizes()} >= {16, 32, 256}

