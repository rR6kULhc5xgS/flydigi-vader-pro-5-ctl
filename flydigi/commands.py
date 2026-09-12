"""High-level operations against a controller.

Command ids and reply layouts mirror the NewXInput branch of each
*CommandFactory in Flydigi.ControllerSdk.dll.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .device import Controller, detect_connection
from .enums import ChipType, ConnectType, MotionChipType, device_name, enum_name
from .protocol import PACKET_SIZE, Ack


class Cmd:
    """NewXInput command ids."""
    HEARTBEAT = 0x01
    READ_UID = 0x04
    READ_HARDWARE_STATUS = 0x03
    READ_DATA_REPORT_STATUS = 0x10
    SET_DATA_REPORT = 0x11
    FEATURE_TOGGLE = 0x13
    SET_REPORT_RATE = 0x14
    SET_JOYSTICK_PRECISION = 0x15
    SET_JOYSTICK_SENSITIVITY = 0x16
    SET_SLEEP_TIME = 0x17
    RESTART = 0x1D


#: Payload sentinel meaning "leave this field as it is".
UNCHANGED = 0xFF


def _version(hi: int, lo: int) -> str | None:
    """Firmware versions pack two BCD-ish nibble pairs into two bytes."""
    v = f"{hi >> 4}.{hi & 0x0F}.{lo >> 4}.{lo & 0x0F}"
    return None if all(part == "0" for part in v.split(".")) else v


@dataclass
class DeviceInfo:
    device_type: int = 0
    name: str = ""
    connect_type: int = 0
    mac: str | None = None
    battery: int = 0
    charging: bool = False
    battery_raw: int = 0
    battery_state: int = 0
    chip_type: int = 0
    motion_chip_type: int = 0
    firmware: str | None = None
    dongle_version: str | None = None
    switch_version: str | None = None
    trigger_version: str | None = None
    screen_version: str | None = None
    adc_version: str | None = None
    nearlink_version: str | None = None
    uid: str | None = None
    raw: bytes = b""

    def describe(self) -> str:
        lines = [
            f"Device        {self.name}  (type {self.device_type})",
            f"Connection    {enum_name(ConnectType, self.connect_type)}",
        ]
        if self.mac and self.mac != "00:00:00:00":
            lines.append(f"MAC           {self.mac}")
        if self.uid:
            lines.append(f"UID           {self.uid}")
        batt = f"{self.battery}/6"
        if self.connect_type == ConnectType.WIRED:
            batt += "  (charging)"
        lines += [
            f"Battery       {batt}",
            f"Main chip     {enum_name(ChipType, self.chip_type)}",
            f"Motion chip   {enum_name(MotionChipType, self.motion_chip_type)}",
        ]
        fw = [("Firmware", self.firmware),
              ("Dongle fw", self.dongle_version),
              ("Switch fw", self.switch_version),
              ("Trigger fw", self.trigger_version),
              ("Screen fw", self.screen_version),
              ("ADC fw", self.adc_version),
              ("NearLink fw", self.nearlink_version)]
        for label, value in fw:
            if value:
                lines.append(f"{label:<13} {value}")
        return "\n".join(lines)


def read_info(ctl: Controller) -> DeviceInfo:
    """HeartBeat (0x01) -- identity, battery and every firmware version."""
    ack = ctl.send_expect(Cmd.HEARTBEAT, timeout=0.6, retries=5)
    d = ack.raw
    info = DeviceInfo(raw=d)

    # HeartBeatControllerCommandNewXInput.ParseAckData walks a cursor from
    # byte 5 when the reply is multi-packet, byte 4 otherwise.
    i = ack.body_start
    if ack.is_multipacket and ack.index != 0:
        return info                      # continuation packet, nothing to read

    info.device_type = d[i]; i += 1
    info.name = device_name(info.device_type)
    info.connect_type = d[i]; i += 1
    mac = bytes(d[i:i + 4][::-1]); i += 4
    info.mac = ":".join(f"{b:02X}" for b in mac)

    # Battery is a 0..6 level, matching the app's Power0..Power6 icons.
    # The high nibble is a charge-state code that alternates between 1 and 2
    # while charging, which is what drives the app's charging animation.
    batt_raw = d[i]; i += 1
    info.battery_raw = batt_raw
    info.battery_state = batt_raw >> 4
    info.battery = 6 if info.battery_state == 1 else (batt_raw & 0x0F)
    info.charging = info.battery_state != 0

    info.chip_type = d[i] & 0x0F; i += 1
    info.motion_chip_type = d[i] & 0x0F; i += 1
    i += 1                               # reserved

    def take() -> str | None:
        nonlocal i
        v = _version(d[i], d[i + 1])
        i += 2
        return v

    info.firmware = take()
    info.dongle_version = take()
    info.switch_version = take()
    info.trigger_version = take()
    info.screen_version = take()
    info.adc_version = take()
    info.nearlink_version = take()
    return info


def read_uid(ctl: Controller) -> str | None:
    """ReadUid (0x04) -- 13-byte unique id from bytes 5..17."""
    ack = ctl.send(Cmd.READ_UID, timeout=0.6, retries=5)
    if ack is None:
        return None
    return ack.raw[5:18].hex()


@dataclass
class DataReportStatus:
    """What the controller is currently reporting, and who owns its mapping.

    ``third_party_control`` is the setting the app labels "Allow third-party
    apps to take over mappings" -- with it on, Steam Input or reWASD drives
    the mapping and the controller's own onboard mapping steps aside.
    """

    controller_data: bool = False
    raw_data: bool = False
    keyboard_data: bool = False
    mouse_data: bool = False
    third_party_control: bool = False
    controlled_by: str = ""
    raw: bytes = b""

    def describe(self) -> str:
        lines = [
            f"Controller data      {'on' if self.controller_data else 'off'}",
            f"Raw (private) data   {'on' if self.raw_data else 'off'}",
            f"Keyboard injection   {'on' if self.keyboard_data else 'off'}",
            f"Mouse injection      {'on' if self.mouse_data else 'off'}",
            f"Third-party mapping  {'on' if self.third_party_control else 'off'}"
            "   (Steam Input / reWASD takeover)",
        ]
        if self.controlled_by:
            lines.append(f"Currently taken over by  {self.controlled_by!r}")
        return "\n".join(lines)


def read_data_report_status(ctl: Controller) -> DataReportStatus:
    """ReadRawDataReportStatus (0x10)."""
    ack = ctl.send_expect(Cmd.READ_DATA_REPORT_STATUS, timeout=0.6, retries=5)
    d = ack.raw
    return DataReportStatus(
        controller_data=d[5] == 1,
        raw_data=d[6] == 1,
        keyboard_data=d[7] == 1,
        mouse_data=d[8] == 1,
        third_party_control=d[9] == 1,
        controlled_by=bytes(d[10:30]).split(b"\x00")[0].decode("ascii", "replace"),
        raw=d,
    )


def _flag(value: bool | None) -> int:
    return UNCHANGED if value is None else (1 if value else 0)


def set_data_report(ctl: Controller, *,
                    controller_data: bool | None = None,
                    raw_data: bool | None = None,
                    keyboard_data: bool | None = None,
                    mouse_data: bool | None = None,
                    third_party_control: bool | None = None) -> bool:
    """EnableRawDataTransportIn (0x11).

    Every field left as ``None`` is sent as 0xFF, which the firmware reads as
    "keep the current value" -- so one setting can be changed in isolation.
    """
    payload = bytes((
        _flag(controller_data),
        _flag(raw_data),
        _flag(keyboard_data),
        _flag(mouse_data),
        _flag(third_party_control),
    ))
    ack = ctl.send(Cmd.SET_DATA_REPORT, payload, timeout=0.6, retries=3)
    return ack is not None


def set_third_party_control(ctl: Controller, enabled: bool) -> bool:
    """Hand controller mapping to Steam Input / reWASD, or take it back."""
    return set_data_report(ctl, third_party_control=enabled)


# ---------------------------------------------------------------- settings

class Feature:
    """Sub-command ids for the 0x13 feature-toggle group.

    Each feature's capability bit and current state live in the 0x03 reply;
    the bit index is the sub-command id minus one.
    """
    FAST_SWAP = 0x01           # FN + A/B/X/Y switches onboard profile
    XBOX_HOME = 0x02
    MOTION_DEBOUNCE = 0x03
    TURBO = 0x04               # the app calls this "Turbo Function"
    JOYSTICK_DEBOUNCE = 0x05
    JOYSTICK_AUTO_CALIBRATION = 0x06
    JOYSTICK_REBOUND = 0x07
    SCREEN_STATUS_BAR = 0x08
    OFF_SCREEN = 0x09
    AUDIO = 0x0A


#: label, sub-command, (usable byte, enabled byte, bit)
FEATURE_TABLE = [
    ("Fast swap config", Feature.FAST_SWAP, 5, 6, 0),
    ("Xbox Home button", Feature.XBOX_HOME, 5, 6, 1),
    ("Motion debounce", Feature.MOTION_DEBOUNCE, 5, 6, 2),
    ("Turbo function", Feature.TURBO, 5, 6, 3),
    ("Joystick debounce", Feature.JOYSTICK_DEBOUNCE, 5, 6, 4),
    ("Joystick auto-calibration", Feature.JOYSTICK_AUTO_CALIBRATION, 5, 6, 5),
    ("Joystick rebound", Feature.JOYSTICK_REBOUND, 5, 6, 6),
    ("Screen status bar always on", Feature.SCREEN_STATUS_BAR, 5, 6, 7),
    ("Off-screen", Feature.OFF_SCREEN, 7, 8, 0),
    ("Audio switch", Feature.AUDIO, 7, 8, 1),
]

#: The sleep periods the official UI offers, in minutes. 0 means never.
SLEEP_CHOICES = [(0, "Never"), (1, "1 min"), (5, "5 min"),
                 (15, "15 min"), (60, "1 hour"), (180, "3 hours")]

REPORT_RATES = {1: "1000 Hz", 2: "500 Hz", 4: "250 Hz", 8: "125 Hz"}

JOYSTICK_PRECISION = {1: "8 bit", 2: "10 bit", 3: "12 bit", 4: "9 bit",
                      5: "11 bit", 6: "14 bit", 7: "16 bit"}


def sleep_label(minutes: int) -> str:
    for value, label in SLEEP_CHOICES:
        if value == minutes:
            return label
    if minutes % 60 == 0:
        return f"{minutes // 60} hours"
    return f"{minutes} min"


@dataclass
class HardwareStatus:
    """Everything the 0x03 reply carries."""

    features: dict = field(default_factory=dict)      # label -> (usable, enabled)
    sleep_minutes: int = 0
    report_rate: int = 0
    joystick_precision: int = 0
    joystick_sensitivity: int = 0
    raw: bytes = b""

    # A device that reports zero has no configurable control for that setting,
    # which is how the app decides whether to show it.
    @property
    def report_rate_usable(self) -> bool:
        return self.report_rate != 0

    @property
    def precision_usable(self) -> bool:
        return self.joystick_precision != 0

    @property
    def sensitivity_usable(self) -> bool:
        return self.joystick_sensitivity != 0

    def describe(self) -> str:
        lines = [f"Sleep timeout        {sleep_label(self.sleep_minutes)}"]
        if self.report_rate_usable:
            lines.append(f"Polling rate         "
                         f"{REPORT_RATES.get(self.report_rate, self.report_rate)}")
        else:
            lines.append("Polling rate         not configurable on this device")
        if self.precision_usable:
            lines.append(f"Stick accuracy       "
                         f"{JOYSTICK_PRECISION.get(self.joystick_precision, self.joystick_precision)}")
        if self.sensitivity_usable:
            lines.append(f"Centre sensitivity   {self.joystick_sensitivity}")
        lines.append("")
        lines.append(f"{'feature':<30}{'supported':<12}state")
        for label, (usable, enabled) in self.features.items():
            lines.append(f"  {label:<28}{'yes' if usable else 'no':<12}"
                         f"{'on' if enabled else 'off'}")
        return "\n".join(lines)


def read_hardware_status(ctl: Controller) -> HardwareStatus:
    """ReadHardwareFunctionStatus (0x03)."""
    ack = ctl.send_expect(Cmd.READ_HARDWARE_STATUS, timeout=0.8, retries=4)
    d = ack.raw
    st = HardwareStatus(
        sleep_minutes=d[9],
        report_rate=d[10],
        joystick_precision=d[11],
        joystick_sensitivity=d[12],
        raw=d,
    )
    for label, _sub, uidx, eidx, bit in FEATURE_TABLE:
        st.features[label] = (bool(d[uidx] >> bit & 1), bool(d[eidx] >> bit & 1))
    return st


def set_feature(ctl: Controller, sub: int, enabled: bool) -> bool:
    """FeatureToggle (0x13) -- one of the Feature.* sub-commands."""
    ack = ctl.send(Cmd.FEATURE_TOGGLE, bytes((sub, 1 if enabled else 0)),
                   timeout=0.6, retries=3)
    return ack is not None


def set_sleep_time(ctl: Controller, minutes: int) -> bool:
    """UpdateSleepTime (0x17). 0 disables sleep entirely.

    The wire field is a single byte and the firmware does no validation, so
    anything above 255 is rejected here rather than silently truncated to a
    value that would mean "never".
    """
    if not 0 <= minutes <= 255:
        raise ValueError("sleep timeout must be 0..255 minutes (0 = never)")
    ack = ctl.send(Cmd.SET_SLEEP_TIME, bytes((minutes,)), timeout=0.6, retries=3)
    return ack is not None


def set_report_rate(ctl: Controller, code: int) -> bool:
    """UpdateReportRate (0x14). Codes are 1/2/4/8, not 1/2/3/4."""
    if code not in REPORT_RATES:
        raise ValueError(f"report rate code must be one of {sorted(REPORT_RATES)}")
    ack = ctl.send(Cmd.SET_REPORT_RATE, bytes((code,)), timeout=0.6, retries=3)
    return ack is not None


def set_joystick_precision(ctl: Controller, code: int) -> bool:
    """UpdateJoystickPrecision (0x15)."""
    if code not in JOYSTICK_PRECISION:
        raise ValueError(f"precision code must be one of {sorted(JOYSTICK_PRECISION)}")
    ack = ctl.send(Cmd.SET_JOYSTICK_PRECISION, bytes((code,)), timeout=0.6, retries=3)
    return ack is not None


def set_joystick_sensitivity(ctl: Controller, value: int) -> bool:
    """UpdateJoystickSensitivity (0x16). Lower is faster; 14..20 in the UI."""
    if not 0 <= value <= 255:
        raise ValueError("sensitivity must fit in one byte")
    ack = ctl.send(Cmd.SET_JOYSTICK_SENSITIVITY, bytes((value,)),
                   timeout=0.6, retries=3)
    return ack is not None


# ----------------------------------------------------------------- profiles

class ProfileCmd:
    READ_VERSIONS = 0xA1        # all dataVersions + active profile + NS binding
    ACTIVATE = 0xA2
    READ_MAPPING = 0xA3
    WRITE_MAPPING_START = 0xA4
    WRITE_MAPPING_PACK = 0xA5
    SAVE = 0xA6                 # persist working config to flash
    READ_LED = 0xA7
    WRITE_LED_START = 0xA8
    WRITE_LED_PACK = 0xA9
    SAVE_AS_NS = 0xAB           # promote a profile into the Switch-mode bank
    READ_MACRO = 0xAC
    WRITE_MACRO_START = 0xAD
    WRITE_MACRO_PACK = 0xAE
    FACTORY_RESET = 0xAF


#: NewXInput transfers 20 payload bytes per packet.
PKG_SIZE = 20

#: cfgId space: 0..3 normal profiles, 4..7 their Nintendo Switch twins.
PROFILE_COUNT = 4
NS_CFG_BASE = 4
LED_CFG_IOS_ID = 8

#: A dataVersion of 0xFFFF means the slot has never been written.
UNWRITTEN = 0xFFFF


@dataclass
class ProfileVersions:
    """Reply to 0xA1.

    ``ns_raw`` is ``data[14]``, which the SDK stores as
    ``Controller.CurrentConfigIdForNs`` -- and then never reads anywhere in
    the app.  In normal (XInput) mode this device reports 0xFF, so the field
    does **not** tell you which profile is bound to Nintendo Switch mode.
    Treat an unresolvable value as "not reported", never as "nothing bound":
    the Windows app applies an NS binding fire-and-forget and offers no way
    to read it back either.
    """

    active: int = 0                       # profile index 0..3
    active_is_ns: bool = False            # controller currently in NS mode
    data_versions: list = field(default_factory=list)
    ns_raw: int = 0xFF                    # data[14], usually unreadable
    raw: bytes = b""

    @property
    def ns_profile(self) -> int | None:
        """Profile bound to NS mode, when the device actually reports one."""
        if self.ns_raw == 0xFF:
            return None
        idx = self.ns_raw - NS_CFG_BASE if self.ns_raw >= NS_CFG_BASE else self.ns_raw
        return idx if 0 <= idx < PROFILE_COUNT else None

    @property
    def ns_reported(self) -> bool:
        return self.ns_profile is not None

    def written(self, index: int) -> bool:
        return (index < len(self.data_versions)
                and self.data_versions[index] != UNWRITTEN)

    def describe(self) -> str:
        lines = []
        for i in range(PROFILE_COUNT):
            dv = self.data_versions[i] if i < len(self.data_versions) else UNWRITTEN
            marks = []
            if i == self.active:
                marks.append("active")
            if self.ns_profile == i:
                marks.append("Switch mode")
            state = "factory default" if dv == UNWRITTEN else f"dataVersion {dv}"
            tag = ("  <- " + ", ".join(marks)) if marks else ""
            lines.append(f"  Profile {i + 1}   {state}{tag}")
        lines.append("")
        if self.ns_reported:
            lines.append(f"Nintendo Switch mode profile   Profile {self.ns_profile + 1}")
        else:
            lines.append(f"Nintendo Switch mode profile   not reported "
                         f"(data[14]=0x{self.ns_raw:02X})")
            lines.append("  The controller only reports this in NS mode, and the "
                         "Windows app never reads it\n  back either -- an existing "
                         "binding cannot be confirmed from here.")
        if self.active_is_ns:
            lines.append("Controller is currently in Nintendo Switch mode.")
        return "\n".join(lines)


def read_profile_versions(ctl: Controller) -> ProfileVersions:
    """ReadMappingConfigVersionAll (0xA1)."""
    ack = ctl.send_expect(ProfileCmd.READ_VERSIONS, timeout=0.8, retries=4)
    d = ack.raw
    raw_active = d[5]
    is_ns = 4 <= raw_active <= 7
    active = (raw_active - 4) if is_ns else (raw_active if raw_active <= 3 else 0)
    versions = [int.from_bytes(d[6 + 2 * i:8 + 2 * i], "little")
                for i in range(PROFILE_COUNT)]
    return ProfileVersions(active=active, active_is_ns=is_ns,
                           data_versions=versions, ns_raw=d[14], raw=d)


def read_blob(ctl: Controller, cmd_id: int, cfg_id: int, *,
              timeout: float = 1.5) -> bytes:
    """Run one of the chunked config reads (0xA3 / 0xA7 / 0xAC).

    The device announces the blob size in the first reply: ``data[3]`` packets
    of ``PKG_SIZE`` bytes, with the payload starting at ``data[6]`` because
    ``data[5]`` echoes the config id.  Gaps stay 0xFF, matching the SDK's
    pre-filled buffer.
    """
    first = ctl.send_expect(cmd_id, bytes((cfg_id, PKG_SIZE)),
                            timeout=timeout, retries=3)
    total = first.raw[3]
    if total == 0:
        return b""
    buf = bytearray(b"\xff" * (total * PKG_SIZE))

    def absorb(frame: bytes) -> int:
        index = frame[4]
        start = PKG_SIZE * index
        buf[start:start + PKG_SIZE] = frame[6:6 + PKG_SIZE]
        return index

    seen = {absorb(first.raw)}
    last = first.raw
    while last[3] != last[4] + 1:
        raw = ctl._read(timeout)
        if raw is None:
            raise DeviceError(
                f"blob read 0x{cmd_id:02X} stalled after "
                f"{len(seen)}/{total} packets")
        if len(raw) < 7 or raw[0] != 0x5A or raw[1] != 0xA5 or raw[2] != cmd_id:
            continue
        seen.add(absorb(raw))
        last = raw
    missing = sorted(set(range(total)) - seen)
    if missing:
        raise DeviceError(f"blob read 0x{cmd_id:02X} missing packets {missing}")
    return bytes(buf)


def read_mapping_blob(ctl: Controller, cfg_id: int) -> bytes:
    """ReadMappingConfig (0xA3) for one profile.

    **This read has a side effect:** the controller makes the profile it
    just served the active one.  Verified live -- reading cfgId 7 left
    ``0xA1`` reporting profile index 3 as active.  Use
    :func:`read_all_profiles` (or restore the active profile yourself with
    :func:`activate_profile`) whenever that matters.

    cfgId 4..7 alias onto profiles 0..3 for both the returned blob and the
    resulting selection, so the Nintendo Switch bank cannot be read back
    separately.
    """
    return read_blob(ctl, ProfileCmd.READ_MAPPING, cfg_id)


def read_all_profiles(ctl: Controller,
                      indices=None) -> dict[int, bytes]:
    """Read several mapping blobs, leaving the active profile as it was.

    Reading a profile selects it, so the originally-active profile is put
    back afterwards.
    """
    if indices is None:
        indices = range(PROFILE_COUNT)
    before = read_profile_versions(ctl)
    out: dict[int, bytes] = {}
    try:
        for idx in indices:
            out[idx] = read_mapping_blob(ctl, idx)
    finally:
        after = read_profile_versions(ctl)
        if after.active != before.active:
            activate_profile(ctl, before.active)
    return out


def activate_profile(ctl: Controller, cfg_id: int, *,
                     verify: bool = True, attempts: int = 3) -> bool:
    """ApplyMappingConfigByCfgId (0xA2).

    The controller sometimes acknowledges this but does not switch -- seen
    when it arrives immediately after a flash save -- so by default the
    selection is read back and the command retried.
    """
    if not 0 <= cfg_id <= LED_CFG_IOS_ID:
        raise ValueError("cfgId must be 0..8")
    target = cfg_id - NS_CFG_BASE if cfg_id >= NS_CFG_BASE else cfg_id
    for attempt in range(max(1, attempts)):
        ack = ctl.send(ProfileCmd.ACTIVATE, bytes((cfg_id,)),
                       timeout=0.6, retries=3)
        if ack is None:
            continue
        if not verify or cfg_id > 7:
            return True
        time.sleep(0.15 + 0.1 * attempt)
        if read_profile_versions(ctl).active == target:
            return True
    return False


def _random_data_version(exclude: int | None = None) -> int:
    """Pick a fresh 16-bit config tag, as the SDK does (Random.Next(65535))."""
    import random
    while True:
        value = random.randrange(0, 65535)
        if value != exclude:
            return value


def save_config(ctl: Controller, data_version: int | None = None) -> int | None:
    """SaveCurrentMappingConfig (0xA6) -- commit the working config to flash.

    **This stamps whichever profile is currently active**, not a profile of
    your choosing -- verified on hardware by activating profile 4 and
    watching only slot 4's dataVersion change. Callers that have just
    uploaded a blob must make that profile active first, which is what
    :func:`write_profile` does.

    Returns the dataVersion written, or None if the controller never acked.
    Flash writes are slow; the SDK allows ten seconds.
    """
    dv = _random_data_version() if data_version is None else data_version
    payload = int(dv).to_bytes(2, "little")
    ack = ctl.send(ProfileCmd.SAVE, payload, timeout=10.0, retries=3)
    return dv if ack is not None else None


def save_as_ns_profile(ctl: Controller, profile_index: int,
                       data_version: int | None = None) -> int | None:
    """SaveCurrentSwitchMappingConfig (0xAB).

    Binds the working config as the profile the controller uses in Nintendo
    Switch mode, which is what the Windows app's "Apply to NS mode" does.

    The frame is built by hand because the shipping SDK gets it slightly
    wrong and the firmware has only ever been fed those exact bytes: the
    length byte says 0x04 where the payload implies 0x05, and the checksum
    is summed over bytes 3..6 only, leaving the cfgId byte out.  Both quirks
    are reproduced deliberately.
    """
    if not 0 <= profile_index < PROFILE_COUNT:
        raise ValueError(f"profile index must be 0..{PROFILE_COUNT - 1}")
    dv = _random_data_version() if data_version is None else data_version
    cfg_id = NS_CFG_BASE + profile_index

    frame = bytearray(PACKET_SIZE)
    frame[0] = 0x06
    frame[1] = 0x5A
    frame[2] = 0xA5
    frame[3] = ProfileCmd.SAVE_AS_NS
    frame[4] = 0x04                      # sic -- payload is 3 bytes
    frame[5] = dv & 0xFF
    frame[6] = (dv >> 8) & 0xFF
    frame[7] = cfg_id
    frame[8] = sum(frame[3:7]) & 0xFF    # sic -- excludes frame[7]

    ack = ctl.send_raw(bytes(frame), ack_id=ProfileCmd.SAVE_AS_NS,
                       timeout=10.0, retries=3)
    return dv if ack is not None else None


def write_mapping_blob(ctl: Controller, cfg_id: int, blob: bytes, *,
                       progress=None) -> bool:
    """Upload a whole mapping blob with WriteMappingConfig (0xA4 + 0xA5).

    Sends one batch starting at packet 0 covering the entire blob, which is
    what the SDK does whenever it has no cached copy to diff against.
    """
    if not 0 <= cfg_id <= 7:
        raise ValueError("cfgId must be 0..7")
    chunks = [blob[i:i + PKG_SIZE] for i in range(0, len(blob), PKG_SIZE)]
    if not chunks:
        return False
    if len(chunks[-1]) < PKG_SIZE:
        chunks[-1] = chunks[-1] + b"\xff" * (PKG_SIZE - len(chunks[-1]))

    start = bytes((cfg_id, 0, len(chunks), PKG_SIZE))
    if ctl.send(ProfileCmd.WRITE_MAPPING_START, start,
                timeout=1.0, retries=3) is None:
        raise DeviceError("controller did not accept the mapping upload header "
                          f"(0x{ProfileCmd.WRITE_MAPPING_START:02X})")

    for i, chunk in enumerate(chunks):
        if ctl.send(ProfileCmd.WRITE_MAPPING_PACK, bytes((i,)) + chunk,
                    timeout=1.0, retries=3) is None:
            raise DeviceError(f"mapping upload stalled on packet {i}/{len(chunks)}")
        if progress is not None:
            progress(i + 1, len(chunks))
    return True


def factory_reset_profile(ctl: Controller, cfg_id: int) -> bool:
    """ResetMappingConfigByCfgId (0xAF) -- restore one profile to defaults."""
    if not 0 <= cfg_id <= 7:
        raise ValueError("cfgId must be 0..7")
    ack = ctl.send(ProfileCmd.FACTORY_RESET, bytes((cfg_id,)),
                   timeout=10.0, retries=3)
    return ack is not None


def write_profile(ctl: Controller, index: int, blob: bytes, *,
                  persist: bool = True, progress=None) -> int | None:
    """Write a mapping blob to a profile and commit it to flash.

    Mirrors the SDK: stamp a fresh dataVersion into the blob, upload it with
    0xA4/0xA5, then persist with 0xA6 carrying the same dataVersion so the
    blob's embedded tag and the device's record agree.

    Returns the dataVersion written, or None when persisting was skipped.
    """
    from .mapping import OFF_DATA_VERSION

    if not 0 <= index < PROFILE_COUNT:
        raise ValueError(f"profile index must be 0..{PROFILE_COUNT - 1}")

    buf = bytearray(blob)
    dv = None
    if persist:
        current = int.from_bytes(buf[OFF_DATA_VERSION:OFF_DATA_VERSION + 2],
                                 "little")
        dv = _random_data_version(exclude=current)
        buf[OFF_DATA_VERSION:OFF_DATA_VERSION + 2] = dv.to_bytes(2, "little")

    write_mapping_blob(ctl, index, bytes(buf), progress=progress)
    if not persist:
        return None

    # 0xA6 stamps the *active* profile, so make sure that is this one before
    # committing -- otherwise the dataVersion lands on someone else's slot.
    if read_profile_versions(ctl).active != index:
        activate_profile(ctl, index)
        time.sleep(0.2)
        if read_profile_versions(ctl).active != index:
            raise DeviceError(
                f"cannot make profile {index + 1} active to commit it; "
                "refusing to save in case another profile gets stamped")

    if save_config(ctl, dv) is None:
        raise DeviceError("mapping uploaded but the controller did not "
                          "acknowledge the flash save (0xA6)")
    return dv


def read_macro_blob(ctl: Controller, cfg_id: int) -> bytes:
    """ReadMacroConfig (0xAC) for one profile.

    Like the mapping read, **this selects the profile it reads**. Use
    :func:`read_all_macros` when reading several.
    """
    return read_blob(ctl, ProfileCmd.READ_MACRO, cfg_id)


def read_all_macros(ctl: Controller, indices=None) -> dict[int, bytes]:
    """Read several macro blobs, leaving the active profile as it was."""
    if indices is None:
        indices = range(PROFILE_COUNT)
    before = read_profile_versions(ctl)
    out: dict[int, bytes] = {}
    try:
        for idx in indices:
            out[idx] = read_macro_blob(ctl, idx)
    finally:
        if read_profile_versions(ctl).active != before.active:
            activate_profile(ctl, before.active)
    return out


def write_macro_blob(ctl: Controller, cfg_id: int, blob: bytes, *,
                     progress=None) -> bool:
    """Upload a macro blob with WriteMacroConfig (0xAD + 0xAE).

    Same chunked shape as the mapping upload, with the macro opcodes.
    """
    if not 0 <= cfg_id <= 7:
        raise ValueError("cfgId must be 0..7")
    chunks = [blob[i:i + PKG_SIZE] for i in range(0, len(blob), PKG_SIZE)]
    if not chunks:
        return False
    if len(chunks[-1]) < PKG_SIZE:
        chunks[-1] = chunks[-1] + b"\xff" * (PKG_SIZE - len(chunks[-1]))
    start = bytes((cfg_id, 0, len(chunks), PKG_SIZE))
    if ctl.send(ProfileCmd.WRITE_MACRO_START, start,
                timeout=1.0, retries=3) is None:
        raise DeviceError("controller did not accept the macro upload header "
                          f"(0x{ProfileCmd.WRITE_MACRO_START:02X})")
    for i, chunk in enumerate(chunks):
        if ctl.send(ProfileCmd.WRITE_MACRO_PACK, bytes((i,)) + chunk,
                    timeout=1.0, retries=3) is None:
            raise DeviceError(f"macro upload stalled on packet {i}/{len(chunks)}")
        if progress is not None:
            progress(i + 1, len(chunks))
    return True


def bind_macro(ctl: Controller, cfg_id: int, macro, *,
               persist: bool = True) -> int | None:
    """Store a macro on a profile and point its button at it.

    Two blobs have to change: the macro blob gains the recorded steps, and
    the mapping blob's key_table slot for that button is set to
    ``ControllerKey.Macro`` so the controller actually runs it.
    """
    from . import macros as _macros
    from .mapping import KEY_MACRO, MappingBlob

    existing_raw = read_macro_blob(ctl, cfg_id)
    existing = [m for m in _macros.parse(existing_raw)
                if m.button != macro.button]
    if len(existing) + 1 > _macros.MAX_MACROS:
        raise DeviceError(
            f"the controller holds at most {_macros.MAX_MACROS} macros per "
            "profile; remove one first")
    blob = _macros.build(existing + [macro], size=len(existing_raw))
    write_macro_blob(ctl, cfg_id, blob)

    mapping = MappingBlob(read_mapping_blob(ctl, cfg_id))
    mapping.set_key_slot(type(mapping.key_slot(macro.button))(
        slot=macro.button, keyid=KEY_MACRO, type=0, turbo=0))
    return write_profile(ctl, cfg_id, bytes(mapping.data), persist=persist)


def unbind_macro(ctl: Controller, cfg_id: int, button: int, *,
                 persist: bool = True) -> int | None:
    """Remove a macro and restore its button to a plain default."""
    from . import macros as _macros
    from .mapping import MappingBlob

    existing_raw = read_macro_blob(ctl, cfg_id)
    remaining = [m for m in _macros.parse(existing_raw) if m.button != button]
    write_macro_blob(ctl, cfg_id,
                     _macros.build(remaining, size=len(existing_raw)))

    mapping = MappingBlob(read_mapping_blob(ctl, cfg_id))
    if mapping.key_slot(button).keyid == 0x20:
        mapping.remap(button, None)
    return write_profile(ctl, cfg_id, bytes(mapping.data), persist=persist)
