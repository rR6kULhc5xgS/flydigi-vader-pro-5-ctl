# Flydigi Vader 5 Pro — Byte-Level Protocol Spec: Button Mapping, Profiles & RGB

**Target device:** Flydigi Vader 5 Pro, USB VID `0x37D7` / PID `0x2401`.
**Device code:** `"f5"` (`DeviceCode.F5`), `DeviceType.F5 = 130` (`0x82`), also `DeviceType.F5HK3 = 145` (`0x91`).
**Protocol family:** `ControllerType.NewXInput` (chosen whenever `hid.VendorId == 14295` i.e. `0x37D7` and manufacturer string is not `"Microsoft"` — see `ControllerHidManager.cs:53`).

Only the **NewXInput** variant of every command is specified below. XInput/DInput variants are noted only where a NewXInput variant is *absent*.

Source of truth: decompiled C# under `decompiled/`.
Decompiler artifact note: `*(byte*)data[N]` in the sources is just `data[N]` (a byte read). `*(bool*)data[N]` is `data[N] != 0`. `StructLayout(0, …)` is `LayoutKind.Sequential`; `MarshalAs(30, …)` is `UnmanagedType.ByValArray`.

---

## 0. PRIORITY SECTION — Nintendo Switch mode profile binding, and the profile model

This section is the answer to the two highest-priority questions. Everything here is re-derived in full detail in Parts 2–4.

### 0.1 There are 4 onboard profiles, and each has a "Nintendo Switch mode" twin

The wire-level *config ID* (`cfgId`) space is **0x00–0x08**:

| cfgId | Meaning |
|---|---|
| `0x00`–`0x03` | The 4 onboard profiles, **normal mode** (XInput / PC / Android) |
| `0x04`–`0x07` | The same 4 profiles' **Nintendo Switch mode** variants (`0x04 + profileIndex`) |
| `0x08` | LED-only config slot for iOS (`MappingConfigConst.LED_CFG_IOS_ID = 8`) |

Evidence:

* `ControllerConfig.cs:47` — `private readonly int[] _mappingConfigRandomId = new int[4];` → exactly **4** profiles are tracked, and `IsAllConfigDefault()` tests all four against `65535` (`0xFFFF` = "never written / factory default").
* `ControllerRepository.PrepareMappingConfigs` enumerates a 4-element `int[]` of config IDs to read (`ControllerRepository.cs:5771`).
* `ReadMappingConfigVersionAllCommandFactory.cs:43` decodes the device's raw current-config byte as:
  ```
  CurrentConfigId = (raw <= 7) ? ((raw > 3) ? raw - 4 : raw) : 0
  ```
  i.e. **raw 0–3 = profile N in normal mode; raw 4–7 = profile (N−4) in Nintendo Switch mode.** This is the single clearest proof that the upper bank is a mode bank, not extra profiles.
* `ControllerBusinessService.ApplySwitchConfigAsync` (`ControllerBusinessService.cs:8757`):
  ```csharp
  if (@params.CfgId > 3 && @params.CfgId < 8)
      return await repository.SaveSwitchConfig(@params.Uid, @params.CfgId, true);
  return true;
  ```
  The "apply profile to NS" IPC command (`IpcCommandEnum.ApplySwitchConfig = 4167`) **only accepts cfgId 4..7** and does nothing otherwise.
* `ControllerRepository.ApplyOnboardConfig` guards `cfgId < 0 || cfgId > 8`.

"Switch" in `SaveCurrentSwitchMappingConfigCommandFactory` **does mean Nintendo Switch**, not "switching profiles". Corroborating evidence that the controller has dedicated NS hardware/firmware:

* `Controller.SwitchVersion` is a separate firmware version string, parsed from the heartbeat (cmd `0x01`) and from `ExtraInfoCommandFactory` alongside `TriggerVersion`, `ScreenVersion`, `AdcVersion`, `NearLinkVersion` — i.e. there is a discrete "Switch" MCU.
* `Controller.IsSupportNs` is set `true` for the Vader 5 Pro (`FlydigiControllerFactory.GenerateControllerVader5`), and `Controller.CurrentConfigIdForNs` is a *separate* field read back from the device.
* NS mode is fed by a distinct HID/gamepad personality, so keyboard/mouse remaps cannot exist there — and indeed the SDK strips them before committing an NS profile (see 0.3).

Unrelated uses of the word "switch" in this tree, for disambiguation: `SwitchToDInputCommandFactory` / `SwitchToXInputCommandFactory` (USB personality switching, in `data.command.bak`, XInput-only), `IpcCommandEnum.SwitchUsb = 15`, `IpcCommandEnum.SwitchToXinputMode = 65528`, `EnableQuickSwitchConfigCommandFactory` ("quick **profile** switching" via a button chord — `0x13` sub-command `0x01`), `EnableMappingSwitchCommandFactory` (master mapping enable — `0x13` sub-command `0x04`), and `MappingConfigParser`'s `JoystickMapType`/`KeyMapType` "switch" statements. None of those are NS mode.

### 0.2 Command `0xAB` — bind/persist a profile as the Nintendo Switch-mode profile

`SaveCurrentSwitchMappingConfigCommandFactory.SaveCurrentSwitchMappingConfigCommandNewXInput`

```
CommandId = 0xAB (171)
timeout   = 10000 ms, maxRetry = 3     (flash write — allow ~10 s)

byte  value
[0]   <HID output report ID>   (see §1.1; the SDK stages 0x06 here, the HID layer overwrites it)
[1]   0x5A
[2]   0xA5
[3]   0xAB
[4]   0x04                      <-- length byte. SEE ANOMALY BELOW: should be 0x05
[5]   dataVersion & 0xFF        random 16-bit "config data version" (see §0.5)
[6]   (dataVersion >> 8) & 0xFF
[7]   cfgId                     <-- 0x04..0x07  = NS-mode slot = 4 + profileIndex
[8]   checksum = sum(a[3] .. a[6]) & 0xFF      <-- ANOMALY: excludes a[7]
[9..31] 0x00 padding (frame is always 32 bytes)

ACK: data[2] == 0xAB
```

**ANOMALY (replicate it for byte-exact compatibility).** The generic NewXInput rule is `a[4] = 2 + payloadLen` and `checksum at a[3 + a[4]] = sum(a[3 .. 3 + a[4]))`. Here `payloadLen` is 3 (`version_lo`, `version_hi`, `cfgId`) so `a[4]` should be `0x05`. The SDK writes `0x04`. The *position* of the checksum byte (`a[8]`) is nevertheless the position that `a[4] = 0x05` would imply — the SDK hard-codes `array[8]`. The net effect is: **length byte is one short, and the checksum omits the `cfgId` byte.** The shipping Windows app has always sent it this way, so the firmware either ignores `a[4]` for this opcode or validates the checksum over `a[4]` bytes only. Reproduce the SDK's bytes exactly; do not "fix" it unless you verify on hardware.

### 0.3 The full "apply profile to Nintendo Switch mode" sequence

From `ControllerRepository.SaveSwitchConfig(uid, cfgId /* 4..7 */, force)` (`ControllerRepository.cs:6928`), new-protocol path:

1. **Load** the profile you want to become the NS profile (the SDK uses the currently-active one, read from its local `current-used` cache).
2. **Sanitise it for NS mode** — this is mandatory, NS mode has no host software to inject keyboard/mouse:
   * For every `KeyConfigBean` with `MapType == Key` that has a `MapKeyboardKeyId`: clear the keyboard binding and set `MapControllerKeyId = KeyId` (identity). On the wire this turns a `0xFE` key_table byte into `0xFF`/identity.
   * `JoystickConfigBean.LeftJoystickParam.MapType = Joystick (0)` and likewise for the right stick (drops stick→keyboard / stick→mouse modes).
3. **Write the mapping blob** with `0xA4`/`0xA5` (§2.4) to slot `data.CfgId` — i.e. the profile's *own normal-mode* slot `0..3`, **not** `4..7`.
4. **Write the LED blob** with `0xA8`/`0xA9` (§1.4) to the same slot.
5. **Pick a fresh random `dataVersion`**: `num = Random.Next(65535)` repeated until `num != currentConfig.DataVersion`. (Range is therefore `0..65534`.)
6. **Send `0xAB`** with `cfgId = 4 + profileIndex` and that `dataVersion` (§0.2). This is the step that binds the just-written profile as the Nintendo-Switch-mode profile.

Old-protocol devices (`Controller.IsOldProtocol()`) take a different path — `SaveSwitchConfigOldProtol` writes the blob **directly to cfgId 4..7** using `WriteAllMappingConfig` and never sends `0xAB`. That confirms the device's slot address space really is 0..7; on the new protocol `0xAB` is the mechanism that promotes the working config into the upper bank.

> **For "A/B and X/Y swapped in NS mode" specifically:** build a profile whose `key_table` (§2.6) contains
> `key_table[4].keyid = 5` (A→B), `key_table[5].keyid = 4` (B→A), `key_table[7].keyid = 8` (X→Y), `key_table[8].keyid = 7` (Y→X),
> leave every other slot `0xFF` (identity), write it to a normal slot with `0xA4`/`0xA5`, then send `0xAB` with `cfgId = 4 + thatSlot`.
> Note that Flydigi's own key IDs follow the **Xbox/XInput** labelling (A=4, B=5, X=7, Y=8); the physical caps on a Switch are transposed, so verify empirically which direction the firmware applies the swap.

### 0.4 Reading back the current NS binding

There is **no dedicated "read NS binding" opcode**. The value comes back as a side field of the version/status reads:

| Transport | Command | ACK offset of NS cfg id | Notes |
|---|---|---|---|
| **NewXInput** | `0xA1` `ReadMappingConfigVersionAll` | `data[14]` → `Controller.CurrentConfigIdForNs` | also gives current cfg id at `data[5]` and all four `dataVersion`s |
| XInput | `0x20` `ReadCurrentMappingConfigId` | `data[18]`, gated on `data[17] == 0xA0` | |
| DInput | `0xEB` | `data[6]`, gated on `data[5] == 0xA0` | ACK identified by `data[2]==0xAA && data[3]==0xAC` |

`ReadCurrentMappingConfigIdCommand` and `ReadMappingConfigVersionCommandFactory` have **no NewXInput implementation at all** (their factories dispatch only XInput vs DInput). On a Vader 5 Pro you must use `0xA1` (§2.9).

### 0.5 `dataVersion` (a.k.a. "mapping config random id")

A 16-bit tag the host generates randomly and stores alongside each profile, so that on reconnect it can tell whether the device's on-flash profile still matches the host's cached copy.

* Lives on the wire in two places: the **mapping blob** at offset `0x0E1`–`0x0E2` (225–226, little-endian; §2.6 `random_data[2]`), and as the payload of the **save** commands `0xA6` / `0xAB`.
* Read back for all 4 profiles at once via `0xA1` (`data[6..13]`, four little-endian `uint16`s).
* `0xFFFF` means "factory default / never written" (`ControllerConfig.IsAllConfigDefault()`).
* Also compared against `255` in `ControllerRepository.ReadNextConfig` as a "config slot is untouched" heuristic.

### 0.6 Profile lifecycle command cheat-sheet (NewXInput)

| Operation | Cmd | Payload | Notes |
|---|---|---|---|
| Read all profile versions + which is active + NS binding | `0xA1` | none | §2.9 |
| Read one profile's mapping blob | `0xA3` | `cfgId`, `pkgSize` | multi-packet ACK, §2.3 |
| **Activate** a profile | `0xA2` | `cfgId` | §2.10; host-side range check is `0..8` |
| Upload mapping blob (delta or whole) | `0xA4` + `0xA5`×N | see §2.4 | chunked |
| **Persist** the working config to flash | `0xA6` | `dataVersion` (u16 LE) | 10 s timeout, §2.11 |
| **Persist as Nintendo Switch profile** | `0xAB` | `dataVersion` (u16 LE) + `cfgId` 4..7 | 10 s timeout, §0.2 |
| **Factory-reset** profiles | `0xAF` | `cfgId` | 10 s timeout, §2.12 |
| Read LED blob | `0xA7` | `cfgId`, `pkgSize` | §1.3 |
| Upload LED blob | `0xA8` + `0xA9`×N | §1.4 | chunked |
| Read macro blob (proto ≥ 3.2) | `0xAC` | `cfgId`, `pkgSize` | §3.3 |
| Upload macro blob (proto ≥ 3.2) | `0xAD` + `0xAE`×N | §3.4 | chunked |
| Master mapping enable/disable | `0x13` sub `0x04` | `enable` | §2.13 |
| Quick-profile-switch chord enable | `0x13` sub `0x01` | `enable` | |
| Read feature/status bitfields | `0x03` | none | §2.14 |

---

## 1. Frame format, transport, and the chunked-transfer engine

### 1.1 NewXInput command frame

Built by `AbstractCommand<T>.CreateSimpleCommand(isNewProtocol: true, packetSize: null)`
(`Flydigi.Common.data/Flydigi.Common.data.command/AbstractCommand.cs:79`):

```
byte  meaning
[0]   HID output report ID       (staged as 0x06 by AbstractControllerCommand.TakeEndpointByDevice()
                                  for ControllerType.NewXInput, then OVERWRITTEN — see note)
[1]   0x5A                        magic
[2]   0xA5                        magic
[3]   CommandId()
[4]   len  = 2 + payloadLen
[5..] payload
[3+len] checksum = sum(a[3] .. a[3+len-1]) & 0xFF     ("Crc(a, 3, 3+a[4])")
rest  0x00 padding
```

