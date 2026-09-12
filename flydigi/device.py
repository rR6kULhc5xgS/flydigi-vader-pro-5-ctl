"""hidraw transport for Flydigi controllers.

The controller exposes several HID interfaces.  Configuration traffic goes
to the vendor-defined one whose report descriptor declares usage page
0xFFA0; the others carry ordinary gamepad input and a wired/dongle marker.
ControllerHidManager.FindSpecialHidDevice picks the same interface on
Windows, matching usage page 0xFFA0 with (pid >> 12) == 2 and
(pid >> 8) != 8.
"""

from __future__ import annotations

import errno
import glob
import os
import select
import time
from dataclasses import dataclass, field

from . import protocol
from .protocol import Ack, PACKET_SIZE

FLYDIGI_VID = 0x37D7

USAGE_PAGE_CONTROL = 0xFFA0   # configuration interface
USAGE_PAGE_WIRED = 0xFFEF     # present when cabled
USAGE_PAGE_DONGLE = 0xFFEE    # present on the 2.4GHz dongle


class DeviceError(Exception):
    pass


class NotFound(DeviceError):
    pass


def _first_usage_page(desc: bytes):
    """Return the first global Usage Page declared in a report descriptor."""
    i = 0
    while i < len(desc):
        b = desc[i]
        i += 1
        if b == 0xFE:                      # long item
            if i >= len(desc):
                break
            size = desc[i]
            i += 2 + size
            continue
        item_type = (b >> 2) & 0x03
        tag = (b >> 4) & 0x0F
        size = {0: 0, 1: 1, 2: 2, 3: 4}[b & 0x03]
        value = int.from_bytes(desc[i:i + size], "little") if size else 0
        i += size
        if item_type == 1 and tag == 0x00:  # Global / Usage Page
            return value
    return None


@dataclass
class HidInterface:
    path: str
    vid: int
    pid: int
    usage_page: int | None
    name: str = ""

    @property
    def is_control(self) -> bool:
        return self.usage_page == USAGE_PAGE_CONTROL


def enumerate_interfaces() -> list[HidInterface]:
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        dev = os.path.join(node, "device")
        uevent = os.path.join(dev, "uevent")
        if not os.path.exists(uevent):
            continue
        vid = pid = None
        name = ""
        try:
            with open(uevent) as fh:
                for line in fh:
                    if line.startswith("HID_ID="):
                        parts = line.strip().split(":")
                        if len(parts) >= 3:
                            vid, pid = int(parts[1], 16), int(parts[2], 16)
                    elif line.startswith("HID_NAME="):
                        name = line.strip().split("=", 1)[1]
        except OSError:
            continue
        if vid is None:
            continue
        usage_page = None
        try:
            with open(os.path.join(dev, "report_descriptor"), "rb") as fh:
                usage_page = _first_usage_page(fh.read())
        except OSError:
            pass
        found.append(HidInterface(
            path="/dev/" + os.path.basename(node),
            vid=vid, pid=pid, usage_page=usage_page, name=name,
        ))
    return found


def find_controllers() -> list[HidInterface]:
    """Control interfaces for every attached Flydigi controller."""
    out = []
    for iface in enumerate_interfaces():
        if iface.vid != FLYDIGI_VID or not iface.is_control:
            continue
        # Mirrors FindSpecialHidDevice's product-id gate.
        if (iface.pid >> 12) != 2 or (iface.pid >> 8) == 8:
            continue
        out.append(iface)
    return out


def detect_connection(vid: int, pid: int) -> str:
    pages = {i.usage_page for i in enumerate_interfaces()
             if i.vid == vid and i.pid == pid}
    if USAGE_PAGE_WIRED in pages:
        return "wired"
    if USAGE_PAGE_DONGLE in pages:
        return "dongle"
    return "unknown"


