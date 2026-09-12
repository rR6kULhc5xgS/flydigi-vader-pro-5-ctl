"""Main window."""

from __future__ import annotations

import sys

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from .. import commands as C
from ..enums import ConnectType
from ..updates import check_firmware
from .pages import (MacrosPage, Page, ProfilesPage, SettingsPage,
                    TestPage)
from .widgets import BatteryBar, Card, FieldGrid
from .worker import start_worker

STYLE = """
QWidget { background: #0d1117; color: #c9d1d9; font-size: 13px; }
#sidebar { background: #010409; border-right: 1px solid #21262d; }
#sidebar::item { padding: 11px 18px; border: none; }
#sidebar::item:selected { background: #161b22; color: #58a6ff; }
#card { background: #161b22; border: 1px solid #21262d; border-radius: 8px; }
#cardTitle { font-size: 14px; font-weight: 600; color: #e6edf3; }
#fieldKey { color: #7d8590; }
#fieldValue { color: #e6edf3; }
#pageTitle { font-size: 21px; font-weight: 700; color: #e6edf3; }
#hint { color: #7d8590; }
#helpTip { color: #8b949e; background: #21262d; border: 1px solid #30363d;
           border-radius: 9px; font-weight: 700; font-size: 11px; }
#warn { color: #d29922; }
QPushButton { background: #21262d; border: 1px solid #30363d; border-radius: 6px;
              padding: 7px 14px; color: #c9d1d9; }
QPushButton:hover { background: #30363d; border-color: #8b949e; }
QPushButton:disabled { color: #484f58; background: #161b22; border-color: #21262d; }
QPushButton#primary { background: #238636; border-color: #2ea043; color: white;
                      font-weight: 600; }
QPushButton#primary:hover { background: #2ea043; }
QPushButton#primary:disabled { background: #1b3d24; border-color: #23482c;
                               color: #5d7f66; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 17px; height: 17px; border-radius: 4px;
                       border: 1px solid #30363d; background: #0d1117; }
QCheckBox::indicator:checked { background: #238636; border-color: #2ea043; }
QCheckBox:disabled { color: #484f58; }
QComboBox, QSpinBox { background: #0d1117; border: 1px solid #30363d;
                      border-radius: 6px; padding: 4px 8px; min-width: 96px; }
QComboBox:disabled, QSpinBox:disabled { color: #484f58; background: #161b22; }
QComboBox QAbstractItemView { background: #161b22; border: 1px solid #30363d;
                              selection-background-color: #1f6feb; }
QScrollArea { background: transparent; }
QScrollBar:vertical { background: #0d1117; width: 10px; }
QScrollBar::handle:vertical { background: #30363d; border-radius: 5px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
"""