* **Total frame size is always 32 bytes.** `packetSize ?? maxPacketCount`, and `AbstractControllerCommand` passes `maxPacketCount = 32`. Therefore the maximum payload is `32 − 7 = 25` bytes.
* **`ByteExtension.Crc(arr, start, end)`** = `(sum of arr[start..end-1]) & 0xFF`; `end` is exclusive. It is a plain 8-bit additive checksum, not a real CRC.
* **The report-ID byte is replaced by the HID layer.** `HidCommunicationProtocol.WriteDataImpl` (`Flydigi.Hid.data/Flydigi.Hid.data/HidCommunicationProtocol.cs:147`) does `data[0] = _outReportId;` unconditionally, where `_outReportId` is taken from the **last** `Report ID` item (`0x85`) found in the HID report descriptor. So the literal `0x06` in `TakeEndpointByDevice()` is a placeholder; a reimplementation must discover the real output report ID from the descriptor (or write via a raw output report on the vendor collection, Usage Page `0xFFEE` / 65518 — see `ControllerHidManager.cs:91`).
* Length convention restated: `len` counts `CommandId` + `len` itself + payload, i.e. every byte the checksum covers. Hence `len = 2 + payloadLen` and the checksum sits immediately after the payload at index `3 + len`.

### 1.2 NewXInput ACK frame

Inbound reads are `hidDevice.Read(64)`; if `data[0] == inReportId` (the **first** `0x85` item in the descriptor) the byte is **stripped** before parsing. So every ACK offset below is relative to a span that starts at the `0x5A`:

```
byte  meaning
[0]   0x5A
[1]   0xA5
[2]   CommandId (echoed)              <-- all NewXInput IsAck() checks test this
[3]   total packet count              (multi-packet reads; 1 for single-frame replies)
[4]   packet index, 0-based           (multi-packet reads)
[5]   first payload byte              (for config reads: cfgId)
[6..] payload
```

`NewXInputProtocol.ParseData` (`data.protocol.dinput/NewXInputProtocol.cs`) additionally routes:
* `data[2] == 0xF7` (247) → raw/origin motion data callback
* `data[2] == 0xEF` (239) → the periodic input/operator report (`OperatorDataParser.Parse`), and returns `false` so it is never treated as a command ACK.

### 1.3 How `HasMultiAck` / `IsAckFinished` sequence a transfer

The dispatcher is `HidCommunicationProtocol._readDataFromDevice` + `CommunicationProtocol` (`Flydigi.Common.data.full/Flydigi.Common.data/CommunicationProtocol.cs`):

1. `WriteData(cmd)` sets `CommandToSend = cmd`. If `cmd.HasMultiAck()` its `GetCommandIdInAck()` is added to the `MultiAckCmdId` set (used only to forward raw frames to listeners). If `cmd.IsAlwaysCanRead()` it is added to `CommandAlwaysCanRead` (it keeps receiving ACKs even after completion). A `CancellationTokenSource` starts a retry timer (`timeoutMs`, default **500 ms**, `maxRetryCount` default **3**).
2. Every inbound frame: if `CommandToSend.IsAck(frame)` is false → **ignored** (the read loop just `continue`s; no state change).
3. On a match: the retry timer is cancelled, the command is removed from the pending queue, and `ParseAck` → `ParseAckData` runs (this is where the reassembly happens).
4. `if (!CommandToSend.IsAckFinished(frame)) continue;` — **the command stays current and the loop waits for more frames.** This is the entire multi-packet read mechanism: one request, N ACK frames, terminated when `IsAckFinished` returns true.
5. When finished, `CommandToSend = null` and the **next** queued command is written. Commands are therefore strictly serialised: one outstanding request at a time.
6. On timeout, `RetryCommandAfterTimeout` re-sends up to `maxRetryCount`; a write failure calls `ForceReset`, which fires every queued command's `timeoutAction` and clears the queue.

For all three NewXInput config reads (`0xA3` mapping, `0xA7` LED, `0xAC` macro) the finish test is identical:

```csharp
IsAckFinished(data) => data[3] == data[4] + 1;      // total == index+1  ->  last packet
```

and reassembly is:

```csharp
if (data[4] == 0)                                   // first packet
    multiAckData = new byte[data[3] * pkgSize];     // total blob size announced by device
Array.Copy(frame, 6, multiAckData, pkgSize * data[4], pkgSize);
if (IsAckFinished(data)) parse(multiAckData);
```

`ReadMappingConfigCommand` additionally pre-fills `multiAckData` with `0xFF` and stamps `CfgId = data[5]` onto the parsed bean. `ReadLedConfigCommand` / `ReadMacroConfigCommand` do not pre-fill.

**`pkgSize` is 20 (0x14) for NewXInput** and 10 for everything else:
```csharp
_pkgSize = (controller.ControllerType == ControllerType.NewXInput) ? 20 : 10;
```
It is sent to the device in the read request, and the device echoes back that many payload bytes per ACK frame starting at `data[6]`.

### 1.4 The chunked **upload** scheme (identical for mapping, LED and macro blobs)

`WriteMappingConfigCommandFactory.CreateCommands` / `WriteRgbConfigCommand.CreateCommands` / `WriteMarcoConfigCommandFactory.CreateCommands` all follow the same shape:

```csharp
int perPkg = (controllerType == NewXInput) ? 20 : 10;
Dictionary<int, List<byte[]>> batches = <Parser>.ParseConfigToArray(newConfig, oldConfig, perPkg);
foreach (batch in batches) {          // key = absolute start packet index
    emit StartCommand(cfgId, startIndex, batch.Count, perPkg);
    for (i = 0; i < batch.Count; i++)
        emit PackCommand(packNum: i, pack: batch[i]);
}
```

**Blob splitting.** `ByteExtension.SplitBytes(blob, perPkg)` cuts the flat blob into `ceil(len/perPkg)` chunks; the final chunk is short if `len % perPkg != 0`. For NewXInput with `perPkg = 20`, all blob sizes below are multiples of 20, so every chunk is exactly 20 bytes.

**Delta upload.** `ParseConfigToArray(config, oldConfig, perPkg)` serialises *both* configs, compares chunk-by-chunk **from the end backwards**, and returns a dictionary of *contiguous runs of changed chunks*, keyed by the absolute index of the run's first chunk. If `oldConfig == null` the whole blob is returned as one run keyed `0`. Consequence: a "write" can produce several `Start`+`Pack…` groups, each patching a different region.

