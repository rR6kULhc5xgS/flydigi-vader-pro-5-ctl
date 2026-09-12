"""Live input reading -- the equivalent of the Windows app's test page.

Enabling the raw stream (command 0x11 with ``raw_data=True``) makes the
controller push an input report at roughly 490 Hz on the control interface,
tagged ``0xEF`` in the usual ``5A A5 <marker>`` framing. It is purely
additive: the controller keeps working as a normal gamepad while it runs.

Report layout, derived from ``Button.IsButtonPressed`` in the vendor SDK and
then confirmed on hardware by correlating against the kernel's evdev
gamepad (the axis offsets and the trigger bytes were *not* as the SDK's
indices implied, so they were re-derived from capture):

    [0..2]   0x5A 0xA5 0xEF
    [3..4]   left stick X    int16 LE, centre 0
    [5..6]   left stick Y    int16 LE, centre 0
    [7..8]   right stick X   int16 LE, centre 0
    [9..10]  right stick Y   int16 LE, centre 0 (positive = up)
    [11]     Up Right Down Left A B Select X   (bits 0..7)
    [12]     Y Start LB RB LT RT L3 R3         (bits 0..7)
    [13]     C Z M1 M2 M3 M4 M5 M6             (bits 0..7)
    [14]     Menu(0x01) Home(0x08) Back(0x10)
    [15]     left trigger    0..255
    [16]     right trigger   0..255
    [17..22] gyro  x,y,z     int16 LE
    [23..28] accel x,y,z     int16 LE
    [31]     checksum, sum(report[2:31]) & 0xFF
"""

from __future__ import annotations

import glob
import os
import select
import struct
import time
from dataclasses import dataclass, field

from . import commands as C
from . import mapping as M
from .device import Controller, DeviceError

MARKER = 0xEF

#: (byte offset, bit) -> ControllerKey slot id
BUTTON_BITS: dict[tuple[int, int], int] = {}
for _off, _keys in (
    (11, [0, 1, 2, 3, 4, 5, 6, 7]),          # Up Right Down Left A B Select X
    (12, [8, 9, 10, 11, 12, 13, 14, 15]),    # Y Start LB RB LT RT L3 R3
    (13, [16, 17, 18, 19, 20, 21, 22, 23]),  # C Z M1..M6
):
    for _bit, _slot in enumerate(_keys):
        BUTTON_BITS[(_off, _bit)] = _slot
BUTTON_BITS[(14, 0)] = 24    # Menu
BUTTON_BITS[(14, 3)] = 27    # Home
BUTTON_BITS[(14, 4)] = 28    # Back

OFF_LX, OFF_LY, OFF_RX, OFF_RY = 3, 5, 7, 9
OFF_LT, OFF_RT = 15, 16
OFF_GYRO, OFF_ACCEL = 17, 23

AXIS_MAX = 32767.0


# ---------------------------------------------------------------- evdev side

#: Linux input codes -> ControllerKey slot. This is the *post-mapping* view:
#: what a game actually receives, including anything a macro plays back.
EV_KEY_MAP = {
    304: 4, 305: 5, 307: 7, 308: 8,          # A B X Y
    310: 10, 311: 11, 312: 12, 313: 13,      # LB RB LT RT
    314: 6, 315: 9, 316: 27,                 # Select Start Home
    317: 14, 318: 15,                        # L3 R3
    544: 0, 545: 2, 546: 3, 547: 1,          # d-pad
}
EV_ABS_LT, EV_ABS_RT = 2, 5                  # ABS_Z, ABS_RZ
EV_ABS_HAT_X, EV_ABS_HAT_Y = 16, 17
EV_SIZE = 24                                 # struct input_event, 64-bit


def find_evdev() -> str | None:
    for path in glob.glob("/dev/input/by-id/*Flydigi*event-joystick"):
        return os.path.realpath(path)
    return None


class EvdevReader:
    """Tracks the kernel gamepad's current state.

    Reading this alongside the raw stream is what makes a remap verifiable:
    the raw stream says which button was *physically* pressed, and this says
    what the controller *emitted* as a result.
    """

    def __init__(self, path: str | None = None):
        self.path = path or find_evdev()
        self._fd: int | None = None
        self.buttons: set[int] = set()
        self.left_trigger = 0.0
        self.right_trigger = 0.0
        self._trigger_range: dict[int, tuple[int, int]] = {}

    def open(self) -> "EvdevReader":
        if self.path:
            try:
                self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                self._fd = None
        return self

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    @property
    def available(self) -> bool:
        return self._fd is not None

    def fileno(self) -> int | None:
        return self._fd

    def pump(self) -> None:
        """Drain pending events into the tracked state."""
        if self._fd is None:
            return
        while True:
            r, _, _ = select.select([self._fd], [], [], 0)
            if not r:
                return
            try:
                data = os.read(self._fd, EV_SIZE * 64)
            except OSError:
                return
            if not data:
                return
            for i in range(0, len(data) - EV_SIZE + 1, EV_SIZE):
                _sec, _us, typ, code, val = struct.unpack_from("<qqHHi", data, i)
                if typ == 1:                          # EV_KEY
                    slot = EV_KEY_MAP.get(code)
                    if slot is None:
                        continue
                    if val:
                        self.buttons.add(slot)
                    else:
                        self.buttons.discard(slot)
                elif typ == 3:                        # EV_ABS
                    self._abs(code, val)

    def _abs(self, code: int, val: int) -> None:
        if code == EV_ABS_HAT_X:
            self.buttons.discard(3); self.buttons.discard(1)
            if val < 0:
                self.buttons.add(3)
            elif val > 0:
                self.buttons.add(1)
        elif code == EV_ABS_HAT_Y:
            self.buttons.discard(0); self.buttons.discard(2)
            if val < 0:
                self.buttons.add(0)
            elif val > 0:
                self.buttons.add(2)
        elif code in (EV_ABS_LT, EV_ABS_RT):
            lo, hi = self._trigger_range.setdefault(code, (0, 255))
            hi = max(hi, val)
            self._trigger_range[code] = (lo, hi)
            frac = 0.0 if hi <= lo else max(0.0, min(1.0, (val - lo) / (hi - lo)))
            if code == EV_ABS_LT:
                self.left_trigger = frac
                self.buttons.discard(12) if frac <= 0.08 else self.buttons.add(12)
            else:
                self.right_trigger = frac
                self.buttons.discard(13) if frac <= 0.08 else self.buttons.add(13)


