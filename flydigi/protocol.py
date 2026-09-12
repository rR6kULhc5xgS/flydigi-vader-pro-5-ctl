"""Wire protocol for Flydigi controllers (NewXInput family).

Frame layout, as reconstructed from Flydigi.ControllerSdk.dll:

    byte 0   endpoint / HID report-ID slot
             0x06 NewXInput, 0xA5 XInput, 0x05 DInput
    byte 1   0x5A   magic
    byte 2   0xA5   magic
    byte 3   command id
    byte 4   length  == 2 + len(payload)
    byte 5.. payload
    then     additive checksum at index 5 + len(payload)

The checksum is ByteExtension.Crc(arr, 3, 3 + arr[4]) -- a plain
sum of bytes over a half-open range, truncated to 8 bits.

On Linux the kernel consumes byte 0 as the report number and strips it
before the report reaches the wire, so byte 0 must be 0x00 rather than
the endpoint value the Windows build puts there.  Everything from byte 1
on is identical to what the Windows app transmits.
"""

from __future__ import annotations

import enum

PACKET_SIZE = 32
MAGIC_1 = 0x5A
MAGIC_2 = 0xA5


class Endpoint(enum.IntEnum):
    DINPUT = 0x05
    NEW_XINPUT = 0x06
    XINPUT = 0xA5


def checksum(buf, start: int, end: int) -> int:
    """ByteExtension.Crc -- additive, half-open range, mod 256."""
    return sum(buf[start:end]) & 0xFF


def build(cmd_id: int, payload: bytes = b"", *,
          endpoint: int = Endpoint.NEW_XINPUT,
          size: int = PACKET_SIZE) -> bytes:
    """Assemble a command frame exactly as the Windows SDK does."""
    if len(payload) + 6 > size:
        raise ValueError(f"payload of {len(payload)} bytes overflows a {size}-byte frame")
    buf = bytearray(size)
    buf[0] = endpoint
    buf[1] = MAGIC_1
    buf[2] = MAGIC_2
    buf[3] = cmd_id
    buf[4] = 2 + len(payload)
    buf[5:5 + len(payload)] = payload
    buf[5 + len(payload)] = checksum(buf, 3, 3 + buf[4])
    return bytes(buf)


def for_linux_write(frame: bytes) -> bytes:
    """Swap the endpoint byte for the report number the kernel expects."""
    return b"\x00" + frame[1:]


class Ack:
    """A parsed reply.

    Replies arrive without a report-ID prefix:

        byte 0   0x5A
        byte 1   0xA5
        byte 2   command id being answered
        byte 3   total packet count
        byte 4   this packet's index
        byte 5.. payload
        byte -1  additive checksum of bytes 2..30

    Single-packet replies set total=1, index=0.  Some commands answer
    with total=0, in which case the payload starts at byte 4 instead --
    HeartBeat's parser keys off ``index < total`` to decide.
    """

    __slots__ = ("raw", "cmd_id", "total", "index")

    def __init__(self, raw: bytes):
        self.raw = raw
        self.cmd_id = raw[2] if len(raw) > 2 else -1
        self.total = raw[3] if len(raw) > 3 else 0
        self.index = raw[4] if len(raw) > 4 else 0

    @property
    def is_multipacket(self) -> bool:
        return self.index < self.total

    @property
    def body_start(self) -> int:
        """Index at which this reply's real payload begins."""
        return 5 if self.is_multipacket else 4

    @property
    def body(self) -> bytes:
        return self.raw[self.body_start:]

    def checksum_ok(self) -> bool:
        if len(self.raw) < PACKET_SIZE:
            return False
        return self.raw[-1] == checksum(self.raw, 2, PACKET_SIZE - 1)

    def is_finished(self) -> bool:
        """HeartBeat's IsAckFinished rule, which the generic reader reuses."""
        return self.index > self.total or self.index == self.total - 1

    def __repr__(self) -> str:
        return (f"<Ack cmd=0x{self.cmd_id:02X} pkt {self.index}/{self.total} "
                f"csum={'ok' if self.checksum_ok() else 'BAD'} "
                f"{self.raw.hex(' ')}>")


def looks_like_ack(raw: bytes) -> bool:
    return len(raw) >= 5 and raw[0] == MAGIC_1 and raw[1] == MAGIC_2
