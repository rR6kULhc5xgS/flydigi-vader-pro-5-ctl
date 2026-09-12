# Flydigi Vader 5 Pro — NewXInput Settings & Information Protocol Spec

Reverse-engineered from the decompiled C# SDK at
`decompiled/ControllerSdk/`.

Scope: every command class in
`Flydigi.ControllerSDK.data.command.setting/` and
`Flydigi.ControllerSDK.data.command.information/`,
**NewXInput variant only**.

All numbers are hex unless suffixed with `d`. Byte indices are given relative to
the buffers described in §3 (request) and §4 (ack).

---

# 0. PRIORITY SECTION — top-requested features

Read this section first. It answers the three highest-priority questions directly and
gives extra depth. Full per-command detail for everything else is in §6.

## 0.1 "Let external software handle mapping instead" (Steam Input / reWASD)

### 0.1.1 Answer

**None of the four commonly-assumed candidates implement this.** The feature is
`ControlByThirdPartyApp`, and on the wire it is:

| Direction | Command | Byte | In target dirs? |
|---|---|---|---|
| **Write (set the toggle)** | **`0x11` `EnableRawDataTransportInCommandFactory`** | payload byte `a[9]` | No (`data.command/`) |
| **Read (get the toggle + owning app)** | **`0x10` `ReadRawDataReportStatusCommandFactory`** | ack `data[9]` = enabled, ack `data[10..29]` = owner tag | **Yes** (`data.command.information/`) |
| Claim/release by the external app | `0x1C` `AcquireControllerCommandFactory` | `a[5]` = acquire, `a[6..25]` = tag | No (`data.command/`) |

**Confidence: very high.** Proof chain, end to end:

1. **UI string** (`extracted/asar/.vite/build/locales/en/translation.json`):
   * `setting_controller_control_by_third_party_app` = **"Allow third-party apps to take over mappings"**
   * `setting_controller_control_by_third_party_app_tooltip` = "When enabled, third-party apps can take over XInput mappings"
   * `control_by_third_party_app_description` = *"When the switch is turned on and a third-party application (**such as Steam, reWASD, etc.**) is opened, the controller mapping will be taken over, and all Space Station settings will be invalid at this time. It should be noted that even if Steam or reWASD is running in the background, the mapping will still be taken over."*
2. **UI binding** (`index-BDdWMsEd.js`): the settings-menu entry `12` is
   `setting_controller_control_by_third_party_app`, gated on `controlByThirdPartyAppUsable`,
   and its switch is bound to `controlByThirdPartyAppEnabled`.
3. **IPC**: `IpcCommandEnum_EnableControlByThirdPartyApp = 16`.
4. **Service** (`ControllerRepository.EnableThirdPartyAppControl(uid, enable)`):
   ```csharp
   ControllerSdk.EnableRawDataInput(device, null, null, null, null, /*enableThirdPartyControl*/ enable, ...);
   ```
   Only the 5th nullable flag is set; all others are `null` = "leave unchanged".
5. **SDK** `EnableRawDataTransportInCommandFactory` (NewXInput class, cmd `0x11`) puts that
   flag in `a[9]`.
6. **Readback**: `ControllerRepository.CheckThirdPartyAcquiredController` ->
   `ReadDataReportStatus` -> `ControllerSdk.ReadRawDataReportStatus` (cmd `0x10`), whose
   ack yields `Config.ThirdPartyAppControlConfig.Enabled` and `.ControlBy`.

**Availability gate for the Vader 5 Pro:** this feature requires
**firmware >= 7.1.4.1** on `DeviceCode == "f5"`:

```csharp
// ControllerBusinessService.cs:843-860  and  ControllerRepository.cs:7493
isControlByThirdPartyAppUsable = (DeviceCode == "k5")
    ? DeviceUtil.CompareVersion("7.0.3.0", FirmwareVersion)
    : (DeviceCode == "f5")
        ? DeviceUtil.CompareVersion("7.1.4.1", FirmwareVersion)   // <-- Vader 5 Pro
        : (VendorId == 14295);
```

Get `FirmwareVersion` from the `0x01` HeartBeat ack (§6.9).

### 0.1.2 `0x11` — write the toggle (exact bytes)

`EnableRawDataTransportInCommandFactory.EnableRawDataTransportInCommand`, cmd `0x11` (17d).
Five independent tri-state flags; **`0xFF` means "do not change this field"**:

```csharp
array[4] = 7;                                              // 2 + 5 payload bytes
array[5] = enableControllerData ?? 0xFF ? ...              // 0xFF if null, else 1/0
array[6] = enableRawData        (0x01 / 0x00 / 0xFF)
array[7] = enableKeyboardData   (0x01 / 0x00 / 0xFF)
array[8] = enableMouseData      (0x01 / 0x00 / 0xFF)
array[9] = enableThirdPartyControl (0x01 / 0x00 / 0xFF)    // <-- THE TOGGLE
array[10] = ByteExtension.Crc(array, 3, 3 + array[4]);     // 3 + 7 = 10
```

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten, §2.1) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x11` |
| 4 | `0x07` length |
| 5 | controller-data flag |
| 6 | raw-data flag |
| 7 | keyboard-data flag |
| 8 | mouse-data flag |
| 9 | **third-party-control flag** |
| 10 | checksum |
| 11..31 | `0x00` |

**ON — "external software handles mapping" (payload byte = `0x01`):**
```
06 5A A5 11 07 FF FF FF FF 01 15 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```
checksum = `(0x11+0x07+0xFF+0xFF+0xFF+0xFF+0x01) & 0xFF` = `1045d & 0xFF` = **`0x15`**

**OFF — controller does its own mapping (payload byte = `0x00`):**
```
06 5A A5 11 07 FF FF FF FF 00 14 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```
checksum = `(0x11+0x07+0xFF+0xFF+0xFF+0xFF+0x00) & 0xFF` = `1044d & 0xFF` = **`0x14`**

**ACK:** `IsAck` = `data[2] == 0x11`. `ParseAckData` not overridden. Confirm the new state
by re-reading with `0x10`.

> These are exactly the frames the shipping app sends for this toggle, because
> `EnableThirdPartyAppControl` passes `null` for the other four flags.

### 0.1.3 `0x10` — read the toggle and the owning app (in scope)

See §6.8 for the full spec. The two bytes that matter here:

| ack idx | property | meaning |
|---|---|---|
| `data[9]` | `ThirdPartyAppControlConfig.Enabled = (data[9] == 1)` | is takeover allowed / active |
| `data[10..29]` | `ThirdPartyAppControlConfig.ControlBy` | 20 bytes ASCII, `TrimEnd('\0')` — name/tag of the app currently holding the controller |

Request frame: `06 5A A5 10 02 12 00 ...` (see §6.8).

### 0.1.4 The four assumed candidates — what they actually are

All four are specified precisely below and in §6. Summary verdict:

| Class | Real meaning | NewXInput support | Verdict |
|---|---|---|---|
| `EnableMappingSwitchCommandFactory` (`0x13` sub `0x04`) | **Turbo Function** (rapid-fire) | Yes | **Not** the external-mapping toggle |
| `EnableQuickSwitchConfigCommandFactory` (`0x13` sub `0x01`) | **"Fast swap"** — hotkey to switch between the 4 onboard profiles | Yes | Not it |
| `SetHardwareMacroEnableCommandFactory` (`0x50` sub `0x06`) | onboard macro/mapping engine on/off (legacy) | **No** | Not usable on this device |
| `DisableMacroMappingCommandFactory` (`0x19` / `0xE9`) | one-shot "clear macro mapping" (legacy) | **No** | Not usable on this device |

**(a) `EnableMappingSwitchCommandFactory` = Turbo Function.** Command `0x13`, sub `0x04`.

*Evidence:* in `index-BDdWMsEd.js` the switch bound to `mappingSwitchEnabled` is labelled
`turbo_mapping_title`, and `turbo_mapping_title` = **"Turbo Function"** in the English
translation. The settings-menu entry `9` is likewise
`{9: r("turbo_mapping_title")}` gated on `{9: r.mappingSwitchUsable}`.
Confidence: **high**.

| idx | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| ON | `06` | `5A` | `A5` | `13` | `04` | `04` | **`01`** | `1C` |
| OFF | `06` | `5A` | `A5` | `13` | `04` | `04` | **`00`** | `1B` |

Payload byte for on = **`0x01`**, off = **`0x00`** (positive logic on NewXInput).
Readback: `0x03` ack `data[6]` bit 3 (`0x08`); usable: `data[5]` bit 3.
`IsAck`: `data[2] == 0x13 && data[5] == 0x04`.

> Legacy variants differ in both sub-command and polarity — XInput `0x50` sub `0x06` with
> `(!enable) ? 1 : 0`, DInput `0x50` sub `0x05` with `enable ? 1 : 0`. Do not copy.

**(b) `EnableQuickSwitchConfigCommandFactory` = "Fast swap".** Command `0x13`, sub `0x01`.

*Evidence:* `setting_controller_config_quick_switch` = "Fast Swap Config"; its description
`fast_switch_tip` = *"The controller can carry four onboard configurations. You can quickly
switch to the 1st/2nd/3rd/4th onboard configuration by pressing {{key}} + A/B/X/Y. To use
the function, please open 'Fast swap' in the Setting"*. This is **profile switching**, not
mapping ownership. Confidence: **high**. Relevant to the user's profile/mapping goal, just
not to the Steam-Input toggle.

| idx | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| ON | `06` | `5A` | `A5` | `13` | `04` | `01` | **`01`** | `19` |
| OFF | `06` | `5A` | `A5` | `13` | `04` | `01` | **`00`** | `18` |

Readback: `0x03` ack `data[6]` bit 0 (`0x01`); usable: `data[5]` bit 0.
`IsAck`: `data[2] == 0x13 && data[5] == 0x01`.
Note the factory signature is `CreateCommand(controller, enable)` — **no callback params**,
so the SDK does not wait for the ack.

**(c) `SetHardwareMacroEnableCommandFactory` — NO NewXInput variant.** Both its classes are
byte-identical legacy frames (`CreateSimpleCommand(false)`, 15 bytes, no `0x5A A5` magic):

| idx | value |
|---|---|
| 0 | `0x06` (endpoint; overwritten by `_outReportId`) |
| 1 | `0x50` command ID (80d) |
| 2 | `0x06` sub-command |
| 3 | **`0x00` = enable, `0x01` = disable** (inverted: `(!enable) ? 1 : 0`) |
| 4..14 | `0x00` |

ON: `06 50 06 00 00 ...` (15 bytes) — OFF: `06 50 06 01 00 ...` (15 bytes)

This frame is **rejected by `NewXInputProtocol.ParseData`** (fails the `0x5A`/`0xA5` magic
gate), so it cannot work on a Vader 5 Pro. Note also that its XInput bytes are
**identical** to `EnableMappingSwitchCommandXInput` (`0x50` sub `0x06`, same inverted
polarity) — on the legacy protocol "hardware macro enable" and "mapping switch" were the
same command, which is why the NewXInput `0x13` sub `0x04` inherited the "MappingSwitch"
name while carrying the Turbo label in the UI. It has **no IPC command enum**, i.e. it is
not reachable from the app UI at all.

**(d) `DisableMacroMappingCommandFactory` — NO NewXInput variant.** A payload-less one-shot:

* XInput branch (`ControllerType == XInput`): cmd `0x19` (25d) -> `06 19 00 00 ... ` (15 bytes)
* all other types incl. **NewXInput**: cmd `0xE9` (233d) -> `06 E9 00 00 ...` (15 bytes)

`CreateCommand()` just returns `CreateSimpleCommand(false, null)` with nothing else set —
no sub-command, no payload, no checksum. Again magic-gated out on NewXInput. No IPC enum.
(This class is declared `public abstract` while its siblings are `internal abstract` — a
visibility quirk with no wire effect.)

### 0.1.5 Practical recipe

```
# turn ON "let Steam Input / reWASD handle mapping"
write: 06 5A A5 11 07 FF FF FF FF 01 15 00*21
read:  06 5A A5 10 02 12 00*26        -> ack data[9] should be 0x01
                                      -> ack data[10..29] = owning app tag once one claims it

# turn it OFF (controller applies its own onboard mapping again)
write: 06 5A A5 11 07 FF FF FF FF 00 14 00*21
read:  06 5A A5 10 02 12 00*26        -> ack data[9] should be 0x00
```

Prerequisite: firmware >= 7.1.4.1. There is **no** `usable` bit for this feature in the
`0x03` ack — availability is decided purely from the firmware version string.

## 0.2 Sleep / power

### 0.2.1 Sleep time — **units are MINUTES, 0 = Never**

**Confidence: very high.** The C# SDK passes the value through untouched, but the shipping
UI enumerates the exact radio-button values in `index-BDdWMsEd.js`:

```js
onChange: s => ke(s.target.value, "sleepTime"), value: C.sleepTime
  value:1   -> setting_controller_sleep_minute      "1 min"
  value:5   -> setting_controller_sleep_minute_5    "5 min"
  value:15  -> setting_controller_sleep_minute_15   "15 min"
  value:60  -> setting_controller_sleep_hour        "1 h"
  value:180 -> setting_controller_sleep_hour_3      "3 h"
  value:0   -> setting_controller_sleep_never       "Never"