class Controller:
    """A live connection to one controller's configuration interface."""

    def __init__(self, iface: HidInterface | None = None, *, endpoint: int = protocol.Endpoint.NEW_XINPUT):
        if iface is None:
            candidates = find_controllers()
            if not candidates:
                raise NotFound(
                    "No Flydigi controller found. Is it plugged in and powered on?"
                )
            iface = candidates[0]
        self.iface = iface
        self.endpoint = endpoint
        self._fd: int | None = None
        self.trace = False

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> "Controller":
        try:
            self._fd = os.open(self.iface.path, os.O_RDWR | os.O_NONBLOCK)
        except PermissionError as exc:
            raise DeviceError(
                f"No permission to open {self.iface.path}. "
                "Either your session lost its uaccess ACL, or a udev rule is needed."
            ) from exc
        except OSError as exc:
            raise DeviceError(f"Cannot open {self.iface.path}: {exc}") from exc
        return self

    def close(self) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False

    # -- raw io ------------------------------------------------------------

    def _drain(self, seconds: float = 0.02) -> None:
        deadline = time.time() + seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return
            r, _, _ = select.select([self._fd], [], [], remaining)
            if not r:
                return
            try:
                os.read(self._fd, 64)
            except OSError:
                return

    def _write(self, frame: bytes) -> None:
        out = protocol.for_linux_write(frame)
        if self.trace:
            print(f"  -> {out.hex(' ')}")
        try:
            os.write(self._fd, out)
        except OSError as exc:
            raise DeviceError(f"Write failed: {exc}") from exc

    def _read(self, timeout: float) -> bytes | None:
        deadline = time.time() + timeout
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                return None
            r, _, _ = select.select([self._fd], [], [], remaining)
            if not r:
                return None
            try:
                data = os.read(self._fd, 64)
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                raise DeviceError(f"Read failed: {exc}") from exc
            if self.trace:
                print(f"  <- {data.hex(' ')}")
            return data

    # -- command layer -----------------------------------------------------

    def send(self, cmd_id: int, payload: bytes = b"", *,
             expect_reply: bool = True,
             ack_id: int | None = None,
             timeout: float = 0.5,
             retries: int = 3,
             size: int = PACKET_SIZE) -> Ack | None:
        """Send one command and return its reply.

        ``ack_id`` overrides which command id the reply is expected to carry
        (a few commands answer under a different id).  Unrelated reports that
        arrive meanwhile are skipped rather than mistaken for the answer.
        """
        if self._fd is None:
            raise DeviceError("Controller is not open")
        frame = protocol.build(cmd_id, payload, endpoint=self.endpoint, size=size)
        want = cmd_id if ack_id is None else ack_id

        for attempt in range(max(1, retries)):
            self._drain()
            self._write(frame)
            if not expect_reply:
                return None
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self._read(max(0.0, deadline - time.time()))
                if raw is None:
                    break
                if not protocol.looks_like_ack(raw):
                    continue
                ack = Ack(raw)
                if ack.cmd_id == want:
                    return ack
            # otherwise fall through and retry
        return None

    def send_raw(self, frame: bytes, *, ack_id: int,
                 timeout: float = 0.5, retries: int = 3) -> Ack | None:
        """Send a pre-built frame verbatim.

        Needed for the handful of commands whose frames the Windows SDK
        builds irregularly, where matching it byte-for-byte matters more
        than being internally consistent.
        """
        if self._fd is None:
            raise DeviceError("Controller is not open")
        for _ in range(max(1, retries)):
            self._drain()
            self._write(bytes(frame))
            deadline = time.time() + timeout
            while time.time() < deadline:
                raw = self._read(max(0.0, deadline - time.time()))
                if raw is None:
                    break
                if not protocol.looks_like_ack(raw):
                    continue
                ack = Ack(raw)
                if ack.cmd_id == ack_id:
                    return ack
        return None

    def send_expect(self, cmd_id: int, payload: bytes = b"", **kw) -> Ack:
        ack = self.send(cmd_id, payload, **kw)
        if ack is None:
            raise DeviceError(
                f"No reply to command 0x{cmd_id:02X} after "
                f"{kw.get('retries', 3)} attempt(s)"
            )
        return ack

    def collect(self, cmd_id: int, payload: bytes = b"", *,
                timeout: float = 0.5, max_packets: int = 64) -> list[Ack]:
        """Send a command whose answer spans several packets."""
        first = self.send_expect(cmd_id, payload, timeout=timeout)
        packets = [first]
        if not first.is_multipacket:
            return packets
        while len(packets) < max_packets and not packets[-1].is_finished():
            raw = self._read(timeout)
            if raw is None:
                break
            if not protocol.looks_like_ack(raw):
                continue
            ack = Ack(raw)
            if ack.cmd_id != first.cmd_id:
                continue
            packets.append(ack)
        return packets