**Absolute destination offset.** The `Start` command carries the absolute `startIndex`; each `Pack` command carries a `packNum` that **restarts at 0 for each batch**. The device therefore must compute
`flashOffset = (startIndex + packNum) * packetSize`.
*(This is an inference from the SDK's indexing — worth confirming on hardware with a two-run delta write. A safe implementation always uses a single run keyed `startIndex = 0` covering the whole blob, which is exactly what `WriteMappingConfig`/`WriteRgbConfigById`/`WriteAllRgbConfigById` do when they pass `oldConfig = null`.)*

**Sequencing / ACK.** `Start` and every `Pack` are ordinary single-ACK commands (`HasMultiAck()` is false, `IsAck` just checks `data[2] == CommandId`). They are queued and sent strictly one at a time; the completion callback is attached only to the very last `Pack` of the very last batch. The `Start` command's constructor passes `commandIndex = 0, commandCount = packetNum + 1`, which only drives the `OnSerialCommandSend` progress callback.

**`WriteXxx` vs `WriteAllXxx`.** For **NewXInput these are byte-identical** — `WriteMappingConfigCommandFactory` and `WriteAllMappingConfigCommandFactory` both emit `0xA4`/`0xA5`; `WriteRgbConfigCommand` and `WriteAllRgbConfigCommand` both emit `0xA8`/`0xA9`. They differ only in their *XInput* start opcodes (`0x23` vs `0x25`, and `0x28` vs `0x2A`). The "All" variants exist for the legacy transports where "start at index" was not expressible.

### 1.5 Command-ID table (NewXInput)

| Cmd | Name | Direction | Payload | Reply |
|---|---|---|---|---|
| `0x01` | HeartBeat / device info | → | none (`len=2`) | multi-frame device info |
| `0x03` | ReadHardwareFunctionStatus | → | none (`len=2`) | feature bitfields, §2.14 |
| `0x13` | Set feature enable | → | `sub`, `enable` | echo w/ `data[5]==sub` |
| `0xA1` | ReadMappingConfigVersionAll | → | none (`len=2`) | §2.9 |
| `0xA2` | ApplyMappingConfigByCfgId | → | `cfgId` | echo |
| `0xA3` | ReadMappingConfig | → | `cfgId`, `pkgSize` | N×20 B chunks |
| `0xA4` | WriteMappingConfig **Start** | → | `cfgId`, `startIdx`, `packetNum`, `packetSize` | echo |
| `0xA5` | WriteMappingConfig **Pack** | → | `packNum`, 20 B chunk | echo |
| `0xA6` | SaveCurrentMappingConfig (flash) | → | `dataVersion` u16 LE | echo, 10 s |
| `0xA7` | ReadLedConfig | → | `cfgId`, `pkgSize` | N×20 B chunks |
| `0xA8` | WriteRgbConfig **Start** | → | `cfgId`, `startIdx`, `packetNum`, `packetSize` | echo |
| `0xA9` | WriteRgbConfig **Pack** | → | `packNum`, 20 B chunk | echo |
| `0xAB` | **SaveCurrentSwitchMappingConfig (NS bind)** | → | `dataVersion` u16 LE, `cfgId` 4..7 | echo, 10 s |
| `0xAC` | ReadMacroConfig | → | `cfgId`, `pkgSize` | N×20 B chunks |
| `0xAD` | WriteMacroConfig **Start** | → | `cfgId`, `startIdx`, `packetNum`, `packetSize` | echo |
| `0xAE` | WriteMacroConfig **Pack** | → | `packNum`, 20 B chunk | echo |
| `0xAF` | ResetMappingConfigByCfgId | → | `cfgId` | echo, 10 s |
| `0xEF` | (inbound) input/operator report | ← | — | not a command |
| `0xF1` | TestIndicator | → | `ledId`, `freq`, `times`, R, G, B | — |
| `0xF5` | TestLed | → | R, G, B | — |
| `0xF7` | (inbound) raw motion data | ← | — | not a command |

`0xAA` is not used by any NewXInput command in this SDK (it appears only as a DInput ACK magic).

**Commands with no NewXInput implementation** (factory dispatches only XInput/DInput — do not use on a Vader 5 Pro):
`ReadCurrentMappingConfigIdCommand` (XInput `0x20` / DInput `0xEB`), `ReadMappingConfigVersionCommandFactory` (both `0x50` sub `0x02`), `SetMappingEnableCommandFactory` (XInput `0x18` / DInput `0xEE`). Use `0xA1` and `0x13` sub `0x04` respectively.

---

# PART 2 — Button mapping & profile configuration

## 2.1 Button / key ID enumeration (`ControllerKey`)

`Flydigi.SharedResources.Data.Protobuf.ControllerKey`. **These integers are the literal byte values used in the `key_table`, in macro records, and in motion-trigger fields.**

### Physical inputs present on a Vader 5 Pro

`FlydigiControllerFactory.GenerateControllerVader5` declares exactly these 29 `SupportKeys`:

| ID (dec) | ID (hex) | Enum | Physical input on Vader 5 Pro |
|---:|---|---|---|
| 0 | `0x00` | `Up` | D-pad up |
| 1 | `0x01` | `Right` | D-pad right |
| 2 | `0x02` | `Down` | D-pad down |
| 3 | `0x03` | `Left` | D-pad left |
| 4 | `0x04` | `A` | A face button |
| 5 | `0x05` | `B` | B face button |
| 6 | `0x06` | `Select` | Select / View / "−" |
| 7 | `0x07` | `X` | X face button |
| 8 | `0x08` | `Y` | Y face button |
| 9 | `0x09` | `Start` | Start / Menu / "+" |
| 10 | `0x0A` | `Lb` | Left bumper (L1) |
| 11 | `0x0B` | `Rb` | Right bumper (R1) |
| 12 | `0x0C` | `Lt` | Left trigger (L2, analog) |
| 13 | `0x0D` | `Rt` | Right trigger (R2, analog) |
| 14 | `0x0E` | `Thl` | Left stick click (L3) |
| 15 | `0x0F` | `Thr` | Right stick click (R3) |
| 16 | `0x10` | `C` | C button (front/extra) |
| 17 | `0x11` | `Z` | Z button (front/extra) |
| 18 | `0x12` | `M1` | Back paddle M1 |
| 19 | `0x13` | `M2` | Back paddle M2 |
| 20 | `0x14` | `M3` | M3 |
| 21 | `0x15` | `M4` | M4 |
| 22 | `0x16` | `M5` | M5 |
| 23 | `0x17` | `M6` | M6 |
| 24 | `0x18` | `Menu` | Menu / Flydigi button |
| 25 | `0x19` | `Turbo` | Turbo button |
| 27 | `0x1B` | `Home` | Home / Guide |
| 240 | `0xF0` | `JsLeft` | Left analog stick (axis pair) |
| 241 | `0xF1` | `JsRight` | Right analog stick (axis pair) |

### Remaining `ControllerKey` values

| ID (dec) | ID (hex) | Enum | Role |
|---:|---|---|---|
| 26 | `0x1A` | *(gap — no member)* | unused slot in `key_table` |
| 28 | `0x1C` | `Back` | Back button (not on Vader 5 Pro) |
| 32 | `0x20` | `Macro` | **sentinel value**, not a slot: "this key runs a macro" |
| 242 | `0xF2` | `JsWheel` | scroll-wheel-style stick (not on Vader 5 Pro) |
| 254 | `0xFE` | `KeyboardMouse` | **sentinel value**: "host-side keyboard/mouse remap" |
| 255 | `0xFF` | `None` | **sentinel value**: identity / unmapped |
| 160 | `0xA0` | `JoystickCenter` | stick-direction pseudo-key (for stick→button mapping) |
| 161 | `0xA1` | `JoystickUp` | ditto |
| 162 | `0xA2` | `JoystickRightUp` | ditto |
| 163 | `0xA3` | `JoystickRight` | ditto |
| 164 | `0xA4` | `JoystickRightDown` | ditto |
| 165 | `0xA5` | `JoystickDown` | ditto |
| 166 | `0xA6` | `JoystickLeftDown` | ditto |
| 167 | `0xA7` | `JoystickLeft` | ditto |
| 168 | `0xA8` | `JoystickLeftUp` | ditto |

### Physical→slot mapping rule

**`key_table` slot index == `ControllerKey` numeric value.** From `MappingConfigParser.ParseToKeyConfig`:

```csharp
for (int i = 0; i < data.Length / 3; i++) {           // data.Length == 96  ->  i in 0..31
    var bean = new KeyConfigBean { KeyId = (ControllerKey)i };
    ...
}
```

So `key_table` is a dense array of **32 slots** covering `ControllerKey` `0x00`–`0x1F`. Slots 26, 29, 30, 31 have no `ControllerKey` member and are unused (`Enum.Parse` on them would throw, so never read them back; leave them `0xFF`). Sticks (`0xF0`/`0xF1`) are **not** in `key_table` — they live in `joy_table` / `joy_extra` (§2.6).

### Live button-state bit positions (input report, not config)

`Button.IsButtonPressed` (`data.model/Button.cs`) — for the **non-mapping** input report on a NewXInput/DInput device (the report identified by `data[2] == 0xEF`), and the *mapping-mode* report layout that the host-side injector uses:

| Key | Normal report | Mapping report |
|---|---|---|
| `Up` | `data[9] & 0x01` | `data[2] & 0x01` |
| `Right` | `data[9] & 0x02` | `data[2] & 0x08` |
| `Down` | `data[9] & 0x04` | `data[2] & 0x02` |
| `Left` | `data[9] & 0x08` | `data[2] & 0x04` |
| `A` | `data[9] & 0x10` | `data[3] & 0x10` |
| `B` | `data[9] & 0x20` | `data[3] & 0x20` |
| `Select` | `data[9] & 0x40` | `data[2] & 0x20` |
| `X` | `data[9] & 0x80` | `data[3] & 0x40` |
| `Y` | `data[10] & 0x01` | `data[3] & 0x80` |
| `Start` | `data[10] & 0x02` | `data[2] & 0x10` |
| `Lb` | `data[10] & 0x04` | `data[3] & 0x01` |
| `Rb` | `data[10] & 0x08` | `data[3] & 0x02` |
| `Lt` | `data[10] & 0x10` | `data[4] > 0` (analog) |
| `Rt` | `data[10] & 0x20` | `data[5] > 0` (analog) |
| `Thl` | `data[10] & 0x40` | `data[2] & 0x40` |
| `Thr` | `data[10] & 0x80` | `data[2] & 0x80` |
| `C` | `data[7] & 0x01` | n/a |
| `Z` | `data[7] & 0x02` | n/a |
| `M1` | `data[7] & 0x04` **or** `data[8] & 0x81` | n/a |
| `M2` | `data[7] & 0x08` | n/a |
| `M3` | `data[7] & 0x10` | n/a |
| `M4` | `data[7] & 0x20` | n/a |
| `M5` | `data[7] & 0x40` | n/a |
| `M6` | `data[7] & 0x80` | n/a |
| `Menu` | `data[8] & 0x01` | n/a |
| `Home` | `data[8] & 0x08` | n/a |
| `Back` | `data[8] & 0x10` | n/a |

(The XInput transport uses the same bit masks at `data[17]`/`data[18]`/`data[19]`/`data[20]` instead of `data[9]`/`data[10]`/`data[7]`/`data[8]`.)

`Joystick` (`data.model/Joystick.cs`): axes are unsigned bytes with **centre = 128**; `Direction` is derived with a 16-count dead-zone (`|x−128| ≤ 16 && |y−128| ≤ 16` → `Center`) and 45°-wide octants via `atan2(y−c, x−c)`:

`Direction` enum: `Center=0, Top=1, TopLeft=2, Left=3, LeftBottom=4, Bottom=5, BottomRight=6, Right=7, RightTop=8`.

## 2.2 Keyboard / mouse output codes — **host-side only**

**The controller never receives a keyboard or mouse code.** When a button is remapped to a keyboard key, `MappingConfigParser.ParseKeyConfigToArray` writes the single byte `0xFE` (`ControllerKey.KeyboardMouse`) into that key's slot:

```csharp
else if (bean.MapType == KeyMapType.MultiFunction || bean.MapTypeKey.HasMapKeyboardKeyId)
    array[num] = 254;
```

and on read-back `ParseToKeyConfig` collapses anything `> 32` back to identity (`data[num] > 32 ? i : data[num]`). The actual keyboard/mouse binding lives **only** in the host's local protobuf profile file and is executed by `SpaceStationService`'s `KeyboardMouseInjectRunner` (via `FeizVkeyMouHelper`), which watches the input report and synthesises Windows input. This is also why `SaveSwitchConfig` strips keyboard bindings before committing an NS profile (§0.3) — nothing on the console side can honour them.

If you need keyboard remaps you must reimplement that injection yourself. The code tables the app uses are Win32 virtual-key codes:

**`KeyboardKey`** — 179 members; it is `System.Windows.Forms.Keys` verbatim (VK codes `0x00`–`0xFF` in the low byte) plus flags and two custom values:

| Member | Value | Notes |
|---|---|---|
| `KeyboardNone` | 0 | |
| `KeyboardLbutton` / `Rbutton` / `Mbutton` | 1 / 2 / 4 | VK_LBUTTON etc. |
| `KeyboardXbutton1` / `Xbutton2` | 5 / 6 | |
| `KeyboardBack` | 8 | Backspace |
| `KeyboardTab` | 9 | |
| `KeyboardEnter` | 13 | |
| `KeyboardShiftKey` / `ControlKey` / `Menu` | 16 / 17 / 18 | plain VK_SHIFT/CONTROL/MENU |
| `KeyboardCapital` | 20 | Caps Lock |
| `KeyboardEscape` | 27 | |
| `KeyboardSpace` | 32 | |
| `KeyboardPageUp` / `PageDown` / `End` / `Home` | 33 / 34 / 35 / 36 | |
| `KeyboardLeft` / `Up` / `Right` / `Down` | 37 / 38 / 39 / 40 | |
| `KeyboardPrintScreen` | 44 | |
| `KeyboardInsert` / `Delete` | 45 / 46 | |
| `KeyboardD0`…`KeyboardD9` | 48…57 | top-row digits |
| `KeyboardA`…`KeyboardZ` | 65…90 | |
| `KeyboardLwin` / `Rwin` / `Apps` | 91 / 92 / 93 | |
| `KeyboardSleep` | 95 | |
| `KeyboardNumPad0`…`NumPad9` | 96…105 | |
| `KeyboardMultiply` / `Add` / `Separator` / `Subtract` / `Decimal` / `Divide` | 106 / 107 / 108 / 109 / 110 / 111 | |
| `KeyboardF1`…`KeyboardF24` | 112…135 | |
| `KeyboardNumLock` / `Scroll` | 144 / 145 | |
| `KeyboardLshiftKey` / `RshiftKey` | 160 / 161 | |
| `KeyboardLcontrolKey` / `RcontrolKey` | 162 / 163 | |
| `KeyboardLmenu` / `Rmenu` | 164 / 165 | left/right Alt |
| `KeyboardBrowserBack`…`BrowserHome` | 166–172 | |
| `KeyboardVolumeMute` / `VolumeDown` / `VolumeUp` | 173 / 174 / 175 | |
| `KeyboardMediaNextTrack` / `PreviousTrack` / `MediaStop` / `MediaPlayPause` | 176 / 177 / 178 / 179 | |
| `KeyboardLaunchMail` / `SelectMedia` / `LaunchApplication1` / `LaunchApplication2` | 180 / 181 / 182 / 183 | |
| `KeyboardOemSemicolon` `;` | 186 | |
| `KeyboardOemPlus` `=` | 187 | |
| `KeyboardOemComma` `,` | 188 | |
| `KeyboardOemMinus` `-` | 189 | |
| `KeyboardOemPeriod` `.` | 190 | |
| `KeyboardOemQuestion` `/` | 191 | |
| `KeyboardOemtilde` `` ` `` | 192 | |
| `KeyboardOemOpenBrackets` `[` | 219 | |
| `KeyboardOemPipe` `\` | 220 | |
| `KeyboardOemCloseBrackets` `]` | 221 | |
| `KeyboardOemQuotes` `'` | 222 | |
| `KeyboardOem8` / `OemBackslash` | 223 / 226 | |
| `KeyboardProcessKey` / `Packet` | 229 / 231 | |
| `KeyboardAttn` `Crsel` `Exsel` `EraseEof` `Play` `Zoom` `NoName` `Pa1` `OemClear` | 246 247 248 249 250 251 252 253 254 | |
| `KeyboardShift` | **65536** (`0x10000`) | modifier flag |
| `KeyboardControl` | **131072** (`0x20000`) | modifier flag |
| `KeyboardAlt` | **262144** (`0x40000`) | modifier flag |
| `Code` | **65535** (`0xFFFF`) | key-code mask |
| `KeyboardModifiers` | **−65536** (`0xFFFF0000`) | modifier mask |
| `KeyboardWheelUp` | **433** | Flydigi extension (not a VK) |
| `KeyboardWheelDown` | **434** | Flydigi extension (not a VK) |

Also present: `KeyboardCancel=3`, `KeyboardClear=12`, `KeyboardLineFeed=10`, `KeyboardPause=19`, `KeyboardHangulMode=21`, `KeyboardJunjaMode=23`, `KeyboardFinalMode=24`, `KeyboardHanjaMode=25`, `KeyboardImeconvert=28`, `KeyboardImenonConvert=29`, `KeyboardImeaccept=30`, `KeyboardImemodeChange=31`, `KeyboardSelect=41`, `KeyboardPrint=42`, `KeyboardExecute=43`, `KeyboardHelp=47`.

**`MouseKey`** — `System.Windows.Forms.MouseButtons` verbatim:

| Member | Value |
|---|---|
| `None` | `0` |
| `Left` | `1048576` (`0x100000`) |
| `Right` | `2097152` (`0x200000`) |
| `Middle` | `4194304` (`0x400000`) |
| `Xbutton1` | `8388608` (`0x800000`) |
| `Xbutton2` | `16777216` (`0x1000000`) |

---

## 2.3 Protocol versions of the mapping blob

`ProtoVersion` is a 16-bit value stored **little-endian at blob offset 0**: `ProtoVersion = (data[1] << 8) | data[0]`. `data[1]` is the major version, `data[0]` the minor.

| ProtoVersion | hex | Parser | `PackageCount` const | Blob length (legacy 10 B pkts) | Blob length as the SDK actually emits it for NewXInput (20 B pkts) |
|---|---|---|---|---|---|
| 512 | `0x0200` | `MappingConfigParserV20` (**stubbed out — empty methods**) | 77 | `(bean.PackageCount + 2) × 10` | `(bean.PackageCount + 2) × 20` |
| 768 | `0x0300` | `V30` | 79 | 790 | 1580 |
| 769 | `0x0301` | `V30` + `V31` | 84 | 840 | 1680 |
| 770 | `0x0302` | `V30` + `V31` + `V32` (**V32 is stubbed out**) | 84 | 840 | 1680 |

Feature gates keyed off `ProtoVersion`:

| Predicate | Effect |
|---|---|
| `major == 3` | use `V30` field layout |
| `minor >= 1` | `V31` extras present (per-stick curve bank, per-macro interval, motion smoothing curve) |
| `minor >= 2` | `V32` (no additional fields implemented) |
| `(ProtoVersion & 0xF) < 2` | macros are **inside** the mapping blob at offset 230 |
| `ProtoVersion >= 770` | macros live in a **separate blob** (cmds `0xAC`/`0xAD`/`0xAE`), and `GetMaxMacroCount()=10`, `GetMaxMacroActionCount()=256`, `GetMinMacroInterval()=1 ms` |
| `ProtoVersion < 770` | `GetMaxMacroCount()=5`, `GetMaxMacroActionCount()=128`, `GetMinMacroInterval()=10 ms` |
| `ProtoVersion < 769` | "old protocol" joystick scaling (the 0–127 ↔ 0–100 remap in `ParseToJoystickConfig`/`ParseJoystickConfigToArray`) |

The Vader 5 Pro reports `ProtoVersion` in the blob itself; based on `IsSupportNs`/`f5` handling and the fact that `ReadMacroConfigCommand` (cmd `0xAC`) exists **only** as a NewXInput class, expect `0x0302` (770). **Verify on hardware by reading the first two bytes of the `0xA3` reply.**

> **BLOB SIZE ANOMALY — read this before implementing uploads.**
> `MappingConfigParser.ParseConfigToArray` allocates `PackageCount × perPkgCount` bytes. `PackageCount` (79 / 84) is a count of **10-byte** units — `84 × 10 = 840` is exactly the struct size, and the largest offset any parser writes is 839. But `perPkgCount` is **20** for NewXInput, so the SDK actually builds a **1680-byte** buffer and emits **84 packets of 20 bytes**, with offsets `0x348`–`0x68F` (840–1679) left as `0xFF` filler. The same latent over-allocation exists in `MacroConfigParser` (`81 × 20 = 1620` where 810 would do).
> Byte-for-byte compatibility means sending the full 84 packets. On the **read** side no such assumption is made: the buffer is sized `data[3] × 20` from the device's own announced packet count, so the device may well report only 42 packets (840 bytes).

## 2.4 Commands `0xA4` / `0xA5` — upload a mapping blob

**`0xA4` — Start** (`WriteMappingConfigCommandFactory.WriteMappingConfigCommandStartNewXInput`; `WriteAllMappingConfigCommandFactory` emits the identical frame):

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA4
[4]  0x06                 len = 2 + 4
[5]  cfgId                0x00..0x07  (0x00..0x03 normal; 0x04..0x07 accepted directly by old-protocol devices)
[6]  startIndex           absolute packet index this batch begins at
[7]  packetNum            number of 0xA5 packets that follow in this batch
[8]  packetSize           0x14 (20) for NewXInput
[9]  checksum = sum(a[3]..a[8]) & 0xFF
[10..31] 0x00

ACK: data[2] == 0xA4
ctor: maxRetry 3, timeout 500 ms, commandIndex 0, commandCount = packetNum + 1
```

**`0xA5` — Pack**:

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA5
[4]  len = pack.Length + 3          = 0x17 (23) for a 20-byte chunk
[5]  packNum                        0-based index WITHIN this batch
[6 .. 6+pack.Length-1]  chunk bytes (20)
[6+pack.Length]         checksum = sum(a[3] .. a[3+len-1]) & 0xFF
                                  = sum(cmd, len, packNum, all 20 payload bytes) & 0xFF
                        (written as array[6 + pack.Length], which equals array[3 + array[4]])
rest 0x00

ACK: data[2] == 0xA5
```

For a 20-byte chunk the frame occupies bytes 0..26 of the 32-byte report.

## 2.5 Command `0xA3` — read a mapping blob

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA3
[4]  0x04                len = 2 + 2
[5]  cfgId
[6]  pkgSize             0x14 (20)
[7]  checksum = sum(a[3]..a[6]) & 0xFF
rest 0x00

HasMultiAck = true, IsNeedCallback = true

ACK frames:
[0]=0x5A [1]=0xA5 [2]=0xA3
[3] = total packet count
[4] = this packet's index (0-based)
[5] = cfgId (echoed; stamped onto the parsed bean as CfgId)
[6 .. 6+pkgSize-1] = 20 payload bytes  ->  blob[20*data[4] ..]
finished when data[3] == data[4] + 1
buffer is pre-filled with 0xFF before the first packet lands
```

## 2.6 Mapping blob — authoritative byte layout

This is the wire layout as implemented by `MappingConfigParser.MappingConfigParserV30` + `…V31`. All multi-byte scalars are **little-endian**. Unwritten bytes are `0xFF`. The declared C struct is `m_fdg_mapping_config_struct_t` (`StructLayout(LayoutKind.Sequential, Pack = 1)`, no `FieldOffset` anywhere in this namespace, every array is `MarshalAs(UnmanagedType.ByValArray, SizeConst = …)`).

| Offset (dec) | Offset (hex) | Size | Struct field | Type | Meaning |
|---:|---|---:|---|---|---|
| 0 | `0x000` | 2 | `version[2]` | `byte[2]` | `[0]`=minor, `[1]`=major. `ProtoVersion = (b1<<8)\|b0` |
| 2 | `0x002` | 1 | `pkg_len` | `byte` | `PackageCount` (echo of 79/84) |
| 3 | `0x003` | 10 | `led[10]` | `byte[10]` | legacy inline LED header (`OldLedConfig`), = first 10 bytes of the LED blob (§4.4) |
| 13 | `0x00D` | 96 | `key_table[32]` | `m_fdg_macro_key_mapping_struct_t[32]` | **button remap table**, 3 B/slot, slot index = `ControllerKey` |
| 109 | `0x06D` | 14 | `joy_table[2]` | `m_fdg_macro_joy_mapping_struct_t[2]` | analog stick response curves — `[0]`=left, `[1]`=right |
| 123 | `0x07B` | 14 | `liner_table[2]` | `m_fdg_macro_joy_mapping_struct_t[2]` | **trigger** (LT/RT) response curves — `[0]`=left, `[1]`=right |
| 137 | `0x089` | 8 | `motion[…]` | `m_fdg_macro_motion_mapping_struct_t` | gyro/motion mapping — **only one element on the wire** (see note) |
| 145 | `0x091` | 9 | `grip` | `m_fdg_motor_grip_struct_t` | grip rumble motors (`VibrationConfigBean`) |
| 154 | `0x09A` | 29 | `trig` | `m_fdg_motor_trig_struct_t` | trigger haptics (`TriggerVibrationConfigBean`) |
| 183 | `0x0B7` | 2 | `lunpan` | `m_fdg_macro_lunpan_struct_t` | "轮盘"/wheel: `{type, rev}`, surfaced raw as `config.Lunpan[0..1]` |
| 185 | `0x0B9` | 40 | `trigger[2]` | `m_fdg_macro_trigger_sturct_t[2]` | auto-trigger / trigger adapter, 20 B each |
| 225 | `0x0E1` | 2 | `random_data[2]` | `byte[2]` | **`dataVersion`**, u16 LE (§0.5) |
| 227 | `0x0E3` | 3 | `reserve1[3]` | `byte[3]` | `0xFF` |
| 230 | `0x0E6` | 538 | `macro` | `m_fdg_macro_page_struct_t` | inline macro page — **written only when `(ProtoVersion & 0xF) < 2`** |
| 768 | `0x300` | 2 | `reserve2[2]` | `byte[2]` | `0xFF` |
| 770 | `0x302` | 16 + 4 | `cfg_name[16]` + `reserve3[4]` | `byte[16]`,`byte[4]` | **profile title, UTF-16LE, 20 bytes = 10 chars** (parser reads/writes `[770,790)`, i.e. it runs past `cfg_name` into `reserve3`) |
| 790 | `0x316` | 24 | `joy_extra[2]` | `m_fdg_macro_joy_extra_v2_struct_t[2]` | **V3.1**: per-stick 9-point curve bank + circularity + edge |
| 814 | `0x32E` | 6 | `reserve4[6]` | `byte[6]` | `0xFF` |
| 820 | `0x334` | 5 | `macro_cycle` | `m_fdg_macro_cycle_struct_t` | **V3.1**: per-macro repeat interval, `interval_ms / 10`, one byte per macro. Written only when `ProtoVersion < 770`; **read** as 10 bytes `[820,830)` |
| 825 | `0x339` | 5 | `reserve5[5]` | `byte[5]` | `0xFF` (the V3.1 macro-interval **read** spills into this) |
| 830 | `0x33E` | 6 | `motion_curve` | `m_fdg_macro_curve_mapping_struct_t` | **V3.1**: motion smoothing curve (`MotionSmoothnessConfig`) |
| 836 | `0x344` | 4 | `reserve6[4]` | `byte[4]` | `0xFF` |
| — | — | **840** | | | **effective total struct size = `0x348`** |

> **`motion[2]` discrepancy.** `m_fdg_mapping_config_struct_t` declares `[MarshalAs(ByValArray, SizeConst = 2)] m_fdg_macro_motion_mapping_struct_t[] motion`, which would occupy 16 bytes at 137 and push every later field +8, giving a 848-byte struct. The **parser** unambiguously places `grip` at 145 (`data[145..154)`) and gives `motion` only 8 bytes (`data[137..145)`), and the resulting total (840) matches `PackageCount(84) × 10` exactly, plus every V3.1 offset (790 / 820 / 830). **Trust the parser offsets in the table above.** The `SizeConst = 2` is either a decompilation artefact or a stale field in the SDK's header port; only `motion[0]` exists on the wire.
> Similarly `cfg_name[16]` under-declares the 20 bytes the parser uses for the title; treat `cfg_name` + `reserve3` as a single 20-byte UTF-16LE field.

### `key_table` — the button remap table (offset 0x00D, 32 × 3 bytes)

`m_fdg_macro_key_mapping_struct_t` — `Pack = 1`, **3 bytes**:

| Offset in slot | Field | Type | Meaning |
|---:|---|---|---|
| +0 | `keyid` | `byte` | what this physical key emits |
| +1 | `type` | `byte` | `KeyMapTypeContinuousEnableType`: `0`=Close, `1`=Press(hold), `2`=Click(toggle) |
| +2 | `turbo` | `byte` | turbo/rapid-fire frequency; `0` = not a turbo mapping |

Slot address = `ControllerKey` value (§2.1). Encoding rules, from `ParseKeyConfigToArray` (write) and `ParseToKeyConfig` (read):

| `keyid` byte | `type` | `turbo` | Meaning |
|---|---|---|---|
| `0x00`–`0x1F` (a real `ControllerKey`) | `0x00` | `0x00` | **plain remap**: this physical key emits that key |
| `0xFF` | `0x00` | `0x00` | **identity** (`MapTypeKey.MapControllerKeyId == own KeyId`) |
| target key | any | **> 0** | **turbo / continuous**: emit `keyid` repeatedly at `turbo` Hz, gated by `type` (`1`=while held, `2`=toggle on click) |
| `0x20` (`ControllerKey.Macro`) | — | — | **run the macro bound to this key** (`KeyMapType.Macro`) |
| `0xFE` (`ControllerKey.KeyboardMouse`) | `0x00` | `0x00` | **host-side keyboard/mouse remap** (`KeyMapType.Keyboard` / `MultiFunction`); the controller itself does nothing (§2.2) |

Read-back precedence in `ParseToKeyConfig`:
1. `keyid == 0x20` → `KeyMapType.Macro`
2. `turbo > 0` → `KeyMapType.Continuous` with `MapControllerKeyId = keyid`, `EnableType = type`, `Frequency = turbo`
3. else → `KeyMapType.Key` with `MapControllerKeyId = (keyid > 0x20) ? slotIndex : keyid` — i.e. **`0xFE` and `0xFF` both read back as identity**, so a keyboard remap is invisible on read-back.

Enums: `KeyMapType { Key=0, Continuous=1, Macro=2, MultiFunction=3, Keyboard=4 }`, `KeyMapTypeContinuousEnableType { Close=0, Press=1, Click=2 }`.

`MappingConfigConst.TURBO_TOUCH_TIME = 25` — turbo tick/debounce constant (ms) used by the firmware/host model.

### `joy_table` / `liner_table` (offsets 0x06D / 0x07B, 2 × 7 bytes)

`m_fdg_macro_joy_mapping_struct_t` — `Pack = 1`, **7 bytes**:

| Offset in slot | Field | Type | `joy_table` meaning (stick) | `liner_table` meaning (trigger) |
|---:|---|---|---|---|
| +0 | `type` | `byte` | `JoystickSensitivityType`: `0`=Default, `1`=Quick, `2`=Slow, `3`=Custom | trigger curve `Type` |
| +1 | `zero` | `byte` | dead-zone / centre, 0..127 (values > 127 are decoded as `127 − v`) | trigger `Zero` |
| +2 | `point0x` | `byte` | Bézier/curve control point 1 X | `Point1.X` |
| +3 | `point0y` | `byte` | control point 1 Y | `Point1.Y` |
| +4 | `point1x` | `byte` | control point 2 X | `Point2.X` |
| +5 | `point1y` | `byte` | control point 2 Y | `Point2.Y` |
| +6 | `end` | `byte` | saturation point, 0..127 (same `>127 → 127−v` decode) | trigger `End` |

Index `0` = left, `1` = right.

Stick X/Y coordinate scaling (`ParseJoystickConfigToArray`), applied to `point*x` only:
```
xPct   = P.X * 100 / 127
scaled = center + (100 - center) * xPct / 100
byteX  = scaled * 127 / 100
```
and if `ProtoVersion < 769` additionally `byteX *= (End - Center)/100`, and `zero` is pre-scaled `center * 127 / 100`. `point*y` is stored raw. If the stick's `MapType != Joystick` (i.e. it is mapped to keyboard/mouse/dpad), `zero` is forced to `127`.
`MappingConfigConst.JOY_LINER_SENST_DEFAULT = 17` is the default linear-sensitivity value.

### `motion` (offset 0x089, 8 bytes)

`m_fdg_macro_motion_mapping_struct_t` — `Pack = 1`, **8 bytes**:

| Offset | Field | Meaning |
|---:|---|---|
| +0 | `type` | `MotionMapType { Off=0, LeftJoystick=1, RightJoystick=2, Mouse=3 }` |
| +1 | `keyid` | enable key #1 (`0xFF` when `type == Mouse`) |
| +2 | `method` | `MotionEnableType { Click=0, Press=1 }` |
| +3 | `zero` | dead zone (`0` when `type == Mouse`) |
| +4 | `sensity_x` | sensitivity X — the SDK writes the **same** value to +4 and +5 (`Sensitivity = max(b4,b5)` on read) |
| +5 | `sensity_y` | sensitivity Y |
| +6 | `mode` | `MotionUseMode { MotionModeFps=0, MotionModeRacer=1 }` |
| +7 | `keyid_ext` | enable key #2 (`0xFF` when `type == Mouse`) |

### `grip` (offset 0x091, 9 bytes) — grip rumble

`m_fdg_motor_grip_struct_t` — `Pack = 1`, **9 bytes**:

| Offset | Field | Meaning |
|---:|---|---|
| +0 | `main_switch` | master enable, **inverted**: `0x00` = enabled, `0xFF` = disabled |
| +1 | `unit[0]` | left motor, `m_fdg_motor_grip_setting_struct_t` (4 B) |
| +5 | `unit[1]` | right motor (4 B) |

`m_fdg_motor_grip_setting_struct_t` — `Pack = 1`, **4 bytes**: `type` (per-motor enable, `0x00`=on / `0xFF`=off), `min`, `max`, `scale`.
On read the SDK normalises: `Min = min(b_min, b_max)`, `Max = max(b_min, b_max)`.

### `trig` (offset 0x09A, 29 bytes) — trigger haptics

`m_fdg_motor_trig_struct_t` — `Pack = 1`, **29 bytes**:

| Offset | Field | Meaning |
|---:|---|---|
| +0 | `main_switch` | master enable (`0`=on, `1`/`0xFF`=off) — **only the left trigger's `Enable` is stored here** |
| +1 | `unit[0]` | left trigger, `m_fdg_motor_trig_mode_struct_t` (14 B) |
| +15 | `unit[1]` | right trigger (14 B) |

`m_fdg_motor_trig_mode_struct_t` — `Pack = 1`, **14 bytes**: `line_gear` (7 B, "linear" mode) then `micr_gear` (7 B, "micro" mode).

`m_fdg_motor_trig_setting_struct_t` — `Pack = 1`, **7 bytes**:

| Offset | Field | `TriggerVibrationTypedConfigBean` |
|---:|---|---|
| +0 | `type` | `Type` |
| +1 | `min` | `MinLevel` |
| +2 | `max` | `MaxLevel` |
| +3 | `filter` | `Filter` |
| +4 | `vibr_limit` | `MinStart` |
| +5 | `scale` | `Scale` |
| +6 | `time_limit` | `MinTime` |

### `trigger[2]` (offset 0x0B9, 2 × 20 bytes) — auto-trigger / trigger adapter

`m_fdg_macro_trigger_sturct_t` — `Pack = 1`, **20 bytes**:

| Offset | Field | Type | Meaning |
|---:|---|---|---|
| +0 | `type` | `byte` | `TriggerAdapterConfigBean.Type` |
| +1 | `bind` | `m_fdg_macro_trigger_bind_sturct_t` (8 B) | see below |
| +9 | `mixed_border` | `byte` | `MixedBorder` |
| +10 | `param[10]` | `byte[10]` | `TriggerAdapterConfigBean.Param` |

`m_fdg_macro_trigger_bind_sturct_t` — `Pack = 1`, **8 bytes**:

| Offset | Field | Meaning |
|---:|---|---|
| +0 | `type` | on write the SDK **derives** this: `(adapter.Type == 5) ? 2 : 0` (the bean's own `Bind.Type` is ignored) |
| +1 | `filter` | `Bind.Filter` |
| +2 | `scale` | `Bind.Scale` |
| +3 | `param[5]` | `Bind.Param` (5 bytes) |

### `joy_extra[2]` (offset 0x316, 2 × 12 bytes) — V3.1 stick curve bank

`m_fdg_macro_joy_extra_v2_struct_t` — `Pack = 1`, **12 bytes**:

| Offset | Field | Type | Meaning |
|---:|---|---|---|
| +0 | `type` | `byte` | `JoystickSensitivityType` (duplicate of `joy_table[i].type`) |
| +1 | `bank[9]` | `byte[9]` | `SensitivityConfig.Points` — 9-point custom response curve |
| +10 | `isRound` | `byte` | `JoystickCircularityType { Rectangle=0, Circular=1 }` |
| +11 | `end` | `byte` | `Edge` (0..127; `>127 → 127−v` on read) |

`m_fdg_macro_joy_extra_struct_t` (v1, **not used on the wire**) — `Pack = 1`, **5 bytes**: `extra_center_l`, `extra_center_r`, `extra_edge_l`, `extra_edge_r`, `extra_circle_radio`.

### `macro_cycle` (offset 0x334, 5 bytes)

`m_fdg_macro_cycle_struct_t` — `Pack = 1`, **5 bytes**: `cycle[5]`. Byte *i* = `MacroConfigBean.Interval[i] / 10`, i.e. the macro's repeat interval in **units of 10 ms**, one byte per macro (5 macros max on proto < 3.2). Written only when `ProtoVersion < 770`; the reader reads 10 bytes `[820,830)` (spilling into `reserve5`) and multiplies by 10.

### `motion_curve` (offset 0x33E, 6 bytes)

`m_fdg_macro_curve_mapping_struct_t` — `Pack = 1`, **6 bytes**: `zero`, `point0x`, `point0y`, `point1x`, `point1y`, `end` → `MotionConfigBean.MappingTypeJoystick.Smoothness` (`Zero`, `Point1.X/Y`, `Point2.X/Y`, `End`). Written unconditionally in V3.1.

### `led[10]` (offset 0x003, 10 bytes)

Copied verbatim from/to `ControllerMappingConfigBean.OldLedConfig` (a `repeated int32` of 10 bytes). It is the **first 10 bytes of the LED blob header** (§4.4) embedded inline for legacy firmware. The authoritative LED data is the separate LED blob (cmd `0xA7`/`0xA8`/`0xA9`).

---

## 2.7 Struct size summary

All structs in `Flydigi.ControllerSDK.data.model.config` are `[StructLayout(LayoutKind.Sequential, Pack = 1)]`. There are **no `FieldOffset` attributes and no `LayoutKind.Explicit`** anywhere in the namespace. Every array field is `[MarshalAs(UnmanagedType.ByValArray, SizeConst = N)]`. Every scalar field is `byte` except in the two runtime-only state structs.

| Struct | Size (B) | Notes |
|---|---:|---|
| `m_fdg_macro_rgb_unit_sturct_t` | **3** | `r, g, b` |
| `m_fdg_macro_rgb_group_sturct_t` | **30** | `unit[10]` × 3 |
| `m_fdg_mapping_rgb_sturct_t` | **500** | 20 B header + `id[16]` × 30 — exactly the LED-blob v2.0 length |
| `m_fdg_macro_key_mapping_struct_t` | **3** | `keyid, type, turbo` |
| `m_fdg_macro_joy_mapping_struct_t` | **7** | `type, zero, point0x, point0y, point1x, point1y, end` |
| `m_fdg_macro_motion_mapping_struct_t` | **8** | |
| `m_fdg_macro_curve_mapping_struct_t` | **6** | |
| `m_fdg_macro_cycle_struct_t` | **5** | `cycle[5]` |
| `m_fdg_macro_lunpan_struct_t` | **2** | `type, rev` |
| `m_fdg_macro_joy_extra_struct_t` | **5** | v1, unused on the wire |
| `m_fdg_macro_joy_extra_v2_struct_t` | **12** | `type, bank[9], isRound, end` |
| `m_fdg_motor_grip_setting_struct_t` | **4** | |
| `m_fdg_motor_grip_struct_t` | **9** | `1 + 2×4` |
| `m_fdg_motor_trig_setting_struct_t` | **7** | |
| `m_fdg_motor_trig_mode_struct_t` | **14** | `2 × 7` |
| `m_fdg_motor_trig_struct_t` | **29** | `1 + 2×14` |
| `m_fdg_macro_trigger_bind_sturct_t` | **8** | `3 + 5` |
| `m_fdg_macro_trigger_sturct_t` | **20** | `1 + 8 + 1 + 10` |
| `m_fdg_macro_step_struct_t` | **4** | `time_l, time_h, btn, event` |
| `m_fdg_macro_unit_struct_t` | **260** | `4 + 64×4` (`MAX_SINGLE_HONG_STEP = 64`) |
| `m_fdg_macro_page_struct_t` | **538** | `1 + 5 + 532` (`MAX_HONG_NUMS = 5`, `MAX_HONG_SIZE = 532`) |
| `m_fdg_mapping_config_struct_t` | **840** effective / 848 as literally declared | see `motion[2]` note in §2.6 |
| `m_fdg_macro_state_struct_t` | 17 on x64 (**runtime only**) | `bool active; nint punit; ushort cur_step; ushort cur_time; uint keystate;` — firmware/host runtime state, **never serialised**. `nint` is pointer-sized so the size is architecture-dependent (13 on 32-bit). |
| `m_fdg_turbo_state_struct_t` | 7 (**runtime only**) | `bool active; ushort cur_time; uint keystate;` — never serialised |

## 2.8 `MappingConfigConst` — every constant

`Flydigi.ControllerSDK.data.model.config.MappingConfigConst`:

| Constant | Value | Hex | Meaning |
|---|---:|---|---|
| `MAX_HONG_NUMS` | **5** | `0x05` | max macros stored in the inline macro page (`macro.offset[5]`). "宏" (hóng) = macro. |
| `MAX_HONG_SIZE` | **532** | `0x214` | size of the inline macro data area (`macro.buf[532]`) |
| `MAX_SINGLE_HONG_STEP` | **64** | `0x40` | max steps in one macro (`m_fdg_macro_unit_struct_t.step[64]`) |
| `LED_CFG_IOS_ID` | **8** | `0x08` | the `cfgId` used for the iOS-mode LED config slot |
| `TURBO_TOUCH_TIME` | **25** | `0x19` | turbo touch/debounce time (ms) |
| `JOY_LINER_SENST_DEFAULT` | **17** | `0x11` | default joystick linear-sensitivity value |

Related limits from `MappingConfigParser` (not in `MappingConfigConst`):

| Function | `ProtoVersion < 770` | `ProtoVersion >= 770` |
|---|---:|---:|
| `GetMaxMacroCount` | 5 | **10** |
| `GetMaxMacroActionCount` | 128 | **256** |
| `GetMinMacroInterval` (ms per timestamp tick) | 10 | **1** |

## 2.9 Command `0xA1` — read all profile versions, active profile, and NS binding

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA1
[4]  0x02              len = 2 + 0 (no payload)
[5]  checksum = sum(a[3], a[4]) & 0xFF   = (0xA1 + 0x02) & 0xFF = 0xA3
rest 0x00

IsAlwaysCanRead = true  (this command keeps receiving ACKs even after it completes)

ACK:
[2]  0xA1
[3]  total packet count (1)
[4]  packet index (0)
[5]  raw current config id, 0..7    ->  CurrentConfigId = (v<=7) ? ((v>3) ? v-4 : v) : 0
                                        (v in 4..7 means the controller is in Nintendo Switch mode)
[6],[7]   dataVersion of profile 0, u16 LE
[8],[9]   dataVersion of profile 1
[10],[11] dataVersion of profile 2
[12],[13] dataVersion of profile 3
[14]      CurrentConfigIdForNs   <-- the profile bound to Nintendo Switch mode
```

`0xFFFF` in any `dataVersion` slot means that profile has never been written (factory default).

## 2.10 Command `0xA2` — activate a profile

`ApplyMappingConfigByCfgIdCommandFactory.ApplyMappingConfigByCfgIdCommandNewXInput`

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA2
[4]  0x03              len = 2 + 1
[5]  cfgId
[6]  checksum = sum(a[3], a[4], a[5]) & 0xFF
rest 0x00

ACK: data[2] == 0xA2
ctor: default 500 ms timeout, 3 retries
```

Host-side range guard is `0 <= cfgId <= 8` (`ControllerRepository.ApplyOnboardConfig`), and that method short-circuits when `controller.CurrentConfigId == cfgId`. The host's own profile array only has 4 entries, so anything above 3 will index out of range in the reference app — in practice **use `0x00`–`0x03`**. `0x08` is the iOS LED slot. The XInput/DInput equivalents are `0x50` sub-command `0x05` and return a success boolean at `data[17]`/`data[4]`; the NewXInput variant only echoes.

## 2.11 Command `0xA6` — persist the working config to flash

`SaveCurrentMappingConfigCommandFactory.SaveCurrentMappingConfigCommandNewXInput`

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA6
[4]  0x04              len = 2 + 2
[5]  dataVersion & 0xFF
[6]  (dataVersion >> 8) & 0xFF
[7]  checksum = sum(a[3]..a[6]) & 0xFF
rest 0x00

ACK: data[2] == 0xA6
ctor: maxRetry 3, timeout 10000 ms      <-- flash write, allow 10 s
```

Note there is **no `cfgId`** — this commits whatever was most recently uploaded to whatever slot it was uploaded to. The NS variant `0xAB` (§0.2) is the same command plus a `cfgId` byte.

`dataVersion` is a fresh `Random().Next(65535)` that must differ from the currently-cached value (`ControllerRepository.SaveConfig`), so the range is `0..65534`.

## 2.12 Command `0xAF` — factory-reset mapping config

`ResetMappingConfigByCfgIdCommandFactory` has **exactly one class** — the factory returns it unconditionally, for every `ControllerType`:

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xAF
[4]  0x03              len = 2 + 1
[5]  cfgId
[6]  checksum = sum(a[3], a[4], a[5]) & 0xFF
rest 0x00

ACK: data[2] == 0xAF
ctor: maxRetry 3, timeout 10000 ms
```

How the reference app uses it (`ControllerRepository.ResetMappingConfig(uid, cfgId)`, 60 s outer timeout):
* **`cfgId == 0`** → send `0xAF` with `cfgId = 0`, i.e. a **device-side reset of everything**; the host then drops its cached profiles. (For device code `f4` it inserts a 5 s settling delay.)
* **`cfgId > 0`** → does **not** touch the device's reset opcode at all. It computes `realCfgId = cfgId - 1`, loads that profile from the bundled *default* profile file, and re-uploads it with `0xA4`/`0xA5` (+ `0xA8`/`0xA9` for LED, + `0xAD`/`0xAE` for macros when `ProtoVersion >= 770`), then `SaveConfig`. So at the IPC layer `cfgId` is **1-based with 0 meaning "all"**.

`IpcCommandEnum.ResetMappingConfig = 4127`, `IpcCommandEnum.ResetDefaultConfig = 28694`.

## 2.13 Command `0x13` — feature enable (replaces `SetMappingEnable` on NewXInput)

`SetMappingEnableCommandFactory` has no NewXInput class. The NewXInput equivalent is the generic feature-enable opcode:

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0x13
[4]  0x04              len = 2 + 2
[5]  sub-command
[6]  enable            0x00 / 0x01
[7]  checksum = sum(a[3]..a[6]) & 0xFF
rest 0x00

ACK: data[2] == 0x13 && data[5] == sub-command
```

| sub | Feature | Source |
|---|---|---|
| `0x01` | Quick-profile-switch button chord | `EnableQuickSwitchConfigCommandFactory` |
| `0x04` | **Master mapping enable** ("MappingSwitch") | `EnableMappingSwitchCommandFactory` |

Polarity is straight (`1` = enabled) on NewXInput. Beware: the legacy variants invert it — XInput `SetMappingEnable` is `0x18` with `a[2] = enable ? 2 : 1`, DInput `0xEE` with `a[2] = enable ? 1 : 0`, and `EnableMappingSwitch` XInput is `0x50` sub `0x06` with `a[3] = enable ? 0 : 1`.

Other feature-enable factories in `data.command.setting` follow the same `0x13`-plus-sub pattern (audio, joystick debounce/rebound/auto-calibration, motion debounce, Xbox home button, dock smart-stop, DS5 data, screen options); read each factory for its sub-code if needed.

## 2.14 Command `0x03` — read feature availability & current settings

```
request: [3]=0x03, [4]=0x02, [5]=checksum   (no payload)
IsNeedCallback = true, IsAlwaysCanRead = true
ACK: data[2] == 0x03

data[5]  bitfield: which features are SUPPORTED
   bit0 QuickSwitchConfigUsable      bit1 XboxHomeButtonUsable
   bit2 MotionDebounceUsable         bit3 MappingSwitchUsable
   bit4 JoystickDebounceUsable       bit5 JoystickAutoCalibrationUsable
   bit6 JoystickReboundUsable        bit7 ScreenConfig.StatusBarAlwaysOnUsable
data[6]  bitfield: current ENABLED state, same bit order
   bit0 QuickSwitchConfigEnabled     bit1 XboxHomeButtonEnabled
   bit2 MotionDebounceEnabled        bit3 MappingSwitchEnabled
   bit4 JoystickDebounceEnabled      bit5 JoystickAutoCalibrationEnabled
   bit6 JoystickReboundEnabled       bit7 ScreenConfig.StatusBarAlwaysOn
data[7]  bit0 = ScreenConfig.OffScreenUsable, bit1 = AudioUsable
data[8]  bit0 = ScreenConfig.OffScreen,       bit1 = AudioEnabled
data[9]  SleepTime
data[10] ReportRate
data[11] JoystickPrecision
data[12] JoystickSensitivity
```

---

# PART 3 — Macros

Two entirely different formats exist. Which one applies is decided by `ProtoVersion`:

* **`(ProtoVersion & 0xF) < 2`** (i.e. proto 3.0 / 3.1): macros live **inside** the mapping blob at offset `0x0E6` (§3.1), max 5 macros, 1 ms tick = 10 ms.
* **`ProtoVersion >= 770`** (proto 3.2): macros live in a **separate blob** with its own commands `0xAC` / `0xAD` / `0xAE` (§3.2), max 10 macros, 1 ms tick, and each macro carries a 20-byte UTF-8 name.

## 3.1 Inline macro page — `m_fdg_macro_page_struct_t` at mapping-blob offset 0x0E6 (538 bytes)

| Offset in page | Size | Field | Meaning |
|---:|---:|---|---|
| +0 | 1 | `nums` | number of macros stored, valid `1..MAX_HONG_NUMS(5)`; anything else means "no macros" |
| +1 | 5 | `offset[5]` | macro *i*'s start offset **in 4-byte units** from the start of `buf` |
| +6 | 532 | `buf[532]` | packed macro records (`MAX_HONG_SIZE`) |

Data area addressing (`ParseToMacroConfig`): `base = MAX_HONG_NUMS + 1 = 6`, macro *i* starts at `base + offset[i] * 4` and ends at `base + offset[i+1] * 4` (or at the end of the page for the last one).

Each macro record (this is `m_fdg_macro_unit_struct_t`, 4-byte header + `step[]`):

| Offset in record | Size | Field | Meaning |
|---:|---:|---|---|
| +0 | 1 | `btn` | `ControllerKey` this macro is bound to |
| +1 | 1 | `count_l` | step count, low byte |
| +2 | 1 | `count_h` | step count, high byte (u16 LE) |
| +3 | 1 | `type` | `MacroEnableType { None=0, Once=1, Press=2, Click=3 }` |
| +4 + 4k | 4 | `step[k]` | `m_fdg_macro_step_struct_t` |

`m_fdg_macro_step_struct_t` (4 bytes):

| Offset | Field | Meaning |
|---:|---|---|
| +0 | `time_l` | timestamp low byte |
| +1 | `time_h` | timestamp high byte — u16 LE, **cumulative** timestamp in units of `GetMinMacroInterval()` (10 ms for proto < 3.2) |
| +2 | `btn` | `ControllerKey` to act on |
| +3 | `event` | `MacroActionEvent { Release=0, Press=1, LeftJoystick=2, RightJoystick=3, Hold=5 }` |

The host model stores per-action **durations**; the wire stores **cumulative** timestamps:
* serialise: `cum += action.Duration / minInterval; write u16 LE cum`
* deserialise: `action.Duration = (cum * minInterval) - previousCumMs`

Offset bookkeeping on serialise (`ParseMacroConfigToArray`): `offset[0]` is implicitly 0; for *j* < count−1, `running += Macros[j].Actions.Count + 1; offset[j+1] = running`. The `+1` accounts for the 4-byte record header (one 4-byte unit). The whole 538-byte page is pre-filled with `0xFF`.

> **Reader quirk:** `ParseToMacroConfig` computes the next macro's start as `base + data[i + 2] * 4`. Since `offset[i]` is at page index `i + 1`, `data[i + 2]` is `offset[i+1]` — correct. But when `i == nums - 1` it overrides the end with `data.Length`, so the last macro absorbs all remaining `0xFF` filler; the parser then clamps `stepCount` to `(len - 4) / 4`. Also `if (val2.Count < num5 * 4) num5 = (val2.Count - 4) / 4;` — note the comparison uses `stepCount * 4` while the record is `4 + stepCount * 4` bytes, so a record that exactly fits is accepted.

## 3.2 Standalone macro blob (proto ≥ 3.2)

`MacroConfigParser.MacroConfigParserV10`. Blob length = `GetPackageCount() × perPkg` = **`81 × 20 = 1620` bytes for NewXInput** (`81 × 10 = 810` on legacy transports); pre-filled with `0xFF`. `GetPackageCount()` returns a constant `81` regardless of version. (See the blob-size anomaly note in §2.3 — the natural size is 810 and the `× 20` doubles it.)

| Offset (dec) | Offset (hex) | Size | Meaning |
|---:|---|---:|---|
| 0 | `0x000` | 2 | `Version`, u16 LE (`MacroConfigBean.Version`) |
| 2 | `0x002` | 2 | macro count, u16 LE, clamped to `GetMaxMacroCount() = 10` |
| 4 | `0x004` | 20 | `offset[10]`, ten u16 LE values, **in 4-byte units** from `0x018`; unused entries are `0xFFFF` |
| 24 | `0x018` | 1596 | macro records |

Each macro record starts at `0x018 + offset[i] * 4`:

| Offset in record | Size | Meaning |
|---:|---:|---|
| +0 | 1 | `ControllerKey` the macro is bound to |
| +1 | 2 | action count, u16 LE |
| +3 | 1 | `MacroEnableType` |
| +4 | 2 | `Interval`, u16 LE — repeat interval in **ms** (read as-is; §3.1's `/10` scaling does *not* apply here) |
| +6 | 6 | `0xFF` filler |
| +12 | 20 | `CfgName`, **UTF-8**, `0xFF`/`NUL`-padded (trailing `0xFF` and `NUL` trimmed on read) |
| +32 + 4k | 4 | step *k*: `{ time_l, time_h, btn, event }` — same as §3.1 but with a **1 ms** tick |

Offset bookkeeping on serialise: `running += actionCount + 8` per macro (the 32-byte header is 8 four-byte units). A record is rejected on read if it is shorter than 32 bytes.

## 3.3 Command `0xAC` — read the macro blob

`ReadMacroConfigCommand` exists **only** as a NewXInput class (the factory returns it unconditionally).

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xAC
[4]  0x04              len = 2 + 2
[5]  cfgId
[6]  pkgSize           0x14 (20)
[7]  checksum = sum(a[3]..a[6]) & 0xFF
rest 0x00

HasMultiAck = true, IsNeedCallback = true
ACK layout, reassembly and IsAckFinished are identical to 0xA3 (§2.5),
except the buffer is NOT pre-filled with 0xFF and data[5] is not consumed.
```

## 3.4 Commands `0xAD` / `0xAE` — upload the macro blob

Identical in shape to `0xA4` / `0xA5`:

```
0xAD Start:  [3]=0xAD [4]=0x06 [5]=cfgId [6]=startIndex [7]=packetNum [8]=packetSize(0x14)
             [9]=checksum = sum(a[3]..a[8]) & 0xFF
0xAE Pack:   [3]=0xAE [4]=pack.Length+3 [5]=packNum [6..]=chunk
             [6+pack.Length]=checksum = sum(a[3] .. a[3+len-1]) & 0xFF
Both ACK on data[2] == CommandId.
0xAD's ctor passes commandIndex 0, commandCount = packetNum + 1.
```

---

# PART 4 — RGB / LED

## 4.1 Command `0xA7` — read the LED blob

`ReadLedConfigCommand.ReadRgbConfigCommandNewXInput`

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xA7
[4]  0x04              len = 2 + 2
[5]  cfgId             0x00..0x03 (profile), 0x08 = iOS LED slot
[6]  pkgSize           0x14 (20)
[7]  checksum = sum(a[3]..a[6]) & 0xFF
rest 0x00

HasMultiAck = true, IsNeedCallback = true

ACK frames:
[2]  0xA7
[3]  total packet count      ->  blob size = data[3] * 20
[4]  packet index (0-based)
[5]  (unused by the parser)
[6 .. 25] 20 payload bytes  ->  blob[20 * data[4] ..]
finished when data[3] == data[4] + 1
buffer is NOT pre-filled
```

## 4.2 Commands `0xA8` / `0xA9` — upload the LED blob

`WriteRgbConfigCommand` and `WriteAllRgbConfigCommand` emit **identical** NewXInput frames (they differ only in their XInput start opcode, `0x28` vs `0x2A`).

```
0xA8 Start:
[3]=0xA8  [4]=0x06  [5]=cfgId  [6]=startIndex  [7]=packetNum  [8]=packetSize(0x14)
[9]=checksum = sum(a[3]..a[8]) & 0xFF

0xA9 Pack:
[3]=0xA9  [4]=pack.Length + 3  [5]=packNum  [6 .. 6+len-1]=chunk
[3 + a[4]]=checksum = sum(a[3] .. a[3+a[4]-1]) & 0xFF     (== array[6 + pack.Length])

Both ACK on data[2] == CommandId.
```

Unlike the mapping/macro blobs, the LED blob is **not** padded to a `PackageCount × perPkg` size — `LedConfigParser.ParseConfigToArray` splits the naturally-sized blob, so the **final chunk may be shorter than 20 bytes** (`SplitBytes` produces a short tail). `0xA9`'s `len` and checksum position adapt automatically.

Delta upload works the same way as §1.4 (`ParseConfigBeanToArray(config, oldConfig, 20)` returns runs of changed chunks keyed by absolute start index). `ControllerSdk.WriteRgbConfigById` / `WriteAllRgbConfigById` pass `oldConfig = null` (full blob); `WriteLedConfigPartial` passes a real `oldConfigBean` and, if nothing changed, invokes the completion callback without sending anything.

Both write paths are gated on `controller.IsSupportLed` (true for the Vader 5 Pro).

## 4.3 LED blob versions

`LedConfigBean.Version` is a 16-bit value at blob offset 0: `Version = (data[1] << 8) | data[0]`, `data[1]` = major, `data[0]` = minor.

| Version | hex | Parser | Header | Geometry |
|---|---|---|---|---|
| 512 | `0x0200` | `RgbConfigParserV20` | 9 fields + 11 × `0xFF` = 20 B | fixed **16 groups × 10 units** × 3 B = 480 B → **total 500 B** |
| 768 | `0x0300` | `RgbConfigParserV30` | 10 fields + 10 × `0xFF` = 20 B | **dynamic**: N groups × `rgb_num` units × 3 B → total `20 + N*rgb_num*3` |

Dispatch on read is `switch (data[1]) { case 2: if (data[0]==0) V20; case 3: if (data[0]==0) V30; }` — so **only exact `2.0` and `3.0` are recognised**; any other version yields an empty bean. On write, `ParseConfigToArray` selects V3.0 iff `Version >> 8 == 3`, else V2.0.

The Vader 5 Pro (`f5`) is a v3.0 device: `ControllerDataMapper.ConvertLedConfigToBean` sends everything except `f3`/`f3p`/`f4` down the `Version >> 8 >= 3` branch, which uses `LedConstants.FlowK5Std` (a `byte[10, 12, 3]` table = **10 colour frames × 12 LED units**). **Confirm `rgb_num` from the device** — see §4.7.

Legacy total-length helper `GetPackageCountByDataVersion(versionLow, versionHigh)` returns `49` for both `2.0` and `3.0` and `0` otherwise; the old transports then allocate `10 * (49 + 1) = 500` bytes. It is marked `[Obsolete("旧协议使用，复合设备协议已不适用")]` — "legacy protocol only, not applicable to the composite-device protocol" — and NewXInput does not use it (the device announces the packet count in `data[3]` instead).

## 4.4 LED blob header (both versions), offsets 0x00–0x13

Struct reference: `m_fdg_mapping_rgb_sturct_t` (`Sequential`, `Pack = 1`, **500 bytes** — exactly the v2.0 blob).

| Offset | Size | Struct field | `LedConfigBean` field | v2.0 | v3.0 | Meaning |
|---:|---:|---|---|:-:|:-:|---|
| `0x00` | 1 | `version[0]` | `Version & 0x0F` | ✔ | ✔ | minor version. **Note the mask is `0x0F`, not `0xFF`** — see anomaly below |
| `0x01` | 1 | `version[1]` | `Version >> 8` | ✔ | ✔ | major version (2 or 3) |
| `0x02` | 1 | `type` | `ClickFeedback` | ✔ | ✔ | `1` = click-feedback effect active, else `0`. Derived on write as `Mode == Feedback(4)` |
| `0x03` | 1 | `loop_start` | `LoopStart` | ✔ | ✔ | first animation frame index. Only ever written non-zero by the `Default(7)` mode path (copied from the stored default profile) |
| `0x04` | 1 | `loop_end` | `LoopEnd` | ✔ | ✔ | last animation frame index — **derived from the mode, see §4.6** |
| `0x05` | 1 | `loop_time` | `LoopTime` | ✔ | ✔ | animation period / speed (`LedConfig.Period`). Default 15; for `f5` with mode `Flow(1)` or `Default(7)` the app default is **4** |
| `0x06` | 1 | `light_scale` | `Brightness` | ✔ | ✔ | brightness, **0–100 linear** (`LedConfig.Brightness`). Defaults: 50 generally; for `f5`: 20 for `Flow`/`Default`, 30 for `On`, 50 otherwise |
| `0x07` | 1 | `rgb_num` | `RgbNum` | ✔ | ✔ | **number of physical LED units per frame**. Device-reported; the host never sets it |
| `0x08` | 1 | `rgb_type` | `LedMode` | ✔ | ✔ | **lighting effect / mode** = `LedType` enum value (§4.5) |
| `0x09` | 1 | (`reserve[0]` in v2.0) | `GripSync` | — | ✔ | v3.0 only: `1` = sync grip lighting, `0` = not. In v2.0 this byte is part of the `0xFF` filler |
| `0x09`–`0x13` | 11 | `reserve[11]` | — | ✔ | — | v2.0 filler, all `0xFF` |
| `0x0A`–`0x13` | 10 | | — | — | ✔ | v3.0 filler, all `0xFF` |
| `0x14`… | | `id[16]` | `LedGroup` | | | colour data, §4.7 |

> **ANOMALY:** `span[0] = (byte)(configBean.Version & 0xF)` in **both** `RgbConfigParserV20.ParseConfigBeanToArray` and `RgbConfigParserV30.ParseConfigBeanToArray`. The minor version is masked to 4 bits on write while the reader does a full-byte compare against `0`. Harmless for `x.0` versions (the only ones the reader accepts) but it means a hypothetical `3.16` would serialise as `3.0`.

`ScreenConfig`, `ThirdPartyAppControlConfig` and `ControllerConfig` in the same namespace are host-side settings objects, not part of any blob.

## 4.5 Lighting-effect / mode enum (`rgb_type` at offset 0x08)

`ControllerDataMapper` maps this byte straight onto `LedType`: `configBean.LedMode = (int)config.Mode` on write, `Mode = Enum.Parse<LedType>(configBean.LedMode.ToString())` on read.

**`Flydigi.SharedResources.Data.Protobuf.LedType` — the authoritative on-wire effect enum:**

| Value | hex | Enum | Proto name | Human name / behaviour |
|---:|---|---|---|---|
| 0 | `0x00` | `Unknown` | `LED_TYPE_Unknown` | invalid / not reported |
| 1 | `0x01` | `Flow` | `LED_TYPE_FLOW` | flowing rainbow / marquee. Colours come from a **firmware-preset table**, not from the user (`LedConstants.FlowK5Std` for the k5/f5 generation); `loop_end = rgb_num` |
| 2 | `0x02` | `Breath` | `LED_TYPE_BREATH` | breathing. Colour frames are stored at **even** group indices only (odd frames are zeroed); `loop_end = colourCount*2 − 1`; `UseColorCount = (loop_end+1)/2` |
| 3 | `0x03` | `Gradient` | `LED_TYPE_GRADIENT` | gradient / colour cycle across N colours; `loop_end = colourCount − 1`; `UseColorCount = loop_end + 1` |
| 4 | `0x04` | `Feedback` | `LED_TYPE_FEEDBACK` | button-press feedback flash; single colour; also sets `ClickFeedback = 1`; `loop_end = colourCount`; `UseColorCount = 1` |
| 5 | `0x05` | `On` | `LED_TYPE_ON` | steady single colour; `loop_end = 0`; `UseColorCount = 1` |
| 6 | `0x06` | `Close` | `LED_TYPE_Close` | LEDs off (all colour bytes zeroed); `loop_end = 0`; `UseColorCount = 0` |
| 7 | `0x07` | `Default` | `LED_TYPE_Default` | restore the device's stored default animation (the host copies `LoopStart`/`LoopEnd`/colours from the bundled default profile and only overrides `LoopTime`); `UseColorCount = 0` |

**Speed / direction.** There is no separate speed or direction field. Animation rate is `loop_time` (offset `0x05`) and the frame window is `loop_start`..`loop_end`. "Direction" is expressed implicitly by the ordering of the colour frames in `LedGroup` (and, for `Flow`, entirely by the firmware preset table).

**Other RGB enums in the protobuf tree that are *not* used by the controller LED blob** (they belong to the cooler / charger / newer generic RGB IPC surface — do not send these values as `rgb_type`):

* `LedModeProto { LED_MODE_OFF=0, LED_MODE_ON=1, LED_MODE_SMART=2 }` — cooler LED mode.
* `RgbEffectTypeProto { OFF=0, STREAMING=1, ROTATION=2, BREATHING=3, STATIC_SINGLE=4, STATIC_MULTI=5, RAINBOW=6, WAVE=7, FLASH=8, SMART=9, CUSTOM=99 }` — generic RGB effect IPC enum.
* `RgbSpeedProto { VERY_SLOW=0, SLOW=1, MEDIUM=2, FAST=3, VERY_FAST=4 }`.
* `RgbBrightnessProto { OFF=0, LOW=1, MEDIUM=2, HIGH=3, MAXIMUM=4 }`.
* `ChargerLedDirection { NONE=0, ToRight=1, ToLeft=2, ToDown=3, ToUp=4, ToRightDown=5, ToRightUp=6, ToLeftDown=7, ToLeftUp=8 }`.

## 4.6 `loop_end` / `UseColorCount` derivation

Write direction (`ControllerDataMapper.ConvertLedConfigToBean`):

| `LedType` | `loop_end` |
|---|---|
| `Flow`(1) | `rgb_num` |
| `Breath`(2) | `Color.Count * 2 − 1` |
| `Gradient`(3) | `Color.Count − 1` |
| `Feedback`(4) | `Color.Count` |
| `On`(5), `Close`(6), `Default`(7) | `0` |

Read direction (`ControllerDataMapper.ConvertLedConfig`) recovers the user-visible colour count:

| `LedType` | `UseColorCount` |
|---|---|
| `Flow`(1), `Close`(6), `Default`(7) | `0` |
| `Feedback`(4), `On`(5) | `1` |
| `Gradient`(3) | `loop_end + 1` |
| `Breath`(2) | `(loop_end + 1) / 2` |

## 4.7 Colour data, LED zones and geometry

Colour encoding is **plain 8-bit R, G, B in that order**, one byte each, no gamma, no packing:

`m_fdg_macro_rgb_unit_sturct_t` (`Sequential`, `Pack = 1`, **3 bytes**): `r` at +0, `g` at +1, `b` at +2.
`m_fdg_macro_rgb_group_sturct_t` (**30 bytes**): `unit[10]`.
`m_fdg_mapping_rgb_sturct_t.id[16]` (**480 bytes**): 16 groups.

The serialiser is order-only — it walks `LedGroup` then `LedUnit` and appends `Red, Green, Blue` — so the byte order on the wire is **RGB**, and no brightness scaling is applied here (brightness is the separate `light_scale` byte).

### v2.0 geometry — group = LED zone, unit = colour slot

`RgbConfigParserV20.ParseDataToConfigBean` reads a fixed grid at `20 + i*10*3 + j*3` for `i in 0..15`, `j in 0..9`: **16 groups of 10 units**, group-major. `ControllerDataMapper`'s v2.0 paths treat the **group index as the physical LED zone** (bounded by `rgb_num`) and the **unit index as the colour slot** (colours are read out of `LedGroup[0].LedUnit[0..4]`). Zones after the first are dimmed by `0.8^1` (`Math.Pow(0.8, k > 0 ? 1 : 0)`) for `Breath`/`Feedback` on non-`k2` devices. For `Breath` the unit index is doubled (`LedUnit[num*2]`) since odd slots are zeroed.
Vader 4 Pro (`f4`) preset table `LedConstants.FlowF4Std` is `byte[5,10,3]` (5 zones × 10 slots); the accessory variant `FlowF4Acc` is `byte[9,10,3]`.

### v3.0 geometry — group = colour frame, unit = LED zone  ← **this is the Vader 5 Pro**

`RgbConfigParserV30.ParseDataToConfigBean`:

```csharp
for (int i = 20; i < data.Count; i += RgbNum * 3) {
    var group = new LedGroup();
    for (int j = i; j < i + RgbNum * 3; j += 3)
        group.LedUnit.Add(new Color { Red = data[j], Green = data[j+1], Blue = data[j+2] });
    LedGroup.Add(group);
}
```

So each group holds exactly `rgb_num` colours, one per **physical LED unit**, and successive groups are successive **animation frames / colour slots**. `ControllerDataMapper` confirms the reading in both directions:
* read: `Color.Add(LedGroup[i].LedUnit[0])` for each group `i` (stepping by 2 for `Breath`) — the colour list is indexed by *group*.
* write: `for (num3 = 0; num3 < LedGroup.Count; num3++) { colourIdx = (Mode==Breath) ? num3/2 : num3; foreach unit in LedGroup[num3].LedUnit → unit = Color[colourIdx]; }` — every unit in a frame gets that frame's colour; odd frames are zeroed for `Breath`; everything is zeroed for `Close(6)`; for `Feedback(4)`/`On(5)` only frame 0 is coloured.
* `Flow(1)` overrides everything with `LedConstants.FlowK5Std`, a `byte[10, 12, 3]` table indexed `[frame, unit, channel]` → **10 frames × 12 units** for the k5/f5 generation.

**Number of LED zones and their indices.** `rgb_num` (offset `0x07`) is **read from the device and never written by the host** — the only assignments in the whole tree are `configBean.RgbNum = data[num++]` in the two parsers. Zone indices are therefore `0 .. rgb_num-1`, positionally in the order the firmware lays them out, and the frame count is `(blobLen − 20) / (rgb_num × 3)`.
The `FlowK5Std` table implies **12 units × 10 frames** for this device generation (blob = `20 + 10*12*3 = 380` bytes → 19 packets of 20). **This must be confirmed against hardware:** read `0xA7`, take `data[3] × 20` as the blob length and byte `0x07` as `rgb_num`, and derive the frame count. Note that the write path `for (i = 0; i < LedGroup.Count && i < RgbNum; i++)` in the `Flow` branch bounds the *frame* index by `rgb_num`, which is only coherent when `rgb_num >= frameCount`; with `rgb_num = 12` and 10 frames it happens to work, but a device reporting `rgb_num < frameCount` would silently leave later frames unwritten.

## 4.8 Command `0xF5` — `TestLed` (live colour override, not persisted)

`TestLedCommandFactory.TestLedControllerCommandNewXInput`. Gated on `controller.IsSupportLed`.

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xF5
[4]  0x05              len = 2 + 3
[5]  R = (color >> 16) & 0xFF
[6]  G = (color >>  8) & 0xFF
[7]  B =  color        & 0xFF
[8]  checksum = sum(a[3]..a[7]) & 0xFF
rest 0x00
```

The `color` argument is a packed 24-bit `0xRRGGBB` int. No ACK handling is overridden, so `IsAck` inherits the base `return false` — the command is fire-and-forget and will time out/retry 3× at 500 ms. Frame layout here is fully self-consistent (unlike `0xF1`).

## 4.9 Command `0xF1` — `TestIndicator` (status-LED test)

`TestIndicatorCommandFactory.TestIndicatorControllerCommandNewXInput`, driven by
`record TestIndicatorConfig(int LedId, LedFrequency LedFreq, int LedTimes, int color)`.

```
[0]  <report id>
[1]  0x5A
[2]  0xA5
[3]  0xF1
[4]  0x07              <-- length byte. ANOMALY: should be 0x08 for 6 payload bytes
[5]  LedId             indicator LED index (opaque; passed straight through from IPC)
[6]  LedFreq           LedFrequency enum, §4.10
[7]  LedTimes          repeat count
[8]  R = (color >> 16) & 0xFF
[9]  G = (color >>  8) & 0xFF
[10] B =  color        & 0xFF
[11] checksum = sum(a[3] .. a[9]) & 0xFF     <-- ANOMALY: excludes a[10] (blue)
rest 0x00
```

**ANOMALY (same shape as `0xAB`, §0.2).** Six payload bytes are written, so `a[4]` should be `0x08`; the SDK writes `0x07`. The checksum *byte position* `a[11]` is the one that `len = 0x08` implies (it is hard-coded as `array[11]`), but the checksum *range* is `Crc(array, 3, 3 + 0x07)` = `sum(a[3]..a[9])`, which **omits the blue byte**. Replicate the SDK's bytes exactly.

The XInput (`0x15`) and DInput (`0xE3`) variants only send `LedId`/`LedFreq`/`LedTimes` and no colour at all.

## 4.10 `LedFrequency` enum

`Flydigi.SharedResources.Data.Protobuf.LedFrequency` — the value at frame offset `[6]` of `0xF1`:

| Value | hex | Enum | Proto name |
|---:|---|---|---|
| 0 | `0x00` | `AlwaysOn` | `LedFrequency_AlwaysOn` |
| 1 | `0x01` | `Half1Hz` | `LedFrequency_Half_1Hz` (0.5 Hz) |
| 2 | `0x02` | `_1Hz` | `LedFrequency_1Hz` |
| 3 | `0x03` | `_2Hz` | `LedFrequency_2Hz` |
| 4 | `0x04` | `_4Hz` | `LedFrequency_4Hz` |
| 5 | `0x05` | `Half1HzOn` | `LedFrequency_Half_1Hz_On` |
| 6 | `0x06` | `Off` | `LedFrequency_Off` |

## 4.11 `LedConfigBean` protobuf field numbers (host-side persistence only)

For reading/writing the app's own cached profile files (`ControllerMappingConfigBeans` protobuf), not the wire:

| Field # | Name | Type |
|---:|---|---|
| 1 | `Version` | int32 |
| 2 | `ClickFeedback` | bool |
| 3 | `LoopStart` | int32 |
| 4 | `LoopEnd` | int32 |
| 5 | `LoopTime` | int32 |
| 6 | `Brightness` | int32 |
| 7 | `RgbNum` | int32 |
| 8 | `LedMode` | int32 |
| 9 | `LedGroup` | repeated `LedGroup` |
| 10 | `GripSync` | bool |

`LedGroup { 1: repeated Color LedUnit }`, `Color { 1: int32 Red, 2: int32 Green, 3: int32 Blue }`.
`LedConfig` (the UI-facing shape) `{ 1: LedType Mode, 2: int32 Period, 3: int32 Brightness, 4: repeated Color Color, 5: int32 UseColorCount, 6: int32 Proto, 7: bool SyncWithGripEnable }`.

---

# PART 5 — Worked sequences

## 5.1 Read everything for one profile

The reference app's order (`ControllerRepository.ReadNextConfig`), repeated for `cfgId = 0,1,2,3`:

```
0xA1                          -> current cfg id (0..7), 4× dataVersion, NS cfg id
for cfgId in 0..3:
    0xA3 cfgId, pkgSize=20    -> mapping blob      (multi-ACK until data[3]==data[4]+1)
    if IsSupportLed:
        0xA7 cfgId, pkgSize=20 -> LED blob
        if ProtoVersion >= 770:
            0xAC cfgId, pkgSize=20 -> macro blob
after the last profile:
    if lastReadCfgId != CurrentConfigId:
        0xA2 CurrentConfigId  -> re-activate whatever was active before
```

## 5.2 Write + persist one profile (normal mode)

```
0xA4 cfgId, startIndex=0, packetNum=84, packetSize=20
0xA5 packNum=0..83  (20-byte chunks of the 1680-byte padded mapping blob)
0xA8 cfgId, startIndex=0, packetNum=ceil(ledLen/20), packetSize=20
0xA9 packNum=0..N-1
[if ProtoVersion >= 770]
0xAD cfgId, startIndex=0, packetNum=81, packetSize=20
0xAE packNum=0..80
0xA6 dataVersion=<fresh random 0..65534>            (10 s timeout — flash write)
```

## 5.3 Write + bind a profile to Nintendo Switch mode

```
# 1. sanitise: no keyboard/mouse key mappings, both sticks MapType=Joystick
# 2. same uploads as 5.2, addressed to the profile's NORMAL slot (0..3)
0xA4 / 0xA5 ...        cfgId = profileIndex
0xA8 / 0xA9 ...        cfgId = profileIndex
[0xAD / 0xAE ...]      cfgId = profileIndex
# 3. commit as the NS profile
0xAB dataVersion=<fresh random>, cfgId = 4 + profileIndex      (10 s timeout)
# 4. verify
0xA1                   -> data[14] should now read 4 + profileIndex
```

## 5.4 Minimal "swap A/B and X/Y" mapping blob

Starting from an 840-byte buffer filled with `0xFF` (then padded to 1680 for the upload):

```
offset 0x000 = 0x02, 0x03            # ProtoVersion 0x0302 (verify against the device's own reply)
offset 0x002 = 0x54                  # pkg_len = 84
offset 0x00D + 3*4  = 05 00 00       # slot 4  (A)  -> emits B(5)
offset 0x00D + 3*5  = 04 00 00       # slot 5  (B)  -> emits A(4)
offset 0x00D + 3*7  = 08 00 00       # slot 7  (X)  -> emits Y(8)
offset 0x00D + 3*8  = 07 00 00       # slot 8  (Y)  -> emits X(7)
   i.e. 0x019 = 05 00 00
        0x01C = 04 00 00
        0x022 = 08 00 00
        0x025 = 07 00 00
offset 0x0E1 = <dataVersion lo>, <dataVersion hi>
offset 0x302..0x315 = UTF-16LE profile title, 20 bytes, 0xFF-padded
everything else left at 0xFF (identity / defaults)
```

Safest practice: **read the profile with `0xA3` first, patch only the four `key_table` slots and `random_data`, and write the whole thing back.** That preserves every stick curve, trigger curve, haptics and macro setting you did not intend to change.

---

# PART 6 — Things that need live-device verification

1. **`ProtoVersion` of the Vader 5 Pro.** Assumed `0x0302` (770). Read bytes `[0]`,`[1]` of the `0xA3` reply. This decides: macro storage location (inline vs `0xAC`), macro limits (5/128/10 ms vs 10/256/1 ms), blob length (79 vs 84 packages), and whether V3.1 extras (`joy_extra`, `macro_cycle`, `motion_curve`) are present.
2. **Blob length actually accepted for uploads.** The SDK sends **84 packets × 20 B = 1680 B** for the mapping blob and **81 × 20 = 1620 B** for the macro blob, even though only 840 / 810 bytes carry data (§2.3). Confirm the firmware tolerates (or requires) the padding. Also confirm what the device reports in `data[3]` of the `0xA3` reply — 42 or 84.
3. **`rgb_num` and the LED frame count** (§4.7). `FlowK5Std` implies 12 units × 10 frames (380-byte blob) but nothing in the SDK hard-codes it for `f5`.
4. **The `motion[2]` vs `motion[1]` struct discrepancy** (§2.6). The parser offsets (840-byte total) are almost certainly right — they line up with `84 × 10` and with all three V3.1 offsets — but a hardware read of `0xA3` will settle it: check that byte `0x091` behaves as the grip-rumble master switch (`0x00`/`0xFF`).
5. **The two length/checksum anomalies** — `0xAB` (§0.2) and `0xF1` (§4.9). Both declare `len` one byte short and checksum one payload byte short of what they transmit. Reproduce the SDK's bytes; if a command is rejected, try the corrected `len` (`0x05` / `0x08`) with the correspondingly wider checksum.
6. **Whether the pack destination is `(startIndex + packNum) * packetSize`** (§1.4). Verify by doing a delta write with two non-adjacent runs, or simply always write the whole blob from `startIndex = 0`.
7. **A/B and X/Y polarity in NS mode.** Flydigi's key IDs are Xbox-labelled (A=4, B=5, X=7, Y=8). Whether the firmware applies the remap before or after the Switch-layout transposition is not determinable from the SDK.
8. **Direct addressing of NS slots (`cfgId` 4..7) on the new protocol.** `SaveSwitchConfigOldProtol` writes mapping blobs *directly* to `cfgId` 4..7 on old-protocol devices. Whether `0xA3`/`0xA4`/`0xA7`/`0xA8` also accept 4..7 on a Vader 5 Pro is untested — the new-protocol path deliberately writes to 0..3 and then uses `0xAB`. Trying `0xA3` with `cfgId = 4..7` is a cheap read-only probe.
9. **`cfgId = 8` (iOS LED slot).** `ApplyOnboardConfig` permits it and `LED_CFG_IOS_ID = 8`, but nothing in the decompiled tree actually sends it.
10. **HID output/input report IDs.** `0x06` is only a placeholder; the real IDs come from the report descriptor (first `0x85` item = input, last `0x85` item = output). Dump the descriptor before hard-coding anything.
11. **`0xF5`/`0xF1` have no ACK handler**, so they will always retry 3× at 500 ms in the SDK. Check whether the device actually replies (which would let you drop the retries).
12. **`0xAA`** is unused by NewXInput in this SDK — if a fuller command map is wanted, it is the obvious gap between `0xA9` and `0xAB`.

---

# Appendix — Enum quick reference

```
ControllerType        : DInput=?, XInput=?, NewXInput=?   (NewXInput selected when VID==0x37D7 && mfr!="Microsoft")
                        report-ID staging byte a[0]: XInput=0xA5, NewXInput=0x06, DInput=0x05

ControllerKey         : see §2.1
KeyMapType            : Key=0, Continuous=1, Macro=2, MultiFunction=3, Keyboard=4
KeyMapTypeContinuousEnableType : Close=0, Press=1, Click=2
MacroEnableType       : None=0, Once=1, Press=2, Click=3
MacroActionEvent      : Release=0, Press=1, LeftJoystick=2, RightJoystick=3, Hold=5
MotionMapType         : Off=0, LeftJoystick=1, RightJoystick=2, Mouse=3
MotionEnableType      : Click=0, Press=1
MotionUseMode         : MotionModeFps=0, MotionModeRacer=1
JoystickMapType       : Joystick=0, Keyboard=1, Mouse=2, Dpad=3
JoystickSensitivityType : Default=0, Quick=1, Slow=2, Custom=3
JoystickCircularityType : Rectangle=0, Circular=1
Direction             : Center=0, Top=1, TopLeft=2, Left=3, LeftBottom=4,
                        Bottom=5, BottomRight=6, Right=7, RightTop=8
LedType (rgb_type)    : Unknown=0, Flow=1, Breath=2, Gradient=3, Feedback=4, On=5, Close=6, Default=7
LedFrequency          : AlwaysOn=0, Half1Hz=1, _1Hz=2, _2Hz=3, _4Hz=4, Half1HzOn=5, Off=6
DeviceType (relevant) : K1=?, K2=?, F3=?, F3P=?, F4=?, F5=130, F5HK3=145, K5=?, K6=?
DeviceCode            : "f1"=Vader 2, "f3"=Vader 3, "f3p"=Vader 3 Pro, "f4"=Vader 4 Pro,
                        "f5"=Vader 5 Pro, "k1"=Apex 3, "k2"=Apex 4, "k5"=Apex 5, "k6"=Apex 6,
                        "fp1".."fp4"=DireWolf 1..4
```

## Key source files

| Concern | Path (relative to `decompiled/`) |
|---|---|
| Frame construction, checksum, ACK dispatch | `Flydigi.Common.data/Flydigi.Common.data.command/AbstractCommand.cs` |
| | `ControllerSdk/Flydigi.ControllerSDK.data.command/AbstractControllerCommand.cs` |
| | `Flydigi.Common.data/Flydigi.Common.util/ByteExtension.cs` |
| | `Flydigi.Common.data.full/Flydigi.Common.data/CommunicationProtocol.cs` |
| | `Flydigi.Hid.data/Flydigi.Hid.data/HidCommunicationProtocol.cs` |
| | `ControllerSdk/Flydigi.ControllerSDK.data.protocol.dinput/NewXInputProtocol.cs` |
| Mapping blob layout | `ControllerSdk/Flydigi.ControllerSDK.data.parser/MappingConfigParser.cs` |
| Macro blob layout | `ControllerSdk/Flydigi.ControllerSDK.data.parser/MacroConfigParser.cs` |
| LED blob layout | `ControllerSdk/Flydigi.ControllerSDK.data.parser/LedConfigParser.cs` |
| Structs | `ControllerSdk/Flydigi.ControllerSDK.data.model.config/m_fdg_*.cs` |
| Constants | `ControllerSdk/Flydigi.ControllerSDK.data.model.config/MappingConfigConst.cs` |
| Config commands | `ControllerSdk/Flydigi.ControllerSDK.data.command.config/*.cs` |
| Test/LED commands | `ControllerSdk/Flydigi.ControllerSDK.data.command.test/TestLedCommandFactory.cs`, `TestIndicatorCommandFactory.cs` |
| Feature toggles | `ControllerSdk/Flydigi.ControllerSDK.data.command.setting/*.cs` |
| Public SDK surface | `ControllerSdk/Flydigi.ControllerSDK/ControllerSdk.cs` |
| Device identification | `ControllerSdk/Flydigi.ControllerSDK.hardware/ControllerHidManager.cs`, `Flydigi.ControllerSDK.factory/FlydigiControllerUtil.cs`, `FlydigiControllerFactory.cs` |
| Button/stick state decode | `ControllerSdk/Flydigi.ControllerSDK.data.model/Button.cs`, `Joystick.cs` |
| Enums | `Flydigi.SharedResources/Flydigi.SharedResources.Data.Protobuf/*.cs` |
| NS-mode orchestration | `SpaceStationService/Flydigi.ControllerService.data/ControllerRepository.cs` (`SaveSwitchConfig` ~L6928, `SaveSwitchConfigOldProtol` ~L7099, `ApplyOnboardConfig` ~L6290, `ResetMappingConfig` ~L5551, `PrepareMappingConfigs` ~L5744) |
| | `SpaceStationService/Flydigi.ControllerService.service.controller/ControllerBusinessService.cs` (`ApplySwitchConfigAsync` ~L8750) |
| LED semantics / defaults | `SpaceStationService/Flydigi.ControllerService.data.mapper/ControllerDataMapper.cs` |
| Flow presets | `SpaceStationService/Flydigi.ControllerService.data/LedConstants.cs` |
| Host-side keyboard injection | `SpaceStationService/Flydigi.ControllerService.service.controller/KeyboardMouseInjectRunner.cs` |