```

`60` -> "1 h" and `180` -> "3 h" prove the unit is **minutes**; `0` -> "Never" proves the
disable value.

| Meaning | Wire byte | Notes |
|---|---|---|
| **Never sleep** | **`0x00`** (0d) | the "Never" radio option |
| 1 minute | `0x01` | |
| 5 minutes | `0x05` | |
| 15 minutes | `0x0F` | |
| 1 hour | `0x3C` (60d) | |
| 3 hours | `0xB4` (180d) | |

**Range:** the wire field is one byte, so 0..255 minutes (0..4 h 15 min) is expressible.
The SDK performs **no clamping and no validation** — `(byte)time` truncates silently, so
`time = 256` would send `0x00` ("Never"). The six values above are the only ones the
official UI ever sends; other values are likely accepted but unverified.

UI description: `setting_controller_sleep_desc` = *"If the controller is not used for this
period of time, it will enter sleep mode and can be awakened by pressing the home button."*

### 0.2.2 Write sleep time — `0x17` (23d) `UpdateSleepTimeCommandFactory`

Full spec in §6.6. Frames for every UI option:

| Option | idx0 | 1 | 2 | 3 | 4 | 5 (time) | 6 (crc) |
|---|---|---|---|---|---|---|---|
| **Never** | `06` | `5A` | `A5` | `17` | `03` | **`00`** | `1A` |
| 1 min | `06` | `5A` | `A5` | `17` | `03` | `01` | `1B` |
| 5 min | `06` | `5A` | `A5` | `17` | `03` | `05` | `1F` |
| 15 min | `06` | `5A` | `A5` | `17` | `03` | `0F` | `29` |
| 1 h | `06` | `5A` | `A5` | `17` | `03` | `3C` | `56` |
| 3 h | `06` | `5A` | `A5` | `17` | `03` | `B4` | `CE` |

(bytes 7..31 = `0x00`; checksum = `(0x17 + 0x03 + time) & 0xFF`)

### 0.2.3 Read sleep time — use `0x03`, **not** `ReadAutoSleepPeriodCommandFactory`

**`ReadAutoSleepPeriodCommandFactory` has no NewXInput variant and cannot work on a
Vader 5 Pro** — see §6.7 for the full analysis and the exact (non-functional) legacy
frame it would emit. The SDK itself routes around it:

```csharp
// ControllerRepository.ReadHardwareFunctionAsync
if (DeviceCode == "f3" || DeviceCode == "f3p" || (DeviceCode == "k2" && DeviceType != 102))
    ControllerSdk.ReadAutoSleepPeriod(...);   // legacy devices only
else
    ControllerSdk.ReadHardwareFunctionStatus(...);   // "f5" (Vader 5 Pro) takes this path
```

**Correct readback:** send `0x03` (`06 5A A5 03 02 05 00...`) and read **ack `data[9]`** —
same minutes encoding, `0x00` = Never. See §0.3 / §6.1.

### 0.2.4 Report rate — `0x14` (20d) `UpdateReportRateCommandFactory`

Full spec in §6.3. **Valid polling rates and their byte codes** (from the
`ReportRate` protobuf enum, whose numeric values are the wire values — the JS bundle
defines the identical enum, and the service converts with
`Enum.Parse<ReportRate>(intValue.ToString())`, which parses a numeric string as the
underlying value):

| Polling rate | Enum name | **Wire byte** | Full frame (bytes 0..6) |
|---|---|---|---|
| (none / unset) | `ReportRate_None` | `0x00` | `06 5A A5 14 03 00 17` |
| **1000 Hz** | `ReportRate_1000` | **`0x01`** | `06 5A A5 14 03 01 18` |
| **500 Hz** | `ReportRate_500` | **`0x02`** | `06 5A A5 14 03 02 19` |
| **250 Hz** | `ReportRate_250` | **`0x04`** | `06 5A A5 14 03 04 1B` |
| **125 Hz** | `ReportRate_125` | **`0x08`** | `06 5A A5 14 03 08 1F` |

Note the **non-contiguous** encoding: `1, 2, 4, 8` — `0x03` is **not** a valid value.
Readback: `0x03` ack `data[10]`.

> **Conflict worth flagging.** The renderer's polling-rate radio group sends the literal
> values `1, 2, **3**, **4**` for 1000/500/250/125 Hz, which does **not** match the
> `ReportRate` enum (`1, 2, 4, 8`). That UI block is gated to
> `deviceCode === "f4"`, so it is not the Vader 5 Pro's code path, and the C# SDK is
> unambiguous: `array[5] = (byte)reportRate` with the enum's own numeric value.
> **Implement `1 / 2 / 4 / 8`** and treat the JS literals as a renderer-side bug affecting
> Vader 4 only. Worth a live check (§9).
>
> Also note the app hides the polling-rate control unless `reportRate != 0` in the `0x03`
> readback (`reportRateUsable: d.reportRate != 0`) — a device that reports `0x00` has no
> configurable report rate. The same trick gates precision and sensitivity
> (`joystickPrecisionUsable: d.joystickPrecision != 0`,
> `joystickSensitivityUsable: d.joystickSensitivity != 0`).

## 0.3 Device info panel — complete `0x03` response decode

`ReadHardwareFunctionStatusCommandFactory` -> `ReadHardwareFunctionEnableStatusCommandNewXInput`,
command **`0x03`** (3d). This one round-trip populates the entire controller-settings panel.

**Request:** `06 5A A5 03 02 05` + `0x00` * 26

**ACK match:** `data[2] == 0x03`.

**Complete byte/bit map** (ack indices, report ID already stripped — §2.2/§4):

| idx | bit | mask | `ControllerConfig` property | UI name (en) |
|---|---|---|---|---|
| `data[0]` | — | — | — | `0x5A` magic |
| `data[1]` | — | — | — | `0xA5` magic |
| `data[2]` | — | — | — | command id `0x03` |
| `data[3]` | — | — | — | total packet count (`0x01`) |
| `data[4]` | — | — | — | packet index (`0x00`) |
| `data[5]` | 0 | `0x01` | `QuickSwitchConfigUsable` | Fast Swap Config — supported |
| `data[5]` | 1 | `0x02` | `XboxHomeButtonUsable` | Xbox Home button — supported |
| `data[5]` | 2 | `0x04` | `MotionDebounceUsable` | Motion debounce — supported |
| `data[5]` | 3 | `0x08` | `MappingSwitchUsable` | **Turbo Function** — supported |
| `data[5]` | 4 | `0x10` | `JoystickDebounceUsable` | Joystick debounce — supported |
| `data[5]` | 5 | `0x20` | `JoystickAutoCalibrationUsable` | Automatic calibration — supported |
| `data[5]` | 6 | `0x40` | `JoystickReboundUsable` | Rebounce algorithm — supported |
| `data[5]` | 7 | `0x80` | `ScreenConfig.StatusBarAlwaysOnUsable` | Status bar always on — supported |
| `data[6]` | 0 | `0x01` | `QuickSwitchConfigEnabled` | Fast Swap Config — **ON/OFF** |
| `data[6]` | 1 | `0x02` | `XboxHomeButtonEnabled` | Xbox Home button — ON/OFF |
| `data[6]` | 2 | `0x04` | `MotionDebounceEnabled` | Motion debounce — ON/OFF |
| `data[6]` | 3 | `0x08` | `MappingSwitchEnabled` | **Turbo Function — ON/OFF** |
| `data[6]` | 4 | `0x10` | `JoystickDebounceEnabled` | Joystick debounce — ON/OFF |
| `data[6]` | 5 | `0x20` | `JoystickAutoCalibrationEnabled` | Automatic calibration — ON/OFF |
| `data[6]` | 6 | `0x40` | `JoystickReboundEnabled` | Rebounce algorithm — ON/OFF |
| `data[6]` | 7 | `0x80` | `ScreenConfig.StatusBarAlwaysOn` | Status bar always on — ON/OFF |
| `data[7]` | 0 | `0x01` | `ScreenConfig.OffScreenUsable` | Off-screen — supported |
| `data[7]` | 1 | `0x02` | `AudioUsable` | Audio Switch — supported |
| `data[7]` | 2..7 | — | *unused by the SDK* | — |
| `data[8]` | 0 | `0x01` | `ScreenConfig.OffScreen` | Off-screen — ON/OFF |
| `data[8]` | 1 | `0x02` | `AudioEnabled` | Audio Switch — ON/OFF |
| `data[8]` | 2..7 | — | *unused by the SDK* | — |
| `data[9]` | — | byte | `SleepTime` | **Controller Sleep Time — minutes, `0` = Never** |
| `data[10]` | — | byte | `ReportRate` | **Joystick Polling Rate — `1`/`2`/`4`/`8` = 1000/500/250/125 Hz** |
| `data[11]` | — | byte | `JoystickPrecision` | **Accuracy — `1`=8bit `2`=10bit `3`=12bit `4`=9bit `5`=11bit `6`=14bit `7`=16bit** |
| `data[12]` | — | byte | `JoystickSensitivity` | **Center sensitivity — `14`=Fast(Highest) .. `17`=Medium(Middle) .. `19`=Slow(Low), `20`=Lowest** |

Not from the wire: `ResetAllMappingUsable` is hard-set to `true`.
Never set by the NewXInput parser (stay `false`): `DockSmartStopUsable`,
`DockSmartStopEnable`, `JoystickCircleEnable`.
Not in this ack at all: `ControlByThirdPartyApp*` (read via `0x10`, §0.1.3) and
`FirmwareVersion` / battery / MAC (read via `0x01`, §6.9).

**The `usable` bit index equals the `0x13` sub-command id minus 1**, which makes
`data[5]`/`data[6]` the capability/state masks for `0x13` sub-commands `0x01`..`0x08`,
continuing into `data[7]`/`data[8]` bits 0..1 for subs `0x09`..`0x0A`:

| `0x13` sub | usable bit | enabled bit | Feature |
|---|---|---|---|
| `0x01` | `data[5]`.0 | `data[6]`.0 | Fast swap config |
| `0x02` | `data[5]`.1 | `data[6]`.1 | Xbox Home button |
| `0x03` | `data[5]`.2 | `data[6]`.2 | Motion debounce |
| `0x04` | `data[5]`.3 | `data[6]`.3 | Turbo Function |
| `0x05` | `data[5]`.4 | `data[6]`.4 | Joystick debounce |
| `0x06` | `data[5]`.5 | `data[6]`.5 | Joystick auto-calibration |
| `0x07` | `data[5]`.6 | `data[6]`.6 | Joystick rebound |
| `0x08` | `data[5]`.7 | `data[6]`.7 | Screen status bar always on |
| `0x09` | `data[7]`.0 | `data[8]`.0 | Off-screen |
| `0x0A` | `data[7]`.1 | `data[8]`.1 | Audio |

Worked ack (all mask#1 features supported and on; off-screen+audio supported, off-screen
on and audio off; 15 min sleep; 1000 Hz; 12-bit; Medium sensitivity):

```
5A A5 03 01 00 FF FF 03 01 0F 01 03 11 ...
```

**Important behavioural note:** this command overrides `IsAlwaysCanRead() => true`, so once
sent it is retained in `CommandAlwaysCanRead` (cleared only on `ForceReset`/`Dispose`) and
**every later frame with `data[2] == 0x03` is re-parsed and re-fires the callback**. Expect
the device to push unsolicited `0x03` frames when settings change on the hardware side
(e.g. the user presses a shortcut) — a free change-notification channel worth subscribing to.

---

## 1. Device identification and variant selection

| Fact | Value | Source |
|---|---|---|
| VID | `0x37D7` (14295d) | `ControllerHidManager.cs:53`, `UsbDetector.cs:29` |
| PID | `0x2401` | control interface predicate below |
| `ControllerType` | `NewXInput` = 3d | `ControllerType.cs`; selected purely by `VendorId == 14295` |
| Control interface | `UsagePage == 0xFFA0` (65440d), `ProductId >> 12 == 2`, `ProductId >> 8 != 8` | `ControllerHidManager.FindSpecialHidDevice` |
| `DeviceType` | `F5` = 130d (`0x82`), also `F5HK3` = 145d, `F5_DBZ` = 144d | `DeviceType.cs` |
| `DeviceCode` | `"f5"` (from `F5` / `F5HK3`; **`F5_DBZ`=144d is unmapped and yields `""`**) | `FlydigiControllerUtil.GetDeviceCodeById` |
| Capability flags | `Serial=2`, `IsSupportScreen=false`, `IsSupportForceTrigger=false`, `IsSupportLed=true`, `IsSupportMotion=true`, `IsSupportVibration=true`, `IsSupportTriggerVibration=true`, `IsSupportNs=true`, 29 mapped keys | `FlydigiControllerFactory.GenerateControllerVader5` (line 787) |

`ControllerType` is decided solely by VID:

```csharp
// ControllerHidManager.cs:53
ControllerType t = (hid.ManufacturerString == "Microsoft")
    ? ControllerType.XInput
    : ((hid.VendorId != 14295) ? ControllerType.DInput : ControllerType.NewXInput);
