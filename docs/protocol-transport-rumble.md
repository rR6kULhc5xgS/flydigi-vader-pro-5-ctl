# Flydigi `NewXInput` protocol — transport, rumble, adaptive triggers

Reverse-engineered from the decompiled C# SDK under `decompiled/`.
Target device: **Flydigi Vader 5 Pro, VID `0x37D7` / PID `0x2401`** → `ControllerType.NewXInput`,
`DeviceCode = "f5"`, `DeviceType = 130 (0x82)`, `Serial = 2 (Vader)`.

All numbers are hex unless suffixed with `d`. `a[N]` = byte N of the outbound buffer,
`data[N]` = byte N of the inbound buffer **after** the report-ID byte has been stripped
(the decompiler artifact `*(byte*)data[N]` means simply `data[N]`).

Primary sources (absolute paths):

| Concern | File |
|---|---|
| Command base class, frame builder, checksum policy | `decompiled/Flydigi.Common.data/Flydigi.Common.data.command/AbstractCommand.cs` |
| Queue thread | `decompiled/Flydigi.Common.data/Flydigi.Common.data/CommunicationThread.cs` |
| Queue + retry/timeout state machine | `decompiled/Flydigi.Common.data/Flydigi.Common.data/CommunicationProtocol.cs` |
| **The only class that touches hidapi** | `decompiled/Flydigi.Hid.data/Flydigi.Hid.data/HidCommunicationProtocol.cs` |
| NewXInput frame demux | `decompiled/ControllerSdk/Flydigi.ControllerSDK.data.protocol.dinput/NewXInputProtocol.cs` |
| Thread factory per controller type | `decompiled/ControllerSdk/Flydigi.ControllerSDK.data/ControllerCommunicationManager.cs` |
| Auto-init command chain | `decompiled/ControllerSdk/Flydigi.ControllerSDK.data/ControllerCommunicationThread.cs` |
| Public API | `decompiled/ControllerSdk/Flydigi.ControllerSDK/ControllerSdk.cs`, `.../Config.cs` |
| Device enumeration filter | `decompiled/Flydigi.Hid.data/Flydigi.Hid/HidManager.cs`, `decompiled/ControllerSdk/Flydigi.ControllerSDK.hardware/ControllerHidManager.cs` |
| Capability flags per model | `decompiled/ControllerSdk/Flydigi.ControllerSDK.factory/FlydigiControllerFactory.cs` (`GenerateControllerVader5`, line ~787) |
| Additive checksum | `decompiled/Flydigi.Common.data/Flydigi.Common.util/ByteExtension.cs` (`Crc`) |

---

# PART 1 — Transport and state machine

## 1.1 Device selection (which hidraw node)

`HidManager.CheckHidDevice()` enumerates all HID devices and keeps those where
`ProductString.Contains("Flydigi")` **or** `VendorId == 14295d (0x37D7)`.
`ControllerHidManager.FindSpecialHidDevice()` then requires, for VID `0x37D7`:

```
hid.UsagePage == 0xFFA0          (65440d)   ← the vendor/command collection
&& (hid.ProductId >> 12) == 2               ← PID 0x2xxx
&& (hid.ProductId >> 8)  != 8
```

PID `0x2401`: `0x2401 >> 12 == 2` ✓, `>> 8 == 0x24 != 8` ✓.

So: **use the interface whose report descriptor declares usage page `0xFFA0`**, not the
gamepad interface. Only `BusType == USB(1)` or `BusType == Bluetooth(2)` devices are accepted
(`CheckConnectedDevice`).

Connection type is inferred from *other* interfaces of the same VID/PID
(`ControllerHidManager.TryCheckConnectedType`):

| Usage page present | `ConnectType` |
|---|---|
| `0xFFEF` (65519d) | `Wired` |
| `0xFFEE` (65518d) | `Dongle` |
| neither | `Unknown` |

Up to 4 controllers are tracked (`CommunicationManager.CommunicationThread = new T1[4]`), keyed
by `Uid`.

## 1.2 The HID layer — exact calls, sizes, report IDs

`HidCommunicationProtocol<T>` (HidApi.Net binding over libhidapi) is the **only** place in the
whole tree that reads/writes HID. `NewXInputProtocol` derives from it.

### Open + report-ID discovery (`Init()`)

```csharp
_hidDevice = new Device(device.HidPath);                       // hid_open_path
ReadOnlySpan<byte> rd = _hidDevice.GetReportDescriptor(4096);  // hid_get_report_descriptor, 4096-byte buffer

if (inReportId == 0) {                                          // NewXInputProtocol passes inReportId = 0
    int i = rd.IndexOf((byte)0x85) + 1;                         // 0x85 = HID "Report ID" item tag
    if (i != 0) inReportId = rd[i];                             // FIRST report ID in the descriptor
}
int j = rd.LastIndexOf((byte)0x85) + 1;
if (j != 0) _outReportId = rd[j];                               // LAST report ID in the descriptor

Task.Run(_readDataFromDevice);                                  // one dedicated read thread per device
```

* `inReportId` = **first** `Report ID` value in the descriptor.
* `_outReportId` = **last** `Report ID` value in the descriptor.
* `NewXInputProtocol` ctor passes `inReportId = 0`, i.e. always auto-detect.

### Write (`WriteDataImpl`)

```csharp
data[0] = _outReportId;                 // OVERWRITES whatever CreateCommand put there
_hidDevice.Write(data);                 // hid_write, full buffer, 32 bytes for NewXInput
return true;                            // false only on exception
```

* Buffer length = **32 bytes** for every NewXInput command (`maxPacketCount = 32`, see 1.5).
  Legacy (XInput/DInput) commands are 15 bytes.
* No timeout — `hid_write` is synchronous.
* `AbstractControllerCommand.TakeEndpointByDevice()` pre-fills `a[0]` with `0x06` for
  `NewXInput` (`0xA5` for XInput, `0x05` otherwise), but **`WriteDataImpl` always replaces it**
  with the descriptor-derived `_outReportId`. The `0x06` only survives on the non-HID
  (XInput filter-driver) transport.
* **Live-verified on Linux hidraw**: byte 0 must be `0x00`, followed by `5A A5 …`. That is
  consistent with the vendor output report having no numbered report ID on this interface;
  either way *do not* hardcode `0x06` — write `0x00` on Linux.

### Read (`_readDataFromDevice`, one thread, loops while `device.IsConnected`)

```csharp
ReadOnlySpan<byte> raw = _hidDevice.Read(64);        // hid_read, blocking, up to 64 bytes requested
ReadOnlySpan<byte> d = raw;
if (raw[0] == inReportId) d = raw.Slice(1, raw.Length - 1);   // strip report ID if present
if (!ParseData(d)) continue;                          // protocol-level demux, see 1.4
... ACK matching, see 1.8
```

* Requested length **64**; the device delivers 32-byte reports in practice.
* **Blocking read, no timeout.** A `HidException` sets `device.IsConnected = false`, which ends
  the thread. `ObjectDisposedException` is swallowed.
* Report-ID stripping is conditional on `raw[0] == inReportId`, so a payload that happens to
  start with `5A` is never mangled.

## 1.3 Outbound frame format (host → device), NewXInput

Built by `AbstractCommand.CreateSimpleCommand(isNewProtocol: true, packetSize: null)`:

```
byte  value                    notes
0x00  <report id>              zero-filled buffer; CreateSimpleCommand writes 0x06, WriteDataImpl overwrites with _outReportId (use 0x00 on Linux hidraw)
0x01  0x5A                     magic
0x02  0xA5                     magic
0x03  cmdId                    = CommandId()
0x04  len                      = 2 + payloadLen   (i.e. counts cmdId + len + payload)
0x05  payload[0]
...
0x03+len   checksum            = sum(a[3] .. a[3+len-1]) & 0xFF   ==  (cmdId + len + Σpayload) & 0xFF
...        0x00 padding        buffer is `new byte[32]`, so everything else is zero
```

