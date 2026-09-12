"""The controller, as a button picker and a live tester.

The front-on product photo is used for everything it actually shows.
Shoulders, triggers and the M1-M6 paddles are not visible from the front,
so they sit in labelled rows above and below the photo instead of being
guessed at on top of it.

Hotspot coordinates are in the photo's own 492x340 pixel space and are
scaled with it.
"""

from __future__ import annotations

import os

from PyQt5.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PyQt5.QtWidgets import QSizePolicy, QWidget

from .. import mapping as M

PHOTO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "assets", "vader5pro.png")
PHOTO_W, PHOTO_H = 492.0, 340.0

# slot -> (cx, cy, rx, ry, shape) in the photo's 492x340 pixel space.
# Measured off the product photo rather than estimated.
HOTSPOTS: dict[int, tuple[float, float, float, float, str]] = {
    240: (120, 88, 40, 40, "ring"),      # left stick, axes drawn here
    14:  (120, 88, 30, 30, "stick"),     # left stick click
    241: (300, 148, 40, 40, "ring"),     # right stick
    15:  (300, 148, 30, 30, "stick"),    # right stick click

    0:   (179, 121, 12, 12, "round"),    # d-pad up
    2:   (179, 167, 12, 12, "round"),    # down
    3:   (157, 144, 12, 12, "round"),    # left
    1:   (201, 144, 12, 12, "round"),    # right

    8:   (360, 57, 15, 15, "round"),     # Y
    7:   (330, 87, 15, 15, "round"),     # X
    5:   (390, 87, 15, 15, "round"),     # B
    4:   (360, 117, 15, 15, "round"),    # A
    17:  (413, 137, 15, 15, "round"),    # Z
    16:  (383, 158, 15, 15, "round"),    # C

    6:   (216, 78, 17, 11, "pill"),      # Select / View
    9:   (274, 78, 17, 11, "pill"),      # Start / Menu
    27:  (243, 42, 15, 14, "round"),     # Home, the Flydigi logo
}

#: Inputs the front view cannot show, as chips above and below.
CHIPS_TOP = [12, 10, 11, 13]                      # LT LB RB RT
CHIPS_BOTTOM = [18, 19, 20, 21, 22, 23, 24, 28, 25]

CHIP_H = 26.0
CHIP_GAP = 7.0
ROW_PAD = 10.0
CAPTION_H = 15.0