```

`IsSupportScreen == false` for the Vader 5 Pro, so the screen-gated SDK entry points
(`EnableScreenStatusBarAlwaysOn`, `OffScreen`, `ReadScreenSetting`) never fire on this
device even though the `0x13` sub-commands `0x08`/`0x09` exist in firmware. The
`ReadHardwareFunctionStatus` ack still reports screen usable/enabled bits (§6.1) — trust
those bits over the SDK's local `IsSupportScreen` flag if you implement screen features.

---

## 2. Transport layer

The NewXInput protocol runs over plain HID (`NewXInputProtocol : HidCommunicationProtocol<Controller>`),
not the XBox filter driver used by the legacy `XInput` variant.

### 2.1 Writing (host -> device)

`HidCommunicationProtocol.WriteDataImpl`:

```csharp
protected override bool WriteDataImpl(byte[] data)
{
    data[0] = _outReportId;          // <-- OVERWRITES byte 0
    _hidDevice.Write(data);
}
```

**Critical:** `AbstractControllerCommand.TakeEndpointByDevice()` returns `0x06` for
NewXInput and `CreateSimpleCommand` stores it at `a[0]`, but `WriteDataImpl` then
**replaces `a[0]` with `_outReportId`**, which is discovered at connect time as the byte
following the **last** `0x85` (HID Report ID) item in the interface's report descriptor:

```csharp
int num2 = reportDescriptor.LastIndexOf((byte)0x85) + 1;
if (num2 != 0) _outReportId = reportDescriptor[num2];
```

*Interpretation:* the `0x06` in `a[0]` is a same-value fallback/documentation artifact.
An independent implementation should write report ID `0x06` (it matches
`TakeEndpointByDevice`), but should confirm against the descriptor of the `0xFFA0`
interface. If the descriptor's last Report ID is not `0x06`, the shipping SDK sends that
value instead, and the device evidently accepts it.

Every NewXInput frame is **32 bytes total including the report-ID byte**
(`CreateSimpleCommand(true, null)` -> `new byte[packetSize ?? maxPacketCount]`, and
`AbstractControllerCommand` passes `maxPacketCount: 32`). Unused trailing bytes are `0x00`.

### 2.2 Reading (device -> host)

`HidCommunicationProtocol._readDataFromDevice`:

```csharp
ReadOnlySpan<byte> raw = _hidDevice.Read(64);
ReadOnlySpan<byte> data = raw;
if (raw[0] == _inReportId)          // _inReportId = byte after the FIRST 0x85 in the descriptor
    data = raw.Slice(1, raw.Length - 1);
```

`NewXInputProtocol` is constructed with `inReportId: 0`, which triggers the
first-`0x85` descriptor scan in `Init()`.

**All ack byte indices in this document are relative to `data`, i.e. AFTER the report-ID
byte has been stripped.** An implementation reading raw HID must drop byte 0 itself.

`NewXInputProtocol.ParseData` gates on the magic and demultiplexes:

```csharp
if (data[0] != 0x5A && (data[1] & 0xFF) != 0xA5) return false;   // not our frame
if ((data[2] & 0xFF) == 0xF7) -> OnOriginDataChanged  (raw/origin data stream)
if ((data[2] & 0xFF) != 0xEF) return true;                        // command ack -> dispatch
// data[2] == 0xEF -> controller input state, OperatorDataParser.Parse
```

So on the NewXInput input pipe:
* `data[2] == 0xEF` = periodic controller-state report (not a command ack)
* `data[2] == 0xF7` = raw/origin data report
* anything else = a command ack, matched by `IsAck` against `data[2]`

### 2.3 Timeouts and retries

From `AbstractCommand`'s ctor defaults and each command's `base(...)` call
(positional order: `controller, action, timeoutAction, maxRetryCount=3, timeoutMs=500`):

| Command | maxRetryCount | timeoutMs |
|---|---|---|
| HeartBeat (`0x01`) | **200** | 500 |
| ReadUid (`0x04`) | **5** | 500 |
| everything else in scope | 3 | 500 |

`CommunicationProtocol` sends one command at a time, arms a `Task.Delay(timeoutMs)`,
and on ack cancels the timer, pops the command, and writes the next queued command.
`IsNeedCallback()` (true when an `action` delegate is present) only affects whether the
retry loop keeps waiting; it does not change the wire bytes.

---

## 3. Request frame format (NewXInput)

`AbstractCommand.CreateSimpleCommand(isNewProtocol: true, packetSize: null)`:

```
byte[] a = new byte[32];
a[0] = 0x06;   // report-ID slot; overwritten by _outReportId at write time (§2.1)
a[1] = 0x5A;
a[2] = 0xA5;
a[3] = CommandId();
```

Each concrete command then fills:

```
a[4]              = 2 + payloadLength          // "length" field
a[5 .. 5+n-1]     = payload (n bytes)
a[3 + a[4]]       = Crc(a, 3, 3 + a[4])        // == a[5 + n]
a[rest]           = 0x00
```

### 3.1 Checksum

```csharp
// Flydigi.Common.util.ByteExtension
public static byte Crc(this byte[] value, int start, int end)
{
    byte b = 0;
    for (int i = start; i < end; i++) b += value[i];   // 8-bit wrapping add
    return b;
}
```

Additive 8-bit sum, `end` exclusive. For a NewXInput request it covers
`a[3] .. a[3 + a[4] - 1]` inclusive = the command ID, the length byte, and the whole
payload. The magic bytes `0x5A 0xA5` and the report ID are **not** covered.

Checksum index = `3 + a[4]` = `5 + payloadLength`. This is confirmed by two
multi-byte-payload commands outside the target directories:

* `AcquireControllerCommandFactory` (`0x1C`): `a[4] = 23` (0x17, 21-byte payload), checksum at `a[26]` = 5+21. ✔
* `EnableRawDataTransportInCommandFactory` (`0x11`): `a[4] = 7` (5-byte payload), checksum at `a[10]` = 5+5. ✔

`UpdateNicknameCommandFactory` **violates** this rule — see §7.1.

### 3.2 Maximum payload

Checksum must land at `5 + n <= 31`, so `n <= 26` bytes and `a[4] <= 28` (0x1C).

---

## 4. ACK frame format (NewXInput)

```
data[0] = 0x5A
data[1] = 0xA5
data[2] = command ID being acked         <-- what every IsAck() tests
data[3] = total packet count
data[4] = packet index (0-based)
data[5 .. ] = payload
```

**Evidence for `data[3]` = count and `data[4]` = index** (rather than a request-style
length byte at `[3]` and payload starting at `[4]`):

1. `ReadMappingConfigCommand.cs:53`, `ReadMacroConfigCommand.cs:58`, `ReadLedConfigCommand.cs:58`:
   `IsAckFinished => data[3] == data[4] + 1` — classic "last packet" test.
2. Those same classes reassemble with
   `Array.Copy(data, 6, controller.multiAckData, _pkgSize * data[4], _pkgSize)` —
   `data[4]` is the multiplier, i.e. the packet index.
3. `HeartBeatCommandFactory` NewXInput:
   ```csharp
   IsAckFinished  => data[4] > data[3] || data[4] == data[3] - 1;
   ParseAckData:  bool flag = data[4] < data[3];   // fragmented form
                  int num = flag ? 5 : 4;           // payload cursor
                  if (flag && data[4] != 0) return; // only packet 0 carries these fields
   ```
   For a normal single-packet ack (`data[3]=1, data[4]=0`): `flag = (0<1) = true` ->
   payload cursor 5, and `data[4]==0` so parsing proceeds. Consistent with (1) and (2).
4. The overwhelming majority of NewXInput `ParseAckData`/`IsAck` bodies read the first
   payload byte at `data[5]`: `ReadHardwareFunctionStatus`, `ReadRawDataReportStatus`,
   `ReadUid`, every `0x13` sub-command echo, `AcquireController`, `K6TriggerStatusCommand`,
   `EnableScreenStatusBarAlwaysOn`, `OffScreen`.

*Interpretation of pointer-deref artifacts:* the decompiler renders span indexing as
`*(byte*)data[N]`; this really means `data[N]` (a `ReadOnlySpan<byte>` element read).
Likewise `*(bool*)data[N]` means `data[N] != 0`, and
`(*(byte*)data[N] & 0xFF)` is a no-op mask on an already-unsigned byte.

**No code anywhere validates an ack checksum.** If the device appends one it is ignored.
Do not rely on its presence or position.

### 4.1 Single-packet ack (all commands in scope except as noted)

`data[3] = 0x01`, `data[4] = 0x00`, payload at `data[5]`.

### 4.2 `IsAckFinished` / `HasMultiAck` in scope

* `HasMultiAck()` — **not overridden by any NewXInput command in either target
  directory.** (Only `ReadUsageCountCommandCommandXInput`, an XInput-only class,
  returns true. `HasMultiAck` merely registers the ID in `MultiAckCmdId` so raw acks are
  forwarded to `IOnRawDataReceivedListener`; it does not change framing.)
* `IsAckFinished()` — overridden only by `HeartBeatControllerCommandNewXInput` (§6.9).
  Everything else inherits `AbstractCommand.IsAckFinished => true`, i.e. one ack completes
  the command.
* `IsAlwaysCanRead()` — overridden only by
  `ReadHardwareFunctionEnableStatusCommandNewXInput` (§6.1), which returns `true`.

---

## 5. Command ID table (NewXInput)

### 5.1 In scope — implemented for NewXInput

| ID | Dec | Feature | Class |
|---|---|---|---|
| `0x01` | 1 | Heartbeat: device type, connect type, MAC, battery, chips, 7 firmware versions | `HeartBeatCommandFactory` |
| `0x02` | 2 | Read nickname / product name | `ReadNicknameCommandFactory` |
| `0x03` | 3 | **Read hardware function status** (all toggles + sleep/report-rate/precision/sensitivity) | `ReadHardwareFunctionStatusCommandFactory` |
| `0x04` | 4 | Read 13-byte UID | `ReadUidCommandFactory` |
| `0x10` | 16 | Read raw-data-report status (XInput/private/keyboard/mouse/3rd-party owner) | `ReadRawDataReportStatusCommandFactory` |
| `0x13` | 19 | **Feature-toggle group** (sub-command at `a[5]`, bool at `a[6]`) | 8 classes, see §5.2 |
| `0x14` | 20 | Set report rate | `UpdateReportRateCommandFactory` |
| `0x15` | 21 | Set joystick precision (bit depth) | `UpdateJoystickPrecisionCommandFactory` |
| `0x16` | 22 | Set joystick sensitivity | `UpdateJoystickSensitivityCommandFactory` |
| `0x17` | 23 | Set auto-sleep time | `UpdateSleepTimeCommandFactory` |
| `0x18` | 24 | Set nickname (**buggy, §7.1**) | `UpdateNicknameCommandFactory` |
| `0x1D` | 29 | Restart / reboot controller | `RestartCommandFactory` |

### 5.2 `0x13` sub-command map

| Sub (`a[5]`) | Feature | Class |
|---|---|---|
| `0x01` | Quick config switch | `EnableQuickSwitchConfigCommandFactory` |
| `0x02` | Xbox Home (Guide) button | `EnableXboxHomeButtonCommandFactory` |
| `0x03` | Motion (gyro) debounce | `EnableMotionDebounceCommandFactory` |
| `0x04` | Mapping switch | `EnableMappingSwitchCommandFactory` |
| `0x05` | Joystick debounce | `EnableJoystickDebounceCommandFactory` |
| `0x06` | Joystick auto-calibration | `EnableJoystickAutoCalibrationCommandFactory` |
| `0x07` | Joystick rebound | `EnableJoystickReboundCommandFactory` |
| `0x08` | Screen status bar always on | `EnableScreenStatusBarAlwaysOnCommandFactory` (out of scope dir; listed for completeness) |
| `0x09` | Off-screen | `OffScreenCommandFactory` (out of scope dir) |
| `0x0A` | Audio | `EnableAudioCommandFactory` |

`0x0B`..`0xFF` unobserved. Sub-command `0x00` unobserved.

### 5.3 In scope — **NO NewXInput implementation**

These factories have no `NewXInput` branch. Called with a NewXInput controller they
silently return a **legacy 15-byte frame with no `0x5A 0xA5` magic**, which the NewXInput
firmware will not parse (see `NewXInputProtocol.ParseData`'s magic gate). Treat every one
of these as **unavailable on the Vader 5 Pro**; the right-hand column gives the working
substitute.

| Class | Falls through to | Substitute on NewXInput |
|---|---|---|
| `ReadAutoSleepPeriodCommandFactory` | DInput `0xF2` sub `0x03` | **`0x03` ReadHardwareFunctionStatus** (`SleepTime` = ack `[9]`) |
| `SetHardwareMacroEnableCommandFactory` | `0x50` sub `0x06` | none found |
| `EnableDockSmartStopCommandFactory` | `0x50` sub `0x10` | none (`DockSmartStopUsable` is never set true by the NewXInput `0x03` parser) |
| `DeviceMaskCommandFactory` | DInput `0x10` | none (note: `0x10` means *ReadRawDataReportStatus* in NewXInput — do not send this frame) |
| `DisableMacroMappingCommandFactory` | DInput `0xE9` | none found |
| `EnableDS5DataCommandFactory` | DInput `0xE8` sub `0x01` | none found |
| `ReadModeUsageCountCommandFactory` | `0x50` sub `0x0C` | none found |
| `ReadUsageCountCommandFactory` | DInput `0xFA` sub `0xA1` | none (note: `0xA1` in NewXInput is *ReadMappingConfigVersionAll*) |
| `DongleInfoCommandFactory` | DInput `0x11` | **`0x01` HeartBeat** -> `controller.DongleVersion` (also: `0x11` in NewXInput is *EnableRawDataTransportIn*) |
| `ExtraInfoCommandFactory` | DInput `0xF5` sub `0x01` | **`0x01` HeartBeat** -> trigger/screen/switch/ADC/NearLink versions |

`ControllerCommunicationThread` corroborates this: the `DongleInfo` and `ExtraInfo`
commands are only queued at connect time when `controller.ControllerType != NewXInput`
(line ~98), while `ReadNickname` (`0x02`) is queued only when it **is** NewXInput (line ~201).

Similarly, `ControllerRepository.ReadHardwareFunctionAsync` routes to
`ReadAutoSleepPeriod` only for `DeviceCode` in {`"f3"`, `"f3p"`, `"k2"`(non-102)}; every
other device — including `"f5"` — uses `ReadHardwareFunctionStatus`.

---

## 6. Per-command specification

### 6.1 `ReadHardwareFunctionStatusCommandFactory` — **0x03** (3d)

**Feature:** single round-trip read of the entire hardware settings page: which features
the firmware supports (*usable*), which are currently on (*enabled*), plus sleep time,
report rate, joystick precision and joystick sensitivity. This is the read-side
counterpart to `0x13`/`0x14`/`0x15`/`0x16`/`0x17` and the **only** way to read sleep time
on a Vader 5 Pro.

Class: `ReadHardwareFunctionEnableStatusCommandNewXInput`.

**Request** — no payload:

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten, §2.1) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x03` command ID |
| 4 | `0x02` length |
| 5 | `0x05` checksum = `Crc(a,3,5)` = `0x03+0x02` |
| 6..31 | `0x00` |

