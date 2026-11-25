# ConfigR heater parameter inventory and Home Assistant gap

This is the companion to [`configr-firmware-analysis.md`](configr-firmware-analysis.md),
and it uses the same static-only method. It lists every heater
`BluetoothParameter` that ConfigR 3.12.2 reads or writes and compares it with
what this integration exposes. It also gives the encoding recovered from
ConfigR and a recommended Home Assistant surface for each parameter.

The base catalogue (sizes, R/W flags, AM6532 presence, current HA status) is
already in [`capability-matrix.md`](capability-matrix.md) under "ConfigR
parameter catalogue". It is not repeated here. This document adds encodings,
enum values, the ConfigR screen that owns each parameter, a safety class and a
recommendation.

**Evidence rule.** A ConfigR encoding is **VERIFIED** only for how ConfigR
serializes the field. It is not AM6532 device proof. Every recommendation
below still requires the gates in capability-matrix.md "Next evidence required
before adding features": live read in two states, bounded reversible write
with readback, and a test fixture.

## How ConfigR defines a parameter (VERIFIED)

Each parameter class calls `BluetoothParameter.ctor(bluetoothParamId,
parameterId16Bit, alternativeBluetoothParamId, serviceId, alternativeServiceId,
parameterSize, isReadable, isWriteable, isProfileParam, isDiagnosticV2Param,
isDiagnosticOnlyParam)`. The parameter names come from the metadata Param
table.

- The service UUID `00000000-000G-1000-8000-00805F9B34FB` gives group `G`.
- The GLNDIMPLEX alias `00000000-000(G+1)-474C-4E44-494D504C4558` is the
  fallback.
- 73 parameter classes exist.

ConfigR does not hold a per-family parameter table in the app; product models
come from the cloud (`/api/Appliances/GetProductModels`). Family applicability
below therefore uses:

- the ConfigR page that uses the parameter (VERIFIED from view-model
  references);
- AM6532 (PLX050E Series C) presence from capability-matrix.md.

Pages and the parameters they use (VERIFIED, counts of references in each view
model):

| ConfigR page | Parameters |
|---|---|
| `ProductModePageViewModel` | HeatingMode, CurrentSetTemperature, RunbackTime, AdvanceHeating, ScheduleAndSetTemperature, ButtonPressOperation, Runback, PresetButtonSetpoint, HeatingModeSetpoint, AwayMode, LastKeyPressValue, SetpointRange, FunctionRule, RunbackSetpoint |
| `GeneralSettingsPageViewModel` | Setback, PreEmptiveHeating, Sound, RfComms, OpenWindowDetection |
| `AdvancedSettingsPageViewModel` | SetpointRange, AdditionalCharge, SpConfig, PowerLoading, TemperatureUnit, SlaveDevice, Setback, RunbackSetpoint, AwayMode, Security, FactoryReset |
| `LockSettingsPageViewModel` | FunctionRule, Security |
| `BoostSettingsPageViewModel` | Runback, RunbackSetpoint |

The settings menu enum lists the user-facing feature names (VERIFIED):

- `EGeneralSettings {Setback, OpenWindow, AdaptiveStart, RFComms, PinLock, LockBoostTemp, LockBoostDuration, Advance, Wifi, MaxBoostDuration}`
- `EEditAdvancedSettings {MaxTemp, MinTemp, TemperatureUnit, OffSet, AdditionalCharge, ChargeTimes, PowerLoading, FactoryReset, SlaveDevice}`

## Already exposed by the integration

| Integration surface | Parameter(s) |
|---|---|
| Climate target | `0/0x1023` CurrentSetTemperature |
| Climate presets | `0/0x1001` HeatingMode (manual, eco, frost_protect, home_all_day, user_timer, away) |
| Away return-time datetime | `0/0x102A` AwayMode |
| Room and LCD temperature sensors | `1/0x0008` TemperatureSensor (selector 1 = room, 2 = LCD) |
| Output power and estimated power sensors | `1/0x0002` Trac |
| Rated power | `3/0x000A` PowerLoading word 1 |
| Error code | `0/0x1011` ErrorStatus |
| Operating mode sensor | raw `0x1001` |
| Adaptive start switch | `0/0x1008` PreEmptiveHeating |
| Open window detection switch | `0/0x1007` OpenWindowDetection |
| Sound switch | `2/0x0005` Sound |
| Display unit select | `0/0x1005` TemperatureUnit |
| Estimated energy (kWh) | estimated power integrated over time; there is no device counter |
| `DeviceInfo.sw_version` | `3/0x2001` UI and `3/0x2009` RF |