class ControllerView(QWidget):
    selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._photo = QPixmap(PHOTO)
        self.pressed: set[int] = set()
        self.output: set[int] = set()
        self.remapped: dict[int, str] = {}
        self.selection: int | None = None
        self.axes: dict[str, float] = {}
        self.live = False
        self._hover: int | None = None
        self._rects: dict[int, QRectF] = {}
        self.setMouseTracking(True)
        self.setMinimumSize(520, 470)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def sizeHint(self) -> QSize:
        return QSize(720, 600)

    # -- state ---------------------------------------------------------

    def set_pressed(self, slots: set[int]) -> None:
        if slots != self.pressed:
            self.pressed = set(slots)
            self.update()

    def set_output(self, slots: set[int]) -> None:
        """Buttons the controller is actually emitting, post-mapping."""
        if slots != self.output:
            self.output = set(slots)
            self.update()

    def set_axes(self, axes: dict[str, float]) -> None:
        self.axes = axes
        if self.live:
            self.update()

    def set_remapped(self, remapped: dict[int, str]) -> None:
        self.remapped = dict(remapped)
        self.update()

    def set_selection(self, slot: int | None) -> None:
        self.selection = slot
        self.update()

    # -- geometry ------------------------------------------------------

    def _geometry(self):
        """Return (scale, photo origin x, photo origin y, top row y, bottom row y)."""
        top_h = CAPTION_H + CHIP_H + ROW_PAD
        bottom_h = ROW_PAD + CHIP_H + CAPTION_H
        avail_h = self.height() - top_h - bottom_h
        scale = min(self.width() / PHOTO_W, avail_h / PHOTO_H)
        scale = max(scale, 0.1)
        pw, ph = PHOTO_W * scale, PHOTO_H * scale
        ox = (self.width() - pw) / 2
        oy = top_h + (avail_h - ph) / 2
        return scale, ox, oy, (oy - ROW_PAD - CHIP_H), (oy + ph + ROW_PAD)

    def _chip_rects(self, slots, y) -> dict[int, QRectF]:
        if not slots:
            return {}
        widths = []
        f = QFont()
        f.setPointSizeF(9)
        f.setBold(True)
        from PyQt5.QtGui import QFontMetricsF
        fm = QFontMetricsF(f)
        for s in slots:
            label = M.KEY_NAMES.get(s, str(s))
            widths.append(max(38.0, fm.horizontalAdvance(label) + 22))
        total = sum(widths) + CHIP_GAP * (len(slots) - 1)
        x = (self.width() - total) / 2
        out = {}
        for s, w in zip(slots, widths):
            out[s] = QRectF(x, y, w, CHIP_H)
            x += w + CHIP_GAP
        return out

    def _rebuild_rects(self):
        scale, ox, oy, top_y, bot_y = self._geometry()
        rects: dict[int, QRectF] = {}
        for slot, (x, y, rx, ry, shape) in HOTSPOTS.items():
            if shape == "ring":
                continue                     # not clickable; the click is the stick
            cx, cy = ox + x * scale, oy + y * scale
            w, h = rx * scale, ry * scale
            rects[slot] = QRectF(cx - w, cy - h, w * 2, h * 2)
        rects.update(self._chip_rects(CHIPS_TOP, top_y))
        rects.update(self._chip_rects(CHIPS_BOTTOM, bot_y))
        self._rects = rects

    def _hit(self, pos) -> int | None:
        best, best_d = None, 1e9
        for slot, rect in self._rects.items():
            if rect.contains(pos):
                d = (QPointF(pos) - rect.center()).manhattanLength()
                if d < best_d:
                    best, best_d = slot, d
        return best

    def mousePressEvent(self, event):
        slot = self._hit(event.pos())
        if slot is not None:
            self.selection = slot
            self.selected.emit(slot)
            self.update()

    def mouseMoveEvent(self, event):
        hover = self._hit(event.pos())
        if hover != self._hover:
            self._hover = hover
            self.setCursor(Qt.PointingHandCursor if hover is not None
                           else Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, event):
        self._hover = None
        self.update()

    # -- painting ------------------------------------------------------

    @property
    def has_photo(self) -> bool:
        return not self._photo.isNull()

    def _draw_schematic(self, p, ox, oy, scale):
        """Stand-in for the product photo, which is not redistributable.

        Every hotspot is drawn and labelled so the view stays usable; run
        tools/extract-assets.py against a local Space Station install to get
        the real artwork.
        """
        def pt(x, y):
            return QPointF(ox + x * scale, oy + y * scale)

        p.setBrush(QColor("#1b212a"))
        p.setPen(QPen(QColor("#2f3742"), max(1.0, 2 * scale)))
        p.drawRoundedRect(QRectF(pt(20, 25), pt(472, 300)),
                          60 * scale, 60 * scale)
        for slot, (x, y, rx, ry, shape) in HOTSPOTS.items():
            if shape == "ring":
                p.setPen(QPen(QColor("#39414d"), max(1.0, 2 * scale)))
                p.setBrush(QColor("#141a22"))
                p.drawEllipse(QRectF(pt(x - rx, y - ry), pt(x + rx, y + ry)))
                continue
            if shape == "stick":
                continue
            p.setPen(QPen(QColor("#4a525e"), max(1.0, 1.4 * scale)))
            p.setBrush(QColor("#232a34"))
            rect = QRectF(pt(x - rx, y - ry), pt(x + rx, y + ry))
            if shape == "pill":
                p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            else:
                p.drawEllipse(rect)
            f = QFont()
            f.setPointSizeF(max(5.5, 7.5 * scale))
            p.setFont(f)
            p.setPen(QPen(QColor("#8b949e")))
            p.drawText(rect, Qt.AlignCenter, M.KEY_NAMES.get(slot, "")[:4])

    def _state_colour(self, slot: int):
        """(outline, fill, text) for a slot's current state."""
        if slot in self.pressed:
            return QColor("#3fb950"), QColor(63, 185, 80, 90), QColor("#d3f9d8")
        if slot == self.selection:
            return QColor("#58a6ff"), QColor(31, 111, 235, 80), QColor("#cfe6ff")
        if slot in self.remapped:
            return QColor("#d29922"), QColor(210, 153, 34, 55), QColor("#f5d90a")
        if slot == self._hover:
            return QColor("#8b949e"), QColor(139, 148, 158, 45), QColor("#e6edf3")
        return QColor(120, 130, 145, 130), QColor(0, 0, 0, 0), QColor("#9aa4b0")

    def paintEvent(self, event):
        self._rebuild_rects()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        scale, ox, oy, top_y, bot_y = self._geometry()

        if not self._photo.isNull():
            target = QRectF(ox, oy, PHOTO_W * scale, PHOTO_H * scale)
            p.drawPixmap(target, self._photo, QRectF(self._photo.rect()))
        else:
            self._draw_schematic(p, ox, oy, scale)

        # sticks: draw the live position first, under the rings
        for slot, axis in ((240, "left"), (241, "right")):
            x, y, r, _ry, _ = HOTSPOTS[slot]
            dx = self.axes.get(f"{axis}_x", 0.0)
            dy = self.axes.get(f"{axis}_y", 0.0)
            if abs(dx) > 0.02 or abs(dy) > 0.02:
                cx = ox + (x + dx * r * 0.72) * scale
                cy = oy + (y + dy * r * 0.72) * scale
                kr = r * 0.42 * scale
                p.setPen(QPen(QColor("#3fb950"), max(1.5, 2 * scale)))
                p.setBrush(QColor(63, 185, 80, 120))
                p.drawEllipse(QRectF(cx - kr, cy - kr, kr * 2, kr * 2))

        # hotspots on the photo
        for slot, (x, y, rx, ry, shape) in HOTSPOTS.items():
            if shape == "ring":
                continue
            rect = self._rects.get(slot)
            if rect is None:
                continue
            emitting = slot in self.output and slot not in self.pressed
            active = (slot in self.pressed or slot == self.selection
                      or slot in self.remapped or slot == self._hover
                      or emitting)
            if not active:
                continue          # nothing drawn over an idle button
            outline, fill, _text = self._state_colour(slot)
            if emitting:
                outline, fill = QColor("#58a6ff"), QColor(31, 111, 235, 70)
            p.setPen(QPen(outline, max(1.4, 2.4 * scale)))
            p.setBrush(fill)
            if shape == "pill":
                p.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)
            else:
                p.drawEllipse(rect)
            tag = self.remapped.get(slot)
            if tag:
                f = QFont(); f.setPointSizeF(max(6.0, 7.5 * scale)); f.setBold(True)
                p.setFont(f)
                p.setPen(QPen(QColor("#f5d90a")))
                p.drawText(QRectF(rect.center().x() - 40, rect.bottom() - 1,
                                  80, 14), Qt.AlignCenter, tag)

        # chip rows
        for slots in (CHIPS_TOP, CHIPS_BOTTOM):
            for slot in slots:
                rect = self._rects.get(slot)
                if rect is None:
                    continue
                emitting = slot in self.output and slot not in self.pressed
                outline, fill, text = self._state_colour(slot)
                if emitting:
                    outline, fill = QColor("#58a6ff"), QColor(31, 111, 235, 70)
                    text = QColor("#cfe6ff")
                p.setPen(QPen(outline, 1.4))
                p.setBrush(fill if fill.alpha() else QColor("#1b212a"))
                p.drawRoundedRect(rect, 6, 6)
                f = QFont(); f.setPointSizeF(9); f.setBold(True)
                p.setFont(f)
                p.setPen(QPen(text))
                p.drawText(rect, Qt.AlignCenter, M.KEY_NAMES.get(slot, str(slot)))
                tag = self.remapped.get(slot)
                if tag:
                    f2 = QFont(); f2.setPointSizeF(7)
                    p.setFont(f2)
                    p.setPen(QPen(QColor("#f5d90a")))
                    p.drawText(QRectF(rect.x(), rect.bottom() - 9,
                                      rect.width(), 10), Qt.AlignCenter, tag)

        # a hint that the outer rows are not on the front face
        f = QFont(); f.setPointSizeF(7.5)
        p.setFont(f)
        p.setPen(QPen(QColor("#5b636e")))
        p.drawText(QRectF(0, top_y - CAPTION_H, self.width(), CAPTION_H),
                   Qt.AlignCenter, "shoulders and triggers")
        p.drawText(QRectF(0, bot_y + CHIP_H + 1, self.width(), CAPTION_H),
                   Qt.AlignCenter, "back paddles and system buttons")
        p.end()