```
06 5A A5 03 02 05 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**Overrides:** `IsNeedCallback() => true`, `IsAlwaysCanRead() => true`.

> `IsAlwaysCanRead()` puts the command into `CommandAlwaysCanRead`, a `HashSet` that is
> only cleared on `ForceReset`/`Dispose`. Consequence: after one
> `ReadHardwareFunctionStatus` call, **every subsequent ack with `data[2] == 0x03` is
> re-parsed and re-fires the callback**, including unsolicited device-pushed `0x03`
> frames. Expect the device to push `0x03` on hardware-side setting changes.

**ACK — `IsAck`:** `data[2] == 0x03`. No sub-command or length check.

**ACK — `ParseAckData`:** four bit-mask bytes then four scalars.

The decompiled bit extraction is:

```csharp
BitArray val = new BitArray(BitConverter.GetBytes((short)data[5]));
bool[] array = new bool[val.Length];   // length 16
val.CopyTo(array, 0);
... array[7], array[6], ... array[0]
```

*Interpretation:* `BitConverter.GetBytes((short)b)` yields `{b, 0x00}` (little-endian), so
`BitArray` index *i* (for i in 0..7) is **bit *i* of `data[N]`**, LSB-first. Equivalent to
`(data[N] >> i) & 1`. Indices 8..15 are always 0 and unused.
(`ByteExtension.GetBitValue` / `GetOneBit` exist but are **dead code — referenced nowhere
in the entire decompiled tree**; all bit work in scope uses `BitArray`.)

**`data[5]` — usable/capability mask #1**

| bit | mask | property |
|---|---|---|
| 0 | `0x01` | `Config.QuickSwitchConfigUsable` |
| 1 | `0x02` | `Config.XboxHomeButtonUsable` |
| 2 | `0x04` | `Config.MotionDebounceUsable` |
| 3 | `0x08` | `Config.MappingSwitchUsable` |
| 4 | `0x10` | `Config.JoystickDebounceUsable` |
| 5 | `0x20` | `Config.JoystickAutoCalibrationUsable` |
| 6 | `0x40` | `Config.JoystickReboundUsable` |
| 7 | `0x80` | `Config.ScreenConfig.StatusBarAlwaysOnUsable` |

**`data[6]` — enabled mask #1** (identical bit order)

| bit | mask | property |
|---|---|---|
| 0 | `0x01` | `Config.QuickSwitchConfigEnabled` |
| 1 | `0x02` | `Config.XboxHomeButtonEnabled` |
| 2 | `0x04` | `Config.MotionDebounceEnabled` |
| 3 | `0x08` | `Config.MappingSwitchEnabled` |
| 4 | `0x10` | `Config.JoystickDebounceEnabled` |
| 5 | `0x20` | `Config.JoystickAutoCalibrationEnabled` |
| 6 | `0x40` | `Config.JoystickReboundEnabled` |
| 7 | `0x80` | `Config.ScreenConfig.StatusBarAlwaysOn` |

Note the bit index maps 1:1 to the `0x13` sub-command id minus 1
(sub `0x01` QuickSwitch -> bit 0, sub `0x02` XboxHome -> bit 1, ... sub `0x08` StatusBar -> bit 7).
That makes `data[5]`/`data[6]` the usable/enabled masks for sub-commands `0x01`..`0x08`.

**`data[7]` — usable/capability mask #2**

| bit | mask | property |
|---|---|---|
| 0 | `0x01` | `Config.ScreenConfig.OffScreenUsable` |
| 1 | `0x02` | `Config.AudioUsable` |
| 2..7 | — | unused by the SDK |

Continuing the pattern: bit 0 -> sub `0x09` (OffScreen), bit 1 -> sub `0x0A` (Audio).

**`data[8]` — enabled mask #2**

| bit | mask | property |
|---|---|---|
| 0 | `0x01` | `Config.ScreenConfig.OffScreen` |
| 1 | `0x02` | `Config.AudioEnabled` |
| 2..7 | — | unused by the SDK |

**Scalars**

| idx | property | semantics |
|---|---|---|
| `data[9]` | `Config.SleepTime` | raw sleep-time byte, same encoding as `0x17`'s payload (§6.5) |
| `data[10]` | `Config.ReportRate` | `ReportRate` enum value (§8.1) |
| `data[11]` | `Config.JoystickPrecision` | `JoystickPrecision` enum value (§8.2) |
| `data[12]` | `Config.JoystickSensitivity` | `JoystickSensitivity` enum value (§8.3) |

Finally `Config.ResetAllMappingUsable = true` (hard-coded, not from the wire), then
`action.Invoke(controller.Config)`.

**Not set by the NewXInput parser** (remain at their defaults, i.e. `false`):
`DockSmartStopUsable`, `DockSmartStopEnable`, `JoystickCircleEnable`.

Worked ack example — everything usable, everything on except audio, 5-unit sleep,
1000 Hz, 12-bit, Middle sensitivity:

```
      [0] [1] [2] [3] [4] [5] [6] [7] [8] [9] [10][11][12]
      5A  A5  03  01  00  FF  FF  03  01  05  01  03  11
```
decodes to: all of mask#1 usable and enabled; OffScreen+Audio usable; OffScreen on,
Audio off; SleepTime=5, ReportRate=`_1000`, Precision=`_12Bit`, Sensitivity=`Middle`.

**Duplicate-write artifact:** the decompiled body parses `data[7]` twice — first writing
only `OffScreenUsable`, then re-parsing it and writing `OffScreenUsable` **and**
`AudioUsable`. Harmless (same value assigned twice). Interpretation: leftover from an
incremental edit; implement it once.

**Other-variant note (do not implement, for orientation only):** the XInput variant
(`0x50` sub `0x07`) and DInput variant (`0xF2` sub `0x03`) use **inverted booleans**
(`Enabled = (byte == 0)`) and a flat one-byte-per-setting layout with sleep at `[20]`/`[7]`.
The NewXInput variant uses **positive logic** (bit set = enabled). Do not carry the
inversion over.

---

### 6.2 `0x13` (19d) — feature toggle group

Eight of the classes in the setting directory share command ID `0x13` and differ only in
the sub-command byte. Generic form:

**Request:**

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x13` |
| 4 | `0x04` length (= 2 + 2 payload bytes) |
| 5 | sub-command id |
| 6 | `0x01` = enable, `0x00` = disable |
| 7 | checksum = `Crc(a,3,7)` = `(0x13 + 0x04 + sub + enable) & 0xFF` |
| 8..31 | `0x00` |

Source (identical in all eight, only the constant differs):

```csharp
array[4] = 4;
array[5] = <SubCommandId>;
array[6] = (byte)(enable ? 1u : 0u);
array[7] = ByteExtension.Crc(array, 3, 3 + array[4]);
```

**ACK — `IsAck`:** `data[2] == 0x13 && data[5] == <SubCommandId>`.
The sub-command echo at `data[5]` is the first payload byte, matching §4. This means acks
for different `0x13` sub-commands are correctly disambiguated.

**ACK — `ParseAckData`:** none of the eight overrides it. The base implementation only
logs. **There is no success/failure indication consumed by the SDK.** `data[6]` in the ack
presumably echoes the new state (untested — see §9).

**`enable` polarity is POSITIVE for NewXInput.** Note carefully that the XInput/DInput
variants of Debounce/Rebound/AutoCalibration/MappingSwitch/MotionDebounce write
`(!enable) ? 1 : 0` — inverted. Do not copy that.

**Concrete frames** (bytes 0..7; bytes 8..31 are `0x00`):

| Feature | Class | Sub | enable=1 | enable=0 |
|---|---|---|---|---|
| Quick config switch | `EnableQuickSwitchConfigCommandFactory` | `0x01` | `06 5A A5 13 04 01 01 19` | `06 5A A5 13 04 01 00 18` |
| Xbox Home button | `EnableXboxHomeButtonCommandFactory` | `0x02` | `06 5A A5 13 04 02 01 1A` | `06 5A A5 13 04 02 00 19` |
| Motion debounce | `EnableMotionDebounceCommandFactory` | `0x03` | `06 5A A5 13 04 03 01 1B` | `06 5A A5 13 04 03 00 1A` |
| Mapping switch | `EnableMappingSwitchCommandFactory` | `0x04` | `06 5A A5 13 04 04 01 1C` | `06 5A A5 13 04 04 00 1B` |
| **Joystick debounce** | `EnableJoystickDebounceCommandFactory` | `0x05` | `06 5A A5 13 04 05 01 1D` | `06 5A A5 13 04 05 00 1C` |
| **Joystick auto-calibration** | `EnableJoystickAutoCalibrationCommandFactory` | `0x06` | `06 5A A5 13 04 06 01 1E` | `06 5A A5 13 04 06 00 1D` |
| **Joystick rebound** | `EnableJoystickReboundCommandFactory` | `0x07` | `06 5A A5 13 04 07 01 1F` | `06 5A A5 13 04 07 00 1E` |
| Audio | `EnableAudioCommandFactory` | `0x0A` | `06 5A A5 13 04 0A 01 22` | `06 5A A5 13 04 0A 00 21` |

Per-command notes:

* **`EnableJoystickDebounceCommandFactory` (sub `0x05`)** — human-facing "joystick
  debounce"/anti-jitter filter. `const byte SubCommandId = 5;` present in the class.
  Readback: `0x03` ack `data[6]` bit 4 (usable: `data[5]` bit 4).
* **`EnableJoystickAutoCalibrationCommandFactory` (sub `0x06`)** — stick auto-centering /
  auto-calibration. `const byte SubCommandId = 6;`.
  Readback: `0x03` ack `data[6]` bit 5 (usable: `data[5]` bit 5).
* **`EnableJoystickReboundCommandFactory` (sub `0x07`)** — stick "rebound"/snap-back
  compensation. `const byte SubCommandId = 7;`.
  Readback: `0x03` ack `data[6]` bit 6 (usable: `data[5]` bit 6).