Checksum helper (`ByteExtension.Crc(value, start, end)`, `end` exclusive):

```csharp
byte b = 0; for (int i = start; i < end; i++) b += value[i]; return b;
```

Every well-formed command calls `Crc(a, 3, 3 + a[4])` and stores it at `a[3 + a[4]]`.

> **Commands that omit the checksum.** `0x12` (vibration), `0x51`/`0x52` (force trigger) never
> call `Crc` — the byte stays `0x00`. The shipping app works this way, so the firmware ignores
> the checksum for these IDs. **Replicate the SDK byte-for-byte** (leave it `0x00`); do not
> "fix" it unless you have verified the checksummed variant live.
>
> **`0x57` (K6 realtime) uses a different rule**: `a[30] = Crc(a, 1, 30)` — sum of bytes
> `0x01..0x1D` stored at `0x1E`, while `a[4] = 0x1C` would imply the checksum at `0x1F` over
> `0x03..0x1E`. Both the range and the position differ from every other command. Treat as
> firmware-specific (or an SDK bug) and verify live.

## 1.4 Inbound frame format (device → host) and demux

After report-ID stripping, `NewXInputProtocol.ParseData` sees:

```
byte  value
0x00  0x5A
0x01  0xA5
0x02  cmdId (echo) — or a stream tag, see below
0x03  totalPackets     (packet count of this reply, 1 for single-packet replies)
0x04  packetIndex      (0-based; last packet has index totalPackets-1)
0x05  payload[0] ...
0x1F  checksum         = sum(data[2..30]) & 0xFF   (live-verified; the SDK never checks it)
```

`NewXInputProtocol.ParseData` verbatim:

```csharp
if (data.IsEmpty) return false;
if (data[0] != 0x5A && (data[1] & 0xFF) != 0xA5) return false;   // NOTE: && not || — a frame passes if EITHER byte matches
if ((data[2] & 0xFF) == 0xF7) operatorChangedListener?.OnOriginDataChanged(device, data);
if ((data[2] & 0xFF) != 0xEF) return true;                        // → continue to ACK matching
OperatorStatus rec = OperatorDataParser.Parse(device, data);      // 0xEF = input-state report
operatorChangedListener?.OnOperatorChanged(device, rec);
operatorChangedListener?.OnOriginDataChanged(device, data);
return false;                                                     // → NOT an ACK, do not touch the queue
```

Consequences:

* `data[2] == 0xEF` → **input/raw state report** (sticks, triggers, gyro/accel; enabled with
  command `0x11`). Returns `false`: it never satisfies an ACK and never drains the queue.
* `data[2] == 0xF7` → **origin/force-trigger stream** (started by command `0xF7`). Notifies the
  listener *and* falls through to ACK matching.
* Anything else → candidate ACK.
* The magic check is `&&`, so a corrupt frame with only one correct magic byte is still
  processed. An implementer should use `&&`-of-equality (i.e. require both) instead.
* The SDK never validates the inbound checksum. Do validate it.

`0xEF` payload layout (for reference; `OperatorDataParser.Parse`, NewXInput branch):
`data[3..4]`=LX LE i16, `data[5..6]`=LY (negated), `data[7..8]`=RX, `data[9..10]`=RY (negated),
`data[15]`=linear LT, `data[16]`=linear RT, `data[17..28]`=gyro X/Y/Z then acc X/Y/Z, LE i16 each.
Stick centre is 0 for NewXInput (not 128).

## 1.5 The command object (`AbstractCommand<TD>`) — defaults that matter

```csharp
protected AbstractCommand(TD device, Action? action = null, Action? timeoutAction = null,
                          int maxRetryCount = 3, int timeoutMs = 500,
                          int commandIndex = 1, int commandCount = 1, int maxPacketCount = 32)
private int _retryCount = 1;                  // starts at 1, i.e. counts attempts
```

`AbstractControllerCommand` (all controller commands) forwards with `maxPacketCount = 32` and
`commandIndex = 0` by default:

```csharp
protected AbstractControllerCommand(Controller c, Action? action = null, Action? timeoutAction = null,
                                    int maxRetryCount = 3, int timeoutMs = 500,
                                    int commandIndex = 0, int commandCount = 1) : base(..., 32)
```

Virtual hooks:

| member | default | meaning |
|---|---|---|
| `CommandId()` | abstract | goes into `a[3]` |
| `GetCommandIdInAck()` | `= CommandId()` | id expected in the reply (only used by `MultiAckCmdId`, see 1.9) |
| `IsNeedCallback()` | `action != null` | **if false, no ACK is awaited at all** |
| `IsAck(data)` | `false` | does this frame belong to me? |
| `IsAckFinished(data)` | `true` | is this the last frame of my reply? |
| `HasMultiAck()` | `false` | reply arrives as several frames |
| `IsAlwaysCanRead()` | `false` | keep receiving frames for this command forever |
| `IsSerialCommand()` | `commandCount > 1` | this command is one packet of a multi-packet *write* |
| `GetTimeoutMs()` | `500` | per-attempt ACK timeout |
| `GetMaxRetryCount()` | `3` | total write attempts (see 1.7) |
| `GetSerialCommandProgress` | `commandIndex / commandCount` | reported to `IOnRawDataReceivedListener.OnSerialCommandSend` |
| `TimeoutAction()` | `null` | invoked once when the command is finally abandoned |

Non-default retry/timeout values in the tree (all NewXInput unless noted):

| command | maxRetryCount | timeoutMs |
|---|---|---|
| `0x01` HeartBeat (NewXInput) | **200** | 500 |
| `0x10` HeartBeat (XInput) | 10 | 500 |
| `0x04` ReadUid | 5 | 500 |
| `0xA6`/`0xAB`/`0xAF` save/reset mapping | 3 | **10000** |
| screen `0xD1` UploadPicData | 3 | 1000 |
| screen `0xD2` UploadPicEnd | 3 | **30000** |
| everything else, incl. all K6 `0x53`–`0x57` | 3 | 500 |

## 1.6 Queue model

Two layers:

**(a) `CommunicationThread<T>` — the outer queue.**
`BlockingCollection<List<AbstractCommand<T>>>` (unbounded). One `Task.Run` worker per device:

```csharp
while (!cancelled) {
    List<AbstractCommand<T>> batch = _commandQueue.Take(token);   // blocks
    CommunicationProtocol?.WriteData(batch);
}
```

`AddCommand(cmd)` wraps a single command in a 1-element list; `AddCommands(list)` enqueues a
batch. `ControllerSdk.SendCommand / SetVibration / …` → `CommunicationManager.AddCommand` →
slot lookup by `Uid` → `CommunicationThread.AddCommand`.

**(b) `CommunicationProtocol<T>` — the in-flight window.**

```csharp
protected readonly List<AbstractCommand<T>> CommandsToSend;   // pending, incl. the in-flight one
protected AbstractCommand<T>? CommandToSend;                  // in flight (at most one)
protected readonly HashSet<AbstractCommand<T>> CommandAlwaysCanRead;
protected readonly List<byte> MultiAckCmdId;
protected CancellationTokenSource? CancellationTokenSource;   // cancels the retry timer
```

`WriteData(List)`:

```csharp
if (data.Count == 0) return;
if (CommandsToSend.Count > 0) { CommandsToSend.AddRange(data); return; }   // busy → just append
WriteData(data.First());                                                    // write head now
CommandsToSend.AddRange(data.TakeLast(data.Count - 1));                     // queue the tail
```

`WriteData(AbstractCommand)`:

```csharp
CommandToSend = data;
if (data.IsAlwaysCanRead())  CommandAlwaysCanRead.Add(data);
if (data.HasMultiAck())      MultiAckCmdId.Add(data.GetCommandIdInAck());
CancellationTokenSource = new CancellationTokenSource();
RetryCommandAfterTimeout(data, CancellationTokenSource.Token);   // arms the timer BEFORE the write
if (data.IsSerialCommand()) OnRawDataReceivedListener?.OnSerialCommandSend(Device, data.GetSerialCommandProgress);
byte[] frame = data.CreateCommand();
if (!WriteDataImpl(frame)) ForceReset("Write data failed");
```

