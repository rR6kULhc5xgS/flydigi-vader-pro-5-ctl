# Working on flydigi-ctl

Linux control software for the Flydigi Vader 5 Pro, reverse-engineered from
the Windows "Flydigi Space Station" app. This file is the orientation for
anyone (human or Claude) picking the project up later.

Read `README.md` first for what the tool does. This file covers how it was
built, what bit me, and what to be careful with.

## Ground rules

- **Never install anything system-wide.** No `pacman`, no `yay`, no global
  `pip`/`npm`. If a toolchain is needed, download it into a `tools/`
  directory that can be deleted in one command. Detect libraries with
  `ldconfig -p`, `fc-list`, or a Python import, never the package manager.
- **Back up before writing to the controller.** `./flydigi-cli backup`, then
  `./flydigi-cli verify <dir>` afterwards. Restoring is
  `./flydigi-cli restore <dir>`.
- **Verify writes by reading back**, and say plainly when something did not
  verify.
- Do not publish the vendor's decompiled code or assets — see *Publishing*.
- Nothing installs outside the project except the optional desktop entry,
  which writes only to `~/.local/share/{applications,icons}` and is
  removable with `tools/install-desktop-entry.sh --uninstall`.

## Commands that must never be implemented

These exist in the vendor SDK and are deliberately absent here:

| Command | Why not |
|---|---|
| `0x1F` SwitchToFirmwareUpgradeMode | Puts the controller in bootloader mode. A failed flash is the one operation that can brick it. |
| `0xFD` factory reset (whole device) | Wipes everything, not just a profile. `0xAF` resets a single profile and is safe. |
| `0xFE` WriteDeviceType | Rewrites the controller's identity. Nothing good comes of this. |

Firmware *checking* is fine and implemented (`updates.py`); firmware
*writing* is not, and should stay that way.

## Layout

```
flydigi/
  protocol.py       frame building, additive checksums, ACK parsing
  device.py         hidraw transport, interface discovery
  commands.py       device info, settings, profiles, blob transfer
  mapping.py        the 840-byte mapping blob and its key table
  macros.py         the macro blob, and recording from live input
  input_monitor.py  the 0xEF live input stream + the kernel gamepad
  updates.py        firmware update checking (read-only, network)
  pb.py             protobuf codec for the vendor's own config files
  enums.py          values lifted from the vendor SDK
  help_text.py      per-setting help, quoting the vendor's translations
  gui/              PyQt5 interface
    controller_view.py  the clickable / live controller picture
docs/
  protocol-*.md     the byte-level protocol specs
  proto/*.proto     schemas recovered from the app's descriptors
tools/
  extract-assets.py pulls the controller photo from a local install
  make-icon.py      draws the app icon (original artwork, safe to ship)
  install-desktop-entry.sh  adds/removes the launcher entry under ~/.local
```

## Protocol in one page

Target is VID `0x37D7` PID `0x2401`, which the SDK classes as
**NewXInput**. Configuration goes over the HID interface whose report
descriptor declares usage page **`0xFFA0`** (`/dev/hidraw3` here; always
discover it, never hard-code).

Request frame, 32 bytes:

```
[0]  report-ID slot          0x06 on Windows, MUST BE 0x00 on Linux
[1]  0x5A
[2]  0xA5
[3]  command id
[4]  length = 2 + len(payload)
[5.. ] payload
[5+len] checksum = sum(frame[3 : 3+frame[4]]) & 0xFF
```

Reply frame, 32 bytes, **no report-ID prefix**:

```
[0]=0x5A [1]=0xA5 [2]=command id [3]=total packets [4]=packet index
[5..] payload        [31] checksum = sum(reply[2:31]) & 0xFF
```

Chunked blob transfers use 20-byte packets: a Start command carrying
`(cfgId, startIndex, packetCount, 20)` then N Pack commands carrying
`(packNum, 20 bytes)`. Reads answer with N packets, payload at `data[6]`,
finished when `data[3] == data[4] + 1`.

Command ids worth knowing: `0x01` heartbeat, `0x03` hardware status,
`0x04` uid, `0x10`/`0x11` data-report status/set, `0x13` feature toggles,
`0x17` sleep, `0xA1` profile versions, `0xA2` activate, `0xA3`/`0xA4`/`0xA5`
mapping read/write, `0xA6` save, `0xAB` bind NS profile, `0xAC`/`0xAD`/`0xAE`
macro read/write, `0xAF` reset profile, `0xEF` live input marker.