@dataclass
class InputState:
    buttons: set = field(default_factory=set)      # ControllerKey slot ids
    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0
    left_trigger: float = 0.0                      # 0..1
    right_trigger: float = 0.0
    gyro: tuple = (0, 0, 0)
    accel: tuple = (0, 0, 0)
    unknown_bits: set = field(default_factory=set)
    #: What the controller actually emitted, read from the kernel gamepad.
    #: Differs from ``buttons`` wherever a remap or a macro is in play.
    output_buttons: set = field(default_factory=set)
    output_available: bool = False
    raw: bytes = b""

    @property
    def axes(self) -> dict[str, float]:
        return {"left_x": self.left_x, "left_y": self.left_y,
                "right_x": self.right_x, "right_y": self.right_y}

    def pressed_names(self) -> list[str]:
        return [M.key_name(s) for s in sorted(self.buttons)]

    def output_names(self) -> list[str]:
        return [M.key_name(s) for s in sorted(self.output_buttons)]


def parse_report(data: bytes) -> InputState | None:
    """Decode one 0xEF report. Returns None if it isn't one."""
    if len(data) < 32 or data[0] != 0x5A or data[1] != 0xA5 or data[2] != MARKER:
        return None
    st = InputState(raw=bytes(data))
    s16 = lambda off: struct.unpack_from("<h", data, off)[0]
    st.left_x = s16(OFF_LX) / AXIS_MAX
    st.left_y = -s16(OFF_LY) / AXIS_MAX          # screen coords: +y is down
    st.right_x = s16(OFF_RX) / AXIS_MAX
    st.right_y = -s16(OFF_RY) / AXIS_MAX
    st.left_trigger = data[OFF_LT] / 255.0
    st.right_trigger = data[OFF_RT] / 255.0
    st.gyro = tuple(struct.unpack_from("<3h", data, OFF_GYRO))
    st.accel = tuple(struct.unpack_from("<3h", data, OFF_ACCEL))
    for off in (11, 12, 13, 14):
        for bit in range(8):
            if data[off] >> bit & 1:
                slot = BUTTON_BITS.get((off, bit))
                if slot is None:
                    st.unknown_bits.add((off, bit))
                else:
                    st.buttons.add(slot)
    # The triggers also report as bits; treat a real pull as the press.
    if st.left_trigger > 0.08:
        st.buttons.add(12)
    if st.right_trigger > 0.08:
        st.buttons.add(13)
    return st


def checksum_ok(data: bytes) -> bool:
    return len(data) >= 32 and data[31] == (sum(data[2:31]) & 0xFF)


class InputMonitor:
    """Turns the raw stream on, yields decoded states, restores it after.

    Always use it as a context manager -- it records whether the stream was
    already enabled and puts that setting back, so a crash cannot leave the
    controller streaming.
    """

    def __init__(self, ctl: Controller, path: str | None = None, *,
                 read_output: bool = True):
        self.ctl = ctl
        self.path = path or ctl.iface.path
        self._fd: int | None = None
        self._was_enabled = False
        self.evdev = EvdevReader() if read_output else None

    def __enter__(self) -> "InputMonitor":
        status = C.read_data_report_status(self.ctl)
        self._was_enabled = status.raw_data
        if not self._was_enabled:
            if not C.set_data_report(self.ctl, raw_data=True):
                raise DeviceError("controller would not enable the raw input "
                                  "stream (0x11)")
            time.sleep(0.2)
        self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        if self.evdev is not None:
            self.evdev.open()
        return self

    def __exit__(self, *exc):
        if self.evdev is not None:
            self.evdev.close()
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        try:
            if not self._was_enabled:
                C.set_data_report(self.ctl, raw_data=False)
        except DeviceError:
            pass
        return False

    def read(self, timeout: float = 0.25) -> InputState | None:
        """Return the most recent state, or None if nothing arrived."""
        if self._fd is None:
            raise DeviceError("monitor is not open")
        latest = None
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            r, _, _ = select.select([self._fd], [], [], remaining)
            if not r:
                break
            try:
                data = os.read(self._fd, 64)
            except OSError:
                break
            state = parse_report(data)
            if state is not None:
                latest = state
                if self.evdev is not None:
                    self.evdev.pump()
                    latest.output_buttons = set(self.evdev.buttons)
                    latest.output_available = self.evdev.available
                # drain anything else already queued so we stay current
                deadline = min(deadline, time.time() + 0.002)
        return latest