Only **one command is in flight at a time**; the next one is written when the current one's
ACK arrives (read thread) or when it is abandoned (timeout task).

`Device` setter (used when an XInput device is re-bound) clears `MultiAckCmdId`,
`CommandAlwaysCanRead` and `CommandsToSend`.

## 1.7 Retry / timeout policy

`RetryCommandAfterTimeout(data, token)`:

* **If `data.IsNeedCallback() == false`** — no ACK is expected:
  ```csharp
  CommandsToSend.Remove(CommandToSend);
  CommandToSend = null;
  if (CommandsToSend.Count > 0) WriteData(CommandsToSend.First());   // synchronous recursion
  ```
  i.e. fire-and-forget; the queue advances immediately.
* **If `IsNeedCallback() == true`** — `await Task.Delay(GetTimeoutMs(), token).ContinueWith(…)`.
  When the timer fires (i.e. no ACK within `timeoutMs`) and the token is not cancelled and
  `CommandToSend` is still this same object:
  ```csharp
  if (retryCount >= maxRetryCount) {          // give up
      TimeoutAction()?.Invoke();
      CommandsToSend.Remove(CommandToSend);
      if (CommandToSend.IsSerialCommand()) CommandsToSend.Clear();   // abort the whole serial burst
      CommandToSend = null;
      if (CommandsToSend.Count > 0) WriteData(CommandsToSend.First());
  } else {
      CommandToSend.AddRetryCount();          // 1 → 2 → 3 …
  }
  if (CommandToSend != null) WriteData(CommandToSend);   // re-send (re-arms a fresh timer)
  ```

Attempt arithmetic with the defaults (`maxRetryCount = 3`, `timeoutMs = 500`):
attempt 1 (`_retryCount = 1`) → 500 ms → `_retryCount = 2`, attempt 2 → 500 ms →
`_retryCount = 3`, attempt 3 → 500 ms → `3 >= 3` → abandon. **3 total writes, ~1.5 s worst
case.** `TimeoutAction` fires exactly once, on abandonment.

> **SDK bug worth not copying:** on the give-up path, `WriteData(CommandsToSend.First())`
> sets `CommandToSend` to the *next* command, and the trailing
> `if (CommandToSend != null) WriteData(CommandToSend)` then writes that next command a
> **second** time and arms a second timer for it. Implement the two paths as mutually
> exclusive.

`ForceReset(reason)` (called on write failure and on `Dispose`): cancels the timer, invokes
`TimeoutAction` for the in-flight command **and every queued command**, clears
`CommandsToSend` / `CommandAlwaysCanRead` / `MultiAckCmdId`, and installs a fresh
`CancellationTokenSource`.

## 1.8 Read loop / ACK matching (exact order)

`HidCommunicationProtocol._readDataFromDevice`, per received frame:

1. `hid_read(64)`; strip report ID if `raw[0] == inReportId`.
2. `ParseData(d)`; `false` → `continue` (frame consumed as input data, queue untouched).
3. `if (CommandToSend == null) goto DRAIN;`
4. `if (!CommandToSend.IsAck(d)) continue;` — **a non-matching frame does not drain the queue.**
5. `CancellationTokenSource?.Cancel();` — kills the retry timer. Note: this happens on the
   **first** matching frame, so subsequent packets of a multi-packet reply are *unguarded* by
   any timeout.
6. `CommandsToSend.Remove(CommandToSend);` — removed before parsing.
7. `CommandToSend.ParseAck(device, d);` → `ParseAckData(device, d)` then `action?.Invoke()`.
   For multi-packet replies this runs **once per packet**.
8. `if (!CommandToSend.IsAckFinished(d)) continue;` — more packets expected; stay in flight
   (but already removed from `CommandsToSend`).
9. `if (CommandToSend.IsAck(d)) CommandToSend = null;` — command complete.
10. `DRAIN:` for each `c` in `CommandAlwaysCanRead`: `if (c.IsAck(d)) c.ParseAck(device, d);`
11. `if (CommandsToSend.Count > 0) WriteData(CommandsToSend.First());` — advance the pipeline.

Typical `IsAck` for NewXInput is just `data[2] == CommandId()`; the `0x13` family adds a
sub-command check (`data[2] == 0x13 && data[5] == <sub id>`), `K6TriggerStatusCommand` adds a
length check (`data.Length > 5 && data[2] == CommandId()`).

## 1.9 `HasMultiAck`, `IsAckFinished`, `IsAlwaysCanRead`, serial commands

* **`HasMultiAck()`** — pushes `GetCommandIdInAck()` into `MultiAckCmdId`. `MultiAckCmdId` is
  **only read by `DInputProtocol` and `XInputProtocol`** (to stop mistaking a reply for an
  input report). `NewXInputProtocol` ignores it entirely, so on the Vader 5 Pro `HasMultiAck`
  has **no effect on the wire or on the read loop**. Multi-packet replies are driven purely by
  `IsAckFinished`.
* **`IsAckFinished(data)`** — decides whether the in-flight command stays in flight.
  Two idioms are used, both over the `totalPackets`/`packetIndex` header:
  * `0x01` HeartBeat: `data[4] > data[3] || data[4] == data[3] - 1`
    (last packet index = total-1; the `>` arm also accepts legacy single-frame replies where
    `data[3]`/`data[4]` are not a count/index pair).
  * `0xA7` ReadLedConfig / `0xA3` ReadMappingConfig / `0xAC` ReadMacroConfig:
    `data[3] == data[4] + 1`.
  Reassembly pattern for those reads: on `data[4] == 0` allocate `data[3] * pkgSize` bytes, then
  `Array.Copy(frame, 6, buffer, pkgSize * data[4], pkgSize)`. **Note the payload of a bulk-read
  reply starts at `data[6]`, not `data[5]`** (`pkgSize` = 20 for NewXInput).
  HeartBeat's `ParseAckData` only parses the packet with `data[4] == 0` and reads its payload
  from offset 5 when `data[4] < data[3]`, else from offset 4.
* **`IsAlwaysCanRead()`** — the command is *additionally* kept in a `HashSet` and every future
  frame is offered to it (step 10) for the lifetime of the protocol object, even after it has
  completed and even while another command is in flight. Used by `0xA1`
  `ReadMappingConfigVersionAll`, `0x03` `ReadHardwareFunctionStatus` and (XInput)
  `ReadCurrentMappingConfigId` so that unsolicited config-change notifications keep being
  parsed. Entries are only removed by `ForceReset` / `Device` reassignment.
* **Serial (multi-packet *write*) commands** — `commandCount > 1` makes
  `IsSerialCommand()` true. The factory builds N command objects with
  `commandIndex = i (+1)` / `commandCount = N` and enqueues them as **one batch**
  (`AddCommands`). Sequencing is implicit: only the head is written, each ACK triggers the
  next, and progress is reported as `commandIndex / commandCount` via
  `IOnRawDataReceivedListener.OnSerialCommandSend`. If a serial command exhausts its retries,
  `CommandsToSend.Clear()` aborts the **entire** remaining burst.
  Users: `0xA4`+`0xA5` (mapping config), `0xA8`+`0xA9` (RGB), `0xAD`+`0xAE` (macro),
  `0x55` (K6 waveform: `commandIndex = i+1`, `commandCount = ceil(len/21)`),
  `0xD0`…`0xD3` (screen upload).

## 1.10 Ordering rules, delays, and pitfalls in the SDK pipeline

* **At most one command in flight**; nothing is pipelined.
* **Only `SetForceTriggerConfig` has an explicit inter-command delay**: when both a left and a
  right config are supplied, the right one is enqueued after `Task.Delay(100)`
  (`ControllerSdk.cs:181`). Everything else relies on the ACK handshake. There are no
  `Thread.Sleep`s anywhere in the NewXInput path.