## Missing high-value user features (ranked)

| # | Feature (ConfigR label) | Parameter / address | Encoding (VERIFIED from ConfigR (de)serializer and enums) | R/W | AM6532 | Safety | Recommendation |
|---|---|---|---|---|---|---|---|
| 1 | **Boost** (ConfigR "Boost", internally Runback) | `0/0x1017` Runback, `0/0x1027` RunbackTime, `0/0x1028` RunbackSetpoint | `0x1017` = `[IsActive bool, Duration]`; the duration enum `ERunbackParamTime` (INFERENCE that it is this byte). Durations: `0`=30 min, `1`=60, `2`=90, `3`=120, `18`=180, `4`=240, `5`=360, `6`=480. `0x1027` = remaining minutes, 16-bit (byte order unverified). `0x1028` = boost setpoint (2 bytes; scale unverified). Mode enum also has `HeatingMode.Mode Boost=5` | RW | present (all three) | safe user setting | **Implemented 2026-11-25** as the climate Boost preset: `0x1027` = minutes (LE16, 1–240) then `0x1023` = `0x1028`'s temperature; `0x1027` = 0 ends it (verified on AM2086; docs/configr-clock-schedule-boost.md). `0x1017` and `Mode=5` are not used |
| 2 | **Setback** (General settings) | `0/0x1016` Setback | `[IsActive bool, Temperature]`; temperature = whole °C, 7–18 | RW | present | safe user setting | **Implemented**: switch (2026-11-24) and number "Setback temperature" 7–18 °C (2026-11-25). Scale verified on AM2086: display 8 °C read `0x08`; a written `0x0F` showed 15 °C; restored. |
| 3 | **Advance** | `0/0x100D` AdvanceHeating | `EAdvanceHeatingParamMode`: `Off=0`, `On=1`, `1–4 h boost=2–5`, `AutoBoost=6`, `Ah1–4 h=12–15`, `1–12 h=0x81–0x8D` | RW | present | safe user setting | `switch` "Advance" (0/1 only) Verified on hardware (2026-11-24); refused outside timer modes. |
| 4 | **Out All Day / Holiday timer modes** | `0/0x1001` byte 1 | `TimerMode: OutAllDay=1, HomeAllDay=2, UserTimer=3, Holiday=4, Away=5`. Byte 0 `Mode: Timer=1, Manual=2, Eco=3, Frost=4, Boost=5, Standby=6`. Bytes 2–4 = `TemperatureSetting (Eco=1/Low=2/High=3)`, `Duration (30 min=1/1 h=2/2 h=3)`, `Temperature` | RW | present | safe user setting | add presets `out_all_day` (`01 01`) and, if verified, `holiday`. `Standby=6` maps to `HVACMode.OFF` only after a device test Out All Day verified on hardware (2026-11-24). |
| 5 | **Schedules / timer programmes** | `0/0x1002` ScheduleAndSetTemperature (31 bytes) | `[selector, …]`. Selector `1/2/3` = OutAllDay/HomeAllDay/UserTimer `TimerModeDetails`, each with 7 `DayData` of `Period(StartTime, EndTime, Temperature)`. One 31-byte (programme, day) record per read, selected by writing `[0, programme, day]`; layout in [`schedule-format.md`](schedule-format.md) (the `0x1F` was the record length, not a mask) | RW | present | user setting, but complex and easy to corrupt | calendars 2026-11-24; single-day save (`dimplex_ble.set_schedule`) 2026-11-25 |
| 6 | **Room-temperature offset (calibration)** ("OffSet") | `2/0x1019` SpConfig | one signed byte, tenths of °C: `b≤100 → +b/10`, else `(b−256)/10` | RW | present | installer setting | `number` (config, °C, step 0.1, plausible ±10, disabled by default) Verified on hardware (2026-11-24): ±3.0 °C in 0.1 steps; the manual's ±0.2 is not a device limit. |
| 7 | **Max / min setpoint** ("MaxTemp/MinTemp") | `0/0x1006` SetpointRange | `[Min @0, Max @2]` | RW | present (read, mapped to climate limits) | installer setting | `number` ×2 (config, disabled by default). Writes need range-safety tests Writes acknowledged but not applied on PLX050E Series C (hardware 2026-11-24) — not exposed. Read only, for the climate min/max. |
| 8 | **Boost/advance lock** ("LockBoostTemp", "LockBoostDuration", "Advance" lock) | `3/0x1037` FunctionRule (3 bytes) | bit array: bit0 `BoostTemperatureAdjustable`, bit1 `BoostDurationAdjustable`, `AdvanceAdjustable` in a later bit | RW | absent on AM6532 inventory | installer setting | do not expose for AM6532 (absent). Families with it: 3 config `switch`es, disabled |
| 9 | **PIN lock / key lock** ("PinLock") | `2/0x1033` Security (33 bytes) | `ESecurityStatus {NoSecurity=0, AuthorizationRequired=1, Authorized=2, IvWrite=3, Authorize=4, SetAuthorizationRequired=5, SetAuthorizationNotRequired=6}` + 32 random bytes | RW | absent | security material | **do not expose** (authentication state) |
| 10 | **Preset temperatures 1/2 and Manual/Boost setpoints** | `3/0x7022` PresetButtonSetpoint, `3/0x7021` HeatingModeSetpoint | 4 bytes `[ReadWrite, Button(Preset2=3, Preset1=4) or Mode(Manual=2, Boost=5), int °C, hundredths]`. Selector-style read/write | RW | absent | safe user setting | not on AM6532. Other families: `number` per preset |
| 11 | **Eco / Comfort / Frost setpoints** | no dedicated parameter. Eco/Frost targets come from mode semantics (`TemperatureSetting Eco=1/Low=2/High=3` in `0x1001`) | — | — | — | — | nothing to add beyond presets. Comfort is a `ButtonPressOperation` type, not a setpoint |
| 12 | **Away target temperature** | `0/0x102A` byte 6 | `[IsActive, date bytes 1–5, Temperature]` | RW | present | safe user setting | `number` "Away temperature" (config). The datetime is already exposed Verified on hardware (2026-11-24); the heater enters Away on ANY write of `0/0x102A`, so it is writable only while Away is on. |
| 13 | **Heat demand** | `3/0x1024` HeatDemand | `[Band, Temperature]` (enum `HeatDemandOffset` in `eSetTemperatureModes`) | RW, diagnostic-only flag | absent | installer | not on AM6532. Elsewhere: diagnostic sensor, disabled |
| 14 | **Wi-Fi / RF comms** ("Wifi", "RFComms") | `2/0x100C` RfComms (1 byte bool) | resource text says Wi-Fi may need additional hardware. **INFERENCE:** the Wi-Fi toggle is this parameter | RW | present (read) | installer/connectivity | keep the read-only diagnostic. Do not write (could cut a hub link) |
| 15 | **Firmware version** | `3/0x2001` selectors (`ESwVersionComponent CC=0, UI=1, RF=2`), `3/0x2012` FirmwareVersion (`CcMajor/Minor/RandD/Cloud`, `UiMajor/Minor/RandD/Cloud`) | `0x2012` 5 bytes (Zigbee products) | R | `0x2001` present, `0x2012` absent | safe | already in `DeviceInfo.sw_version`; optionally a diagnostic `sensor`. No `update` entity (firmware is cloud-delivered, see the firmware analysis) |
| 16 | **Error tone** | `3/0x7029` ErrorTone | `NoTone=0, SingleTone=1, DualTone=2` | RW | absent | safe | not on AM6532 |
| 17 | **Button operation mapping** | `0/0x7020` ButtonPressOperation | `[ButtonNumber, EOperationType, EOperation(Off=0/On=1)]`. `EOperationType`: `Comfort=1, Eco=2, Standby=3, Boost=4, PairingMode=5, HalfHeat=6, FullHeat=7, Advance=8, Buzzer=9, Preset1=10, Preset2=11` | RW | absent | installer | do not expose. `PairingMode=5` is a pairing trigger |
| 18 | **Sound tone/volume** | `2/0x0005` bytes 1–2 | `[IsActive, Tone, Volume]` | RW | present | safe | optional config `select`/`number` once ranges are tested. The on/off switch already exists |