class DevicePage(Page):
    def __init__(self, submit=None):
        super().__init__("Device")
        self.submit = submit
        card = Card("Identity")
        self.fields = FieldGrid()
        card.add(self.fields)
        self.outer.addWidget(card)

        power = Card("Power")
        self.battery = BatteryBar()
        power.add(self.battery)
        self.outer.addWidget(power)

        fw = Card("Firmware")
        self.fw_fields = FieldGrid()
        fw.add(self.fw_fields)
        row = QHBoxLayout()
        self.check_btn = QPushButton("Check for updates")
        self.check_btn.clicked.connect(self._check)
        row.addWidget(self.check_btn)
        self.update_label = QLabel("")
        self.update_label.setObjectName("hint")
        self.update_label.setWordWrap(True)
        row.addWidget(self.update_label, 1)
        fw.add_layout(row)
        hint = QLabel("This tool never writes firmware. An available update "
                      "has to be applied from the Windows app.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        fw.add(hint)
        self.outer.addWidget(fw)
        self.finish()

    def _check(self) -> None:
        if self.submit is None:
            return
        self.check_btn.setEnabled(False)
        self.update_label.setText("Contacting Flydigi\u2026")
        self.submit("updates", lambda ctl: check_firmware(
            "f5", C.read_info(ctl).device_type, C.read_info(ctl)))

    def show_updates(self, updates) -> None:
        self.check_btn.setEnabled(True)
        pending = [u for u in updates if u.newer_available]
        if not pending:
            self.update_label.setText("Everything is up to date.")
            return
        self.update_label.setText("  ".join(
            f"{u.label}: {u.installed or '-'} \u2192 {u.latest}" for u in pending)
            + "   (update available)")

    def update_failed(self, message: str) -> None:
        self.check_btn.setEnabled(True)
        self.update_label.setText(message)

    def update_info(self, info) -> None:
        self.fields.set("Model", info.name)
        self.fields.set("Device type", str(info.device_type))
        try:
            conn = ConnectType(info.connect_type).name.title()
        except ValueError:
            conn = str(info.connect_type)
        self.fields.set("Connection", conn)
        if info.uid:
            self.fields.set("UID", info.uid)
        if info.mac and info.mac != "00:00:00:00":
            self.fields.set("MAC", info.mac)
        self.battery.set_level(info.battery,
                               info.connect_type == ConnectType.WIRED)
        for label, value in (("Controller", info.firmware),
                             ("Switch", info.switch_version),
                             ("Dongle", info.dongle_version),
                             ("Trigger", info.trigger_version),
                             ("Screen", info.screen_version),
                             ("ADC", info.adc_version),
                             ("NearLink", info.nearlink_version)):
            if value:
                self.fw_fields.set(label, value)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Flydigi Control")
        self.resize(1000, 720)

        self.sidebar = QListWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(184)
        self.stack = QStackedWidget()

        self.device_page = DevicePage(self._submit)
        self.profiles_page = ProfilesPage(self._submit)
        self.test_page = TestPage(self._set_streaming, self._submit)
        self.macros_page = MacrosPage(self._submit, self._set_streaming)
        self.settings_page = SettingsPage(self._submit)

        for name, page in (("Device", self.device_page),
                           ("Profiles", self.profiles_page),
                           ("Test", self.test_page),
                           ("Macros", self.macros_page),
                           ("Settings", self.settings_page)):
            self.sidebar.addItem(QListWidgetItem(name))
            self.stack.addWidget(page)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.sidebar.setCurrentRow(0)

        self.status = QLabel("Connecting…")
        self.status.setObjectName("hint")

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.stack, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(26, 0, 26, 10)
        bar.addWidget(self.status)
        bar.addStretch(1)
        right.addLayout(bar)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addWidget(self.sidebar)
        container = QWidget()
        container.setLayout(right)
        row.addWidget(container, 1)

        self.thread, self.worker = start_worker()
        self.worker.connected.connect(self._on_connected)
        self.worker.disconnected.connect(self._on_disconnected)
        self.worker.result.connect(self._on_result)
        self.worker.failed.connect(self._on_failed)
        self.worker.input_state.connect(self.test_page.apply_state)
        self.worker.input_state.connect(self.macros_page.feed)
        self.worker.stream_stopped.connect(self._on_stream_stopped)
        self.sidebar.currentRowChanged.connect(self._on_page_changed)
        self.thread.start()

        self.poll = QTimer(self)
        self.poll.setInterval(5000)
        self.poll.timeout.connect(lambda: self._submit("info", C.read_info))

    def _submit(self, tag, fn):
        self.worker.submit(tag, fn)

    def _set_streaming(self, on: bool) -> None:
        # Polling device info would contend with the input stream.
        if on:
            self.poll.stop()
        else:
            self.poll.start()
        self.worker.set_streaming(on)

    def _on_page_changed(self, row: int) -> None:
        # Leaving the Test page stops the stream so the controller does not
        # keep pushing 490 reports a second for nothing.
        page = self.stack.widget(row)
        if page is self.test_page:
            self.test_page.refresh()
        if page is not self.test_page:
            self.test_page.stopped()
        if page is not self.macros_page and self.macros_page.recording:
            self.macros_page.record_btn.setChecked(False)

    def _on_stream_stopped(self, reason: str) -> None:
        self.test_page.stopped(reason)
        self.status.setText(f"Live input stopped: {reason}")
        self.poll.start()

    def _on_connected(self, info):
        self.device_page.update_info(info)
        self.status.setText(
            f"{info.name} on {self.worker.ctl.iface.path} — firmware {info.firmware}")
        self.settings_page.refresh()
        self.profiles_page.refresh()
        self.macros_page.refresh()
        self.test_page.refresh()
        self.poll.start()

    def _on_disconnected(self, reason):
        self.status.setText(reason)
        self.profiles_page.setEnabled(False)
        self.settings_page.setEnabled(False)

    def _on_result(self, tag, value):
        if tag == "info":
            self.device_page.update_info(value)
        elif tag == "updates":
            self.device_page.show_updates(value)
        elif tag == "status":
            self.settings_page.apply_status(value)
        elif tag == "hardware":
            self.settings_page.apply_hardware(value)
        elif tag == "profile_versions":
            self.profiles_page.apply_versions(value)
        elif tag == "profile_blob":
            self.profiles_page.apply_blob(value)
        elif tag == "activate":
            self.profiles_page.apply_versions(value)
            self.status.setText(f"Profile {value.active + 1} is now active.")
        elif tag == "write_profile":
            index, readback, versions, dv, ok = value
            self.profiles_page.apply_blob((index, readback, versions))
            self.profiles_page.apply_versions(versions)
            self.status.setText(
                f"Profile {index + 1} written (dataVersion {dv})"
                + ("" if ok else " — READ-BACK MISMATCH"))
            if not ok:
                QMessageBox.warning(
                    self, "Verification failed",
                    "The profile was written but read back differently. "
                    "Check the controller before relying on this profile.")
        elif tag == "test_mapping":
            self.test_page.apply_mapping(value)
        elif tag == "macro_blob":
            self.macros_page.apply_macros(value)
        elif tag == "macro_saved":
            self.macros_page.apply_macros(value)
            self.macros_page._discard()
            self.status.setText(f"Macros updated on profile {value[0] + 1}.")
            self.profiles_page.refresh()
        elif tag == "bind_ns":
            index, ok, versions = value
            self.profiles_page.apply_versions(versions)
            if ok:
                self.status.setText(
                    f"Profile {index + 1} bound to Nintendo Switch mode. "
                    "Verify in NS mode — it cannot be read back over USB.")
            else:
                self.status.setText("The controller did not acknowledge the "
                                    "Switch-mode binding.")

    def _on_failed(self, tag, message):
        line = message.strip().splitlines()[-1]
        self.status.setText(f"{tag} failed: {line}")
        if tag == "updates":
            self.device_page.update_failed(line)
        if tag in ("write_profile", "bind_ns"):
            QMessageBox.critical(self, "Write failed", message)
            self.profiles_page.refresh()

    def closeEvent(self, event):
        self.poll.stop()
        self.worker.set_streaming(False)
        self.worker.stop()
        self.thread.quit()
        self.thread.wait(2000)
        super().closeEvent(event)


#: Warnings that come from the desktop environment rather than this app.
#: KDE writes Qt6-style 16-field font descriptions into kdeglobals, which
#: Qt5's QFont::fromString rejects, and Wayland has no window-activation
#: protocol for QWindow::requestActivate. Neither is actionable here, and
#: both are noisy, so they are filtered out.
_IGNORED_QT_WARNINGS = (
    "QFont::fromString: Invalid description",
    "Wayland does not support QWindow::requestActivate",
)


def _install_message_filter() -> None:
    from PyQt5.QtCore import qInstallMessageHandler

    def handler(mode, context, message):
        if any(noise in message for noise in _IGNORED_QT_WARNINGS):
            return
        sys.stderr.write(message + "\n")

    qInstallMessageHandler(handler)


def _pick_font() -> "QFont":
    """Choose a UI font that is actually installed."""
    from PyQt5.QtGui import QFont, QFontDatabase

    available = set(QFontDatabase().families())
    for family in ("Inter", "Cantarell", "Noto Sans", "DejaVu Sans"):
        if family in available:
            return QFont(family, 10)
    return QFont()


def _pick_mono() -> str:
    from PyQt5.QtGui import QFontDatabase

    available = set(QFontDatabase().families())
    for family in ("JetBrains Mono", "Noto Sans Mono", "DejaVu Sans Mono",
                   "Liberation Mono", "monospace"):
        if family in available:
            return family
    return "monospace"


def main(argv=None) -> int:
    _install_message_filter()
    app = QApplication(argv or sys.argv)
    app.setApplicationName("Flydigi Control")
    app.setFont(_pick_font())
    app.setStyleSheet(
        STYLE + f'\n#fieldValue {{ font-family: "{_pick_mono()}"; }}\n')
    win = MainWindow()
    win.show()
    return app.exec_()
