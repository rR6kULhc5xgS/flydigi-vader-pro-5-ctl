"""The 840-byte onboard mapping blob.

Layout follows MappingConfigParser (V3.0 + V3.1) in Flydigi.ControllerSdk.
All multi-byte scalars are little-endian and unwritten bytes are 0xFF.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BLOB_SIZE = 840

#: Mapping-blob layout versions this code has actually been validated
#: against. The blob announces its own version in its first two bytes, and
#: Flydigi has changed the layout between versions before -- 3.1 added the
#: per-stick curve bank at offset 790, and 3.2 moved macros out into their
#: own blob. If a firmware update bumps this past what is listed here, the
#: offsets below may no longer describe reality, so say so loudly rather
#: than silently misreading someone's configuration.
SUPPORTED_PROTO_VERSIONS = frozenset({770})          # 3.2


def describe_proto_version(value: int) -> str:
    return f"{value >> 8}.{value & 0xFF} ({value})"


class UnsupportedLayout(Exception):
    """The blob announces a layout version this code does not know."""


# -- offsets ---------------------------------------------------------------

OFF_VERSION = 0        # 2  [minor, major]
OFF_PKG_LEN = 2        # 1
OFF_LED = 3            # 10  legacy inline LED header
OFF_KEY_TABLE = 13     # 96  32 slots x 3 bytes
OFF_JOY_TABLE = 109    # 14  2 x 7   stick curves
OFF_LINER_TABLE = 123  # 14  2 x 7   trigger curves
OFF_MOTION = 137       # 8
OFF_GRIP = 145         # 9   grip rumble
OFF_TRIG = 154         # 29  trigger haptics
OFF_LUNPAN = 183       # 2
OFF_TRIGGER = 185      # 40  2 x 20  auto-trigger
OFF_DATA_VERSION = 225  # 2
OFF_MACRO_PAGE = 230   # 538
OFF_CFG_NAME = 770     # 20  UTF-16LE, 10 chars
OFF_JOY_EXTRA = 790    # 24  2 x 12  V3.1 curve bank
OFF_MACRO_CYCLE = 820  # 5
OFF_MOTION_CURVE = 830  # 6

KEY_SLOT_COUNT = 32
KEY_SLOT_SIZE = 3

# -- button identities ------------------------------------------------------

#: ControllerKey values, from the recovered ControllerEnum.proto.
KEY_NAMES = {
    0: "Up", 1: "Right", 2: "Down", 3: "Left",
    4: "A", 5: "B", 6: "Select", 7: "X", 8: "Y", 9: "Start",
    10: "LB", 11: "RB", 12: "LT", 13: "RT",
    14: "LS (click)", 15: "RS (click)",
    16: "C", 17: "Z",
    18: "M1", 19: "M2", 20: "M3", 21: "M4", 22: "M5", 23: "M6",
    24: "Menu", 25: "Turbo", 27: "Home", 28: "Back",
    32: "Macro",
    160: "Stick centre", 161: "Stick up", 162: "Stick up-right",
    163: "Stick right", 164: "Stick down-right", 165: "Stick down",
    166: "Stick down-left", 167: "Stick left", 168: "Stick up-left",
    240: "Left stick", 241: "Right stick", 242: "Stick wheel",
    254: "Keyboard/mouse", 255: "None",
}

#: Name -> id, for the buttons a user can actually bind.
KEY_IDS = {v: k for k, v in KEY_NAMES.items()}

#: The physical inputs on a Vader 5 Pro, in the order a UI should show them.
PHYSICAL_KEYS = [0, 1, 2, 3, 4, 5, 7, 8, 6, 9, 10, 11, 12, 13, 14, 15,
                 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 27, 28]

KEY_MACRO = 32
KEY_KEYBOARD_MOUSE = 0xFE
KEY_IDENTITY = 0xFF


class MapType:
    KEY = 0
    CONTINUOUS = 1        # turbo / rapid fire
    MACRO = 2
    MULTI_FUNCTION = 3
    KEYBOARD = 4


class TurboMode:
    CLOSE = 0
    PRESS = 1             # fires while held
    CLICK = 2             # toggles on click


def key_name(key_id: int) -> str:
    return KEY_NAMES.get(key_id, f"key{key_id}")


@dataclass
class KeySlot:
    """One entry of key_table: what a physical button does."""

    slot: int                  # physical button (a ControllerKey id)
    keyid: int = KEY_IDENTITY
    type: int = 0
    turbo: int = 0

    @property
    def is_identity(self) -> bool:
        # 0xFE and 0xFF both read back as identity, and so does a slot
        # pointing at itself.
        return (self.turbo == 0
                and (self.keyid > KEY_MACRO or self.keyid == self.slot))

    @property
    def map_type(self) -> int:
        if self.keyid == KEY_MACRO:
            return MapType.MACRO
        if self.turbo > 0:
            return MapType.CONTINUOUS
        return MapType.KEY

    @property
    def target(self) -> int:
        """Which key this button emits."""
        if self.keyid > KEY_MACRO:
            return self.slot
        return self.keyid

    def describe(self) -> str:
        if self.map_type == MapType.MACRO:
            return "runs macro"
        if self.map_type == MapType.CONTINUOUS:
            mode = {TurboMode.PRESS: "while held",
                    TurboMode.CLICK: "toggle"}.get(self.type, f"mode {self.type}")
            return f"turbo {key_name(self.target)} @{self.turbo} ({mode})"
        if self.keyid == KEY_KEYBOARD_MOUSE:
            return "keyboard/mouse (host side)"
        if self.is_identity:
            return "-"
        return f"-> {key_name(self.target)}"


class MappingBlob:
    """Read/modify/write wrapper over the raw 840-byte blob."""

    def __init__(self, data: bytes, *, strict: bool = False):
        if len(data) < BLOB_SIZE:
            data = bytes(data) + b"\xff" * (BLOB_SIZE - len(data))
        # Keep whatever the device actually served. A future firmware may
        # send a longer blob; truncating here would silently drop its tail
        # and then write it back short.
        self.data = bytearray(data)
        if strict and not self.proto_supported:
            raise UnsupportedLayout(self.layout_warning())

    # -- header ---------------------------------------------------------

    @property
    def proto_version(self) -> int:
        return (self.data[1] << 8) | self.data[0]

    @property
    def data_version(self) -> int:
        return int.from_bytes(self.data[OFF_DATA_VERSION:OFF_DATA_VERSION + 2],
                              "little")

    @data_version.setter
    def data_version(self, value: int) -> None:
        self.data[OFF_DATA_VERSION:OFF_DATA_VERSION + 2] = \
            int(value).to_bytes(2, "little")

    @property
    def proto_supported(self) -> bool:
        return self.proto_version in SUPPORTED_PROTO_VERSIONS

    def layout_warning(self) -> str | None:
        """Explain why this blob may not be safe to interpret."""
        if self.proto_supported:
            return None
        known = ", ".join(sorted(describe_proto_version(v)
                                 for v in SUPPORTED_PROTO_VERSIONS))
        return (f"This profile reports mapping layout "
                f"{describe_proto_version(self.proto_version)}, but this "
                f"software has only been validated against {known}. The "
                f"field offsets may have moved, so what is shown could be "
                f"wrong and writing it back could corrupt the profile. "
                f"Re-check the layout against a current Space Station build "
                f"before trusting this (see CLAUDE.md).")

    @property
    def title(self) -> str:
        raw = bytes(self.data[OFF_CFG_NAME:OFF_CFG_NAME + 20])
        text = raw.decode("utf-16-le", "replace")
        out = []
        for ch in text:
            if ch in ("\x00", "￿"):
                break
            out.append(ch)
        return "".join(out)

    @title.setter
    def title(self, value: str) -> None:
        enc = value.encode("utf-16-le")[:20]
        buf = bytearray(b"\x00" * 20)
        buf[:len(enc)] = enc
        self.data[OFF_CFG_NAME:OFF_CFG_NAME + 20] = buf

    # -- key table ------------------------------------------------------

    def key_slot(self, slot: int) -> KeySlot:
        base = OFF_KEY_TABLE + slot * KEY_SLOT_SIZE
        return KeySlot(slot=slot, keyid=self.data[base],
                       type=self.data[base + 1], turbo=self.data[base + 2])

    def set_key_slot(self, slot: KeySlot) -> None:
        base = OFF_KEY_TABLE + slot.slot * KEY_SLOT_SIZE
        self.data[base] = slot.keyid & 0xFF
        self.data[base + 1] = slot.type & 0xFF
        self.data[base + 2] = slot.turbo & 0xFF

    def key_table(self) -> list[KeySlot]:
        return [self.key_slot(i) for i in range(KEY_SLOT_COUNT)]

    def remap(self, slot: int, target: int | None) -> None:
        """Point a physical button at another key, or restore identity."""
        if target is None:
            self.set_key_slot(KeySlot(slot=slot, keyid=KEY_IDENTITY))
        else:
            self.set_key_slot(KeySlot(slot=slot, keyid=target))

    def set_turbo(self, slot: int, target: int, frequency: int,
                  mode: int = TurboMode.PRESS) -> None:
        self.set_key_slot(KeySlot(slot=slot, keyid=target, type=mode,
                                  turbo=frequency))

    def clear_keyboard_bindings(self) -> None:
        """Drop host-side keyboard/mouse remaps.

        Nintendo Switch mode has no host software to inject keystrokes, so
        the SDK strips these before committing a profile to the NS bank.
        """
        for i in range(KEY_SLOT_COUNT):
            if self.data[OFF_KEY_TABLE + i * KEY_SLOT_SIZE] == KEY_KEYBOARD_MOUSE:
                self.remap(i, None)

    # -- misc -----------------------------------------------------------

    def describe_keys(self, only_changed: bool = False) -> str:
        lines = [f"{'button':<14}{'emits':<22}raw"]
        for slot in self.key_table():
            if slot.slot not in KEY_NAMES:
                continue
            if only_changed and slot.is_identity:
                continue
            raw = f"{slot.keyid:02X} {slot.type:02X} {slot.turbo:02X}"
            lines.append(f"  {key_name(slot.slot):<12}{slot.describe():<22}{raw}")
        return "\n".join(lines)

    def copy(self) -> "MappingBlob":
        return MappingBlob(bytes(self.data))

    def __eq__(self, other) -> bool:
        return isinstance(other, MappingBlob) and other.data == self.data