* `EnableQuickSwitchConfigCommandFactory` (sub `0x01`) — factory signature is
  `CreateCommand(controller, enable)` only; **no `action`/`timeoutAction`**, so
  `IsNeedCallback()` is false and the SDK fires it without waiting for the ack.
* `EnableAudioCommandFactory` (sub `0x0A`) — factory **unconditionally** returns the
  NewXInput class for *every* controller type (`return new EnableAudioCommandNewXInput(...)`);
  its XInput (`0xA2`) and DInput (`0xFA` sub `0xA2`) classes are dead code. Also no
  `action` parameter.
* `EnableXboxHomeButtonCommandFactory` (sub `0x02`) — the NewXInput class is correct but
  **unreachable through the public SDK API**: `ControllerSdk.EnableXboxHomeButtonImpl`
  guards with `if (controller.ControllerType == ControllerType.XInput)`. See §7.3. The
  frame above is still the right thing to send directly.
* `EnableMotionDebounceCommandFactory` (sub `0x03`), `EnableMappingSwitchCommandFactory`
  (sub `0x04`) — nothing unusual.

---

### 6.3 `UpdateReportRateCommandFactory` — **0x14** (20d)

**Feature:** USB/RF polling (report) rate.

Class: `UpdateReportRateCommandNewXInput`. Parameter: `ReportRate reportRate`
(`Flydigi.SharedResources.Data.Protobuf.ReportRate`), written as `(byte)reportRate`.

**Request:**

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x14` |
| 4 | `0x03` length |
| 5 | `(byte)reportRate` |
| 6 | checksum = `Crc(a,3,6)` = `(0x14 + 0x03 + rate) & 0xFF` |
| 7..31 | `0x00` |

**Parameter values** (§8.1): `0`=None, `1`=1000 Hz, `2`=500 Hz, `4`=250 Hz, `8`=125 Hz.
Note the non-contiguous, mask-like encoding — value `3` is **not** valid.

Worked example, 1000 Hz (`ReportRate._1000` = 1):

```
06 5A A5 14 03 01 18 00 ...
```
All four rates:

| Rate | payload | checksum | frame (0..6) |
|---|---|---|---|
| 1000 Hz | `0x01` | `0x18` | `06 5A A5 14 03 01 18` |
| 500 Hz | `0x02` | `0x19` | `06 5A A5 14 03 02 19` |
| 250 Hz | `0x04` | `0x1B` | `06 5A A5 14 03 04 1B` |
| 125 Hz | `0x08` | `0x1F` | `06 5A A5 14 03 08 1F` |

**ACK:** `IsAck` = `data[2] == 0x14`. `ParseAckData` not overridden (log only).
Readback via `0x03` ack `data[10]`.

---

### 6.4 `UpdateJoystickPrecisionCommandFactory` — **0x15** (21d)

**Feature:** analog-stick reporting resolution (bit depth).

Class: `UpdateJoystickPrecisionCommandNewXInput`. Parameter:
`JoystickPrecision precision`, written as `(byte)precision`.

**Request:**

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x15` |
| 4 | `0x03` length |
| 5 | `(byte)precision` |
| 6 | checksum = `(0x15 + 0x03 + precision) & 0xFF` |
| 7..31 | `0x00` |

**Parameter values** (§8.2) — note the enum order is **not** monotonic in bit depth:

| Value | Enum | Bit depth |
|---|---|---|
| `0x00` | `None` | — |
| `0x01` | `_8Bit` | 8 |
| `0x02` | `_10Bit` | 10 |
| `0x03` | `_12Bit` | 12 |
| `0x04` | `_9Bit` | 9 |
| `0x05` | `_11Bit` | 11 |
| `0x06` | `_14Bit` | 14 |
| `0x07` | `_16Bit` | 16 |

Worked example, 12-bit (`_12Bit` = 3): `06 5A A5 15 03 03 1B 00 ...`

All values:

| Value | frame (0..6) |
|---|---|
| `0x01` 8-bit | `06 5A A5 15 03 01 19` |
| `0x02` 10-bit | `06 5A A5 15 03 02 1A` |
| `0x03` 12-bit | `06 5A A5 15 03 03 1B` |
| `0x04` 9-bit | `06 5A A5 15 03 04 1C` |
| `0x05` 11-bit | `06 5A A5 15 03 05 1D` |
| `0x06` 14-bit | `06 5A A5 15 03 06 1E` |
| `0x07` 16-bit | `06 5A A5 15 03 07 1F` |

**ACK:** `IsAck` = `data[2] == 0x15`. `ParseAckData` not overridden.
Readback via `0x03` ack `data[11]`.

*Note:* this class's NewXInput ctor declares `Action? action, Action? timeoutAction`
without defaults (unlike its siblings) — a signature quirk with no wire effect.

---

### 6.5 `UpdateJoystickSensitivityCommandFactory` — **0x16** (22d)

**Feature:** analog-stick response curve / sensitivity.

Class: `UpdateJoystickSensitivityCommandNewXInput`. Parameter:
`JoystickSensitivity sensitivity`, written as `(byte)sensitivity`.

**Request:**

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x16` |
| 4 | `0x03` length |
| 5 | `(byte)sensitivity` |
| 6 | checksum = `(0x16 + 0x03 + sensitivity) & 0xFF` |
| 7..31 | `0x00` |

**Parameter values** (§8.3) — a 7-step scale starting at **14d**, where a *larger* wire
value means *lower* sensitivity:

| Value | Hex | Enum |
|---|---|---|
| 0 | `0x00` | `None` |
| 14 | `0x0E` | `Highest` |
| 15 | `0x0F` | `High` |
| 16 | `0x10` | `MiddleHigh` |
| 17 | `0x11` | `Middle` |
| 18 | `0x12` | `LowMiddle` |
| 19 | `0x13` | `Low` |
| 20 | `0x14` | `Lowest` |

Worked example, `Middle` (17d = `0x11`): `06 5A A5 16 03 11 2A 00 ...`

All steps:

| Enum | frame (0..6) |
|---|---|
| `Highest` (`0x0E`) | `06 5A A5 16 03 0E 27` |
| `High` (`0x0F`) | `06 5A A5 16 03 0F 28` |
| `MiddleHigh` (`0x10`) | `06 5A A5 16 03 10 29` |
| `Middle` (`0x11`) | `06 5A A5 16 03 11 2A` |
| `LowMiddle` (`0x12`) | `06 5A A5 16 03 12 2B` |
| `Low` (`0x13`) | `06 5A A5 16 03 13 2C` |
| `Lowest` (`0x14`) | `06 5A A5 16 03 14 2D` |

**ACK:** `IsAck` = `data[2] == 0x16`. `ParseAckData` not overridden.
Readback via `0x03` ack `data[12]`.

**ID-collision warning:** `0x16` is also `SleepCommandFactory`'s *XInput* command id
("sleep now"). Those are different protocols (XInput endpoint `0xA5`, legacy 15-byte
frame) so there is no ambiguity on the NewXInput pipe — but do not mix the tables.

---

### 6.6 `UpdateSleepTimeCommandFactory` — **0x17** (23d)

**Feature:** auto-sleep (idle power-off) timer.

Class: `UpdateSleepTimeCommandNewXInput`. Parameter: `int time`, written as `(byte)time`
with **no scaling, no clamping, no validation** anywhere in the SDK — the value travels
verbatim from the public API `ControllerSdk.UpdateSleepTime(Controller, int time)` through
`UpdateSleepTimeImpl(controller, period)` and the protobuf `UpdateSleepTimeParams.Time`
(`int32`) into `a[5]`.

**Request:**

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x17` |
| 4 | `0x03` length |
| 5 | `(byte)time` |
| 6 | checksum = `(0x17 + 0x03 + time) & 0xFF` |
| 7..31 | `0x00` |

Worked example, `time = 5`: `06 5A A5 17 03 05 1F 00 ...`

More values:

| `time` | payload | checksum | frame (0..6) |
|---|---|---|---|
| 0 | `0x00` | `0x1A` | `06 5A A5 17 03 00 1A` |
| 1 | `0x01` | `0x1B` | `06 5A A5 17 03 01 1B` |
| 3 | `0x03` | `0x1D` | `06 5A A5 17 03 03 1D` |
| 5 | `0x05` | `0x1F` | `06 5A A5 17 03 05 1F` |
| 10 | `0x0A` | `0x24` | `06 5A A5 17 03 0A 24` |
| 15 | `0x0F` | `0x29` | `06 5A A5 17 03 0F 29` |
| 30 | `0x1E` | `0x38` | `06 5A A5 17 03 1E 38` |
| 255 | `0xFF` | `0x19` | `06 5A A5 17 03 FF 19` |

**UNITS ARE NOT DETERMINABLE FROM THE SDK.** Everything downstream is a raw `int`:
`ControllerConfig.SleepTime` is `int`, the protobuf field is `int32`, and no table,
multiplier, or named constant exists anywhere in the decompiled tree. Working
hypotheses, in order of likelihood:

1. **Minutes** — the field is called *AutoSleepPeriod* on the read side and the legacy
   `0x30`/`0xF2` read path exposes the same byte, which typical Flydigi UIs present as a
   minutes picker (1/3/5/10/15/30). A single byte gives 0–255 min.
2. `0` = "never sleep" / disabled. Untested.
3. An index into a firmware-side table rather than a direct duration.

This must be resolved on a live device (§9). Note the round-trip is lossless: write
`0x17` with byte *N*, read `0x03` and expect `data[9] == N`, which lets you enumerate the
accepted value set empirically.

**ACK:** `IsAck` = `data[2] == 0x17`. `ParseAckData` not overridden (log only).
Factory signature has no `action` parameter exposed through `ControllerSdk`
(`UpdateSleepTimeImpl` passes none), so the SDK does not wait for the ack.

**Readback:** `0x03` ReadHardwareFunctionStatus, ack `data[9]` — **not**
`ReadAutoSleepPeriodCommandFactory`, which has no NewXInput variant (§6.7).

---

### 6.7 `ReadAutoSleepPeriodCommandFactory` — **NO NewXInput VARIANT**

**Read this section before implementing sleep-time readback.**

The factory contains only two classes and this selector:

```csharp
public static AbstractControllerCommand CreateCommand(Controller controller, ...)
{
    if (controller.ControllerType == ControllerType.XInput)
        return new ReadAutoSleepPeriodCommandXInput(controller, action, timeoutAction);
    return new ReadAutoSleepPeriodCommandDInput(controller, action, timeoutAction);
}
```

There is **no `ControllerType.NewXInput` branch**. A Vader 5 Pro therefore gets
`ReadAutoSleepPeriodCommandDInput`, which builds a **legacy** frame via
`CreateSimpleCommand(false, null)` — 15 bytes, **no `0x5A 0xA5` magic**:

| idx | value |
|---|---|
| 0 | `0x06` (`TakeEndpointByDevice()` for NewXInput; then overwritten by `_outReportId`) |
| 1 | `0xF2` command ID (242d) |
| 2 | `0x03` sub-command |
| 3..14 | `0x00` |

```
06 F2 03 00 00 00 00 00 00 00 00 00 00 00 00        (15 bytes, not 32)
```

`NewXInputProtocol.ParseData` rejects anything whose `data[0] != 0x5A && data[1] != 0xA5`,
and its ack matcher is `data[2] == 0xF2 && data[3] == 0x03` — indices that mean
"command id" and "packet count" in the NewXInput ack layout. **This command cannot work
on a Vader 5 Pro.** Confirmed by the caller:

```csharp
// ControllerRepository.ReadHardwareFunctionAsync
if (DeviceCode == "f3" || DeviceCode == "f3p" || (DeviceCode == "k2" && DeviceType != 102))
    ControllerSdk.ReadAutoSleepPeriod(...);
else
    ControllerSdk.ReadHardwareFunctionStatus(...);   // <-- "f5" goes here
```

**Use `0x03` (§6.1) instead.** For reference, the two legacy variants parse:

*XInput (`0x30` sub `0x04`)* — `IsAck`: `data[15] == 0x30 && data[16] == 0x04`
| idx | property |
|---|---|
| `data[17]` | `Config.SleepTime` |
| `data[18]` | `Config.QuickSwitchConfigEnabled = (data[18] == 1)` |
(also hard-sets `QuickSwitchConfigUsable = true`)

*DInput (`0xF2` sub `0x03`)* — `IsAck`: `data[2] == 0xF2 && data[3] == 0x03`
| idx | property |
|---|---|
| `data[4]` | `Config.SleepTime` |
| `data[5]` | `Config.QuickSwitchConfigEnabled = (data[5] == 1)` |

Both call `IsNeedCallback() => true` (XInput) / rely on `action` being non-null, and both
ctors pass `base(controller, null, timeoutAction)`.

---