Full detail is in `docs/protocol-*.md`.

## Things that will bite you

Every one of these cost real time to find. They are not in the vendor docs.

1. **Byte 0 must be `0x00` on Linux.** The Windows SDK writes the "endpoint"
   (`0x06`) there, but that byte is the HID report-ID slot. This device
   declares no report IDs, so the kernel requires 0. Send `0x06` and the
   controller silently ignores you — no error, just no reply.

2. **`0xA6` (save) commits to whichever profile is *active*, not the one you
   just uploaded.** Verified by activating profile 4 and watching only slot
   4's tag change. `write_profile()` makes the target active first and
   refuses to save if it cannot. Do not call `save_config()` directly after
   an upload.

3. **Reading a blob selects that profile.** `0xA3` (mapping), `0xAC`
   (macro) and `0xA7` (LED) all leave the profile they read as the active
   one. Use `read_all_profiles()` / `read_all_macros()`, which restore the
   previous selection.

4. **`activate_profile` can acknowledge without acting**, especially right
   after a flash write. It now reads the selection back and retries.

5. **`dataVersion` moves on its own.** It is a 16-bit revision tag, and the
   controller bumps it whenever a setting changes *on the device* — the FN
   chords for vibration strength, fast profile swap, and on-device macro
   recording all do this. A tag-only difference is not a bug and not data
   loss; `./flydigi-cli verify` reports it separately from real drift.

6. **cfgId 4–7 alias onto profiles 0–3.** They are the Nintendo Switch bank
   in the write direction, but reading them returns the normal profile. So
   the NS binding **cannot be read back** over USB in XInput mode. `data[14]`
   of the `0xA1` reply is `CurrentConfigIdForNs`, but the vendor app *writes
   that field and never reads it anywhere*, and it reports `0xFF` here.
   Never report "no NS profile bound" from it — report "not readable".

7. **The live input report is not laid out the way the SDK implies.** The
   SDK's `Button.IsButtonPressed` offsets are for a different report. The
   real `0xEF` layout was derived by correlating against the kernel's evdev
   gamepad: sticks are **signed 16-bit centred on 0** at offsets 3/5/7/9
   (not 8-bit centred on 128), and **triggers are analog bytes at 15 and
   16**, not bitfields. Buttons sit at 11–13, two bytes later than the SDK
   doc suggests. See the docstring in `input_monitor.py`.

8. **The macro blob is 1660 bytes, not the 1620 the docs imply.** The device
   serves 83 packets. Always size a write from what the read returned.

9. **`0xAB` (bind NS profile) has a bug in the vendor SDK that must be
   reproduced.** Its length byte says `0x04` where the payload implies
   `0x05`, and its checksum covers bytes 3–6 only, excluding the cfgId at
   byte 7. The firmware has only ever been fed those bytes. `save_as_ns_profile`
   builds the frame by hand for this reason — do not "fix" it.

10. **Some SDK commands have no NewXInput variant** and cannot work here:
    `ReadAutoSleepPeriod` (use `0x03` byte 9 instead),
    `ReadCurrentMappingConfigId`, `ReadMappingConfigVersion`,
    `SetHardwareMacroEnable`, `DisableMacroMapping`, `TestVibration`. Check
    the factory's `CreateCommand` switch before implementing anything.

11. **The K6 adaptive-trigger block (`0x53`–`0x57`) is dead code** for this
    controller — gated to DeviceType 149 (Apex 6). Do not build UI for it.

12. **A zero in the `0x03` reply means "not configurable"**, which is how the
    app decides whether to show a control. This device reports report-rate 0,
    so polling rate genuinely is not adjustable here.

13. **`EnableMappingSwitch` is the Turbo function**, not a "master mapping"
    switch, and the Steam Input toggle is *none* of the obvious candidates —
    it is `0x11` byte 9 (`ControlByThirdPartyApp`), read back via `0x10`
    byte 9. Requires firmware ≥ 7.1.4.1.

## When the firmware changes

The mapping blob announces its own layout version in its first two bytes
(`MappingBlob.proto_version`), and Flydigi has changed that layout before —
3.1 added the per-stick curve bank at offset 790, 3.2 moved macros into a
separate blob. `SUPPORTED_PROTO_VERSIONS` in `mapping.py` lists what this
code has actually been validated against (currently 3.2 / 770).

