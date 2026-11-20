# Dimplex source audit

Audit run: current session. The requested reference date is 2026-11-18.

This is an evidence inventory, not a claim that the proprietary ConfigR implementation is fully recoverable as source code.

## Scope and revisions

| Source | Revision/artifact | Inventory | Result |
|---|---|---:|---|
| `philcluff/dimplex-qrad-smart-bridge` | `5091078d9153b8c615afc593e862893734da4fa8` | 29 files; 22 text/source files; 3,701 source lines | Full archive scan; protocol-bearing files manually reviewed |
| `JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant` | `7cd0271c6e902e63bc29ffd54565fe9daa9b2660` | 6 files; 5 text/source files; 2,599 source lines | Full archive scan; complete YAML, helper and cards reviewed |
| `bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump` | `b343fe3d07ce1a177766ffa23778fb0802119758` | 6 files; 4 images and 2 binary flash dumps | No source code in the repository; binary strings were checked |
| `KRoperUK/dimplex-controller-py` | `28fdc66cfbb2368a114ef10771e99b06ea445d2f` | 62 files; 54 text/source files; 12,388 source lines | Full archive scan; package, tests, API reference and research docs reviewed |
| ConfigR APK extraction | `com.Dimplex.DimplexToolkit` APK/XAPK, extracted locally | 11,728 JADX files; 1,466,483 decompiled Java lines; native ConfigR AOT artifacts | Full textual/artifact search; relevant generated bindings and native string tables reviewed |

The four GitHub archives were downloaded from their `main` branches during this audit and scanned locally. The four revisions above were then recorded from the GitHub API.

## ConfigR evidence boundary

ConfigR is a .NET MAUI/Mono application. The APK does **not** contain ordinary readable C# source for the product logic. The relevant implementation is in native/AOT artifacts including:

- `lib/arm64-v8a/libaot-ConfigR.dll.so`
- `lib/arm64-v8a/libaot-ConfigR.dll.so.strings`
- `lib/arm64-v8a/libassembly-store.so`
- `assembly-store.strings`
- the generated `crc64*` Android binding classes

The generated Java classes are mostly JNI/`TypeManager.Activate` bindings, not the ConfigR business implementation. The decompiler scan found 45 files containing 67 JADX error/decompilation markers. Therefore a literal line-by-line review of the proprietary ConfigR implementation is not possible from this extraction. The protocol facts used here are the recoverable class/parameter names, native string tables, APK resources, and the independently documented/replayed GATT behaviour.

Recoverable ConfigR terminology includes `Trac`, `Sound`, `SetRtc`, `LastKeyPressValue`, `TemperatureSensor`, `PowerLoading`, `HeatingMode`, `ScheduleAndSetTemperature`, `TemperatureUnit`, `SetpointRange`, `OpenWindowDetection`, `PreEmptiveHeating`, `RfComms`, `AdvanceHeating`, `ErrorStatus`, `Setback`, `Runback`, `SpConfig`, `TestMode`, `CurrentSetTemperature`, `RunbackTime`, `RunbackSetpoint`, `AwayMode`, `SwVersion`, `Gdid`, `Brand`, `HeaterType`, `EpIdentifier`, and protected credential/security parameters.

## Repository findings

### Q-Rad smart bridge

The complete text inventory covers the firmware YAML, all BLE tools, captures, protocol notes, research, backlog, README and CI. The protocol notes define the Q-Rad service topology and the 33 ConfigR-named parameters observed on the Q-Rad capture, plus unnamed present characteristics. They explicitly distinguish confirmed, inferred and unknown values.

Important evidence retained:

- Passkey-display BLE pairing, persistent bond, one central per radiator.
- Main control service plus identity, settings and device-information services.
- Selector-before-read semantics for room/LCD/core temperature, Trac element state and software version.
- `0x0006` clock, `0x1002` schedule buffer, `0x1007` open-window, `0x1008` pre-emptive heating, `0x100d` advance, `0x1016` setback, `0x1017` runback, `0x1027` runback time, `0x1028` runback setpoint, `0x102a` away, and the settings fields.
- Protected/property-less writes are explicitly excluded.
- `0x1024` HeatDemand is a ConfigR parameter but absent from the captured Q-Rad firmware; it must not be substituted for the model-specific Trac output byte.

### Sunhouse project

The complete 1,307-line ESPHome YAML and both JavaScript cards were reviewed. It adds these protocol behaviours beyond the Q-Rad project:

- the older clock write order used as a 3-second keep-alive;
- explicit security parameter setup and `esp_ble_set_encryption()` on connect;
- inferred mode 5/6 commands;
- advance/boost writes;
- schedule read/write request format for the 31-byte buffer;
- seven-day, four-period schedule representation.

These are protocol-family evidence, not proof that the same writes are safe on `PLX050E;C;` AM6532. The current integration therefore does not silently promote them to normal climate commands.

### Quantum dump

The repository contains no source code. It contains photographs and two binary flash dumps. The binary string scan found only limited readable product text and cannot establish BLE characteristic semantics or a safe command mapping.

### `dimplex-controller-py`

This is a cloud API client, not a local BLE client. Its complete package and documentation inventory confirms cloud-only fields and actions such as room temperature, active setpoint, mode flags, Boost, Away, Advance, FrostProtect, Manual, Eco, open-window detection, EcoStart, setback, schedules, error/warning codes, firmware metadata and energy telemetry. It also documents the important cloud distinction between `0xFF` “follow schedule” and a real setpoint, and the corrected mode bit values.

Those fields are not evidence that the corresponding local BLE characteristic exists on AM6532. Cloud-only values remain documented as a separate source family and are not added to the local integration without local GATT evidence.

