"""Small shared widgets."""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel,
                             QSizePolicy, QVBoxLayout, QWidget)


class Card(QFrame):
    """A titled panel."""

    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 16)
        outer.setSpacing(10)
        if title:
            lab = QLabel(title)
            lab.setObjectName("cardTitle")
            outer.addWidget(lab)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def add(self, w):
        self.body.addWidget(w)
        return w

    def add_layout(self, l):
        self.body.addLayout(l)
        return l


class FieldGrid(QWidget):
    """Two-column label/value grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(18)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        self._rows: dict[str, QLabel] = {}

    def set(self, name: str, value: str) -> None:
        if name in self._rows:
            self._rows[name].setText(value)
            return
        row = self.grid.rowCount()
        key = QLabel(name)
        key.setObjectName("fieldKey")
        val = QLabel(value)
        val.setObjectName("fieldValue")
        val.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.grid.addWidget(key, row, 0, Qt.AlignLeft | Qt.AlignTop)
        self.grid.addWidget(val, row, 1, Qt.AlignLeft | Qt.AlignTop)
        self._rows[name] = val

    def clear(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._rows.clear()


class BatteryBar(QWidget):
    """Six-segment battery indicator, matching the app's 0..6 levels."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._level = 0
        self._charging = False
        self.setFixedHeight(18)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self._cells = []
        for _ in range(6):
            cell = QFrame()
            cell.setFixedSize(16, 14)
            cell.setObjectName("battCell")
            row.addWidget(cell)
            self._cells.append(cell)
        self._label = QLabel("")
        self._label.setObjectName("fieldValue")
        row.addWidget(self._label)
        row.addStretch(1)

    def set_level(self, level: int, charging: bool = False) -> None:
        self._level, self._charging = level, charging
        for i, cell in enumerate(self._cells):
            on = i < level
            colour = "#3fb950" if level > 2 else "#d29922" if level > 1 else "#f85149"
            cell.setStyleSheet(
                f"#battCell {{ border-radius:3px; background:{colour if on else '#2d333b'}; }}")
        self._label.setText(f"{level}/6" + ("  charging" if charging else ""))


class HelpTip(QLabel):
    """A small "?" that carries the vendor's own explanation of a setting."""

    def __init__(self, summary: str, caveat: str = "", parent=None):
        super().__init__("?", parent)
        self.setObjectName("helpTip")
        self.setFixedSize(17, 17)
        self.setAlignment(Qt.AlignCenter)
        text = summary
        if caveat:
            text += "\n\n" + caveat
        self.setToolTip(self._wrap(text))
        # Deliberately no cursor override: the what's-this cursor reads as a
        # mode change rather than a hint.

    @staticmethod
    def _wrap(text: str, width: int = 62) -> str:
        import textwrap
        blocks = []
        for para in text.split("\n\n"):
            blocks.append("\n".join(textwrap.wrap(para.strip(), width)))
        return "\n\n".join(blocks)


class LabelledRow(QWidget):
    """A label, an optional help tip, then a control -- aligned in a grid.

    Rows built through :class:`SettingList` share one grid so their columns
    actually line up.
    """


class SettingList(QWidget):
    """Rows of (label + help + control) that share a single grid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(9)
        self.grid.setColumnStretch(3, 1)
        self._row = 0

    def add(self, label: str, control, summary: str = "", caveat: str = "",
            note: str = ""):
        from PyQt5.QtWidgets import QLabel as _QL
        lab = _QL(label)
        lab.setObjectName("fieldKey")
        self.grid.addWidget(lab, self._row, 0, Qt.AlignLeft | Qt.AlignVCenter)
        if summary:
            self.grid.addWidget(HelpTip(summary, caveat), self._row, 1,
                                Qt.AlignLeft | Qt.AlignVCenter)
        self.grid.addWidget(control, self._row, 2, Qt.AlignLeft | Qt.AlignVCenter)
        if note:
            n = _QL(note)
            n.setObjectName("hint")
            n.setWordWrap(True)
            self.grid.addWidget(n, self._row, 3, Qt.AlignLeft | Qt.AlignVCenter)
        self._row += 1
        return control

    def add_widget(self, widget, span: int = 4):
        self.grid.addWidget(widget, self._row, 0, 1, span)
        self._row += 1
        return widget


class Bar(QWidget):
    """A horizontal 0..1 fill, for trigger travel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self.setFixedHeight(14)
        self.setMinimumWidth(140)

    def set_value(self, value: float) -> None:
        value = max(0.0, min(1.0, value))
        if abs(value - self._value) > 0.002:
            self._value = value
            self.update()

    def paintEvent(self, event):
        from PyQt5.QtGui import QColor, QPainter
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect().adjusted(0, 2, -1, -3)
        p.setPen(QColor("#30363d"))
        p.setBrush(QColor("#0d1117"))
        p.drawRoundedRect(r, 5, 5)
        if self._value > 0.002:
            fill = QRectF(r.x() + 1, r.y() + 1,
                          (r.width() - 2) * self._value, r.height() - 2)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#3fb950") if self._value > 0.08
                       else QColor("#39414d"))
            p.drawRoundedRect(fill, 4, 4)
        p.end()
