# Capability-matrix source research

This report inventories field/value evidence available from the requested first-party repositories and the local ConfigR extraction. It does **not** edit or supersede `docs/capability-matrix.md`. Classifications are deliberately conservative: a source-documented field is not a verified AM6532 live mapping, and a write shown in another model/profile is not safe to copy.

## Sources inspected

* ConfigR extraction: `/tmp/configr-apk/native/configr-utf16-l.txt`, `/tmp/configr-apk/native/all-strings.txt`, `/tmp/configr-apk/native/ConfigR-candidate.dll`; repository interpretation [`docs/configr.md`](configr.md). Native-AOT strings/classes provide names, lengths and profile terminology, but no independently proven AM6532 packet trace.
* Q-Rad Smart Bridge: [repository](https://github.com/philcluff/dimplex-qrad-smart-bridge), especially [raw `docs/ble-protocol.md`](https://raw.githubusercontent.com/philcluff/dimplex-qrad-smart-bridge/main/docs/ble-protocol.md), [raw `docs/research.md`](https://raw.githubusercontent.com/philcluff/dimplex-qrad-smart-bridge/main/docs/research.md), and [raw `docs/backlog.md`](https://raw.githubusercontent.com/philcluff/dimplex-qrad-smart-bridge/main/docs/backlog.md). Its protocol notes are live observations on QRAD100E Series D, with Sunhouse-derived rows explicitly marked inferred.
* Sunhouse ESPHome: [repository](https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant), [raw YAML](https://raw.githubusercontent.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant/main/sunhouse-heater.yaml), [raw README](https://raw.githubusercontent.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant/main/README.md). This is an SSHE/Sunhouse implementation; its writes are not AM6532 proof.
* Quantum dump: [repository](https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump), [tree](https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump/tree/main). The repository contains PCB photographs plus `Raw Dump.bin` and `Smart MX25L16066 .bin` (2 MiB each), with no protocol documentation or decoded field table. It therefore contributes no safe GATT field/value mapping.
* Cloud/API controller: [repository](https://github.com/KRoperUK/dimplex-controller-py), [raw decompiled API reference](https://raw.githubusercontent.com/KRoperUK/dimplex-controller-py/main/docs/decompiled-api-reference.md), [raw models](https://raw.githubusercontent.com/KRoperUK/dimplex-controller-py/main/dimplex_controller/models.py), [raw client](https://raw.githubusercontent.com/KRoperUK/dimplex-controller-py/main/dimplex_controller/client.py), [raw telemetry](https://raw.githubusercontent.com/KRoperUK/dimplex-controller-py/main/dimplex_controller/telemetry.py). This documents the GDHV cloud API and decompiled Dimplex Control APK 2.26.0, not BLE packet bytes.

## Classification rules

* **Verified live**: direct device observation in a cited source (model/profile stated).
* **Source-documented**: exact name/layout/value documented by a source, but not live on this AM6532.
* **ConfigR-only**: name/length/profile recovered from ConfigR without a matching live payload in this project.
* **Unknown**: payload exists or a claim is suggested, but semantics/layout are unresolved.
* **Protected/unsafe**: security, OTA, factory/EOL/test, or undocumented write boundary; do not probe/write blindly.
* **Absent**: explicitly not present in the cited device/profile, or no evidence in the requested source. Absence is scoped to that inventory, not a universal claim.

## BLE service and address inventory

All four main service groups use Bluetooth SIG base `00000000-0000-1000-8000-00805f9b34fb` with service number in the third group and characteristic UUID `0000XXXX-0000-1000-8000-00805f9b34fb` on QRAD. Q-Rad docs list groups 0 (20 chars, main control), 1 (7, identity/misc), 2 (6, settings), and 3 (20, info/calibration/diagnostics), plus two Cypress OTA bootloader services (`00060000-f8ce-11e4-abf4-0002a5d5c51b/c`) that are protected/unsafe. Newer GDHV products may use vendor base `0000XXXX-000S-474C-4E44-494D504C4558`; do not assume the base from model names alone. (QRAD `ble-protocol.md`, services section.)

## Exact field matrix (BLE / ConfigR nomenclature)

| Address | Exact name and layout/value evidence | Scope and classification | Safety / recommendation |
|---|---|---|---|
| `0/0x0006` | `SetRtc`, write-only 7 bytes; QRAD live value `[20,YY,MM,DD,hh,mm,flag]`, e.g. `14 1a 09 0b 09 0f 01`; newer `0x0106` is `SetRtcWithSeconds`, 8 bytes. | QRAD100E D live for clock shape; ConfigR documented. | Write confirmed by ConfigR but not safe for AM6532 without exact model test; retain as unknown/write boundary. |
| `0/0x1001` | `HeatingMode`, 6 bytes `[mode, submode,b2,b3,b4,b5]`. QRAD live: mode `1 Timer`, `2 Manual`, `3 Eco`, `4 Frost Protect`; submode for Timer: `1 Out All Day`, `2 Home All Day`, `3 User Timer`, `5 Away`; byte 3 `01` while Boost runs. QRAD writes `[mode,sub,0,0,0,0]`; AM6532 writes/readback are verified for Manual, Eco, Frost Protect, and Timer/Away; AM2086 accepted/read back Home All Day `01 02 00 00 00 00`. | QRAD100E D verified live; ConfigR/other implementation documents same family. AM2086 controlled Home All Day readback confirmed the exact timer submode. | Use heat-only `HVACMode.HEAT` with translated User Timer, Home All Day, Away, Manual, Eco, and Frost Protect presets; expose Away's explicit return deadline separately. |
| `0/0x1002` | `ScheduleAndSetTemperature`, 31 bytes. QRAD inferred layout: bytes 0–2 header `[response/timerMode/day]`; four 6-byte periods at offsets 3, 9, 15, 21, each `[startH,startM,endH,endM,temp,0]`; four tail bytes. Sunhouse reads by writing `[0,timerMode,day]`; writes `[1,timerMode,day,...]`. | QRAD marks inferred from Sunhouse; Sunhouse implementation parses 4 periods. AM6532 live inventory shows 31 zero bytes only. | Unknown/unsafe schedule write for AM6532; do not expose calendar from this alone. |
| `0/0x1003` | Effective/current setpoint mirror, LE16 whole °C. QRAD live mirrors `0x1023`, with one transient mismatch; AM6532 live `1400`/`1200` observations. | Verified live on QRAD and AM6532 as readable values; semantic independence unknown. | Read-only diagnostic/raw evidence; no second target entity. |
| `0/0x1005` | `TemperatureUnit`, 1 byte; AM6532/QRAD read `00` interpreted Celsius in project. ConfigR documents Celsius/Fahrenheit. | AM6532 read/write/readback verified; native climate values remain Celsius. | Expose only the separate display-unit select; do not change climate native-unit semantics. |
| `0/0x1006` | `SetpointRange`, 4 bytes `[min,?,max,?]`; live `07 00 1e 00` = 7–30 °C. | Verified live QRAD and AM6532; exact unused-byte semantics unknown. | Read safe; no write mapping. |
| `0/0x1007` | `OpenWindowDetection`, 9 bytes; live AM6532 `01 00 00 00 00 00 00 00 00`, QRAD same shape. | ConfigR/source documented and live payload; enabled-bit semantics unverified. | Read only; no AM6532 write. |
| `0/0x1008` | `PreEmptiveHeating`, 1 byte; AM6532 live `01`, QRAD live/notes `00`. | Source-documented plus AM6532 raw observation; boolean meaning not proven. | Read only until reversible state test. |
| `0/0x100A`, `0x100B`, `0x100F` | Undecoded 1-byte/status values; AM6532 inventory `01`, `05`, `00` respectively. | Unknown; no authoritative ConfigR mapping in requested sources. | Raw inspection only. |
| `0/0x100C` | `RfComms`, 1 byte; AM6532 `01`; QRAD notes list it as settings. | ConfigR label + live AM6532 payload. | Read allowed; current project blocks writes. |
| `0/0x100D` | `AdvanceHeating`, 1 byte; QRAD confirmed read: `1` engage/`0` release; Sunhouse inferred boost-hours use; AM6532 `00`. | QRAD live read; write only inferred for Q-Rad/Sunhouse. | Do not copy writes to AM6532. |
| `0/0x1011` | `ErrorStatus`, 1 byte fault-code ID; `00` means no error. | ConfigR and QRAD live; AM6532 live `00`. | Safe diagnostic read; unknown nonzero-code dictionary. |
| `0/0x1012–0x1015` | Four 10-byte blocks; ConfigR/QRAD candidates include lock/preset definitions, no proven meaning. | Unknown and protected/unsafe in this integration. | Block read/write; never infer from neighbors. |
| `0/0x1016` | `Setback`, 2 bytes; QRAD `00 07`, AM6532 `00 08`/`0008`; units unknown. | ConfigR label + live raw values on QRAD/AM6532. | Read only; no writes. |
| `0/0x1017` | `Runback`, 2 bytes; AM6532 `01 7c`; QRAD `01 7c`; encoding unknown. | ConfigR label + live raw values. | Read only; no writes. |
| `0/0x1023` | `CurrentSetTemperature`, 2-byte LE16 whole °C, write `[temp,0]`; AM6532 and QRAD writes/readback confirmed. | Verified live on QRAD and AM6532. | Safe AM6532 target write only as bounded whole-degree command. |
| `3/0x1024` | ConfigR `HeatDemand` (UUID `00001024-...`; ConfigR's own label string says "0124", a ConfigR typo), 4 bytes `[band,temperature]` per QRAD notes. Not present on QRAD D and absent from AM6532 inventory. | ConfigR-only; absent on observed profiles. | Do not probe merely because named by ConfigR; distinct from Trac/output byte. |
| `0/0x1027` | `RunbackTime` / boost countdown, 2 bytes; QRAD observed 0→30 (`0x1e`)→0, likely minutes; AM6532 `0000`. | QRAD live, ConfigR label; AM6532 raw only. | No AM6532 write. |
| `0/0x1028` | `RunbackSetpoint`, 2-byte LE16; QRAD `1500` = 21 °C, AM6532 `1500`. | QRAD live and AM6532 raw. | No separate target or write without model test. |
| `0/0x102A` | `AwayMode`, 8 bytes `[active,YY,MM,DD,mm,hh,temp]` (QRAD docs show 7 meaningful bytes despite ConfigR table length 8); QRAD active-until example and 7 °C; AM6532 active payload `01 1a 0b 16 35 12 07` read back after a controlled write. | QRAD and AM6532 live; active/inactive AM6532 write/readback verified. | Local PLX050E Away control is verified; the climate maps the reported Timer/Away state to the friendly Away preset and requires an explicit future deadline through the separate datetime entity. |
| `1/0x0001` | Undocumented on ConfigR Q-Rad table; observed backlight `[selector,R,G,B]`, e.g. `02 b9 ff 96`, with colors including `ff7878`, `00ff00`, `87ceeb`, `00008c`. | QRAD live and AM6532 raw `02 9e ff 96`; model behavior not established. | No HA control. |
| `1/0x0002` | ConfigR `Trac`, selector write `[n,0,0]`; read `[n,active]` (QRAD) / 3-byte AM6532 reads. QRAD elements 1/2 active flags; QRAD debug page C is demand percent and D element flags, but source says not exposed directly in BLE. AM6532 live `010164`/`020164` active and `010000`/`020000` idle, third-byte percent correlation is model-specific. | Selector protocol source-documented; QRAD live; AM6532 live correlation. | Same-connection selector exchange only; do not call third byte universal HeatDemand. |
| `1/0x0007` | `LastKeyPressValue`, 1 byte; QRAD `03`, AM6532 `06`; transient last-button event. | Verified live QRAD/AM6532. | Read diagnostic only; no normal entity/write. |
| `1/0x0008` | `TemperatureSensor`, selector write `[n,0,0]`, read `[n,whole,tenths]`; n=1 room, n=2 LCD, n=3 core. QRAD live 24.5 °C; AM6532 selectors 1 `011308` (19.8 °C), 2 `021d01` (29.1 °C), 3 `030000` unavailable. | Selector layout verified live QRAD and AM6532; core semantics absent on AM6532. | Read-only selector exchange; expose only verified room/LCD diagnostics. |
| `1/0x100E` | ConfigR `OtaEnable`, 1 byte; absent on QRAD; no AM6532 evidence. | ConfigR-only/absent observed profile. | Protected/unsafe; never write. |
| `1/0x1010`, `0x1020`, `0x300E` | ConfigR names EolTest, TestMode, Esp32OtaEnable; AM6532 raw values `47`, `00`, `00`; Q-Rad raw/unknown. | Protected/unsafe. | Current project blocks writes; no semantic mapping. |
| `2/0x0005` | ConfigR `Sound`, 3 bytes; AM6532 `000000`, QRAD `010000`; controlled read/write/readback establishes a boolean setting (`00` off, `01` on). | ConfigR/source + live readback. | Implemented as the Sound configuration switch with independent low-frequency polling. |
| `2/0x100C` | See `0/0x100C` above; service-2 address is ConfigR `RfComms`; AM6532 live `01`. | Verified raw read, protected write. | Never write. |
| `2/0x1019` | ConfigR `SpConfig`, 1 byte; AM6532 raw `00`; semantics unknown. | ConfigR-only name + raw value. | No write. |
| `2/0x1030` | ConfigR `FactoryReset`, 1-byte write; absent Q-Rad. | Protected/unsafe. | Never probe/write. |
| `2/0x1033` | ConfigR `Security`, 33 bytes; absent Q-Rad; security handshake only on products exposing it. | ConfigR-only/protected. | Never read/write or reproduce security material. |
| `2/0x1034` | ConfigR `SlaveInfo`, 74 bytes; absent Q-Rad. | ConfigR-only/protected. | Never probe. |
| `3/0x000A` | `PowerLoading`, 8 bytes. Layout documented by Q-Rad: `[element1 LE16, element2 LE16, 0,0,0,0]`; QRAD `e8 03 98 08 00...` = 1000 W and 2200 W; AM6532 `f4 01 98 08 00...` = 500 W and 2200 (second word unknown in AM6532 context). | QRAD live layout; AM6532 live first word agrees PLX050E 500 W. | Read-only diagnostic; write blocked; never treat second word or total as metered power. |
| `3/0x1010`, `0x1037` | ConfigR `EolTest` / `FunctionRule`; AM6532 no safe semantics. | Protected/unsafe. | Never write. |
| `3/0x2001` | `SwVersion`, selector `[n,0,0]` → `[n,major,minor]`; n=0 controller, 1 UI, 2 RF. AM6532 selector 1 `010107` (UI 1.7); QRAD selector mapping source-documented and UI debug 018. | Selector layout verified source/live; other selectors placeholder/unavailable on AM6532. | Read-only metadata. |
| `3/0x2002` | `Gdid`, 6 bytes, per-unit ID; QRAD confirmed and redacted; ConfigR table says RW but security-sensitive. | ConfigR/source and QRAD live; AM6532 not read in current evidence. | Protected/privacy-sensitive; do not expose or write. |
| `3/0x2004` | `Brand`, QRAD `<Dimplex>` plus padding; AM6532 `3c44696d706c65783e` plus padding. | Verified live metadata. | Read-only. |
| `3/0x2005` | `HeaterType`, ASCII `QRAD100E;D;` on QRAD; AM6532 `PLX050E;C;`. | Verified live identity per model. | Read-only model metadata. |
| `3/0x2006–0x2008` | ConfigR `Btpk` 5 bytes, `EncKey` 16, `AuthKey` 16; no safe decoded values. | Protected/unsafe; absent/blocked in observed live use. | Never read/write/reproduce. |
| `3/0x2009` | RF firmware revision, 1 byte; QRAD/AM6532 `05`. | Verified live metadata. | Read-only. |
| `3/0x200A` | `EpIdentifier`, 6 bytes; QRAD `EP4099`, AM6532 `EP4213`. | Verified live metadata by model. | Read-only; not a control. |
| `3/0x200B`, `0x200C` | Undecoded board/build values; AM6532 `7f9d`, `1416050d0e00`; QRAD `e5 26`, `14 13 03 0f 0f 23`. | Unknown. | Retain raw only. |
| `3/0x200D` | Board/firmware code; QRAD `PGPHS600`, AM6532 `PGLHS600`. | Verified live metadata. | Do not interpret as semantic firmware version. |
| `3/0x200F`, `0x1037` | Undocumented / likely write-only or function rule. | Protected/unsafe. | Never write. |
| `3/0x2012` | ConfigR `FirmwareVersion`, 5 bytes; absent on QRAD and no AM6532 evidence. | ConfigR-only/absent observed profiles. | No firmware entity/update. |
| Service 4/5 fields (`0x7005–0x7008`, `0x700B`, `0x700E`, `0x701F`, `0x7020–0x7029`) | ConfigR names DSM mode, seasonal band, off-peak counter, runtime, charge mode, external controller, relay status, button operation, heating/preset setpoints, PCB status, activation, error tone. | ConfigR-only and absent on QRAD; no AM6532 evidence. | Treat as absent/unknown; no probing or writes. |

## Non-BLE cloud/API fields (do not merge into BLE matrix)

`dimplex-controller-py` documents official Dimplex Control APK 2.26.0 cloud JSON, not characteristic payloads. Exact mode flags are: Timer `0x01`, Boost `0x02`, Away `0x04`, Holiday `0x08`, Advance `0x10`, FrostProtect `0x20`, Eco `0x40`, Manual `0x80`, Hygiene `0x100`, Standalone `0x200`, SafeMode `0x400`, Shutdown `0x800`, Comms `0x1000`, Normal `0x2000`, Standby `0x4000`. `ApplianceModeSettings` fields are `ApplianceModes`, `Status` (0 inactive/1 active), `Temperature` (short °C), `Time` (Boost minutes), `Date` (Away-until), `StatusTwo`, `NumberOfDays`, and `Frequency`. Cloud action scope is QRad/Quantum/Storage Heater, not proven BLE: Boost `2`, Away `4`, Advance `16`, Frost `32` at 7 °C, Manual `128`, Eco `64`; mode pickers 7–30 °C except Away 7–18 °C; `255` means no explicit setpoint/follow schedule. Relevant endpoints are `SetApplianceMode`, `SetApplianceSetpointTemperature`, `SetSetbackTemperature`, `SetEcoStart`, `SetOpenWindowDetection`, `Get/SetTimerMode`. The source explicitly warns Quantum rejects schedule-rewrite endpoint `SetTimerMode` for mode/setpoint changes (HTTP 403), so cloud success and BLE write safety must not be conflated. (Citations: raw decompiled API reference sections 1–2; raw `client.py` methods.)

Cloud telemetry is also separate: `POST /Reports/GetTsiEnergyReportDataForHub` returns per-appliance points using keys such as `T1`/`ST` and sometimes `T2`/`energy2`; the library warns never to sum registers. This does **not** prove an AM6532 BLE energy counter or authorize exposing `3/0x000A` second word as power.

## Model/profile conflicts and absences

1. **QRAD vs AM6532 Trac:** QRAD docs say `Trac` reads `[n,active]`; AM6532 returns three bytes and its third byte correlates with PLX report-screen output percentage. This is a model-specific live correlation, not universal ConfigR `HeatDemand`.
2. **PowerLoading:** QRAD first/second LE16 words are 1000/2200 W and shown on its debug screen as G/H. AM6532 is 500/2200; only first word is corroborated by PLX050E rating. The second word's meaning is unknown for AM6532.
3. **Mode writes:** QRAD confirms six-byte mode writes and Sunhouse uses mode
   5/6 for Boost/Standby, but QRAD says 5/6 were not observed and labels the
   Sunhouse rows inferred. The local integration exposes only the bounded
   Manual, Eco, Frost Protect, Timer/Home All Day, and Away mappings verified
   on the supported PLX050E profile.
4. **Temperature sensor selector 3:** ConfigR names selector 3 core
   temperature; QRAD source documents the selector, but AM6532 returns
   zero/unavailable. Do not expose core temperature.
5. **Ambient temperature:** QRAD debug screen displays ambient but protocol
   notes state it was not exposed in any readable characteristic; selector 1 is
   the safe sensor path.
6. **Quantum dump:** raw flash binaries/photos contain no decoded packet
   specification in the repository; no fields should be inferred from binary
   artifacts here.
7. **ConfigR model universe:** the 61-parameter ConfigR table says 33 exist on
   QRAD D. Presence of a class/name is not proof of availability on
   PLX050E/AM6532.

## Recommendations for capability-matrix update

* Keep current AM6532 verified rows limited to identity, target, limits, unit,
  error, room/LCD selector reads, verified mode/preset values, rated first
  word, Sound, Adaptive Start, Open Window Detection, and model-specific Trac
  active/output correlation.
* Add explicit evidence labels distinguishing `verified live`,
  `source-documented`, `ConfigR-only`, `unknown`, `protected/unsafe`, and
  `absent`; record model/profile beside every value.
* Do not convert clock, schedule, backlight, advance, setback, runback,
  unnamed, OTA, test, security, or cloud-only values into controls without
  model-specific evidence.
* Any future command needs exact AM6532 payload identification, repeated state observations, a bounded reversible write/readback, an unambiguous semantic mapping, and a regression fixture. Keep selector operations write-then-read on one authenticated connection.
