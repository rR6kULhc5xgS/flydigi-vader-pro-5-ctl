#!/usr/bin/env python3
"""Draw the application icon.

Original artwork, so it can be redistributed with the project -- unlike the
vendor's product photo. A simple gamepad silhouette that stays legible at
16 px.
"""

import os
import sys

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QApplication

SIZES = (16, 24, 32, 48, 64, 128, 256)

BODY = QColor("#2f3742")
BODY_EDGE = QColor("#8b949e")
ACCENT = QColor("#3fb950")
DETAIL = QColor("#c9d1d9")


def draw(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 100.0                      # work in a 100x100 space

    def pt(x, y):
        return QPointF(x * s, y * s)

    def rect(x0, y0, x1, y1):
        return QRectF(pt(x0, y0), pt(x1, y1))

    # body: a rounded slab with two grips swept down and out
    path = QPainterPath()
    path.addRoundedRect(rect(10, 30, 90, 68), 22 * s, 22 * s)
    path.addEllipse(rect(8, 40, 40, 80))
    path.addEllipse(rect(60, 40, 92, 80))
    p.setBrush(BODY)
    p.setPen(QPen(BODY_EDGE, max(1.0, 2.5 * s)))
    p.drawPath(path.simplified())

    # left stick
    p.setBrush(ACCENT)
    p.setPen(QPen(ACCENT.darker(140), max(1.0, 1.5 * s)))
    p.drawEllipse(rect(20, 40, 38, 58))

    # d-pad
    p.setBrush(DETAIL)
    p.setPen(Qt.NoPen)
    if size >= 24:
        p.drawRoundedRect(rect(42, 52, 50, 68), 2 * s, 2 * s)
        p.drawRoundedRect(rect(38, 56, 54, 64), 2 * s, 2 * s)

    # face buttons
    p.setBrush(DETAIL)
    r = 5.0
    for cx, cy in ((70, 40), (62, 48), (78, 48), (70, 56)):
        p.drawEllipse(rect(cx - r, cy - r, cx + r, cy + r))

    p.end()
    return img


def main() -> int:
    app = QApplication(sys.argv)
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "icons"
    written = []
    for size in SIZES:
        d = os.path.join(out_dir, f"{size}x{size}", "apps")
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, "flydigi-control.png")
        draw(size).save(path)
        written.append(path)
    print(f"wrote {len(written)} icons under {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
