"""Enumerations lifted from the Windows SDK.

Values come from Flydigi.ControllerSDK.data.model and
Flydigi.SharedResources.Data.Protobuf.
"""

from __future__ import annotations

import enum

from ._devicetypes import DEVICE_TYPE_NAMES


class ConnectType(enum.IntEnum):
    UNKNOWN = 0
    WIRED = 1
    DONGLE = 2
    BLUETOOTH = 3


class ChipType(enum.IntEnum):
    UNKNOWN = 0
    WCH = 1
    TELINK = 2
    KRLY = 3
    NEARLINK = 4
    MEGAHUNT = 5
    PUYA = 6
    ESP = 7
    FREQ = 8


class MotionChipType(enum.IntEnum):
    NONE = 0
    ST = 1
    QST = 2
    SD = 3
    SL = 4


# Device types that are Vader 5 Pro variants.
VADER5_TYPES = {130: "Vader 5 Pro",
                144: "Vader 5 Pro (Dragon Ball)",
                145: "Vader 5 Pro (Honkai 3rd)"}


def device_name(device_type: int) -> str:
    if device_type in VADER5_TYPES:
        return VADER5_TYPES[device_type]
    return DEVICE_TYPE_NAMES.get(device_type, f"Unknown (type {device_type})")


def enum_name(cls, value: int) -> str:
    try:
        return cls(value).name.title()
    except ValueError:
        return f"{value}"