If a firmware update bumps it, reads are flagged with a warning in both the
CLI and the GUI, and `write_profile` **refuses** rather than writing offsets
that may no longer be correct. To support a new version: decompile the
current `MappingConfigParser`, diff its offsets against
`docs/protocol-rgb-mapping.md` §2.6, update the `OFF_*` constants and add
the version to `SUPPORTED_PROTO_VERSIONS`.

Blob *sizes* are taken from what the device announces, not hardcoded, so a
longer blob is read in full rather than truncated. The macro blob already
turned out to be 1660 bytes where the docs said 1620.

## Adding a command from a newer Space Station build

The infrastructure is the expensive part and it is already built. Per
command the work is:

1. Extract the new installer and decompile `Flydigi.ControllerSdk.dll`
   (recipe in *Re-deriving the protocol*).
2. Find the relevant `*CommandFactory` and read **only** the NewXInput
   branch — the XInput/DInput ones are for other hardware and several have
   no NewXInput variant at all.
3. Note `CommandId()` and what `CreateCommand()` writes into the payload.
4. Add a function in `commands.py` using `ctl.send(...)`; the framing,
   checksum, retries and chunked transfer are already handled.
5. Test it on profile 4, read it back, restore.

`tools/recover-protos.py` regenerates `docs/proto/` from a new build's
`*Reflection.cs`, which is the fastest way to spot new enums or config
fields between versions.

## Verifying work

There is a real controller involved, so lean on read-back:

```sh
./flydigi-cli backup                       # before anything
./flydigi-cli verify backups/<timestamp>   # after
./flydigi-cli monitor                      # live physical + emitted input
```

For the GUI, render it offscreen and *look at it* rather than assuming:

```sh
QT_QPA_PLATFORM=offscreen python3 -c "...; w.grab().save('/tmp/x.png')"
```

Profile 4 is the conventional scratch slot — it ships as a factory default,
so it is the safe target for testing writes. Restore it afterwards.

## Re-deriving the protocol

The vendor material lives one directory up (`../app`, `../decompiled`,
`../extracted`) on the machine this was built on. None of it is in the repo.
To rebuild that working set:

1. Decompile the .NET assemblies. A self-contained ILSpy works without
   installing anything: download the .NET runtime with `dotnet-install.sh
   --runtime dotnet --install-dir tools/dotnet`, fetch the `ilspycmd` nupkg
   from nuget, unzip it, and run
   `tools/dotnet/dotnet ilspy/tools/net8.0/any/ilspycmd.dll <dll> -o out -p`.
   The interesting assembly is `Flydigi.ControllerSdk.dll`; the service logic
   is in `SpaceStationService.dll`.
2. Extract the Electron frontend from `resources/app.asar`. Header: bytes
   4–8 are the header size, 12–16 the JSON length, JSON at offset 16, and
   **file data starts at `8 + header_size`** (not `16 + json_length` — the
   padding will misalign every file by a few bytes).
3. Recover the protobuf schemas from the `*Reflection.cs` files: each embeds
   a base64 `FileDescriptorProto` inside a `string.Concat(new string[N]{...})`.
   `docs/proto/` is the decoded output.
4. `locales/en/translation.json` in the asar is the source for all the help
   text in `help_text.py`. Prefer the vendor's own wording.

## Not implemented, on purpose

- **LED/RGB editing.** Fully documented in `docs/protocol-rgb-mapping.md`
  (10 zones, 10 animation frames, `0xA7`/`0xA8`/`0xA9`) but never built.
- **Stick curves, rumble tuning, trigger haptics.** Documented, not built.
- **Keyboard/mouse mapping.** The controller only stores a `0xFE` marker;
  the keystroke is produced by host software. That needs a `uinput` daemon
  running in the background, which the user explicitly did not want. Such
  mappings are shown read-only.
- **Firmware flashing.** See above.

## Publishing

This repo is the publishable part. Do **not** commit:

- `../decompiled/` — Flydigi's proprietary code run through a decompiler.
- `../extracted/`, `../app/`, the installer — their binaries and assets.
- `backups/` — contains your controller's UID and MAC (gitignored).
- `flydigi/gui/assets/vader5pro.png` — Flydigi's product photo, not
  redistributable (gitignored). `tools/extract-assets.py` pulls it from a
  local install; without it the GUI draws a schematic instead.

The protocol write-ups in `docs/` are original descriptions written for
interoperability and are fine to publish. The `.proto` files are recovered
interface descriptions containing no vendor code.