* **Batch-of-fire-and-forget stall (real SDK bug).** `WriteData(List)` writes the head and
  *then* appends the tail to `CommandsToSend`. If the head does not need a callback, its
  synchronous "advance" step runs while `CommandsToSend` is still empty, so nothing pulls the
  tail afterwards; `CommandToSend` is `null` and the queue is stuck until some inbound non-`0xEF`
  frame reaches step 11 (`DRAIN`). Conversely, if a *nested* recursion does happen (queue
  non-empty and several no-callback commands), the frames go out in **reverse** order because
  each recursive `WriteData` completes before the outer `WriteDataImpl` runs.
  → **Do not model your implementation on this.** Use a plain sequential state machine:
  build frame → write → wait for ACK (or skip the wait for no-ACK commands) → next.
* `AddCommands` when `CommandsToSend.Count > 0` just appends and returns; nothing is written.

## 1.11 Required initialisation / handshake

### Automatic (what the SDK itself does on connect)

`ControllerCommunicationManager.GenerateCommunicationThread` creates a
`ControllerCommunicationThread` (which starts the queue worker and, via `NewXInputProtocol`
→ `HidCommunicationProtocol.Init`, opens the device and starts the read thread). The
constructor immediately enqueues `SendHeartCommand`:

1. **`0x01` HeartBeat** (`maxRetryCount = 200`, 500 ms). Its ACK carries `DeviceType`,
   `ConnectType`, MAC, battery, chip types and the firmware versions of main/dongle/switch/
   trigger/screen/adc/nearlink. `FlydigiControllerFactory.GenerateController(DeviceType, …)`
   runs from this ACK and is what populates `IsSupportVibration`,
   `IsSupportTriggerVibration`, `IsSupportForceTrigger`, key list, etc.
   **Everything capability-gated in the SDK is unavailable until this ACK is parsed.**
2. On success, one batch is enqueued (NewXInput path only):
   `0xA1 ReadMappingConfigVersionAll` → `0x02 ReadNickname` → `0x04 ReadUid`
   (`0x04` is skipped for device codes `f3`/`f3p`, which derive the Uid from the MAC).
   Commands specific to old protocols (`DongleInfo`, `ExtraInfo`, `ReadCurrentMappingConfigId`)
   are **not** sent for NewXInput.

The device's `Uid` (from `0x04`) is the key used by `CommunicationManager.AddCommand`, so in the
SDK no user command can be routed before `0x04` has answered.

### Practically required for a third-party client

Nothing else is strictly required to make **vibration (`0x12`) work** — it is a plain
fire-and-forget command. Concretely required only for the extra features:

| Goal | Command |
|---|---|
| know the model/capabilities/firmware | `0x01` HeartBeat |
| receive `0xEF` input/raw reports | `0x11` with `enableRawData = true` (the app pairs it with `enableControllerData = false`) |
| claim third-party control + advertise your app name | `0x1C` Acquire (`acquire = true`, 20-char ASCII tag) |
| read back who currently holds control / which streams are on | `0x10` ReadRawDataReportStatus |
| receive the force-trigger `0xF7` stream | `0xF7` with `a[5] = 1` (stop with `2`) |

`SpaceStationService` uses `0x1C` + `0x11(enableThirdPartyControl)` for its
"third-party app control" feature and polls `0x10` every 30 s
(`ControllerRepository.StartThirdPartyMonitor`). For `DeviceCode == "f5"` that feature is gated
on firmware **≥ 7.1.4.1** (`DeviceUtil.CompareVersion("7.1.4.1", fw)`), i.e. older Vader 5 Pro
firmware will not report `ControlBy`.

## 1.12 `0x1C` — AcquireController

`Flydigi.ControllerSDK.data.command/AcquireControllerCommandFactory.cs`. One class for **all**
controller types (always the `5A A5` frame).

```
a[0]  report id (0x00 on Linux)
a[1]  0x5A
a[2]  0xA5
a[3]  0x1C
a[4]  0x17            len = 23d  (2 + 21 payload bytes)
a[5]  acquire         0x01 = take control, 0x00 = release
a[6]..a[25]           tagName, ASCII, truncated to 20 bytes, NUL/zero padded
a[26] checksum        = Crc(a, 3, 3 + 0x17) = sum(a[3]..a[25]) & 0xFF
a[27]..a[31] 0x00
```

* ACK: `data[2] == 0x1C`; result = `data[5] == 1` (granted) — delivered through
  `Action<bool>`.
* `IsNeedCallback()` = `action != null`; `ControllerSdk.AcquireController` always passes one,
  so the SDK does wait for the ACK (3 × 500 ms).
* Release with the same frame and `a[5] = 0x00`.

## 1.13 `0x11` — EnableRawDataTransportIn

`Flydigi.ControllerSDK.data.command/EnableRawDataTransportInCommandFactory.cs`, NewXInput class.

```
a[3]  0x11
a[4]  0x07            len = 7d (2 + 5 payload bytes)
a[5]  enableControllerData    0x01 on, 0x00 off, 0xFF = "leave unchanged" (C# null)
a[6]  enableRawData           idem   ← this is what turns the 0xEF report stream on
a[7]  enableKeyboardData      idem
a[8]  enableMouseData         idem
a[9]  enableThirdPartyControl idem
a[10] checksum        = sum(a[3]..a[9]) & 0xFF
```

`null` → `0xFF` for each of the five flags, letting you change one flag without disturbing the
others. ACK: `data[2] == 0x11`. `IsNeedCallback()` is `action != null` (the SDK sometimes fires
and forgets this one).

Read-back is `0x10` (`ReadRawDataReportStatusCommandFactory`, `len = 2`, no payload):

```
ACK: data[2]=0x10
     data[5]  XInputEnabled        (== controller data)
     data[6]  PrivateDataEnabled   (== raw data / 0xEF stream)
     data[7]  KeyboardEnabled
     data[8]  MouseEnabled
     data[9]  ThirdPartyControl enabled
     data[10..29] ControlBy — 20 bytes ASCII, NUL-trimmed (the tag from 0x1C)
```

## 1.14 RECIPE — how to correctly send a command and read its reply

Assume `fd` is the opened hidraw node of the `0xFFA0` interface.

**Send**

1. `buf = bytes(32)` (all zero).
2. `buf[0] = 0x00` — report-ID slot (Linux hidraw; the Windows SDK puts the descriptor's last
   Report ID here).
3. `buf[1] = 0x5A; buf[2] = 0xA5; buf[3] = cmdId;`
4. `buf[4] = 2 + len(payload)`; copy the payload to `buf[5:]`.
5. `buf[3 + buf[4]] = sum(buf[3 : 3 + buf[4]]) & 0xFF`
   — **except** for `0x12`, `0x51`, `0x52` (leave `0x00`) and `0x57`
   (`buf[30] = sum(buf[1:30]) & 0xFF`).
6. `write(fd, buf)` — one 32-byte write, no report-ID prefix beyond `buf[0]`.

**Receive**

7. `raw = read(fd, 64)` (blocking; use `poll`/`O_NONBLOCK` with your own deadline —
   `hid_read` has no timeout).
8. If `raw[0] == firstReportIdInDescriptor` (or simply if `raw[0] != 0x5A` and
   `raw[1] == 0x5A`), drop `raw[0]`. Call the result `d`.
9. Require `d[0] == 0x5A && d[1] == 0xA5`, and validate
   `d[31] == sum(d[2:31]) & 0xFF` (the SDK skips this; you should not).
10. Dispatch on `d[2]`:
    * `0xEF` → input-state report. Not an ACK. Keep waiting.
    * `0xF7` → force-trigger/origin stream. Not an ACK unless you sent `0xF7`.
    * `== cmdId you sent` → your ACK. Otherwise an unrelated/late frame: ignore.
11. For a single-packet reply, `d[3] == 1`, `d[4] == 0`; payload is `d[5:]` (bulk reads use
    `d[6:]` with 20 bytes per packet — see 1.9).
