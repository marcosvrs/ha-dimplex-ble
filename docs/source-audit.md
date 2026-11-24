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

- Passkey-display BLE pairing, persistent bond. ("One central per radiator" is the bridge project's claim; hardware test 2026-11-24 showed an AM2086 keeps valid bonds with at least two centrals, see `esphome-ble-findings.md` H14.)
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

The current integration deliberately exposes only evidence-backed Home
Assistant entities:

| Surface | Evidence | Current treatment |
|---|---|---|
| Climate target/current temperature | Repeated AM6532 reads and write/readback | Native Celsius climate target |
| User Timer | Device mode payload and display confirmation | Climate heat-only mode with `user_timer` preset; target remains available |
| Home All Day, Manual, Eco Mode, Frost Protect, Away Mode | Device mode/Away reads and verified write paths | Climate presets; Away requires the separate return-time datetime |
| Trac heating state | Repeated live output correlation | Honest `HEATING`/`IDLE` action |
| Sound | Verified setting and readback | Independent configuration switch |
| Adaptive Start | AM2086 reversible read/write plus PLXE manual | Configuration switch |
| Open Window Detection | AM2086 reversible read/write plus PLXE manual | Configuration switch |
| Rated/output/calculated power | Rated power and Trac output percentage | Diagnostic sensors plus derived power sensor |
| LCD/controller temperature and error code | Repeated selected reads | Diagnostic sensors |
| Display unit | Reversible read/write | Configuration select |

The integration no longer exposes arbitrary raw GATT text controls or a
snapshot batch of unknown diagnostic values. ConfigR names and upstream
characteristics remain research evidence, not sufficient Home Assistant
semantics.

Protected credential/security characteristics remain blocked:
`3/0x2002`, `3/0x2006`, `3/0x2007`, and `3/0x2008`. They are never copied into
state, recorder data, diagnostics, or logs.

The following catalogued values remain intentionally unexposed until their
meaning and safe contract are confirmed on the supported heater:

- clock and schedule buffers;
- backlight, advance, setback, runback, and runback-setpoint values;
- unnamed group-2 and group-3 values;
- test, OTA, RF, disconnect, and other security-adjacent values;
- ConfigR-only cloud or absent characteristics.

## Confidence statement

**High confidence:** the available textual source trees were scanned, the
protocol-bearing files were reviewed, the binary repositories were identified
as such, and no additional writable AM6532 field is supported strongly enough
to add to the Home Assistant surface.

**Not 100% provable:** the proprietary ConfigR native/AOT implementation cannot
be reconstructed line by line from this APK extraction, and decompiler-error
files leave implementation gaps. Unknown AM6532 characteristics cannot be
assigned meanings from names alone. The safe conclusion is “no further
evidence-backed semantic mapping or writable control found,” not “all
proprietary source semantics are known.”
