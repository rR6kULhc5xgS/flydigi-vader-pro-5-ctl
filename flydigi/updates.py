"""Firmware update checking against Flydigi's public API.

This module only ever *reads*. Nothing here can flash the controller --
there is deliberately no code path that writes firmware, because a failed
flash is the one operation on this device that can brick it.

The request mirrors what the Windows app sends: the product code, the
numeric device type, the app version it claims to be, and the currently
installed chip versions. The controller's UID is **not** sent.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field

API_BASE = "https://api.flydigi.com/pc"
FIRMWARE_ENDPOINT = f"{API_BASE}/Update/firmware"
SOFTWARE_ENDPOINT = f"{API_BASE}/Update/software"

#: The Windows build this protocol was reverse-engineered from.
REFERENCE_APP_VERSION = "4.2.0.9"

#: Which chip a version string belongs to, keyed by the API's field name.
CHIP_LABELS = {
    "main_chip": "Controller",
    "dongle_chip": "Dongle",
    "si_chip": "Switch",
    "trigger_chip": "Trigger",
    "screen_chip": "Screen",
    "adc_chip": "ADC",
    "led_chip": "LED",
    "rf_chip": "RF",
}


class UpdateCheckError(Exception):
    pass


def compare_versions(a: str, b: str) -> int:
    """Compare dotted version strings. Returns -1, 0 or 1."""
    def parts(v):
        out = []
        for chunk in str(v).split("."):
            try:
                out.append(int(chunk))
            except ValueError:
                out.append(0)
        return out
    pa, pb = parts(a), parts(b)
    pad = max(len(pa), len(pb))
    pa += [0] * (pad - len(pa))
    pb += [0] * (pad - len(pb))
    return (pa > pb) - (pa < pb)


@dataclass
class ChipUpdate:
    chip: str
    label: str
    installed: str
    latest: str
    notes: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def newer_available(self) -> bool:
        if not self.latest:
            return False
        if not self.installed:
            return True
        return compare_versions(self.latest, self.installed) > 0


def _post(url: str, payload: dict, timeout: float) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json",
                 "User-Agent": "flydigi-ctl (Linux)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise UpdateCheckError(f"{url} returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise UpdateCheckError(f"cannot reach {url}: {exc.reason}") from exc
    except OSError as exc:
        raise UpdateCheckError(f"cannot reach {url}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise UpdateCheckError(
            f"{url} did not return JSON: {text[:200]!r}") from exc


def check_firmware(device_code: str, device_type: int, info, *,
                   app_version: str = REFERENCE_APP_VERSION,
                   timeout: float = 10.0) -> list[ChipUpdate]:
    """Ask Flydigi which firmware versions are current for this controller.

    ``info`` is a :class:`~flydigi.commands.DeviceInfo`; its version strings
    are sent so the API can answer per chip.
    """
    payload = {
        "device_code": device_code,
        "device_id": device_type,
        "app_version": app_version,
    }
    installed = {
        "main_chip": info.firmware or "",
        "dongle_chip": info.dongle_version or "",
        "si_chip": info.switch_version or "",
        "trigger_chip": info.trigger_version or "",
        "screen_chip": info.screen_version or "",
        "adc_chip": info.adc_version or "",
    }
    for key, value in installed.items():
        if value:
            payload[key] = value
    payload.setdefault("main_chip", "")

    data = _post(FIRMWARE_ENDPOINT, payload, timeout)
    chips = data.get("chip_list") or data.get("data", {}).get("chip_list") or {}
    if not isinstance(chips, dict):
        raise UpdateCheckError(f"unexpected reply shape: {str(data)[:200]}")

    out: list[ChipUpdate] = []
    for key, entry in chips.items():
        if not isinstance(entry, dict):
            continue
        latest = str(entry.get("version")
                     or entry.get("newVersion")
                     or entry.get("new_version") or "")
        out.append(ChipUpdate(
            chip=key,
            label=CHIP_LABELS.get(key, key),
            installed=installed.get(key, ""),
            latest=latest,
            notes=str(entry.get("description") or entry.get("remark") or ""),
            raw=entry,
        ))
    return out