12. For a multi-packet reply, loop while `d[4] != d[3] - 1`, reassembling by `d[4]`.
    Only cancel your timeout on the **first** packet if you want SDK-identical behaviour;
    better: re-arm the deadline per packet.

**Timeouts / retries (SDK-equivalent)**

13. Deadline `500 ms` per attempt; up to `3` attempts total (`200` for `0x01`, `5` for `0x04`,
    `10 s` for `0xA6/0xAB/0xAF`, `30 s` for `0xD2`). On the third timeout, declare failure.
14. Commands with **no** reply (`0x12` vibration, `0x57` K6 realtime, and any command the SDK
    constructs with `action == null`): write and move on immediately — do not wait, do not
    retry.
15. Serialise: never have two commands outstanding. Between two commands with ACKs, wait for
    the ACK; between two fire-and-forget frames, back-to-back writes are what the SDK does
    (except that its own queue bug can reorder them — see 1.10).
16. Special: send the right-side `SetForceTrigger` (`0x51`) **≥ 100 ms** after the left-side
    one.

## 1.15 NewXInput command-ID map (complete, from `CommandId()` overrides)

`0x13` is a multiplexed "hardware function toggle" (`a[5]` = sub-id, `a[6]` = value, `len = 4`).

| id | command | notes |
|---|---|---|
| `0x01` | HeartBeat | `len=2`; identity/versions/battery; retry 200 |
| `0x02` | ReadNickname | `len=2`; name = UTF-8 of `data[4 .. len-6]` |
| `0x03` | ReadHardwareFunctionStatus | `len=2`; `IsAlwaysCanRead`; bitfields at `data[5..8]`, sleep/report-rate/precision/sensitivity at `data[9..12]` |
| `0x04` | ReadUid | `len=2`; 13-byte uid at `data[5..17]`; retry 5 |
| `0x10` | ReadRawDataReportStatus | see 1.13 |
| `0x11` | EnableRawDataTransportIn | see 1.13 |
| `0x12` | **Vibration** | see Part 2 — no checksum |
| `0x13` | HW function toggle | sub-ids: `1` QuickSwitchConfig, `2` XboxHomeButton, `3` MotionDebounce, `4` MappingSwitch, `5` JoystickDebounce, `6` JoystickAutoCalibration, `7` JoystickRebound, `8` ScreenStatusBarAlwaysOn, `9` OffScreen, `10` Audio, `16` DockSmartStop |
| `0x14` | UpdateReportRate | |
| `0x15` | UpdateJoystickPrecision | |
| `0x16` | UpdateJoystickSensitivity | |
| `0x17` | UpdateSleepTime | |
| `0x18` | UpdateNickname | `len = 2 + utf8Len`, name at `a[5..]`; **the SDK stores the checksum at `a[6]` instead of `a[3+len]`** — only correct for a 1-byte name |
| `0x1C` | **AcquireController** | see 1.12 |
| `0x1D` | Restart | `len = 2`, no payload; reboots the controller |
| `0x1F` | **SwitchToFirmwareUpgradeMode** | ☠ see Part 3.7 |
| `0x51` | SetForceTrigger (normal modes) | no checksum |
| `0x52` | SetForceTrigger (sync-with-grip) | no checksum |
| `0x53` | K6 trigger mode | Part 3 |
| `0x54` | K6 trigger local mode | Part 3 |
| `0x55` | K6 trigger waveform (serial) | Part 3 |
| `0x56` | K6 trigger strength mapping | Part 3 |
| `0x57` | K6 trigger realtime frame | Part 3 — odd checksum |
| `0xA1` | ReadMappingConfigVersionAll | `IsAlwaysCanRead` |
| `0xA2` | ApplyMappingConfigByCfgId | |
| `0xA3` | ReadMappingConfig | multi-packet reply, 20 B/pkt |
| `0xA4` | WriteMappingConfig start | serial head |
| `0xA5` | WriteMappingConfig pack | serial body |
| `0xA6` | SaveCurrentMappingConfig | 10 s timeout |
| `0xA7` | ReadLedConfig | multi-packet reply, 20 B/pkt |
| `0xA8` | WriteRgbConfig start | serial head |
| `0xA9` | WriteRgbConfig pack | serial body |
| `0xAB` | SaveCurrentSwitchMappingConfig | 10 s timeout |
| `0xAC` | ReadMacroConfig | multi-packet reply |
| `0xAD` | WriteMacroConfig start | serial head |
| `0xAE` | WriteMacroConfig pack | serial body |
| `0xAF` | ResetMappingConfigByCfgId | 10 s timeout |
| `0xD0`–`0xD3` | Screen picture upload (start/data/end/finish) | screen models only |
| `0xF0` | CalibrationAdc | factory test |
| `0xF1` | TestIndicator | factory test |
| `0xF2` | TestScreen | factory test |
| `0xF4` | TestRf / TestLoss | factory test |
| `0xF5` | TestLed | factory test |
| `0xF6` | TestJoystick | factory test |
| `0xF7` | TestForceTrigger | `len=3`, `a[5]=1` start / `2` stop; starts the `0xF7` stream |
| `0xFD` | TestRecoverFactory | ☠ factory reset |
| `0xFE` | WriteDeviceType | ☠ rewrites the device identity |

Commands whose factory has **no NewXInput branch** silently fall through to the legacy
15-byte builder (`CreateSimpleCommand(false)` → `a[0]=0x06, a[1]=cmdId`, no `5A A5`) and will
not be understood by a Vader 5 Pro: `TestVibration`, `SyncTriggerWithGrip` (throws
`NotImplementedException` for non-XInput), `Sleep`, `DeviceMask`, `EnableDS5Data`,
`DisableMacroMapping`, `SetMappingEnable`, `ReadCurrentMappingConfigId`,
`ReadMappingConfigVersion`, `ReadModeUsageCount`, `ReadUsageCount`, `DongleInfo`, `ExtraInfo`,
`ReadAutoSleepPeriod`, `ReadScreenSetting`, `SetHardwareMacroEnable`, `EnableDockSmartStop`,
`SwitchToDInput`/`SwitchToXInput`.

---

# PART 2 — Rumble / vibration

## 2.1 Capabilities of the Vader 5 Pro

`FlydigiControllerFactory.GenerateControllerVader5` (line ~787):

```csharp
Serial = 2;                       // Vader
IsSupportLinearButton   = true;
IsSupportMotion         = true;
IsSupportLed            = true;
IsSupportTriggerVibration = true; //  ← the 4-motor variant of 0x12 applies
IsSupportVibration      = true;
IsSupportNs             = true;
IsSupportForceTrigger   = false;  //  ← SetForceTriggerConfig is a no-op in the SDK
IsSupportScreen         = false;
```

`ControllerSdk.SetVibration` gate:

```csharp
if (controller.IsSupportVibration &&
    (config.VibrationType != VibrationType.Trigger || controller.IsSupportTriggerVibration))
        → VibrationCommandFactory.CreateCommand(...)
```

## 2.2 `0x12` — Vibration (`VibrationCommandFactory`, NewXInput)

Command object: `base(controller, null, timeoutAction)` → **`action == null` ⇒
`IsNeedCallback() == false` ⇒ no ACK, no retry, fire-and-forget.**
No `IsAck` override either. The device sends nothing back.

Frame, `IsSupportTriggerVibration == true` (Vader 5 Pro):

```
a[0]  report id (0x00 on Linux)
a[1]  0x5A
a[2]  0xA5
a[3]  0x12
a[4]  0x06                      len = 6  (⇒ payload = a[5..8], checksum slot = a[9])
a[5]  grip  LEFT  level         written unless VibrationType == Trigger
a[6]  grip  RIGHT level         written unless VibrationType == Trigger
a[7]  trigger LEFT  level       written only if VibrationType == Both or Trigger
a[8]  trigger RIGHT level       written only if VibrationType == Both or Trigger
a[9]  0x00                      checksum slot — LEFT AT ZERO BY THE SDK
a[10]..a[31] 0x00
```