## Remaining ConfigR parameters (lower value, or not for panel heaters)

| Parameter / address | ConfigR meaning (VERIFIED) | Safety | Recommendation |
|---|---|---|---|
| `0/0x0006` SetRtc (7 B), `0/0x0106` SetRtcWithSeconds (8 B) | phone clock written once per connect if drift >60 s | safe, but it is the device clock | **Implemented 2026-11-25**: read on connect (hourly on a held link), written when drift >60 s, option on by default; no entity |
| `1/0x0007` LastKeyPressValue | `Auto=21, Off=22, Manual=23` | safe | an existing diagnostic could map these names |
| `0/0x4012` HeatSetting | `NoHeat=0, HalfHeat=1, FullHeat=2, FanOnly=4` | safe | absent on AM6532 |
| `4/0x7008` Runtime | `[mode, 16-bit minutes]`, modes Daily/Critical/Incremental/Adaptive/Fan | diagnostic | absent |
| `3/0x7024` PcbStatus (12 B) | `[TestStatus, Major, Minor, …]` | diagnostic | absent |
| `1/0x0003` Relay, `1/0x701F` RelayStatus | relays `CoreElement1–3`, `BoostElement`, `FanElement` | diagnostic | absent (storage heaters) |
| `3/0x101B` HeaterSize, `2/0x101C` ChargeTimes, `2/0x101E` AdditionalCharge (0–7 h), `4/0x7005` DsmMode, `4/0x7006` SeasonalBand, `4/0x7007` OffpeakCounter, `1/0x000E` OffpeakStatus, `5/0x700B` ChargeMode, `5/0x700E` ExternalController | storage-heater charge control | installer | not panel heaters |
| `2/0x101D` SlaveDevice, `2/0x1034` SlaveInfo (74 B) | wall-controller/slave linking | installer, linking state | do not expose |
| `0x8002–0x8016` (group `0x1000`) | fan, ventilation and humidity parameters | — | not heaters |
| `0/0x7026` ActivateProduct (8 B) | one-time activation; product "will disconnect and enter manual mode" | dangerous | do not expose |
| `1/0x1020` TestMode, `3/0x1010` EolTest | factory test | dangerous | keep blocked |
| `2/0x1030` FactoryReset | factory reset | dangerous | keep blocked |
| `3/0x2002` Gdid, `3/0x2006` Btpk, `3/0x2007` EncKey, `3/0x2008` AuthKey, `2/0x1033` Security | identity and credential material | security material | keep blocked |
| `3/0x2004` Brand, `3/0x2005` HeaterType, `3/0x200A` EpIdentifier | metadata (`<Dimplex>`, `PLX050E;C;`, `EP4213`) | safe | already diagnostics/metadata; `EpIdentifier` could go into `DeviceInfo.hw_version` |
| `1/0x100E` OtaEnable, `1/0x300E` Esp32OtaEnable, OTA command chars `…C51C`/`…C51D` | firmware update entry (see the firmware analysis) | dangerous (OTA) | keep blocked |
| `3/0x000A` PowerLoading bytes 2–7 | `Element1Power`, `Element2Power` | installer write | read-only element 2 power could become a diagnostic; never write |

## Suggested next steps (ordered by user value and evidence cost)

1. **Boost (Runback `0x1017/0x1027/0x1028`) and Advance (`0x100D`).**
   - Read all three on AM6532 in idle and boosted states (button on the
     heater).
   - Then make one bounded write of `0x1017=[01, 00]` (30 min) with readback,
     and cancel it with `[00, xx]`.
2. **Out All Day preset** (`0x1001 = 01 01 …`): same procedure as the verified
   Home All Day/User Timer writes.
3. **Setback** (`0x1016`) and **Away temperature** (`0x102A` byte 6):
   determine the temperature byte scale from two reads at different device
   settings.
4. **Temperature offset** (`2/0x1019`): read only first. Compare against a
   change made on the device's own menu.
5. Schedules stay research-only until the 31-byte layout is mapped from
   `TimerModeDetails`/`DayData` with live captures.
