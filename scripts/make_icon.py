"""Render icons/claude-usage.svg into icons/claude-usage.ico (Windows shortcuts, window and taskbar).

Run after changing the SVG: python scripts/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ICONS = os.path.join(os.path.dirname(__file__), "..", "src", "claude_usage", "icons")
SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)


def png(renderer: QSvgRenderer, size: int) -> bytes:
    img = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def ico(images: list[tuple[int, bytes]]) -> bytes:
    """An ICO of PNG-compressed entries (Windows Vista and later read these at every size)."""
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        side = 0 if size >= 256 else size  # 0 means 256 in an ICO directory entry
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(os.path.join(ICONS, "claude-usage.svg"))
    assert renderer.isValid(), "claude-usage.svg did not parse"
    out = os.path.join(ICONS, "claude-usage.ico")
    with open(out, "wb") as f:
        f.write(ico([(s, png(renderer, s)) for s in SIZES]))
    print(f"wrote {os.path.normpath(out)}")


if __name__ == "__main__":
    main()