Frame, `IsSupportTriggerVibration == false` (other models): identical but only `a[5]`/`a[6]`
are ever written; `a[4]` is still `0x06`.

### `VibrationType` (protobuf enum, `Flydigi.SharedResources.Data.Protobuf/VibrationType.cs`)

| value | name | `a[5] a[6]` (grip L/R) | `a[7] a[8]` (trigger L/R) |
|---|---|---|---|
| `0` | `Both` | `LevelLeft`, `LevelRight` | `LevelLeft`, `LevelRight` |
| `1` | `Grip` | `LevelLeft`, `LevelRight` | `0x00`, `0x00` |
| `2` | `Trigger` | `0x00`, `0x00` | `LevelLeft`, `LevelRight` |
| `3` | `GripTrigger` | `LevelLeft`, `LevelRight` | `0x00`, `0x00` ← **same as `Grip`** |

`GripTrigger` (3) is not handled by the NewXInput builder — the `Both || Trigger` test fails,
so the trigger bytes stay zero. Use `Both` (0) if you want all four motors.

`VibrationConfig` is `record VibrationConfig(VibrationType, int LevelLeft, int LevelRight)` —
there is **one** left level and **one** right level shared by grip and trigger; a single `0x12`
frame cannot set grip and trigger to different intensities. Send two frames (`Grip`, then
`Trigger`) if you need that.

### Intensity range / scaling

* Levels are `int` cast straight to `byte` — **no clamping, no scaling anywhere in the SDK**.
  Effective range `0x00` (off) … `0xFF` (max); values > 255 wrap.
* The service layer's rumble entry point is
  `ControllerRepository.UpdateMotorConfig(byte leftValue, byte rightValue)` — i.e. the
  XInput/PS5 rumble bytes (`0x00`–`0xFF`) are forwarded verbatim.
* Per-motor min/max/scale remapping happens **inside the controller**, from the stored mapping
  config (see 2.4), not in this command.

### Motor identity (physical)

| where | index/byte | motor |
|---|---|---|
| `0x12` `a[5]` | — | grip (body) motor, **left** |
| `0x12` `a[6]` | — | grip (body) motor, **right** |
| `0x12` `a[7]` | — | trigger motor, **left** (LT) |
| `0x12` `a[8]` | — | trigger motor, **right** (RT) |
| `m_fdg_motor_grip_struct_t.unit[i]` | `0` / `1` | grip left / grip right |
| `m_fdg_motor_trig_struct_t.unit[i]` | `0` / `1` | trigger left / trigger right |
| `K6StrengthMappingTarget` | `0/1/2/3` | LeftTrigger / RightTrigger / LeftMotor / RightMotor |

The `unit` index ↔ side mapping is confirmed by
`MappingConfigParser.ParseTriggerConfigToArray`: `bean = (i == 1) ? Right : Left`.

## 2.3 `TestVibrationCommandFactory` — **not usable on NewXInput**

`Flydigi.ControllerSDK.data.command.test/TestVibrationCommandFactory.cs`:

```csharp
public static AbstractControllerCommand CreateCommand(...)
    => controller.ControllerType == ControllerType.XInput
        ? new TestVibrationCommandXInput(...)     // cmd 0x30, legacy 15-byte frame
        : new TestVibrationCommandDInput(...);    // cmd 0xF5, legacy 15-byte frame
```

There is **no NewXInput class**, so a Vader 5 Pro gets the DInput builder, which emits the
legacy 15-byte frame `a[0]=0x06 (endpoint), a[1]=0xF5, a[2]=0x02, a[3]=Type, a[4]=Side,
a[5]=Min, a[6]=Max` with no `5A A5` header and no checksum. Recorded here for completeness:

```
TestVibrationConfig(int Type, int Side, int Min, int Max)
  XInput : a[1]=0x30 a[2]=0x0E a[3]=Type a[4]=Side a[5]=Min a[6]=Max   (15-byte frame)
  DInput : a[1]=0xF5 a[2]=0x02 a[3]=Type a[4]=Side a[5]=Min a[6]=Max   (15-byte frame)
```

Do not use it for the Vader 5 Pro; use `0x12`.

## 2.4 Motor config structs (persisted mapping config, not a live command)

These structs live inside the per-profile mapping config blob that is read with `0xA3` and
written with `0xA4`/`0xA5`. `[StructLayout(Pack = 1)]` throughout — no padding, all fields
`byte`.

### `m_fdg_motor_grip_setting_struct_t` — 4 bytes

| off | field | notes |
|---|---|---|
| `0x00` | `type` | used by `MappingConfigParser` as a **per-motor enable**: `0x00` = enabled, `0xFF` = disabled |
| `0x01` | `min` | lower bound of the remap window (parser stores `min(b2,b3)`) |
| `0x02` | `max` | upper bound (parser stores `max(b2,b3)`) |
| `0x03` | `scale` | gain / scaling factor |

### `m_fdg_motor_grip_struct_t` — 9 bytes

| off | field | notes |
|---|---|---|
| `0x00` | `main_switch` | global grip-rumble enable: `0x00` = enabled, `0xFF` = disabled |
| `0x01`–`0x04` | `unit[0]` | grip **left** (`m_fdg_motor_grip_setting_struct_t`) |
| `0x05`–`0x08` | `unit[1]` | grip **right** |

### `m_fdg_motor_trig_setting_struct_t` — 7 bytes

| off | field | protobuf name in `TriggerVibrationTypedConfigBean` |
|---|---|---|
| `0x00` | `type` | `Type` |
| `0x01` | `min` | `MinLevel` |
| `0x02` | `max` | `MaxLevel` |
| `0x03` | `filter` | `Filter` |
| `0x04` | `vibr_limit` | `MinStart` |
| `0x05` | `scale` | `Scale` |
| `0x06` | `time_limit` | `MinTime` |

### `m_fdg_motor_trig_mode_struct_t` — 14 bytes

| off | field | notes |
|---|---|---|
| `0x00`–`0x06` | `line_gear` | linear/analog gear (`LinearConfigBean`) |
| `0x07`–`0x0D` | `micr_gear` | micro-switch gear (`MicroConfigBean`) |

### `m_fdg_motor_trig_struct_t` — 29 bytes

| off | field | notes |
|---|---|---|
| `0x00` | `main_switch` | trigger-rumble enable: `0x00` = enabled, `0x01` = disabled (parser: `Enable = (b[0] == 0)`, writer: `b[0] = Enable ? 0 : 1`) |
| `0x01`–`0x0E` | `unit[0]` | trigger **left** (`m_fdg_motor_trig_mode_struct_t`) |
| `0x0F`–`0x1C` | `unit[1]` | trigger **right** |

### Placement inside the V3.0 mapping-config blob

From `MappingConfigParser.MappingConfigParserV30` (`PackageCount = 79`; blob ≥ 790 bytes):

| field | byte range (decimal) | hex range | size |
|---|---|---|---|
| `version` (proto version, LE16) | 0–1 | `0x000`–`0x001` | 2 |
| `pkg_len` | 2 | `0x002` | 1 |
| `led` (legacy) | 3–12 | `0x003`–`0x00C` | 10 |
| `key_table` | 13–108 | `0x00D`–`0x06C` | 96 |
| `joy_table` | 109–122 | `0x06D`–`0x07A` | 14 |
| `liner_table` (trigger curves) | 123–136 | `0x07B`–`0x088` | 14 |
| `motion` | 137–144 | `0x089`–`0x090` | 8 ¹ |
| **`grip` (`m_fdg_motor_grip_struct_t`)** | **145–153** | **`0x091`–`0x099`** | **9** |
| **`trig` (`m_fdg_motor_trig_struct_t`)** | **154–182** | **`0x09A`–`0x0B6`** | **29** |
| `lunpan` | 183–184 | `0x0B7`–`0x0B8` | 2 |
| `trigger` (adapter/force cfg, 2 × 20) | 185–224 | `0x0B9`–`0x0E0` | 40 |
| `random_data` (data version, LE16) | 225–226 | `0x0E1`–`0x0E2` | 2 |
| `macro` page | 230–767 | `0x0E6`–`0x2FF` | 538 |
| `cfg_name` (UTF-16LE, 20 B) | 770–789 | `0x302`–`0x315` | 20 |

