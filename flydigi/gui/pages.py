"""Profile / mapping editor, live tester and settings pages."""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QSpinBox,
                             QVBoxLayout, QWidget)

from .. import commands as C
from .. import help_text as H
from .. import macros as MAC
from .. import mapping as M
from .controller_view import ControllerView
from .widgets import Bar, Card, FieldGrid, HelpTip, SettingList


class Page(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(26, 22, 26, 22)
        self.outer.setSpacing(14)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        self.outer.addWidget(heading)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName("hint")
            sub.setWordWrap(True)
            self.outer.addWidget(sub)

    def finish(self):
        self.outer.addStretch(1)


# --------------------------------------------------------------- mapping

def _target_choices() -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = [("default (itself)", None)]
    for key_id in M.PHYSICAL_KEYS:
        out.append((M.KEY_NAMES[key_id], key_id))
    return out


class ProfilesPage(Page):
    """Pick a button on the controller, then say what it should do."""

    def __init__(self, submit):
        super().__init__(
            "Profiles",
            "The controller stores four configurations on itself, so they "
            "work on any machine with nothing running. Click a button on the "
            "controller below to change what it does.")
        self.submit = submit
        self.blob: M.MappingBlob | None = None
        self.original = b""
        self.loaded_index = 0
        self.versions: C.ProfileVersions | None = None
        self.slot: int | None = None
        self._loading = True

        # -- profile row
        top = Card("Onboard profile")
        row = QHBoxLayout()
        self.picker = QComboBox()
        for i in range(C.PROFILE_COUNT):
            self.picker.addItem(f"Profile {i + 1}", i)
        self.picker.currentIndexChanged.connect(self._on_pick)
        row.addWidget(self.picker)
        self.activate_btn = QPushButton("Make active")
        self.activate_btn.clicked.connect(self._activate)
        row.addWidget(self.activate_btn)
        self.ns_btn = QPushButton("Apply to NS mode")
        self.ns_btn.clicked.connect(self._bind_ns)
        row.addWidget(self.ns_btn)
        row.addWidget(HelpTip(*H.setting("ns_mode")))
        row.addStretch(1)
        top.add_layout(row)
        self.summary = QLabel("")
        self.summary.setObjectName("hint")
        self.summary.setWordWrap(True)
        top.add(self.summary)
        self.outer.addWidget(top)

        # -- the controller
        editor = Card("Button mapping")
        self.view = ControllerView()
        self.view.selected.connect(self._select_slot)
        editor.add(self.view)

        legend = QLabel("Click a button to edit it.   Amber = remapped    Blue = selected    Green = pressed")
        legend.setObjectName("hint")
        editor.add(legend)

        # -- detail for the selected button
        self.detail = SettingList()
        self.detail_title = QLabel("Select a button")
        self.detail_title.setObjectName("cardTitle")
        editor.add(self.detail_title)

        self.target = QComboBox()
        for name, value in _target_choices():
            self.target.addItem(name, value)
        self.target.currentIndexChanged.connect(self._edited)
        self.detail.add("Emits", self.target,
                        "Which button the controller reports when this "
                        "physical button is pressed.")

        self.turbo = QSpinBox()
        self.turbo.setRange(0, 40)
        self.turbo.setSpecialValueText("off")
        self.turbo.setSuffix(" Hz")
        self.turbo.valueChanged.connect(self._edited)
        self.detail.add("Turbo", self.turbo, *H.setting("turbo_key"))

        self.mode = QComboBox()
        self.mode.addItem("while held", M.TurboMode.PRESS)
        self.mode.addItem("toggle on click", M.TurboMode.CLICK)
        self.mode.currentIndexChanged.connect(self._edited)
        self.detail.add("Turbo mode", self.mode,
                        "While held repeats only as long as you hold the "
                        "button. Toggle starts and stops on each press.")

        self.detail_note = QLabel("")
        self.detail_note.setObjectName("warn")
        self.detail_note.setWordWrap(True)
        self.detail.add_widget(self.detail_note)
        editor.add(self.detail)

        actions = QHBoxLayout()
        self.save_btn = QPushButton("Write to controller")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self._revert)
        self.revert_btn.setEnabled(False)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.revert_btn)
        actions.addWidget(HelpTip(*H.setting("onboard_profile")))
        self.dirty_label = QLabel("")
        self.dirty_label.setObjectName("warn")
        actions.addWidget(self.dirty_label)
        actions.addStretch(1)
        editor.add_layout(actions)
        self.outer.addWidget(editor)
        self.finish()
        self._set_detail_enabled(False)
        self._loading = False

    # -- loading --------------------------------------------------------

    def refresh(self) -> None:
        self.submit("profile_versions", C.read_profile_versions)

    def apply_versions(self, versions: C.ProfileVersions) -> None:
        self.versions = versions
        self._loading = True
        for i in range(C.PROFILE_COUNT):
            marks = []
            if i == versions.active:
                marks.append("active")
            if versions.ns_profile == i:
                marks.append("Switch")
            text = f"Profile {i + 1}"
            if marks:
                text += "  (" + ", ".join(marks) + ")"
            self.picker.setItemText(i, text)
        self._loading = False
        if self.blob is None:
            self.load_profile(self.picker.currentData())

    def load_profile(self, index: int) -> None:
        self.submit("profile_blob",
                    lambda ctl, i=index: (i, C.read_mapping_blob(ctl, i),
                                          C.read_profile_versions(ctl)))

    def apply_blob(self, payload) -> None:
        index, raw, versions = payload
        self.loaded_index = index
        self.blob = M.MappingBlob(raw)
        self.original = bytes(self.blob.data)
        self.versions = versions
        self._loading = True
        self.picker.setCurrentIndex(index)
        self._loading = False
        dv = (versions.data_versions[index]
              if index < len(versions.data_versions) else None)
        state = "factory default" if dv == C.UNWRITTEN else f"dataVersion {dv}"
        self.summary.setText(f"{self.blob.title}  —  {state}")
        self._refresh_view()
        self._select_slot(self.slot if self.slot is not None else 4)
        self._set_dirty(False)

    def _refresh_view(self) -> None:
        if self.blob is None:
            return
        remapped = {}
        for s in self.blob.key_table():
            if s.slot not in M.KEY_NAMES or s.is_identity:
                continue
            if s.map_type == M.MapType.MACRO:
                remapped[s.slot] = "macro"
            elif s.map_type == M.MapType.CONTINUOUS:
                remapped[s.slot] = f"{M.key_name(s.target)}×"
            elif s.keyid == M.KEY_KEYBOARD_MOUSE:
                remapped[s.slot] = "kbd"
            else:
                remapped[s.slot] = M.key_name(s.target)
        self.view.set_remapped(remapped)

    # -- selection ------------------------------------------------------

    def _set_detail_enabled(self, on: bool) -> None:
        for w in (self.target, self.turbo, self.mode):
            w.setEnabled(on)

    def _select_slot(self, slot: int) -> None:
        if self.blob is None:
            return
        self.slot = slot
        self.view.set_selection(slot)
        entry = self.blob.key_slot(slot)
        self._loading = True
        note = ""
        editable = True
        if entry.keyid == M.KEY_KEYBOARD_MOUSE:
            note = H.setting("keyboard_mapping")[0]
            editable = False
        elif entry.map_type == M.MapType.MACRO:
            note = ("This button runs a macro stored on the controller. "
                    "Edit it on the Macros page.")
            editable = False
        target = None if entry.is_identity else entry.target
        idx = self.target.findData(target)
        self.target.setCurrentIndex(idx if idx >= 0 else 0)
        self.turbo.setValue(entry.turbo
                            if entry.map_type == M.MapType.CONTINUOUS else 0)
        midx = self.mode.findData(entry.type)
        self.mode.setCurrentIndex(midx if midx >= 0 else 0)
        self.detail_title.setText(f"{M.key_name(slot)}")
        self.detail_note.setText(note)
        self._set_detail_enabled(editable)
        self.mode.setEnabled(editable and self.turbo.value() > 0)
        self._loading = False

    def _edited(self, *_):
        if self._loading or self.blob is None or self.slot is None:
            return
        self.mode.setEnabled(self.turbo.value() > 0)
        target = self.target.currentData()
        hz = self.turbo.value()
        if hz > 0:
            dest = target if target is not None else self.slot
            self.blob.set_turbo(self.slot, dest, hz, self.mode.currentData())
        else:
            self.blob.remap(self.slot, target)
        self._refresh_view()
        self._set_dirty(bytes(self.blob.data) != self.original)

    def _set_dirty(self, dirty: bool) -> None:
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)
        self.dirty_label.setText("unsaved changes" if dirty else "")

    # -- actions --------------------------------------------------------

    def _on_pick(self, _index: int) -> None:
        if self._loading:
            return
        if self.save_btn.isEnabled():
            reply = QMessageBox.question(
                self, "Discard changes?",
                "This profile has unsaved changes. Switch anyway?",
                QMessageBox.Discard | QMessageBox.Cancel)
            if reply != QMessageBox.Discard:
                self._loading = True
                self.picker.setCurrentIndex(self.loaded_index)
                self._loading = False
                return
        self.load_profile(self.picker.currentData())

    def _revert(self) -> None:
        if self.blob is not None and self.versions is not None:
            self.apply_blob((self.loaded_index, self.original, self.versions))

    def _save(self) -> None:
        if self.blob is None:
            return
        index, data = self.loaded_index, bytes(self.blob.data)
        self.save_btn.setEnabled(False)
        self.submit("write_profile",
                    lambda ctl: _write_and_reload(ctl, index, data))

    def _activate(self) -> None:
        index = self.picker.currentData()
        self.submit("activate", lambda ctl: (C.activate_profile(ctl, index),
                                             C.read_profile_versions(ctl))[1])

    def _bind_ns(self) -> None:
        index = self.picker.currentData()
        title = self.blob.title if self.blob else f"Profile {index + 1}"
        summary, caveat = H.setting("ns_mode")
        if QMessageBox.question(
                self, "Apply to Nintendo Switch mode",
                f"Make “{title}” the profile the controller uses in Nintendo "
                f"Switch mode?\n\n{summary}\n\n{caveat}",
                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        self.submit("bind_ns", lambda ctl: _bind_ns(ctl, index))


def _write_and_reload(ctl, index: int, data: bytes):
    before = C.read_profile_versions(ctl).active
    dv = C.write_profile(ctl, index, data)
    readback = C.read_mapping_blob(ctl, index)
    if C.read_profile_versions(ctl).active != before:
        C.activate_profile(ctl, before)
    versions = C.read_profile_versions(ctl)
    ok = readback[:M.OFF_DATA_VERSION] == data[:M.OFF_DATA_VERSION]
    return (index, readback, versions, dv, ok)


def _bind_ns(ctl, index: int):
    before = C.read_profile_versions(ctl).active
    blob = M.MappingBlob(C.read_mapping_blob(ctl, index))
    C.activate_profile(ctl, index)
    result = C.save_as_ns_profile(ctl, index, data_version=blob.data_version)
    if before != index:
        C.activate_profile(ctl, before)
    return (index, result is not None, C.read_profile_versions(ctl))


# ------------------------------------------------------------------ tester

class TestPage(Page):
    """Live input, so a mapping or a macro can actually be verified."""

    def __init__(self, on_toggle, submit=None):
        super().__init__(
            "Test",
            "Press buttons and watch them here. Green is the button you "
            "physically pressed; blue is what the controller actually emits "
            "as a result. Press M5 and you should see M5 go green and "
            "whatever it is mapped to go blue — and a macro button will play "
            "its whole sequence out in blue.")
        self.on_toggle = on_toggle
        self.submit = submit
        self.mapping: M.MappingBlob | None = None

        card = Card()
        row = QHBoxLayout()
        self.toggle = QPushButton("Start testing")
        self.toggle.setObjectName("primary")
        self.toggle.setCheckable(True)
        self.toggle.toggled.connect(self._toggled)
        row.addWidget(self.toggle)
        self.state = QLabel("")
        self.state.setObjectName("hint")
        row.addWidget(self.state)
        row.addStretch(1)
        card.add_layout(row)

        self.view = ControllerView()
        self.view.live = True
        card.add(self.view)

        readout = SettingList()
        self.pressed = QLabel("—")
        self.pressed.setObjectName("fieldValue")
        self.pressed.setWordWrap(True)
        readout.add("Pressed", self.pressed,
                    "The physical buttons, read from the controller's raw "
                    "input stream before any remapping is applied.")
        self.emitted = QLabel("—")
        self.emitted.setObjectName("fieldValue")
        self.emitted.setWordWrap(True)
        readout.add("Registers as", self.emitted,
                    "What the controller actually sends, read from the "
                    "kernel's gamepad device. This is what a game sees, so "
                    "it reflects remaps, turbo and macro playback.")
        self.translation = QLabel("")
        self.translation.setObjectName("warn")
        self.translation.setWordWrap(True)
        readout.add_widget(self.translation)
        card.add(readout)
        self.outer.addWidget(card)

        analog = Card("Analog")
        self.fields = SettingList()
        self.lt = Bar(); self.rt = Bar()
        self.fields.add("Left trigger", self.lt)
        self.fields.add("Right trigger", self.rt)
        self.sticks = FieldGrid()
        self.fields.add_widget(self.sticks)
        analog.add(self.fields)
        self.outer.addWidget(analog)
        self.finish()

    def refresh(self) -> None:
        """Load the active profile's mapping so remaps can be annotated."""
        if self.submit is None:
            return
        self.submit("test_mapping", lambda ctl: C.read_mapping_blob(
            ctl, C.read_profile_versions(ctl).active))

    def apply_mapping(self, raw: bytes) -> None:
        self.mapping = M.MappingBlob(raw)
        remapped = {}
        for slot in self.mapping.key_table():
            if slot.slot not in M.KEY_NAMES or slot.is_identity:
                continue
            if slot.map_type == M.MapType.MACRO:
                remapped[slot.slot] = "macro"
            elif slot.map_type == M.MapType.CONTINUOUS:
                remapped[slot.slot] = f"{M.key_name(slot.target)}×"
            elif slot.keyid == M.KEY_KEYBOARD_MOUSE:
                remapped[slot.slot] = "kbd"
            else:
                remapped[slot.slot] = M.key_name(slot.target)
        self.view.set_remapped(remapped)

    def _toggled(self, on: bool) -> None:
        self.toggle.setText("Stop testing" if on else "Start testing")
        self.state.setText("streaming at ~490 Hz" if on else "")
        if not on:
            self.view.set_pressed(set())
            self.view.set_output(set())
            self.view.set_axes({})
            self.pressed.setText("—")
            self.emitted.setText("—")
            self.translation.setText("")
        self.on_toggle(on)

    def apply_state(self, st) -> None:
        self.view.set_pressed(st.buttons)
        self.view.set_output(st.output_buttons)
        self.view.set_axes(st.axes)
        names = st.pressed_names()
        self.pressed.setText("  ".join(names) if names else "nothing pressed")
        if st.output_available:
            out = st.output_names()
            self.emitted.setText("  ".join(out) if out else "nothing")
        else:
            self.emitted.setText("kernel gamepad not readable")

        # Spell out the translation for anything whose output differs from
        # the button that was pressed.
        notes = []
        for slot in sorted(st.buttons):
            configured = None
            if self.mapping is not None:
                entry = self.mapping.key_slot(slot)
                if entry.map_type == M.MapType.MACRO:
                    configured = "macro"
                elif not entry.is_identity:
                    configured = M.key_name(entry.target)
            if configured:
                notes.append(f"{M.key_name(slot)} → {configured}")
        extra = sorted(st.output_buttons - st.buttons)
        if extra and not notes:
            notes.append("emitting " + ", ".join(M.key_name(s) for s in extra))
        self.translation.setText("     ".join(notes))
        self.lt.set_value(st.left_trigger)
        self.rt.set_value(st.right_trigger)
        self.sticks.set("Left stick", f"{st.left_x:+.3f}, {st.left_y:+.3f}")
        self.sticks.set("Right stick", f"{st.right_x:+.3f}, {st.right_y:+.3f}")
        self.sticks.set("Gyro", "  ".join(f"{v:+6d}" for v in st.gyro))
        self.sticks.set("Accel", "  ".join(f"{v:+6d}" for v in st.accel))
        if st.unknown_bits:
            self.sticks.set("Unlabelled bits",
                            ", ".join(f"byte{o} bit{b}"
                                      for o, b in sorted(st.unknown_bits)))

    def stopped(self, reason: str = "") -> None:
        if self.toggle.isChecked():
            self.toggle.setChecked(False)
        if reason:
            self.state.setText(reason)


# ------------------------------------------------------------------ settings

class SettingsPage(Page):
    def __init__(self, submit):
        super().__init__("Settings")
        self.submit = submit
        self._loading = True

        owner = Card("Mapping ownership")
        self.owner_list = SettingList()
        self.third_party = QCheckBox("Allow third-party apps to take over")
        self.third_party.toggled.connect(self._on_third_party)
        self.owner_list.add("Third-party mapping", self.third_party,
                            *H.setting("third_party"))
        self.owner_label = QLabel("")
        self.owner_label.setObjectName("warn")
        self.owner_label.setWordWrap(True)
        self.owner_list.add_widget(self.owner_label)
        owner.add(self.owner_list)
        self.outer.addWidget(owner)

        power = Card("Power")
        self.power_list = SettingList()
        self.sleep = QComboBox()
        for minutes, label in C.SLEEP_CHOICES:
            self.sleep.addItem(label, minutes)
        self.sleep.currentIndexChanged.connect(self._on_sleep)
        self.power_list.add("Sleep after", self.sleep, *H.setting("sleep"))
        power.add(self.power_list)
        self.outer.addWidget(power)

        feats = Card("Hardware features")
        self.feature_list = SettingList()
        self.feature_boxes: dict[str, tuple[QCheckBox, int]] = {}
        for label, sub, *_ in C.FEATURE_TABLE:
            box = QCheckBox()
            box.toggled.connect(
                lambda checked, s=sub, l=label: self._on_feature(s, l, checked))
            summary, caveat = H.feature(label)
            self.feature_list.add(label, box, summary, caveat)
            self.feature_boxes[label] = (box, sub)
        feats.add(self.feature_list)
        self.outer.addWidget(feats)

        sticks = Card("Sticks")
        self.stick_list = SettingList()
        self.precision = QLabel("—"); self.precision.setObjectName("fieldValue")
        self.sensitivity = QLabel("—"); self.sensitivity.setObjectName("fieldValue")
        self.rate = QLabel("—"); self.rate.setObjectName("fieldValue")
        self.stick_list.add("Accuracy", self.precision, *H.setting("precision"))
        self.stick_list.add("Centre sensitivity", self.sensitivity,
                            *H.setting("sensitivity"))
        self.stick_list.add("Polling rate", self.rate, *H.setting("report_rate"))
        sticks.add(self.stick_list)
        self.outer.addWidget(sticks)
        self.finish()
        self._loading = False

    def refresh(self) -> None:
        self.submit("status", C.read_data_report_status)
        self.submit("hardware", C.read_hardware_status)

    def apply_status(self, st) -> None:
        self._loading = True
        self.third_party.setChecked(st.third_party_control)
        self._loading = False
        self.owner_label.setText(
            f"Currently held by “{st.controlled_by}”" if st.controlled_by else "")

    def apply_hardware(self, hw) -> None:
        self._loading = True
        idx = self.sleep.findData(hw.sleep_minutes)
        if idx < 0:
            self.sleep.addItem(C.sleep_label(hw.sleep_minutes), hw.sleep_minutes)
            idx = self.sleep.findData(hw.sleep_minutes)
        self.sleep.setCurrentIndex(idx)
        for label, (box, _sub) in self.feature_boxes.items():
            usable, enabled = hw.features.get(label, (False, False))
            box.setChecked(enabled)
            box.setEnabled(usable)
            box.setText("" if usable else "not supported on this controller")
            if not usable:
                box.setToolTip("The controller reports this feature as "
                               "unavailable, so the switch is disabled.")
        self.precision.setText(
            C.JOYSTICK_PRECISION.get(hw.joystick_precision,
                                     str(hw.joystick_precision))
            if hw.precision_usable else "not configurable")
        self.sensitivity.setText(str(hw.joystick_sensitivity)
                                 if hw.sensitivity_usable else "not configurable")
        self.rate.setText(C.REPORT_RATES.get(hw.report_rate, str(hw.report_rate))
                          if hw.report_rate_usable else "not configurable")
        self._loading = False

    def _on_third_party(self, checked: bool) -> None:
        if self._loading:
            return
        self.submit("status", lambda ctl: (C.set_third_party_control(ctl, checked),
                                           C.read_data_report_status(ctl))[1])

    def _on_sleep(self, _index: int) -> None:
        if self._loading:
            return
        minutes = self.sleep.currentData()
        self.submit("hardware", lambda ctl: (C.set_sleep_time(ctl, minutes),
                                             C.read_hardware_status(ctl))[1])

    def _on_feature(self, sub: int, label: str, checked: bool) -> None:
        if self._loading:
            return
        self.submit("hardware", lambda ctl: (C.set_feature(ctl, sub, checked),
                                             C.read_hardware_status(ctl))[1])


# ------------------------------------------------------------------- macros

class MacrosPage(Page):
    """Record a sequence of buttons and store it on the controller."""

    def __init__(self, submit, set_streaming):
        super().__init__(
            "Macros",
            "A macro is a recorded sequence of button presses. It is stored "
            "on the controller and replayed by it, so nothing needs to run "
            "on this computer. Macros can only press controller buttons, "
            "not keyboard keys.")
        self.submit = submit
        self.set_streaming = set_streaming
        self.index = 0
        self.steps: list = []
        self.recording = False
        self._held: set[int] = set()
        self._last_t = None
        self._loading = True

        top = Card("Stored macros")
        row = QHBoxLayout()
        self.picker = QComboBox()
        for i in range(C.PROFILE_COUNT):
            self.picker.addItem(f"Profile {i + 1}", i)
        self.picker.currentIndexChanged.connect(self._on_pick)
        row.addWidget(self.picker)
        self.refresh_btn = QPushButton("Reload")
        self.refresh_btn.clicked.connect(self.refresh)
        row.addWidget(self.refresh_btn)
        self.remove_btn = QPushButton("Remove selected")
        self.remove_btn.clicked.connect(self._remove)
        self.remove_btn.setEnabled(False)
        row.addWidget(self.remove_btn)
        row.addStretch(1)
        top.add_layout(row)
        self.existing = QComboBox()
        self.existing.currentIndexChanged.connect(
            lambda i: self.remove_btn.setEnabled(self.existing.currentData() is not None))
        top.add(self.existing)
        self.listing = QLabel("")
        self.listing.setObjectName("fieldValue")
        self.listing.setWordWrap(True)
        top.add(self.listing)
        self.outer.addWidget(top)

        rec = Card("Record a macro")
        form = SettingList()
        self.button = QComboBox()
        for key_id in M.PHYSICAL_KEYS:
            self.button.addItem(M.KEY_NAMES[key_id], key_id)
        idx = self.button.findData(18)
        if idx >= 0:
            self.button.setCurrentIndex(idx)
        form.add("Run it with", self.button,
                 "The physical button that plays the macro back. It is "
                 "ignored while recording so it does not record itself.")
        self.name = QLineEdit()
        self.name.setMaxLength(19)
        self.name.setPlaceholderText("optional name")
        form.add("Name", self.name, "Stored on the controller, up to 19 bytes.")
        self.kind = QComboBox()
        self.kind.addItem("play once per press", MAC.EnableType.ONCE)
        self.kind.addItem("repeat while held", MAC.EnableType.PRESS)
        self.kind.addItem("toggle on/off", MAC.EnableType.CLICK)
        form.add("Behaviour", self.kind, *H.setting("macro"))
        self.interval = QSpinBox()
        self.interval.setRange(0, 5000)
        self.interval.setSuffix(" ms")
        self.interval.setSpecialValueText("none")
        form.add("Gap between repeats", self.interval,
                 "Only matters for the repeating behaviours.")
        rec.add(form)

        actions = QHBoxLayout()
        self.record_btn = QPushButton("Start recording")
        self.record_btn.setCheckable(True)
        self.record_btn.toggled.connect(self._toggle_record)
        actions.addWidget(self.record_btn)
        self.save_btn = QPushButton("Save to controller")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        actions.addWidget(self.save_btn)
        self.discard_btn = QPushButton("Discard")
        self.discard_btn.clicked.connect(self._discard)
        self.discard_btn.setEnabled(False)
        actions.addWidget(self.discard_btn)
        actions.addStretch(1)
        rec.add_layout(actions)

        self.captured = QLabel("Nothing recorded yet.")
        self.captured.setObjectName("hint")
        self.captured.setWordWrap(True)
        rec.add(self.captured)
        self.outer.addWidget(rec)
        self.finish()
        self._loading = False

    # -- loading --------------------------------------------------------

    def refresh(self) -> None:
        index = self.picker.currentData()
        self.index = index
        self.submit("macro_blob",
                    lambda ctl, i=index: (i, C.read_macro_blob(ctl, i)))

    def apply_macros(self, payload) -> None:
        index, blob = payload
        self.index = index
        macros = MAC.parse(blob)
        self._loading = True
        self.existing.clear()
        if macros:
            for m in macros:
                self.existing.addItem(
                    f"{M.key_name(m.button)} — {m.name or '(unnamed)'}", m.button)
        else:
            self.existing.addItem("no macros on this profile", None)
        self._loading = False
        self.remove_btn.setEnabled(self.existing.currentData() is not None)
        self.listing.setText(MAC.describe(macros).replace("  ", " ") or "")

    def _on_pick(self, _i: int) -> None:
        if self._loading:
            return
        self.refresh()

    # -- recording ------------------------------------------------------

    def _toggle_record(self, on: bool) -> None:
        self.recording = on
        self.record_btn.setText("Stop recording" if on else "Start recording")
        if on:
            self.steps = []
            self._held = set()
            self._last_t = None
            self.captured.setText("Recording… press the buttons you want.")
        self.set_streaming(on)
        self.save_btn.setEnabled(not on and bool(self.steps))
        self.discard_btn.setEnabled(not on and bool(self.steps))
        if not on:
            self._finish()

    def feed(self, state) -> None:
        """Called with each live input frame while recording."""
        if not self.recording:
            return
        import time as _t
        ignore = {self.button.currentData()}
        current = {b for b in state.buttons if b not in ignore}
        added = sorted(current - self._held)
        removed = sorted(self._held - current)
        if not added and not removed:
            return
        now = _t.monotonic()
        if self.steps and self._last_t is not None:
            self.steps[-1].duration_ms = max(1, int((now - self._last_t) * 1000))
        for b in added:
            self.steps.append(MAC.Step(b, MAC.Event.PRESS, 0))
        for b in removed:
            self.steps.append(MAC.Step(b, MAC.Event.RELEASE, 0))
        self._held = current
        self._last_t = now
        self.captured.setText(
            f"{len(self.steps)} steps: "
            + ", ".join(s.describe() for s in self.steps[-6:]))

    def _finish(self) -> None:
        for b in sorted(self._held):
            self.steps.append(MAC.Step(b, MAC.Event.RELEASE, 0))
        self._held = set()
        if self.steps:
            self.steps[-1].duration_ms = max(self.steps[-1].duration_ms, 20)
            total = sum(s.duration_ms for s in self.steps)
            self.captured.setText(
                f"{len(self.steps)} steps, {total} ms — "
                + ", ".join(s.describe() for s in self.steps))
        else:
            self.captured.setText("Nothing recorded.")
        self.save_btn.setEnabled(bool(self.steps))
        self.discard_btn.setEnabled(bool(self.steps))

    def _discard(self) -> None:
        self.steps = []
        self.captured.setText("Discarded.")
        self.save_btn.setEnabled(False)
        self.discard_btn.setEnabled(False)

    def _save(self) -> None:
        if not self.steps:
            return
        macro = MAC.Macro(button=self.button.currentData(),
                          name=self.name.text().strip(),
                          enable_type=self.kind.currentData(),
                          interval_ms=self.interval.value(),
                          steps=list(self.steps))
        index = self.picker.currentData()
        if QMessageBox.question(
                self, "Save macro",
                f"Store this macro on profile {index + 1} and set "
                f"{M.key_name(macro.button)} to run it?\n\n"
                "This writes to the controller's flash.",
                QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        self.save_btn.setEnabled(False)
        self.submit("macro_saved", lambda ctl: _save_macro(ctl, index, macro))

    def _remove(self) -> None:
        button = self.existing.currentData()
        if button is None:
            return
        index = self.picker.currentData()
        if QMessageBox.question(
                self, "Remove macro",
                f"Remove the macro on {M.key_name(button)} from profile "
                f"{index + 1}?", QMessageBox.Ok | QMessageBox.Cancel) != QMessageBox.Ok:
            return
        self.submit("macro_saved",
                    lambda ctl: _remove_macro(ctl, index, button))


def _save_macro(ctl, index: int, macro):
    before = C.read_profile_versions(ctl).active
    C.bind_macro(ctl, index, macro)
    blob = C.read_macro_blob(ctl, index)
    if C.read_profile_versions(ctl).active != before:
        C.activate_profile(ctl, before)
    return (index, blob)


def _remove_macro(ctl, index: int, button: int):
    before = C.read_profile_versions(ctl).active
    C.unbind_macro(ctl, index, button)
    blob = C.read_macro_blob(ctl, index)
    if C.read_profile_versions(ctl).active != before:
        C.activate_profile(ctl, before)
    return (index, blob)