### 6.8 `ReadRawDataReportStatusCommandFactory` — **0x10** (16d)

**Feature:** reports which data streams the controller is emitting and, critically for
§0.1, **whether third-party mapping takeover is enabled and which app currently holds the
controller**. Read counterpart of `0x11` `EnableRawDataTransportInCommandFactory` (§0.1.2).

Class: `ReadRawDataReportStatusCommand`. The factory has a **single class** and returns it
unconditionally for every controller type
(`return new ReadRawDataReportStatusCommand(...)`), so it is NewXInput-only by design.

**Request** — no payload:

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x10` |
| 4 | `0x02` length |
| 5 | `0x12` checksum = `0x10 + 0x02` |
| 6..31 | `0x00` |

```
06 5A A5 10 02 12 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**Overrides:** `IsNeedCallback() => true`.

**ACK — `IsAck`:** `data[2] == 0x10`.

**ACK — `ParseAckData`** (field by field):

| idx | expression | property | meaning |
|---|---|---|---|
| `data[5]` | `== 1` | `Config.XInputEnabled` | controller emits standard XInput reports |
| `data[6]` | `== 1` | `Config.PrivateDataEnabled` | controller emits Flydigi private/raw data reports |
| `data[7]` | `== 1` | `Config.KeyboardEnabled` | controller emits HID keyboard reports |
| `data[8]` | `== 1` | `Config.MouseEnabled` | controller emits HID mouse reports |
| `data[9]` | `== 1` | `Config.ThirdPartyAppControlConfig.Enabled` | **third-party mapping takeover allowed (§0.1)** |
| `data[10..29]` | `Encoding.ASCII.GetString(data.Slice(10, 20)).TrimEnd('\0')` | `Config.ThirdPartyAppControlConfig.ControlBy` | **20-byte NUL-padded ASCII tag of the owning app** |

Payload is 25 bytes (`data[5]`..`data[29]`), exactly mirroring `0x11`'s five flags plus the
20-byte tag written by `0x1C AcquireController`.

The callback is invoked as `action?.Invoke(device.Config)`.

*Note:* the ctor is `base(controller, null, timeoutAction)` — the `Action<ControllerConfig>`
lands in the derived `action` field, not the base `Action`, hence the `IsNeedCallback`
override.

Worked ack — XInput on, private data off, no keyboard/mouse, takeover enabled and held by
`"Steam"`:

```
      [0] [1] [2] [3] [4] [5] [6] [7] [8] [9] [10..29]
      5A  A5  10  01  00  01  00  00  00  01  53 74 65 61 6D 00 00 ... 00
```
-> `XInputEnabled=true`, `PrivateDataEnabled=false`, `KeyboardEnabled=false`,
`MouseEnabled=false`, `ThirdPartyAppControlConfig.Enabled=true`, `ControlBy="Steam"`.

---

### 6.9 `HeartBeatCommandFactory` — **0x01** (1d)

**Feature:** the connect-time and periodic status poll. Single richest read in the
protocol: device model, connection type, MAC, battery, both chip types, and **seven
independent firmware version strings**. On NewXInput this **replaces** both
`DongleInfoCommandFactory` and `ExtraInfoCommandFactory` (§6.14).

Class: `HeartBeatControllerCommandNewXInput`. `maxRetryCount = 200` (the constructor's 4th
positional argument), timeout 500 ms — by far the most persistent command in the SDK.

**Request** — no payload:

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x01` |
| 4 | `0x02` length |
| 5 | `0x03` checksum = `0x01 + 0x02` |
| 6..31 | `0x00` |

```
06 5A A5 01 02 03 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**ACK — `IsAck`:** `data[2] == 0x01`.

**ACK — `IsAckFinished`** (the only override in scope):

```csharp
return data[4] > data[3] || data[4] == data[3] - 1;
```

i.e. finished when the packet index is the last one (`index == count - 1`), or when
`index > count` (a degenerate form where the two bytes are not a count/index pair).
For the normal single-packet ack (`data[3]=1, data[4]=0`) this is `0 == 0` -> true.

**ACK — `ParseAckData`.** The cursor logic:

```csharp
bool flag = data[4] < data[3];   // true for the fragmented/normal form
int num = flag ? 5 : 4;          // payload cursor
if (flag && data[4] != 0) return;  // only packet index 0 carries these fields
```

*Interpretation:* `num` starts at **5** for a normal ack (`data[3]=1, data[4]=0`), giving the
map below. The `num = 4` branch is a compatibility path for a device that answers without
the packet-index byte; it shifts every index in the table down by one. Implement the
`num = 5` form and treat `data[4] >= data[3]` as the legacy fallback.

Resolving the `num++` sequence (single-packet form):

| idx | property | decode |
|---|---|---|
| `data[5]` | `DeviceType` -> `FlydigiControllerFactory.GenerateController` | `0x82`=130d `F5`, `0x91`=145d `F5HK3`, `0x90`=144d `F5_DBZ` for a Vader 5 |
| `data[6]` | `FlydigiDevice.ConnectType` | `0`=Unknown, `1`=Wired, `2`=Dongle, `3`=Bluetooth |
| `data[7]`, `data[8]`, `data[9]`, `data[10]` | `FlydigiDevice.DeviceMac` | collected in that order into a 4-byte array, then **`Array.Reverse`d**, then hex-joined with `:` -> printed as `data[10]:data[9]:data[8]:data[7]` |
| `data[11]` | `FlydigiDevice.Battery` | non-`"f4"` devices: `(data[11] >> 4 == 1) ? 6 : (data[11] & 0x0F)`. High nibble `1` is a charging/full sentinel mapping to level `6`; otherwise the low nibble is the level. |
| `data[12]` | `FlydigiDevice.ChipType` | `data[12] & 0x0F`, **only assigned if `ChipType == Unknown`**; otherwise the byte is skipped. `0`=Unknown `1`=Wch `2`=Telink `3`=Krly `4`=NearLink `5`=Megahunt `6`=Puya `7`=Esp `8`=Freq `9`=Jieli |
| `data[13]` | `Controller.MotionChipType` | `data[13] & 0x0F` |
| `data[14]` | — | **skipped** (`num++` with no read); reserved |
| `data[15]`, `data[16]` | `FlydigiDevice.FirmwareVersion` | `"{d15>>4}.{d15&0xF}.{d16>>4}.{d16&0xF}"` |
| `data[17]`, `data[18]` | `Controller.DongleVersion` | same nibble format; set to `null` if all four parts are 0 |
| `data[19]`, `data[20]` | `Controller.SwitchVersion` | same; `null` if all-zero |
| `data[21]`, `data[22]` | `Controller.TriggerVersion` | same; `null` if all-zero |
| `data[23]`, `data[24]` | `Controller.ScreenVersion` | same; `null` if all-zero |
| `data[25]`, `data[26]` | `Controller.AdcVersion` | same; `null` if all-zero |
| `data[27]`, `data[28]` | `Controller.NearLinkVersion` | same; `null` if all-zero |

Payload spans `data[5]`..`data[28]` (24 bytes).

**Version nibble encoding.** Each version is 4 parts packed into 2 bytes:

```
part1 = hi nibble of first byte    part2 = lo nibble of first byte
part3 = hi nibble of second byte   part4 = lo nibble of second byte
```

C# evaluates the interpolated string's holes left to right, and the `num++` post-increments
land such that the **first byte supplies parts 1-2 and the second byte parts 3-4**.
Example: `data[15]=0x71, data[16]=0x41` -> FirmwareVersion `"7.1.4.1"` — which is exactly
the threshold string used for the third-party-takeover gate in §0.1.1. `FirmwareVersion`
is **not** nulled when all-zero (unlike the other six).

**`"f4"`-only battery quirk (does not apply to the Vader 5 Pro):** for `DeviceCode == "f4"`
the parser switches on the **hard-coded** `data[10]` (not the cursor `data[11]`):
`3 -> Battery = 2`, `5 -> Battery = 6`, default `-> Battery = data[11]` raw. Since
`data[10]` is also the high byte of the MAC this looks like a firmware-specific hack;
`"f5"` takes the normal `else` branch.

Worked ack — Vader 5 Pro, wired, MAC `AA:BB:CC:DD`, charging, Megahunt main +
Telink motion, firmware 7.1.4.1, no dongle/switch/screen/adc/nearlink, trigger 1.0.0.2:

```
      [0] [1] [2] [3] [4] [5] [6] [7] [8] [9] [10] [11][12][13][14]
      5A  A5  01  01  00  82  01  DD  CC  BB  AA   15  05  02  00
      [15][16] [17][18] [19][20] [21][22] [23][24] [25][26] [27][28]
      71  41   00  00   00  00   10  02   00  00   00  00   00  00
```
-> DeviceType `F5`, ConnectType `Wired`, MAC `AA:BB:CC:DD`,
Battery `6` (`0x15 >> 4 == 1` -> charging sentinel), ChipType `Megahunt`,
MotionChipType `Telink`, FirmwareVersion `"7.1.4.1"`, TriggerVersion `"1.0.0.2"`,
Dongle/Switch/Screen/Adc/NearLink `null`.

---

### 6.10 `ReadNicknameCommandFactory` — **0x02** (2d)

**Feature:** read the user-assigned controller nickname (stored into
`FlydigiDevice.ProductName`). Queued automatically at connect time **only** for NewXInput
controllers (`ControllerCommunicationThread`, line ~201).

Class: `ReadNickNameControllerCommandNewXInput`. Factory returns it unconditionally.

**Request** — no payload:

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x02` |
| 4 | `0x02` length |
| 5 | `0x04` checksum = `0x02 + 0x02` |
| 6..31 | `0x00` |

```
06 5A A5 02 02 04 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**ACK — `IsAck`:** `data[2] == 0x02`.

**ACK — `ParseAckData`** (as decompiled):

```csharp
ReadOnlySpan<byte> s = data.Slice(4, data.Length - 6);
if (s[0] != 0xFF && s[0] != 0)                       // *(bool*)s[0] means s[0] != 0
    controller.ProductName = Encoding.UTF8.GetString(s).Trim();
```

**This is off by one — see §7.2.** Per §4 the payload starts at `data[5]`, not `data[4]`;
`data[4]` is the packet-index byte, which is `0x00` for a single-packet ack, so the guard
`s[0] != 0` fails and **the nickname is never assigned**.

**Corrected specification** (what an implementation should do):

| ack bytes | content |
|---|---|
| `data[5] .. data[5 + L - 1]` | nickname, UTF-8, NUL-padded |

Read UTF-8 from `data[5]` up to the first `0x00` (or to the end of the payload region).
Treat a first byte of `0xFF` (erased flash) or `0x00` (empty) as "no nickname set".
Note the SDK's `.Trim()` strips whitespace but **not** `'\0'` (`char.IsWhiteSpace('\0')`
is false in .NET), so the shipping code would retain trailing NULs — prefer
`TrimEnd('\0')` as `ReadRawDataReportStatus` correctly does.

The ack presumably also carries the length in `data[3]`/a NUL terminator; the SDK derives
the extent from `data.Length - 6` instead, which for a 31-byte span yields 25 bytes.
Confirm the real terminator convention on hardware (§9).

---

### 6.11 `UpdateNicknameCommandFactory` — **0x18** (24d)

**Feature:** write the controller nickname.

Class: `UpdateNicknameCommandNewXInput`. Factory returns it unconditionally for every
controller type (NewXInput-only by design; no XInput/DInput classes exist).

**Request** — variable-length UTF-8 payload:

```csharp
byte[] bytes = Encoding.UTF8.GetBytes(nickName);
array[4] = (byte)(2 + bytes.Length);
Array.Copy(bytes, 0, array, 5, bytes.Length);
array[6] = ByteExtension.Crc(array, 3, 3 + array[4]);   // <-- BUG, see §7.1
```