Field order matches `m_fdg_mapping_config_struct_t`; sub-struct sizes cross-check:
`key_table` = 32 × 3 (`m_fdg_macro_key_mapping_struct_t`),
`joy_table`/`liner_table` = 2 × 7 (`m_fdg_macro_joy_mapping_struct_t`),
`trigger` = 2 × 20 (`m_fdg_macro_trigger_sturct_t` = `type` + 8-byte `bind` + `mixed_border`
+ 10-byte `param`), `lunpan` = 2.

¹ `m_fdg_mapping_config_struct_t` declares `motion` as `m_fdg_macro_motion_mapping_struct_t[2]`
(2 × 8 = 16 bytes), but the V3.0 parser reads/writes only the 8 bytes at 137–144 before `grip`
starts at 145. **The parser offsets are authoritative for the wire format**; the C struct is a
firmware header that is not byte-identical to the V3.0 blob here.

---

# PART 3 — Adaptive triggers (K6) and misc

## 3.1 Availability gate — the K6 commands are Apex 6 only

```csharp
private static bool IsK6TriggerProtocolSupported(Controller c)
    => c.ControllerType == ControllerType.NewXInput
       && (c.DeviceType == 149 || c.DeviceCode == "k6");
```

`DeviceType 149d = 0x95 = DeviceType.K6` (Apex 6; `150 = K6Pro`). The **Vader 5 Pro is
`f5` / `130 (0x82)`**, so `ControllerSdk.SetK6Trigger*` / `SendK6TriggerRealtimeFrame` return
early with `action?.Invoke(false)` and send nothing. The frames below are fully specified
anyway (they can be emitted by hand), but expect no response from a Vader 5 Pro.

All five K6 commands are 32-byte `5A A5` frames with the standard `Crc(a, 3, 3 + a[4])`
checksum, **except `0x57`**.

Shared base `K6TriggerStatusCommand` (`0x53`, `0x54`, `0x55`, `0x56`):

```csharp
: base(controller, null, timeoutAction, maxRetryCount: 3, timeoutMs: 500, commandIndex, commandCount)
public override bool IsNeedCallback() => true;                       // always waits for an ACK
public override bool IsAck(d) => d.Length > 5 && d[2] == CommandId();
protected override void ParseAckData(dev, d) => action?.Invoke(d.Length > 5 && d[5] == 1);
protected static byte ClampByte(int v)               => Math.Clamp(v, 0, 255);
protected static byte ClampByte(int v, int lo, int hi)=> Math.Clamp(v, lo, hi);
```

⇒ **ACK convention for `0x53`–`0x56`: `data[2] == cmdId` and `data[5] == 0x01` means success,
anything else means failure.**

## 3.2 `0x53` — K6 trigger mode

```
a[3]  0x53
a[4]  0x04                len = 4
a[5]  triggerMode         K6TriggerMode
a[6]  gripMode            K6GripMode
a[7]  checksum = sum(a[3]..a[6]) & 0xFF
```

| enum | value | name |
|---|---|---|
| `K6TriggerMode` | `0` | `Local` |
| | `1` | `BindGrip` |
| | `2` | `Realtime` |
| `K6GripMode` | `0` | `RotorMapping` |
| | `1` | `Realtime` |

## 3.3 `0x54` — K6 trigger local mode

`K6LocalModeConfig(K6TriggerSide Side, int StartTravel, int EndTravel, bool LoopEnabled, int LoopInterval, int StartGain, int EndGain)`

```
a[3]  0x54
a[4]  0x09                len = 9
a[5]  Side                K6TriggerSide: 0 = Left, 1 = Right
a[6]  StartTravel         ClampByte 0..255
a[7]  EndTravel           ClampByte 0..255
a[8]  LoopEnabled         0x01 / 0x00
a[9]  LoopInterval        ClampByte 0..255
a[10] StartGain           ClampByte 0..255
a[11] EndGain             ClampByte 0..255
a[12] checksum = sum(a[3]..a[11]) & 0xFF
```

## 3.4 `0x55` — K6 trigger waveform (serial / multi-packet write)

`K6WaveformConfig(K6TriggerSide Side, byte[] WaveformData)`; the payload is split into
**21-byte** chunks (`WaveformBytesPerPacket = 21`), one command per chunk, all enqueued as one
batch with `commandIndex = i + 1`, `commandCount = ceil(len / 21)`.

```
a[3]  0x55
a[4]  0x1B                len = 27d  (2 + 25 payload bytes)
a[5]  Side                0 = Left, 1 = Right
a[6]  totalLen >> 8       total waveform length, big-endian, min(len, 0xFFFF)
a[7]  totalLen & 0xFF
a[8]  segmentNumber       = packet index i, ClampByte 0..255
a[9]..a[29]  waveform chunk (up to 21 bytes; last chunk zero-padded)
a[30] checksum = sum(a[3]..a[29]) & 0xFF
a[31] 0x00
```

The per-packet callback aggregates: `action(!hasFailure)` is invoked only after the **last**
packet's ACK. A retry exhaustion on any packet clears the rest of the batch
(`IsSerialCommand()` ⇒ `CommandsToSend.Clear()`).

## 3.5 `0x56` — K6 trigger strength mapping

`K6StrengthMappingConfig(K6StrengthMappingTarget Target, int SegmentIndex, int StartIntensity, int EndIntensity, int StartFrequency, int EndFrequency, int StartAmplitude, int EndAmplitude, K6WaveformMode WaveformMode, K6WaveformShape WaveformShape)`

```
a[3]  0x56
a[4]  0x0C                len = 12d
a[5]  Target              K6StrengthMappingTarget
a[6]  SegmentIndex        ClampByte(v, 0, 9)   ← 10 segments max
a[7]  StartIntensity      0..255
a[8]  EndIntensity        0..255
a[9]  StartFrequency      0..255
a[10] EndFrequency        0..255
a[11] StartAmplitude      0..255
a[12] EndAmplitude        0..255
a[13] WaveformMode        K6WaveformMode
a[14] WaveformShape       K6WaveformShape
a[15] checksum = sum(a[3]..a[14]) & 0xFF
```

| enum | value | name |
|---|---|---|
| `K6StrengthMappingTarget` | `0` | `LeftTrigger` |
| | `1` | `RightTrigger` |
| | `2` | `LeftMotor` |
| | `3` | `RightMotor` |
| `K6WaveformMode` | `0` | `Standard` |
| | `1` | `Special` |
| `K6WaveformShape` | `0` | `Sine` |
| | `1` | `Square` |
| | `2` | `Sawtooth` |
| | `3` | `Triangle` |
| `K6TriggerSide` | `0` | `Left` |
| | `1` | `Right` |

## 3.6 `0x57` — K6 realtime frame (fire-and-forget, non-standard checksum)

`K6RealtimeFrame(int EffectiveChannel, IReadOnlyList<K6RealtimeSample> Samples)`,
`K6RealtimeSample(int Trigger, int LeftGrip, int RightGrip)`.
`IsNeedCallback()` is overridden to **`false`** ⇒ no ACK, no retry.

```
a[3]  0x57
a[4]  0x1C                len = 28d  (as declared by the SDK — see the discrepancy below)
a[5]  EffectiveChannel    Clamp(0..255)
      for i in 0..min(Samples.Count, 8)-1:   base = 6 + i*3
a[6+3i+0]  Samples[i].Trigger     Clamp(0..255)
a[6+3i+1]  Samples[i].LeftGrip    Clamp(0..255)
a[6+3i+2]  Samples[i].RightGrip   Clamp(0..255)
      → up to 8 samples occupy a[6]..a[29]
a[30] checksum = Crc(a, 1, 30) = sum(a[1]..a[29]) & 0xFF        ← NOTE: includes 0x5A/0xA5, excludes a[30]
a[31] 0x00
```

