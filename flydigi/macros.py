"""Onboard macros.

Macros live in their own blob (protocol 3.2 and later), transferred with
0xAC / 0xAD / 0xAE. They are stored on the controller and replayed by it,
so a macro needs nothing running on the host -- which also means a macro
can only emit *controller* buttons, never keystrokes.

Blob layout (MacroConfigParser.MacroConfigParserV10), 81 packets x 20 =
1620 bytes, pre-filled 0xFF:

    [0..1]    version, u16 LE
    [2..3]    macro count, u16 LE, at most 10
    [4..23]   offset[10], u16 LE each, in 4-byte units from 0x018
    [24..]    macro records

Each record at ``0x018 + offset[i] * 4``:

    +0   (1)  ControllerKey this macro is bound to
    +1   (2)  action count, u16 LE
    +3   (1)  MacroEnableType
    +4   (2)  repeat interval, u16 LE, milliseconds
    +6   (6)  0xFF filler
    +12  (20) name, UTF-8, 0xFF/NUL padded
    +32  (4k) steps: {time_lo, time_hi, button, event}

Step timestamps on the wire are **cumulative** milliseconds; this module
exposes per-step durations and converts both ways.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import mapping as M

#: The doc-derived size is 81 packets, but the device actually serves 83
#: (1660 bytes) -- confirmed by reading it back. Writes match the device.
BLOB_SIZE = 1660
PACKET_COUNT = 83
MAX_MACROS = 10
DATA_START = 0x018
RECORD_HEADER = 32
STEP_SIZE = 4
NAME_LEN = 20
NAME_OFFSET = 12

OFF_VERSION = 0
OFF_COUNT = 2
OFF_OFFSETS = 4

#: milliseconds per timestamp tick for protocol >= 3.2
TICK_MS = 1


class EnableType:
    NONE = 0
    ONCE = 1          # play once per press
    PRESS = 2         # repeat while held
    CLICK = 3         # toggle on/off with each press

    NAMES = {NONE: "disabled", ONCE: "once", PRESS: "while held",
             CLICK: "toggle"}


class Event:
    RELEASE = 0
    PRESS = 1
    LEFT_JOYSTICK = 2
    RIGHT_JOYSTICK = 3
    HOLD = 5

    NAMES = {RELEASE: "release", PRESS: "press",
             LEFT_JOYSTICK: "left stick", RIGHT_JOYSTICK: "right stick",
             HOLD: "hold"}


@dataclass
class Step:
    button: int
    event: int = Event.PRESS
    duration_ms: int = 0        # time spent before the *next* step

    def describe(self) -> str:
        name = Event.NAMES.get(self.event, str(self.event))
        return f"{name} {M.key_name(self.button)} +{self.duration_ms}ms"


@dataclass
class Macro:
    button: int                          # which physical button runs it
    enable_type: int = EnableType.ONCE
    interval_ms: int = 0                 # gap between repeats
    name: str = ""
    steps: list = field(default_factory=list)

    @property
    def total_ms(self) -> int:
        return sum(s.duration_ms for s in self.steps)

    def describe(self) -> str:
        head = (f"{M.key_name(self.button)}: {self.name or '(unnamed)'} — "
                f"{len(self.steps)} steps, {self.total_ms}ms, "
                f"{EnableType.NAMES.get(self.enable_type, self.enable_type)}")
        if self.interval_ms:
            head += f", repeat every {self.interval_ms}ms"
        return head


def _trim(raw: bytes) -> str:
    out = bytearray()
    for b in raw:
        if b in (0x00, 0xFF):
            break
        out.append(b)
    return out.decode("utf-8", "replace")


def parse(blob: bytes) -> list[Macro]:
    """Decode a macro blob. Unused or malformed records are skipped."""
    if len(blob) < DATA_START:
        return []
    count = int.from_bytes(blob[OFF_COUNT:OFF_COUNT + 2], "little")
    count = min(count, MAX_MACROS)
    if count == 0 or count == 0xFFFF:
        return []
    offsets = [int.from_bytes(blob[OFF_OFFSETS + 2 * i:OFF_OFFSETS + 2 * i + 2],
                              "little")
               for i in range(MAX_MACROS)]
    macros: list[Macro] = []
    for i in range(count):
        off = offsets[i]
        if off == 0xFFFF:
            continue
        base = DATA_START + off * 4
        if base + RECORD_HEADER > len(blob):
            continue
        button = blob[base]
        n_steps = int.from_bytes(blob[base + 1:base + 3], "little")
        enable = blob[base + 3]
        interval = int.from_bytes(blob[base + 4:base + 6], "little")
        name = _trim(blob[base + NAME_OFFSET:base + NAME_OFFSET + NAME_LEN])
        if button == 0xFF or n_steps in (0, 0xFFFF):
            continue
        avail = (len(blob) - base - RECORD_HEADER) // STEP_SIZE
        n_steps = min(n_steps, avail)
        steps: list[Step] = []
        previous_cum = 0
        for k in range(n_steps):
            s = base + RECORD_HEADER + k * STEP_SIZE
            cum = int.from_bytes(blob[s:s + 2], "little") * TICK_MS
            btn = blob[s + 2]
            event = blob[s + 3]
            if btn == 0xFF:
                break
            steps.append(Step(button=btn, event=event,
                              duration_ms=max(0, cum - previous_cum)))
            previous_cum = cum
        if not steps:
            continue
        macros.append(Macro(button=button, enable_type=enable,
                            interval_ms=0 if interval == 0xFFFF else interval,
                            name=name, steps=steps))
    return macros


def build(macros: list[Macro], version: int = 0x0100,
          size: int = BLOB_SIZE) -> bytes:
    """Encode macros back into a blob of the size the device serves."""
    if len(macros) > MAX_MACROS:
        raise ValueError(f"at most {MAX_MACROS} macros fit on the controller")
    blob = bytearray(b"\xff" * size)
    blob[OFF_VERSION:OFF_VERSION + 2] = int(version).to_bytes(2, "little")
    blob[OFF_COUNT:OFF_COUNT + 2] = len(macros).to_bytes(2, "little")

    running = 0
    for i, macro in enumerate(macros):
        blob[OFF_OFFSETS + 2 * i:OFF_OFFSETS + 2 * i + 2] = \
            running.to_bytes(2, "little")
        base = DATA_START + running * 4
        need = RECORD_HEADER + len(macro.steps) * STEP_SIZE
        if base + need > len(blob):
            raise ValueError("macros do not fit in the controller's macro blob")
        blob[base] = macro.button & 0xFF
        blob[base + 1:base + 3] = len(macro.steps).to_bytes(2, "little")
        blob[base + 3] = macro.enable_type & 0xFF
        blob[base + 4:base + 6] = int(macro.interval_ms).to_bytes(2, "little")
        blob[base + 6:base + 12] = b"\xff" * 6
        name = macro.name.encode("utf-8")[:NAME_LEN]
        field_ = bytearray(b"\x00" * NAME_LEN)
        field_[:len(name)] = name
        blob[base + NAME_OFFSET:base + NAME_OFFSET + NAME_LEN] = field_
        cum = 0
        for k, step in enumerate(macro.steps):
            cum += max(0, int(step.duration_ms)) // TICK_MS
            s = base + RECORD_HEADER + k * STEP_SIZE
            blob[s:s + 2] = min(cum, 0xFFFF).to_bytes(2, "little")
            blob[s + 2] = step.button & 0xFF
            blob[s + 3] = step.event & 0xFF
        running += len(macro.steps) + 8      # the 32-byte header is 8 units
    return bytes(blob)


def describe(macros: list[Macro]) -> str:
    if not macros:
        return "  no macros stored on this profile"
    lines = []
    for m in macros:
        lines.append("  " + m.describe())
        for s in m.steps:
            lines.append(f"      {s.describe()}")
    return "\n".join(lines)


# ------------------------------------------------------------- recording

def record(monitor, stop, *, ignore=frozenset(), max_steps: int = 200,
           on_event=None) -> list[Step]:
    """Record button presses from the live input stream into steps.

    ``monitor`` is an open :class:`~flydigi.input_monitor.InputMonitor`;
    ``stop`` is a callable polled between frames that returns True to
    finish. Buttons in ``ignore`` are not recorded, which lets the caller
    exclude the button the macro will be bound to.

    Each press and release becomes a step, and the gap before the next
    event becomes the previous step's duration -- the shape the wire
    format wants.
    """
    import time as _time

    steps: list[Step] = []
    held: set[int] = set()
    last_t = None
    while not stop() and len(steps) < max_steps:
        state = monitor.read(timeout=0.05)
        if state is None:
            continue
        now = _time.monotonic()
        current = {b for b in state.buttons if b not in ignore}
        changed = []
        for b in sorted(current - held):
            changed.append((b, Event.PRESS))
        for b in sorted(held - current):
            changed.append((b, Event.RELEASE))
        if not changed:
            continue
        if steps and last_t is not None:
            steps[-1].duration_ms = max(1, int((now - last_t) * 1000))
        for b, ev in changed:
            steps.append(Step(button=b, event=ev, duration_ms=0))
            if on_event is not None:
                on_event(steps[-1])
        held = current
        last_t = now
    # release anything still held so the macro ends clean
    if held and steps:
        if last_t is not None:
            steps[-1].duration_ms = max(1, steps[-1].duration_ms or 30)
        for b in sorted(held):
            steps.append(Step(button=b, event=Event.RELEASE, duration_ms=0))
    if steps:
        steps[-1].duration_ms = max(steps[-1].duration_ms, 20)
    return steps
