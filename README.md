# flydigi-ctl

Linux control software for the Flydigi Vader 5 Pro, reverse-engineered from
the Windows "Flydigi Space Station" application.

Flydigi ship no Linux software. This talks to the controller directly over
`hidraw` using the same protocol the Windows app uses, so button mappings,
onboard profiles, the Nintendo Switch mode binding and the power settings can
all be managed from Linux.

This is an independent, unofficial project — see `NOTICE.md`. If you are
picking the code up to extend it, start with `CLAUDE.md`, which covers the
architecture and the protocol quirks that are easy to get wrong.

## Requirements

Nothing to install — everything is already present on a normal Arch system:

* Python 3.11+
* PyQt5 (for the GUI only)

No root, and no udev rule on a normal desktop session: `systemd-logind` grants
your session an ACL on the controller's hidraw node. Check with
`getfacl /dev/hidraw3`. If you run headless or over SSH and get a permission
error, add a rule such as:

```
# /etc/udev/rules.d/70-flydigi.rules
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="37d7", MODE="0660", TAG+="uaccess"
```

## Quick start

```sh
./flydigi-cli info          # identity, battery, firmware
./flydigi-cli profiles      # the four onboard profiles
./flydigi-cli keys          # the active profile's button mapping
./flydigi-cli monitor       # live input, to check a mapping works
./flydigi-gui               # desktop interface
```