Discrepancies to be aware of (both are what the code does, verbatim):
`a[4] = 0x1C` implies 26 payload bytes and a checksum at `a[31]`, but only 25 payload bytes
are written and the checksum lands at `a[30]`; and the summed range starts at `a[1]`
(the magic bytes) rather than `a[3]`. Reproduce exactly; flag for live verification if you ever
get a K6.

## 3.7 `0x1F` — SwitchToFirmwareUpgradeMode ☠ AVOID

`Flydigi.ControllerSDK.data.command/SwitchToFirmwareUpgradeModeCommandFactory.cs`,
`SwitchToFirmwareUpgradeModeCommandNewXInput`:

```
a[3]  0x1F        ← NewXInput firmware-upgrade command id
a[4]  0x03
a[5]  chipDefine  (ChipModule: 0=Main, 1=Rf, 2=Si, 4=Screen, 5=Trigger, 6=Dongle, 7=Adc)
a[6]  checksum
```

**Never emit command id `0x1F`.** It puts the selected chip into bootloader/DFU mode. Two more
IDs to keep away from: `0xFD` (TestRecoverFactory — factory reset) and `0xFE`
(WriteDeviceType — rewrites the device identity).

## 3.8 `0x51` / `0x52` — SetForceTrigger (adaptive trigger, non-K6 line)

`SetForceTriggerCommandFactory`, NewXInput class. `IsSupportForceTrigger == false` for the
Vader 5 Pro, so `ControllerSdk.SetForceTriggerConfig` never sends it for this model — included
because it is the *non-K6* adaptive-trigger protocol used by Apex 4/5 and the
`ForceTriggerConfigSyncWithGrip` variant referenced by the task.

Command id: `0x52` if the config is `ForceTriggerConfigSyncWithGrip`, otherwise `0x51`.

```
non-sync (0x51):
a[3]  0x51
a[4]  0x0A                len = 10d
a[5]  applyFlag           0x01 when onlyPreview == false, 0x00 when onlyPreview == true
a[6]..            config.CreateParams()  (2..7 bytes, see table)
a[13] 0x00                checksum slot — NOT WRITTEN by the SDK

sync-with-grip (0x52):
a[3]  0x52
a[4]  0x0B                len = 11d   (declared; only 8 payload bytes are written)
a[5]..a[12]  config.CreateParams()  (8 bytes)
a[14] 0x00                checksum slot — NOT WRITTEN
```

`CreateParams()` payloads (`side` = `ForceTriggerSide`: `1` Left, `2` Right, `3` Both;
`AdapterTriggerType`: `0` Normal, `1` Race, `2` Sniper, `3` Recoil, `4` Lock, `5` Vibration):

| config class | bytes |
|---|---|
| `ForceTriggerConfigNormal(side)` | `side, 0x00` |
| `ForceTriggerConfigRace(side, stroke, resistance, matchStroke)` | `side, 0x01, stroke, max(resistance,1), matchStroke?1:0` |
| `ForceTriggerConfigSniper(side, stroke, pressureLevel, strength, frequency, matchStroke)` | `side, 0x02, stroke, max(pressureLevel,1), max(strength,1), max(frequency,1), matchStroke?1:0` |
| `ForceTriggerConfigRecoil(side, stroke, recoilStroke, strength, matchStroke)` | `side, 0x03, stroke, recoilStroke, max(strength,1), 0x00, matchStroke?1:0` |
| `ForceTriggerConfigLock(side, stroke, strength=255, matchStroke=true)` | `side, 0x04, stroke, strength ?? 1, matchStroke?1:0` |
| `ForceTriggerConfigVibration(side, stroke, pressureLevel, strength, frequency, matchStroke)` | `side, 0x05, stroke, max(pressureLevel,1), max(strength,1), max(frequency,1), matchStroke?1:0` |
| `ForceTriggerConfigSyncWithGrip(side, bindType, filter, scale, stroke, pressureLevel, strength, frequency)` | `side, bindType, filter, scale, stroke, pressureLevel, strength, frequency` (→ `0x52`) |
| `ForceTriggerConfigCommon(byte[] command)` | raw passthrough; if `cmd[1]==1 && cmd[2]==0 && cmd[4]==1` then `cmd[4]=0` |

`ControllerSdk.SetForceTriggerConfig(left, right, …)` sends the **left** config immediately and
the **right** config after `Task.Delay(100)` — keep that ≥100 ms gap.

## 3.9 `SyncTriggerWithGripCommandFactory` — XInput only

```csharp
public static AbstractControllerCommand CreateCommand(Controller c, SyncTriggerWithGripCommandConfig cfg, Action? t = null)
{
    if (c.ControllerType == ControllerType.XInput) return new SyncTriggerWithGripControllerCommandXInput(c, cfg, t);
    throw new NotImplementedException("SyncTriggerWithGripControllerCommandXInput not implemented");
}
```

So `ControllerSdk.SyncTriggerWithGrip` **throws** for a NewXInput device. The XInput frame
(legacy 15-byte, no `5A A5`) is:

```
a[1]  0x51 (cmd id)
a[2]  0x01
a[3]  Type
a[4]  Flag
a[5]  Min
a[6]  Max
a[7]  Filter
a[8]  TimeLimit
a[9]  VibrLimit
a[10] LevelLimit
```

`record SyncTriggerWithGripCommandConfig(byte Type, byte Flag, byte Min, byte Max, byte Filter, byte TimeLimit, byte VibrLimit, byte LevelLimit)`.
The NewXInput equivalent is `0x52` (`ForceTriggerConfigSyncWithGrip`, 3.8), whose parameter
order is `side, bindType, filter, scale, stroke, pressureLevel, strength, frequency` — note it
is **not** the same field order.

---

# Needs live-device verification

1. **Report-ID byte.** The SDK writes `_outReportId` = the *last* `Report ID` in the report
   descriptor and strips the *first* one from inbound reports. Live testing on Linux showed
   `0x00` works for writes and inbound frames start directly at `0x5A`. Dump the descriptor
   (`0x85` items) of the `0xFFA0` interface to confirm what the numbers actually are on this
   unit, and whether a non-zero write prefix is ever required (e.g. over the dongle interface).
2. **Missing checksums on `0x12`, `0x51`, `0x52`.** Confirm the firmware truly ignores the
   checksum byte for these IDs, and whether a *correct* checksum is also accepted (so that a
   uniform implementation is possible).
3. **`0x12` semantics per `VibrationType`.** Verify that `a[5]/a[6]` are grip L/R and
   `a[7]/a[8]` are trigger L/R on this exact model, that `0x00` stops the motors, and whether
   the effect latches (needs an explicit zero frame) or auto-decays. Also whether zeroing only
   two of the four bytes stops just those motors (`Grip`/`Trigger` selectivity).
4. **Level curve.** Whether `0x00`–`0xFF` is linear or is remapped by the stored
   `m_fdg_motor_grip_struct_t.min/max/scale`; and the actual dead zone (lowest level that
   produces motion).
5. **`0x53`–`0x57` on an `f5`.** The SDK gate says Apex 6 only. Send `0x53` by hand and see
   whether the Vader 5 Pro replies at all (`data[2] == 0x53`, `data[5] == 1/0`) or stays silent.
6. **`0x57` checksum anomaly** (`sum(a[1]..a[29])` at `a[30]`, with `a[4] = 0x1C`) — only
   testable on a K6.
7. **`0x51` on an `f5`.** `IsSupportForceTrigger == false` for this model, but the Vader 5 Pro
   does have trigger motors; check whether `0x51`/`0x52` are accepted or ignored.
8. **Response checksum.** Confirm `data[31] == sum(data[2..30]) & 0xFF` holds for every reply
   (the SDK never checks it, so it is unverified for multi-packet and `0xEF`/`0xF7` frames).
9. **Fire-and-forget rate limits.** `0x12` has no ACK; find the maximum sustainable frame rate
   before the device drops frames (the SDK sends one per rumble update with no pacing).