**Corrected layout** (what to implement):

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x18` |
| 4 | `2 + L` where `L` = UTF-8 byte length of the nickname |
| 5 .. 4+L | nickname bytes (UTF-8, **not** NUL-terminated by the SDK) |
| **5+L** | checksum = `Crc(a, 3, 5+L)` = `(0x18 + (2+L) + sum(nickname bytes)) & 0xFF` |
| 6+L .. 31 | `0x00` |

**Length limit:** the checksum must land at `5 + L <= 31`, so **`L <= 26` bytes** and
`a[4] <= 0x1C`. The SDK does **not** validate or truncate; a longer nickname throws
`IndexOutOfRangeException` inside `Array.Copy`/the checksum store. Enforce `L <= 26`
yourself. Note `L` is *UTF-8 bytes*, not characters — multi-byte names hit the limit sooner.

Worked example, nickname `"Vader"` (`56 61 64 65 72`, L = 5, `a[4] = 0x07`):

**Correct frame** (checksum `0x11` at index `5 + 5 = 10`):
```
06 5A A5 18 07 56 61 64 65 72 11 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```
checksum = `(0x18 + 0x07 + 0x56 + 0x61 + 0x64 + 0x65 + 0x72) & 0xFF` = `0x111 & 0xFF` = `0x11`

**Frame the shipping SDK actually emits** (checksum written to index 6, clobbering the
second nickname byte `0x61` `'a'`, and index 10 left as `0x00`):
```
06 5A A5 18 07 56 11 64 65 72 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```
-> the device would see the name `"V\x11der"` with a zero/absent checksum. This is only
accidentally correct for a **1-byte** nickname (where `5 + L == 6`).

*Evaluation-order note:* the checksum's right-hand side is computed before the store, so
the `Crc` covers the **original** nickname bytes; the corruption at index 6 happens after.

**ACK — `IsAck`:** `data[2] == 0x18`. `ParseAckData` not overridden (log only). Verify by
re-reading with `0x02` (§6.10).

---

### 6.12 `ReadUidCommandFactory` — **0x04** (4d)

**Feature:** read the controller's 13-byte factory unique ID (used as the SDK/service key
for a device, `FlydigiDevice.Uid`).

Class: `ReadUidControllerCommandNewXInput`. `maxRetryCount = 5`.

**Request** — no payload:

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x04` |
| 4 | `0x02` length |
| 5 | `0x06` checksum = `0x04 + 0x02` |
| 6..31 | `0x00` |

```
06 5A A5 04 02 06 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**ACK — `IsAck`:** `data[2] == 0x04`.

**ACK — `ParseAckData`:**

| ack bytes | property |
|---|---|
| `data[5]` .. `data[17]` | `FlydigiDevice.Uid` = the 13 bytes formatted `"%02x"` each and concatenated, **lowercase, no separators** -> a 26-character string |

Exactly 13 bytes, payload `data[5]`..`data[17]`.

Worked ack:
```
      [0] [1] [2] [3] [4] [5..17]
      5A  A5  04  01  00  0A 1B 2C 3D 4E 5F 60 71 82 93 A4 B5 C6
```
-> `Uid = "0a1b2c3d4e5f60718293a4b5c6"`

*(For orientation: the XInput variant is `0xA0` with the UID at `data[16..28]`, and the
DInput variant is `0xFA` sub `0xA0` with the UID at `data[4..16]`. Neither applies here.)*

---

### 6.13 `RestartCommandFactory` — **0x1D** (29d)

**Feature:** reboot the controller. UI string `restart` = "Reboot",
`controller_restarting` = "Restaring.Please wait...".

Class name is **`UpdateNicknameCommandNewXInput`** — a copy-paste artifact in the
decompiled source; it is unrelated to nicknames. Factory returns it unconditionally.

**Request** — no payload:

| idx | value |
|---|---|
| 0 | `0x06` report ID (overwritten) |
| 1 | `0x5A` |
| 2 | `0xA5` |
| 3 | `0x1D` |
| 4 | `0x02` length |
| 5 | `0x1F` checksum = `0x1D + 0x02` |
| 6..31 | `0x00` |

```
06 5A A5 1D 02 1F 00 00 00 00 00 00 00 00 00 00
00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**ACK — `IsAck`:** `data[2] == 0x1D`. `ParseAckData` not overridden.

Practical note: the device reboots, so the HID handle dies and the ack may never arrive.
The SDK's default 3 retries x 500 ms will fire the `timeoutAction`. Expect USB
re-enumeration; the app shows a "Restarting, please wait" modal and waits for
`deviceConnectionChanged`. Do not treat a timeout here as failure.

---

### 6.14 Commands in scope with **no NewXInput implementation**

Every class below emits a **legacy 15-byte frame** built by
`CreateSimpleCommand(false, null)`:

```
a[0] = TakeEndpointByDevice()   // 0x06 for NewXInput, then overwritten by _outReportId
a[1] = CommandId()
a[2] = <sub-command, if any>
a[3] = <parameter, if any>
a[4..14] = 0x00                 // total length 15, NOT 32
```

There is **no `0x5A 0xA5` magic**, so `NewXInputProtocol.ParseData` discards any reply and
the firmware almost certainly ignores the request. **Do not implement these against a
Vader 5 Pro.** Listed for completeness and to document the fall-through hazard.

| Class | Selector result for NewXInput | Frame (15 bytes) | Notes |
|---|---|---|---|
| `ReadAutoSleepPeriodCommandFactory` | DInput, cmd `0xF2` sub `0x03` | `06 F2 03 00`*11 | **See §6.7 / §0.2.3.** Use `0x03` instead. |
| `SetHardwareMacroEnableCommandFactory` | either branch, cmd `0x50` sub `0x06`, `a[3] = (!enable) ? 1 : 0` | ON `06 50 06 00 ...` / OFF `06 50 06 01 ...` | **See §0.1.4(c).** Inverted polarity. Byte-identical to `EnableMappingSwitchCommandXInput`. No IPC enum. |
| `DisableMacroMappingCommandFactory` | DInput, cmd `0xE9` | `06 E9 00 00`*12 | **See §0.1.4(d).** Payload-less one-shot; XInput branch would be `0x19`. Declared `public abstract` (siblings are `internal`). No IPC enum. |
| `EnableDockSmartStopCommandFactory` | DInput, cmd `0x50` sub `0x10`, `a[3] = enable ? 1 : 0` | ON `06 50 10 01 ...` / OFF `06 50 10 00 ...` | "Dock Smart Sleep": sleeps on the charging dock, wakes when picked up. Both branches byte-identical (positive polarity, unlike its siblings). The NewXInput `0x03` parser **never sets `DockSmartStopUsable`**, so the app hides this control on a Vader 5 Pro. |
| `EnableDS5DataCommandFactory` | only class, cmd `0xE8` sub `0x01`, `a[3] = enable ? 1 : 0` | ON `06 E8 01 01 ...` / OFF `06 E8 01 00 ...` | DualSense/DS5 report emulation. Factory has a vestigial `switch` on `ControllerType` whose every arm returns the same DInput class. |
| `DeviceMaskCommandFactory` | only class, cmd `0x10` | `06 10 <ctrl> <media> <gyro> 00`*10 | Suppress controller/media/gyro reports. `a[2]=maskController`, `a[3]=maskMedia`, `a[4]=maskGyro`, each `1`/`0`. **Hazard: `0x10` is `ReadRawDataReportStatus` in NewXInput — never send this as a NewXInput frame.** |
| `ReadModeUsageCountCommandFactory` | DInput, cmd `0x50` sub `0x0C` | `06 50 0C 00`*11 | Per-mode usage counters. `IsAck`: `data[2]==0x50 && data[3]==0x0C`; `ParseAckData` only calls the base logger — **the SDK never actually decodes the counts**. |
| `ReadUsageCountCommandFactory` | DInput, cmd `0xFA` sub `0xA1` | `06 FA A1 00`*11 | Per-key press counters. XInput branch (`0xA1`) is the only one overriding `HasMultiAck() => true`. Decode (DInput indices): three `{keyId, count24}` groups — `KeyUsageCount[data[4]] = (data[5]<<16)\|(data[6]<<8)\|data[7]`, then `data[8..11]`, then `data[12..15]`. **Hazard: `0xA1` is `ReadMappingConfigVersionAll` in NewXInput.** |
| `DongleInfoCommandFactory` | DInput, cmd `0x11` | `06 11 00 00`*12 | Dongle firmware version. DInput decode: `DongleVersion = "0.{data[2]}.{data[3]>>4}.{data[3]&0xF}"`, `IsAck`: `data[1]==0x11`. **Use `0x01` HeartBeat `data[17..18]` instead. Hazard: `0x11` is `EnableRawDataTransportIn` in NewXInput.** |
| `ExtraInfoCommandFactory` | DInput, cmd `0xF5` sub `0x01` | `06 F5 01 00`*11 | Trigger/screen/switch/ADC/NearLink versions. DInput `IsAck` is unusually strict: `data[0]==0xFF && data[1]==0xF0 && data[2]==0xF5 && data[3]==0x01`. **Use `0x01` HeartBeat `data[19..28]` instead.** `ControllerCommunicationThread` only queues this when `ControllerType != NewXInput`. |

For `EnableXboxHomeButtonCommandFactory` — which *does* have a working NewXInput class but
is gated off in the SDK — see §6.2 and §7.3.

---

## 7. Known SDK bugs and decompilation artifacts

### 7.1 `UpdateNicknameCommandFactory` writes the checksum to the wrong index (functional bug)

```csharp
array[4] = (byte)(2 + bytes.Length);
Array.Copy(bytes, 0, array, 5, bytes.Length);
array[6] = ByteExtension.Crc(array, 3, 3 + array[4]);   // should be array[3 + array[4]]
```

Correct index is `3 + array[4]` == `5 + bytes.Length`, as used by every other
variable-length NewXInput command (`AcquireController` -> `array[26]` for `a[4]=23`;
`EnableRawDataTransportIn` -> `array[10]` for `a[4]=7`). Effects for a nickname longer
than 1 byte:

* byte 2 of the nickname is overwritten with the checksum;
* the real checksum position (`5+L`) stays `0x00`.

Correct in your implementation (§6.11). If the device rejects the frame on checksum, the
shipping app's "set nickname" simply never works for names longer than one byte — a good
live-device sanity check.

### 7.2 `ReadNicknameCommandFactory` slices the ack one byte early (functional bug)

`data.Slice(4, data.Length - 6)` should be `data.Slice(5, ...)`. Because `data[4]` is the
packet-index byte (`0x00` for single-packet acks) the guard `s[0] != 0` short-circuits and
`ProductName` is never assigned. See §6.10 for the corrected read. Secondary issue:
`.Trim()` does not strip `'\0'` in .NET — use `TrimEnd('\0')`.

### 7.3 `EnableXboxHomeButton` NewXInput path is unreachable

`EnableXboxHomeButtonCommandFactory` has a correct NewXInput class (`0x13` sub `0x02`), but:

```csharp
private void EnableXboxHomeButtonImpl(Controller controller, bool enable)
{
    if (controller.ControllerType == ControllerType.XInput)   // NewXInput excluded
        AddCommandToCommunicationManager(controller, EnableXboxHomeButtonCommandFactory.CreateCommand(controller, enable));
}
```

So the public `ControllerSdk.EnableXboxHomeButton` is a no-op on a Vader 5 Pro even though
the `0x03` ack exposes `XboxHomeButtonUsable`/`XboxHomeButtonEnabled` (bit 1). The frame in
§6.2 should work if sent directly. Worth live verification (§9).

### 7.4 `WriteDataImpl` overwrites `a[0]`

See §2.1. `TakeEndpointByDevice()`'s `0x06` never reaches the wire on a HID transport; the
descriptor-derived `_outReportId` does. They are believed equal for this device.

### 7.5 `ReadHardwareFunctionStatus` parses `data[7]` twice

Cosmetic. The first pass sets only `OffScreenUsable`; the second sets `OffScreenUsable` and
`AudioUsable`. Same value, no effect. Implement once.

### 7.6 XInput/DInput `ReadHardwareFunctionStatus` ctors pass `timeoutAction` as `action`

`base(controller, timeoutAction)` in the XInput and DInput classes binds the timeout
delegate to the base `action` (success) parameter. The NewXInput class is correct
(`base(controller, null, timeoutAction)`). Irrelevant to the wire format; noted so the
NewXInput ctor is not "fixed" to match its broken siblings.

### 7.7 Dead code in `ByteExtension`

`GetBitValue(value, fromIndex, length)` and `GetOneBit(value, index)` are defined but
**referenced nowhere in the entire decompiled tree**. All bit extraction in scope uses
`BitArray` (§6.1). For reference, `GetBitValue` computes `(value >> min(fromIndex, ...)) & ((1 << length) - 1)`
— an LSB-first extraction, consistent with the `BitArray` interpretation. Its
`Math.Min(fromIndex, num)` clamping is itself suspect; do not rely on it.

### 7.8 Vestigial `if (1 == 0) { }` blocks and `switch` arms

The decompiler emits `if (1 == 0) { }` around switch statements and, in
`DeviceMaskCommandFactory` / `EnableDS5DataCommandFactory` / `EnableAudioCommandFactory`,
leaves a `ControllerType` lookup whose result is discarded before returning a single fixed
class. These are artifacts; the behaviour is "always return that one class".

### 7.9 Pointer-deref rendering

`*(byte*)data[N]` means `data[N]`; `*(bool*)data[N]` means `data[N] != 0`;
`(x & 0xFF)` on a `byte` is a no-op. Applied consistently throughout this document.

### 7.10 `DeviceType.F5_DBZ` (144d) has no `DeviceCode`

`GetDeviceCodeById` maps `F5` (130d) and `F5HK3` (145d) to `"f5"` but omits `F5_DBZ`
(144d), which falls to `default: return ""`. A device reporting `0x90` in the HeartBeat ack
would get an empty `DeviceCode`, disabling every `DeviceCode`-gated feature (including the
third-party-takeover check in §0.1.1, which would fall through to `VendorId == 14295` and
thus be *enabled* without a firmware check). Handle `0x90` as `"f5"` yourself.