## Cross-reference to the current integration

The current component exposes 17 experimental raw text controls. Every one is a present, non-protected AM6532 characteristic whose exact semantics or safe write contract is not yet established:

| Address | ConfigR/upstream label or evidence | Current treatment |
|---|---|---|
| `0/0x0006` | `SetRtc`; Q-Rad/AM6532 clock-like seven-byte payload | Experimental raw read/write-length control; no clock write automation |
| `0/0x1002` | `ScheduleAndSetTemperature`; 31-byte schedule buffer | Experimental raw control; no schedule UI or inferred write |
| `1/0x0001` | observed four-byte backlight colour payload | Experimental raw control |
| `0/0x1007` | `OpenWindowDetection` | Experimental raw control |
| `0/0x1008` | `PreEmptiveHeating` | Experimental raw control |
| `0/0x100d` | `AdvanceHeating` | Experimental raw control |
| `0/0x1016` | `Setback` | Experimental raw control |
| `0/0x1017` | `Runback` | Experimental raw control |
| `0/0x1027` | `RunbackTime` / boost countdown | Experimental raw control |
| `0/0x1028` | `RunbackSetpoint` | Experimental raw control |
| `0/0x102a` | `AwayMode` | Experimental raw control |
| `2/0x0005` | `Sound` | Experimental raw control |
| `2/0x100a` | unnamed boolean candidate | Experimental raw control |
| `2/0x100b` | unnamed value | Experimental raw control |
| `2/0x100f` | unnamed value | Experimental raw control |
| `2/0x1018` | unnamed value | Experimental raw control |
| `2/0x1019` | `SpConfig` | Experimental raw control |

The audit found no additional **safe-to-expose experimental control** supported by the available evidence.

The component now also exposes fourteen additional non-writable diagnostic
sensors, disabled by default, containing the raw value observed during platform
setup:

| Address | Label | Safety boundary |
|---|---|---|
| `1/0x0007` | Last key press | Transient UI diagnostic |
| `3/0x000B`, `3/0x000C` | Calibration-like values | Read-only; no write service |
| `3/0x1022`, `3/0x1029`, `3/0x2003`, `3/0x200B`, `3/0x200C`, `3/0x200F` | Unclassified device values | Raw diagnostics only |
| `1/0x1010`, `1/0x1020` | Test-status values | Read-only; writes remain blocked |
| `1/0x300E` | OTA status value | Read-only; not treated as firmware update support |
| `2/0x100C` | RF communications status | Read-only; writes remain blocked |
| `2/0x700F` | Disconnect status value | Read-only; writes remain blocked |

Protected credential/security characteristics are deliberately not added:
`3/0x2002`, `3/0x2006`, `3/0x2007`, and `3/0x2008` remain blocked at the client
read boundary. Exposing their bytes as Home Assistant state would leak device
identity or encryption material into the UI, recorder, diagnostics, and logs.

The read-only sensors are snapshots from setup rather than semantic mappings.
Unknown values remain hexadecimal and are not given fabricated units, device
classes, or meanings.

Established packet layouts are translated into additional attributes while the
raw hexadecimal state is preserved: RTC (`0/0x0006`), backlight RGB
(`1/0x0001`), last-key code (`1/0x0007`), advance flag (`0/0x100D`), runback
setpoint (`0/0x1028`), and Away fields (`0/0x102A`). Unknown values remain raw.

The following are intentionally not promoted:

- `0/0x1001`, `0/0x1023`, `0/0x1005`, `0/0x1006`: already mapped to the climate or display-unit configuration.
- `1/0x0002`, `1/0x0008`, `3/0x000a`, `0/0x1011`, `3/0x2001`, `3/0x2002`, `3/0x2004`, `3/0x2005`, `3/0x2009`, `3/0x200a`, `3/0x200d`: read/selector/metadata paths already mapped or intentionally diagnostic-only.
- `3/0x000b`, `3/0x000c`, `3/0x1022`, `3/0x2003`, `3/0x200b`, `3/0x200c`, `3/0x1029`, `3/0x200f`: present but semantically unknown, calibration-like, or write-sensitive; exposed only as disabled-by-default read-only diagnostics.
- `1/0x1010`, `1/0x1020`, `1/0x300e`, `2/0x100c`, `2/0x700f`: test, OTA, RF or disconnect/security-adjacent paths; exposed only as disabled-by-default read-only diagnostics, with writes blocked.
- `0/0x1012`–`0x1015`, `3/0x2006`–`0x2008` and all absent ConfigR parameters: protected, property-less, absent, or not proven on AM6532.
- ConfigR-only fields such as `0x000e`, `0x0106`, `0x101b`–`0x101e`, `0x1024`, `0x1030`, `0x1033`, `0x1034`, `0x1037`, `0x2012`, `0x4012`, and group 4/5 fields were not invented locally.

## Confidence statement

**High confidence:** the complete available textual source trees were scanned, the protocol-bearing files were reviewed, the binary repositories were identified as such, and no additional writable AM6532 field is supported strongly enough to add. Fourteen additional present fields are now exposed strictly as disabled-by-default, read-only hexadecimal diagnostics.

**Not 100% provable:** the proprietary ConfigR native/AOT implementation cannot be reconstructed line by line from this APK extraction, and the 45 decompiler-error files leave implementation gaps. Unknown AM6532 characteristics also cannot be assigned meanings from names alone. The safe conclusion is therefore “no further evidence-backed semantic mapping or writable control found,” not “all proprietary source semantics are known.”

The implementation now includes the fourteen read-only diagnostic sensors described above; no protected credential bytes were exposed.