The GUI has five pages: **Device** (identity, battery, firmware and an
update check), **Profiles** (click a button on the controller to remap it),
**Test** (press buttons and watch them light up), **Macros** (record a
sequence and store it on the controller) and **Settings** (every hardware
toggle, each with the vendor's own explanation behind a `?`).

## Start-menu entry

To get "Flydigi Control" in your application launcher:

```sh
tools/install-desktop-entry.sh
```

Everything goes under `~/.local` — no root, nothing system-wide. The entry
points at wherever this checkout lives, so **re-run it if you move the
project**. Remove it with `tools/install-desktop-entry.sh --uninstall`.

The icon is drawn by `tools/make-icon.py` and is original artwork, so unlike
the controller photo it ships with the project.

## What it can do

| Command | Purpose |
|---|---|
| `list` | show the controller's HID interfaces and which is the control one |
| `info` | model, connection, UID, battery, every firmware version |
| `status` | mapping ownership plus every hardware setting |
| `buttons` | button names and their protocol ids |
| `profiles` | list the four onboard profiles |
| `activate N` | switch the active profile |
| `keys [-p N] [-a]` | show a profile's mapping |
| `remap BTN TARGET` | point a button at another button (`--clear` to undo) |
| `turbo BTN --hz N` | make a button fire repeatedly |
| `rename TITLE` | rename a profile |
| `ns-bind N` | make a profile the Nintendo Switch mode profile |
| `sleep [VALUE]` | read or set the auto-sleep timeout |
| `feature [NAME] [on\|off]` | read or toggle a hardware feature |
| `third-party [on\|off]` | let Steam Input / reWASD take over mapping |
| `monitor` | live physical input, plus what the controller emits |
| `macro-list` | macros stored on a profile |
| `macro-record BTN` | record a sequence and bind it to a button |
| `macro-clear BTN` | remove a button's macro |
| `updates` | installed firmware, and check for newer versions |
| `backup [DIR]` / `restore DIR` | save and reload everything |
| `verify DIR` | check the controller still matches a backup |

Add `--trace` to any command to see every HID frame.

### Examples

```sh
# Swap A/B and X/Y on profile 2, then make it the Switch-mode profile
./flydigi-cli remap A B -p 2
./flydigi-cli remap B A -p 2
./flydigi-cli remap X Y -p 2
./flydigi-cli remap Y X -p 2
./flydigi-cli ns-bind 2

# Hand the mapping to Steam Input
./flydigi-cli third-party on

# Never sleep while docked
./flydigi-cli sleep never

# Back up before experimenting, restore if it goes wrong
./flydigi-cli backup
./flydigi-cli restore backups/20260909-174232
```

Every write command takes `-n` / `--dry-run`.

## Live input testing

The Test page (and `monitor`) read **two** streams at once:

* the controller's **raw input stream** — command `0x11` with the raw-data
  flag, about 490 Hz, tagged `0xEF`. This is the *physical* button state
  before any remapping, and it is the only source that can see the C and Z
  paddles and M1–M6 at all. Enabling it is purely additive, and the flag is
  put back when you stop.
* the kernel's **gamepad device** (`/dev/input/event*`) — what the
  controller actually emits, *after* remapping.

Reading both is what makes a mapping verifiable. Press M5 and it lights up
green (physical) while whatever it is mapped to lights up blue (emitted),
with the translation spelled out as `M5 → A`. A macro button plays its whole
recorded sequence out in blue, which is the only practical way to confirm a
macro does what you meant.

## Macros

Macros are recorded from the live input stream, stored in the controller's
own macro blob and replayed by the controller. **Nothing runs on your PC** —
there is no daemon and no background process.

```sh
./flydigi-cli macro-record M1 --name "TapAB" --type once
# press the buttons you want, then hit Enter
./flydigi-cli macro-list
./flydigi-cli macro-clear M1
```

Behaviours are `once` (play on each press), `held` (repeat while held) and
`toggle` (start and stop on alternate presses). Up to 10 macros per profile,
each with a name of up to 19 bytes.

Because a macro is replayed by the controller it can only press *controller*
buttons. Keyboard and mouse output would need a host-side injector (a
`uinput` daemon running in the background), which is deliberately not built.

## Turning the controller off

There is **no way to shorten the ~5 second Home-button hold**. Nothing in
the vendor SDK or in the app's own settings exposes a hold duration — it is
firmware behaviour. `SleepCommandFactory` exists in the SDK but has only
XInput (`0x16`) and DInput (`0xE4`) implementations and **no NewXInput
variant**, so the Windows app cannot put a Vader 5 Pro to sleep on demand
either.

The closest thing that does work is a short auto-sleep:

```sh
./flydigi-cli sleep 1      # sleeps after a minute of no input
```

"Dock Smart Sleep" would sleep it when docked, but this controller reports
that feature as unsupported.

## Two features worth knowing about

**Nintendo Switch mode profile.** The controller keeps a separate binding for
which profile it uses in NS mode — this is the Windows app's "Apply to NS
mode". `ns-bind N` sets it. Note that **the binding cannot be read back** over
USB while the controller is in XInput mode: the field the protocol exposes for
it (`data[14]` of the `0xA1` reply) reads `0xFF`, and the Windows app never
reads it either. Verify a binding by putting the controller into NS mode.

**Third-party mapping takeover.** `third-party on` is the app's "Allow
third-party apps to take over mappings". With it on, Steam Input or reWASD
drives the mapping and the controller's onboard mapping steps aside; the
controller also stops reporting its own XInput data. Requires firmware
≥ 7.1.4.1.

## Firmware

This tool **cannot flash firmware**, by design — a failed flash is the one
operation that can brick the controller. `updates` reads the installed
versions and asks Flydigi's API what is current; applying an update needs the
Windows app. The check sends the product code, device type and firmware
versions. It does not send the controller's UID.

## Safety notes

* `backup` first. `restore` puts everything back byte-for-byte, and `verify`
  reports any drift.
* Each profile carries a 16-bit **revision tag** (`dataVersion`). The
  controller bumps it itself whenever a setting is changed on the device —
  the FN chords for vibration strength, fast profile swap and on-device
  macro recording all do this — so the tag moving on its own is normal and
  does not mean a mapping changed. `verify` reports a tag-only difference
  separately from a real one.
* Reading a profile's mapping or macro blob **selects that profile**. Use
  `read_all_profiles` / `read_all_macros`, which restore the previous
  selection, rather than looping over the single-blob readers.
* `save_config` (`0xA6`) commits to **whichever profile is currently
  active**, not the one you just uploaded. `write_profile` makes the target
  active first and refuses to save if it cannot — without that, a
  dataVersion lands on the wrong profile.
* `activate_profile` verifies the switch and retries; the controller
  sometimes acknowledges the command without acting on it, particularly
  right after a flash write.
* Config ids 4–7 alias onto profiles 0–3, so the Switch bank is not separately
  readable.
* The following commands are deliberately **not implemented**: `0x1F`
  (firmware upgrade mode), `0xFD` (factory reset of the whole device) and
  `0xFE` (rewrite the device type).
* Adaptive-trigger commands (`0x53`–`0x57`) are gated to the Apex 6 in the
  vendor SDK and do nothing on a Vader 5 Pro, so they are left out.

## Layout

```
flydigi/
  protocol.py   frame building, checksums, ACK parsing
  device.py     hidraw transport, device discovery
  commands.py   device info, settings, profiles, blob transfer
  mapping.py    the 840-byte mapping blob
  macros.py     the macro blob, and recording from live input
  input_monitor.py  the 0xEF live input stream
  help_text.py  per-setting explanations, from the vendor's translations
  pb.py         protobuf codec for the vendor's config files
  updates.py    firmware update checking (read-only)
  enums.py      values lifted from the vendor SDK
  gui/          PyQt5 interface
    controller_view.py  the clickable/live controller picture
```

Protocol documentation lives in `docs/`, and the `.proto` schemas recovered
from the Windows app are in `docs/proto/`. `CLAUDE.md` has the architecture
notes and the list of protocol gotchas.

## Artwork

The controller picture is Flydigi's own product photo and is not
distributed with this project. Extract it from a local Space Station
install if you want it:

```sh
python3 tools/extract-assets.py "/path/to/Space Station/resources/app.asar"
```

Without it the GUI draws a schematic instead; everything still works.