---

## 8. Enumerations and value tables

All values verified against both the C# protobuf enums
(`Flydigi.SharedResources/Flydigi.SharedResources.Data.Protobuf/`) and the identical
TypeScript enums compiled into the renderer bundle
(`extracted/asar/.vite/renderer/main_window/assets/index-BA01cjWW.js`).

The wire byte **is** the enum's underlying numeric value. Proof: the service converts the
raw `int` read off the wire with
`Enum.Parse<ReportRate>(config.ReportRate.ToString())` — `Enum.Parse` on a numeric string
resolves by underlying value, so `"4"` -> `ReportRate._250`.

### 8.1 `ReportRate` (cmd `0x14` payload; `0x03` ack `data[10]`)

| Value | Hex | Name | Polling rate |
|---|---|---|---|
| 0 | `0x00` | `ReportRate_None` | unset / not configurable |
| 1 | `0x01` | `ReportRate_1000` | 1000 Hz |
| 2 | `0x02` | `ReportRate_500` | 500 Hz |
| 4 | `0x04` | `ReportRate_250` | 250 Hz |
| 8 | `0x08` | `ReportRate_125` | 125 Hz |

`0x03`, `0x05`-`0x07`, `>0x08` are not defined. See the renderer discrepancy note in §0.2.4.

### 8.2 `JoystickPrecision` (cmd `0x15` payload; `0x03` ack `data[11]`)

| Value | Hex | Name | Bit depth | In Vader 5 UI? |
|---|---|---|---|---|
| 0 | `0x00` | `JoystickPrecision_None` | — | — |
| 1 | `0x01` | `JoystickPrecision_8Bit` | 8 | yes |
| 2 | `0x02` | `JoystickPrecision_10Bit` | 10 | yes |
| 3 | `0x03` | `JoystickPrecision_12Bit` | 12 | yes |
| 4 | `0x04` | `JoystickPrecision_9Bit` | 9 | yes |
| 5 | `0x05` | `JoystickPrecision_11Bit` | 11 | yes |
| 6 | `0x06` | `JoystickPrecision_14Bit` | 14 | no |
| 7 | `0x07` | `JoystickPrecision_16Bit` | 16 | no |

Note the enum order is **not** monotonic in bit depth (8, 10, 12, 9, 11, 14, 16). The UI
offers 12/11/10/9/8 bit (hiding 12 and 11 for `deviceCode == "fp4"`); 14-bit and 16-bit
exist in the protocol but are never offered. UI tip
(`setting_controller_joystick_accuracy_tip`): *"This setting configures the minimum data
step size for thumbstick movement. A higher precision setting results in a smaller step
size."*

### 8.3 `JoystickSensitivity` (cmd `0x16` payload; `0x03` ack `data[12]`)

| Value | Hex | Name | UI label | In Vader 5 UI? |
|---|---|---|---|---|
| 0 | `0x00` | `JoystickSensitivity_None` | — | — |
| 14 | `0x0E` | `JoystickSensitivity_Highest` | **Fast** | yes |
| 15 | `0x0F` | `JoystickSensitivity_High` | — | no |
| 16 | `0x10` | `JoystickSensitivity_MiddleHigh` | — | no |
| 17 | `0x11` | `JoystickSensitivity_Middle` | **Medium** | yes |
| 18 | `0x12` | `JoystickSensitivity_LowMiddle` | — | no |
| 19 | `0x13` | `JoystickSensitivity_Low` | **Slow** | yes |
| 20 | `0x14` | `JoystickSensitivity_Lowest` | — | no |

Higher wire value = **lower** sensitivity. The scale starts at 14d, not 1. The UI exposes
only three of the seven steps (14 / 17 / 19). UI tip
(`setting_controller_joystick_center_sensitivity_tip`): *"Affects the thumbstick
sensitivity around the center zone."* Default seen in the renderer's initial state: `17`.

### 8.4 Sleep time (cmd `0x17` payload; `0x03` ack `data[9]`)

Not an enum — a raw byte in **minutes**, `0` = Never. See §0.2.1 for the full derivation
and the six UI values.

### 8.5 `ConnectType` (`0x01` ack `data[6]`)

| Value | Name |
|---|---|
| 0 | `ConnectType_Unknown` |
| 1 | `ConnectType_Wired` |
| 2 | `ConnectType_Dongle` |
| 3 | `ConnectType_Bluetooth` |

### 8.6 `ChipType` (`0x01` ack `data[12] & 0x0F`) and `MotionChipType` (`data[13] & 0x0F`)

| Value | Name |
|---|---|
| 0 | `Unknown` |
| 1 | `Wch` |
| 2 | `Telink` |
| 3 | `Krly` |
| 4 | `NearLink` |
| 5 | `Megahunt` |
| 6 | `Puya` |
| 7 | `Esp` |
| 8 | `Freq` |
| 9 | `Jieli` |

Declared chips for a Vader 5 (`GenerateControllerVader5`): main = `Megahunt`,
RF = `Telink`, dongle = `Telink`, SI = `Jieli`.

### 8.7 `ControllerType` (host-side only, never on the wire)

`Unknown = 0`, `XInput = 1`, `DInput = 2`, `NewXInput = 3`.

### 8.8 `DeviceType` values relevant to the Vader 5 family (`0x01` ack `data[5]`)

| Value | Hex | Name | `DeviceCode` |
|---|---|---|---|
| 130 | `0x82` | `F5` | `"f5"` |
| 144 | `0x90` | `F5_DBZ` | `""` (**unmapped — see §7.10**) |
| 145 | `0x91` | `F5HK3` / `F5_DBZ`-adjacent | `"f5"` |

(`F5HK3` and `F5_DBZ` are declared twice in the enum with values 145 and 144/145; treat
`0x82`, `0x90` and `0x91` all as Vader 5.)

---

## 9. Live-device verification checklist

Ordered by risk. Items marked **BLOCKING** must be resolved before the corresponding
feature can be shipped.

### 9.1 Transport

1. **Out report ID.** Dump the report descriptor of the `UsagePage == 0xFFA0` interface and
   confirm the **last** Report ID (`0x85`) item is `0x06`. If it differs, `WriteDataImpl`
   sends that value instead of the `0x06` this document assumes (§2.1).
2. **In report ID.** Confirm the **first** Report ID in the same descriptor, and that
   incoming ack reports actually start with it (so byte 0 must be stripped before the §4
   indices apply).
3. **Frame length.** Confirm 32-byte writes are accepted (the SDK always writes exactly 32,
   never a short frame).

### 9.2 ACK framing — **BLOCKING for every read command**

4. **`data[3]` semantics.** Send `0x03` and inspect the raw ack. This document concludes
   `data[3]` = total packet count (`0x01`) and `data[4]` = packet index (`0x00`), with the
   payload at `data[5]`. The alternative reading — `data[3]` = a request-style length byte
   (`2 + payloadLen`) — would show `data[3] == 0x0A` for `0x03`'s 8-byte payload. **One
   capture settles it.** Every payload index in §6 shifts if this is wrong.
5. **Ack checksum.** Determine whether the device appends an additive checksum and at what
   index. The SDK never validates one, so this is unknown.
6. **`ReadNickname` off-by-one (§7.2).** Confirm the nickname's first byte lands at
   `data[5]`, and identify the terminator convention (NUL vs a length in `data[3]`).

### 9.3 Priority feature 1 — third-party mapping takeover

7. **BLOCKING: firmware version.** Read `0x01` and decode `data[15..16]`. The feature needs
   **>= 7.1.4.1** on `"f5"` (§0.1.1). Below that, expect the frames in §0.1.2 to be
   rejected or ignored.
8. Send the ON frame `06 5A A5 11 07 FF FF FF FF 01 15 ...`, then read `0x10`
   (`06 5A A5 10 02 12 ...`) and confirm ack `data[9] == 0x01`.
9. Send the OFF frame `06 5A A5 11 07 FF FF FF FF 00 14 ...` and confirm ack `data[9] == 0x00`.
10. **Confirm `0xFF` really means "don't change".** Verify that the ON/OFF frames above
    leave `data[5..8]` (XInput/private/keyboard/mouse) untouched in the `0x10` readback.
    This is inferred from `EnableRawDataTransportIn`'s `null -> 0xFF` mapping, not proven.
11. Launch Steam, then re-read `0x10` and check that `data[10..29]` becomes a non-empty
    ASCII tag. Record the exact tag strings Steam and reWASD use.
12. Confirm the observable effect: with the toggle ON, onboard remaps/macros stop being
    applied and Steam Input sees raw buttons.

### 9.4 Priority feature 2 — sleep and report rate

13. Write each of the six sleep values (`0x00`, `0x01`, `0x05`, `0x0F`, `0x3C`, `0xB4`) with
    `0x17` and confirm `0x03` ack `data[9]` echoes it exactly.
14. **Confirm `0x00` = "Never"** by leaving the controller idle past the longest timeout.
15. **Confirm minutes, not seconds**, by timing the `0x01` (1-minute) setting. §0.2.1 makes
    minutes near-certain (`60` -> "1 h", `180` -> "3 h") but a stopwatch closes it.
16. Probe whether out-of-menu values (e.g. `0x02`, `0xFF` = 255 min) are accepted or
    clamped — the SDK does no validation.
17. **BLOCKING for report rate: resolve the `1/2/4/8` vs `1/2/3/4` conflict (§0.2.4).**
    Write `0x04` (enum `_250`) with `0x14` and read back `0x03` `data[10]`. If it echoes
    `0x04`, the enum is authoritative (expected). Also try `0x03` — if *that* echoes, the
    renderer's literals are right and the enum is stale.
18. Confirm `reportRate == 0` in the `0x03` ack really means "not configurable" (the app
    hides the control in that case), and check whether the Vader 5 Pro reports non-zero at
    all — the renderer gates its polling-rate UI to `deviceCode === "f4"`, hinting the
    Vader 5 may not expose it.

### 9.5 Priority feature 3 — `0x03` decode

19. Toggle each `0x13` sub-command one at a time and confirm the matching `data[6]`/`data[8]`
    bit flips, validating the bit map in §0.3 **and** the claim that bit index == sub-command
    id − 1.
20. Confirm the `usable` masks (`data[5]`, `data[7]`) actually reflect Vader 5 Pro
    capability. In particular check `data[5]` bit 7 and `data[7]` bit 0
    (screen features) against `IsSupportScreen == false` in `GenerateControllerVader5` — if
    the firmware reports them usable, the SDK's local flag is simply stale.
21. Confirm `data[7]`/`data[8]` bits 2-7 are genuinely unused (the SDK reads only bits 0-1).
22. Check whether the device **pushes** unsolicited `0x03` frames when settings are changed
    via controller shortcuts — `IsAlwaysCanRead() => true` strongly implies it (§0.3), and
    this would be the cleanest change-notification mechanism.

### 9.6 Other in-scope commands

23. **`UpdateNickname` (§7.1).** Confirm the correct checksum index `5+L` is accepted, and
    that the shipping SDK's `array[6]` frame is *rejected* for `L > 1`. Also establish
    whether the device wants a NUL terminator and confirm the `L <= 26` limit.
24. **`EnableXboxHomeButton` (§7.3).** Send `06 5A A5 13 04 02 01 1A ...` directly and check
    `0x03` ack `data[6]` bit 1. The SDK gates this off for NewXInput, so it is untested by
    the vendor.
25. **`Restart` (`0x1D`).** Confirm the reboot happens and expect no ack / a timeout.
26. **`0x13` ack payload.** Capture a `0x13` ack and determine whether `data[6]` echoes the
    new state or carries a status/error code — the SDK reads neither.
27. **`ReadUid` (`0x04`).** Confirm 13 bytes at `data[5..17]` and that the resulting 26-char
    string matches the UID the app displays.
28. **HeartBeat `data[14]`.** Identify the byte the parser skips.
29. **HeartBeat `data[11]` battery.** Confirm the `>> 4 == 1` charging sentinel and the
    real range of the low nibble (the SDK's `6` sentinel suggests levels 0-5).
30. Confirm the legacy-only commands in §6.14 are indeed inert on this device (send
    `06 F2 03 ...` and verify nothing comes back), so the fall-through hazard is documented
    rather than merely theorised.

### 9.7 Sub-command space

31. Probe `0x13` sub-commands `0x00` and `0x0B`..`0x0F` for undocumented features.
32. Probe unused command IDs in the low range (`0x05`..`0x0F`, `0x19`..`0x1C`, `0x1E`) —
    `0x1C` is `AcquireController` and `0x1F` is `SwitchToFirmwareUpgradeMode`, so the gaps
    at `0x05`-`0x0F` are the most likely home of undiscovered read commands.
