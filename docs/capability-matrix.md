# Dimplex capability matrix

This matrix is the implementation boundary for the AM6532 panel heater currently
commissioned through the integration. ConfigR is the source of truth for product
terminology and lifecycle behavior. A ConfigR label is not, by itself, proof that
the AM6532 exposes a safe packet or command; those rows remain unknown until the
packet mapping and device behavior are verified.

## Evidence classes

- **LIVE-AM6532**: read from the commissioned panel heater through the Home
  Assistant raw-GATT action on 2026-11-19, then confirmed by the normal coordinator poll.
- **CONFIGR-PROFILE**: recovered from the official ConfigR Native AOT resource
  bundle. It describes product profiles and diagnostics supported by the app, not
  necessarily every model.
- **IMPLEMENTED**: exposed by this integration today.
- **SOURCE-DOCUMENTED**: exact layout/value from a requested repository, but not
  live-verified on this PLX050E.
- **CONFIGR-ONLY**: name/profile/resource evidence without an AM6532 packet.
- **PROTECTED/UNSAFE**: security, credential, OTA, factory, EOL, test, or
  undocumented write boundary; values are not probed or reproduced.
- **CLOUD-ONLY**: Dimplex Control cloud/API fields from `dimplex-controller-py`.
- **ABSENT**: not present in the cited device/profile inventory; scope is
  limited to that inventory and is not a universal product claim.

No MAC address, PIN, or credential is included in this document.
Detailed source comparison is recorded in
[`capability-matrix-source-research.md`](capability-matrix-source-research.md).

## Source and transport boundaries

This document combines evidence from different transports without conflating
their capabilities:

| Source | Transport/scope | What it proves here |
|---|---|---|
| [ConfigR extraction](configr.md) and `/tmp/configr-apk/` | Native AOT installer application and resources | Authoritative ConfigR parameter names, product-profile terminology, commissioning lifecycle, and resource labels. The extraction does not prove an AM6532 characteristic mapping. |
| [Q-Rad BLE bridge](https://github.com/philcluff/dimplex-qrad-smart-bridge), especially [`ble-protocol.md`](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md) | Direct BLE GATT on QRAD E Series D | Characteristic addresses, observed payloads, selector exchanges, confirmed Q-Rad behavior, and explicitly marked inferences. |
| [Sunhouse ESPHome project](https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant/blob/main/sunhouse-heater.yaml) | Direct BLE GATT on a different Dimplex/Sunhouse product | Repeated protocol-family layouts and candidate schedule/mode/boost writes. Its Q-Rad applicability is not assumed. |
| [Dimplex Quantum board dump](https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump) | PCB photographs and flash dump | Hardware architecture context only; it contains no verified AM6532 GATT field semantics. |
| [dimplex-controller-py](https://github.com/KRoperUK/dimplex-controller-py) | Dimplex Control cloud API via Azure B2C | Cloud appliance/status/schedule/energy field names and values. It is a separate hub/cloud path, not evidence of a local AM6532 BLE field. |
| **LIVE-AM6532** | This integration's authenticated raw-GATT reads | Exact PLX050E Series C inventory and payloads recorded on 2026-11-19. This is the strongest evidence for this integration's device mapping. |

The same numeric characteristic can therefore appear in more than one source
with different confidence. A row marked **CONFIGR-ONLY**, **Q-RAD-ONLY**,
**SUNHOUSE-ONLY**, or **CLOUD-ONLY** is not a supported AM6532 capability.

## ConfigR parameter catalogue

ConfigR's extracted parameter model defines 61 named parameters. The table
below preserves the complete catalogue and separates three scopes:

- **Q-Rad D** is the upstream bridge's reported presence on its QRAD100E
  Series D capture.
- **AM6532** is the live PLX050E Series C inventory recorded in this
  repository.
- **HA status** states whether this integration maps the field, keeps it as
  raw diagnostic evidence, or deliberately blocks it.

ConfigR names are authoritative terminology, not automatic packet proof.

| Group/char | Length | Access | ConfigR name | Q-Rad D | AM6532 | HA status |
|---|---:|---|---|---|---|---|
| `1/0x0002` | 3 | R | Trac | yes | present | Mapped through selector write/read; active flag and PLX output correlation verified |
| `1/0x0003` | 2 | R | Relay | no | absent | Not available |
| `2/0x0005` | 3 | RW | Sound | yes | present | Verified `00 00 00`/`01 00 00` boolean; exposed as the Sound configuration switch and refreshed on an independent five-minute schedule |
| `0/0x0006` | 7 | W | SetRtc | yes | present | Raw read observed; no clock entity/write |
| `1/0x0007` | 1 | R | LastKeyPressValue | yes | present | Read-only diagnostic sensor; transient UI event |
| `1/0x0008` | 3 | R | TemperatureSensor | yes | present | Selector 1 room and selector 2 LCD mapped; selector 3 unavailable |
| `3/0x000A` | 8 | RW | PowerLoading | yes | present | First LE16 rated power mapped; remaining bytes retained/unknown; writes blocked |
| `1/0x000E` | 1 | R | OffpeakStatus | no | absent | Not available |
| `0/0x0106` | 8 | W | SetRtcWithSeconds | no | absent | Not available |
| `0/0x1001` | 6 | RW | HeatingMode | yes | present | BLE reads confirm all four PLX modes. Manual (`02`), Eco Mode (`03`), Frost Protect (`04`), and Timer Mode/User Timer (`01 03`) writes were accepted and read back; climate maps Timer Mode to `AUTO` and the other verified modes to `HEAT` with presets |
| `0/0x1002` | 31 | RW | ScheduleAndSetTemperature | yes | present | Read buffer observed; schedule structure/write not verified on AM6532 |
| `0/0x1005` | 1 | RW | TemperatureUnit | yes | present | Celsius observed; reversible Fahrenheit write/readback verified; exposed as a configuration select |
| `0/0x1006` | 4 | RW | SetpointRange | yes | present | Min/max mapped; write blocked by evidence boundary |
| `0/0x1007` | 9 | RW | OpenWindowDetection | yes | present | Verified configuration switch; `00/01` in the first byte with eight zero bytes, read/write/readback verified on AM2086; behavior matches the official [PLXE Series C manual, Issue 7](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf) |
| `0/0x1008` | 1 | RW | PreEmptiveHeating | yes | present | Verified configuration switch; `00/01` boolean read/write/readback verified on AM2086; Dimplex documents this behavior as Adaptive Start in the same manual |
| `2/0x100C` | 1 | RW | RfComms | yes | present | Read-only diagnostic sensor; writes blocked |
| `0/0x100D` | 1 | RW | AdvanceHeating | yes | present | Raw read only; reversible AM6532 command unverified |
| `1/0x100E` | 1 | RW | OtaEnable | yes | absent | Not available on live AM6532 inventory |
| `1/0x1010` | 1 | RW | EolTest | yes | present | Read-only diagnostic sensor; writes blocked |
| `0/0x1011` | 1 | R | ErrorStatus | yes | present | Error-code sensor mapped |
| `0/0x1016` | 2 | RW | Setback | yes | present | Raw read only; units/write semantics unverified |
| `0/0x1017` | 2 | RW | Runback | yes | present | Raw read only; units/write semantics unverified |
| `2/0x1019` | 1 | RW | SpConfig | yes | present | Raw read only; semantics unverified |
| `3/0x101B` | 1 | R | HeaterSize | no | absent | Not available |
| `2/0x101C` | 16 | RW | ChargeTimes | no | absent | Not available |
| `2/0x101D` | 1 | RW | SlaveDevice | no | absent | Not available |
| `2/0x101E` | 1 | RW | AdditionalCharge | no | absent | Not available |
| `1/0x1020` | 1 | RW | TestMode | yes | present | Read-only diagnostic sensor; writes blocked |
| `0/0x1023` | 2 | RW | CurrentSetTemperature | yes | present | Native target mapped; whole-degree writes verified |
| `3/0x1024` | 4 | RW | HeatDemand | no | absent | No read or entity; distinct from Trac byte |
| `0/0x1027` | 2 | RW | RunbackTime | yes | present | Raw countdown only |
| `0/0x1028` | 2 | RW | RunbackSetpoint | yes | present | Raw read only; not a second HA target |
| `0/0x102A` | 8 | RW | AwayMode | yes | present | Raw inactive payload; no AM6532 command |
| `2/0x1030` | 1 | W | FactoryReset | no | absent | Blocked/not available; never probe |
| `2/0x1033` | 33 | RW | Security | no | absent | Protected; never expose |
| `2/0x1034` | 74 | RW | SlaveInfo | no | absent | Protected/not available |
| `3/0x1037` | 3 | RW | FunctionRule | no | absent | Protected/not available |
| `3/0x2001` | 3 | R | SwVersion | yes | present | Selector 1 UI revision 1.7 observed and mapped to `DeviceInfo.sw_version`; other selectors placeholder |
| `3/0x2002` | 6 | RW | Gdid | yes | present | Protected read; identifier redacted |
| `3/0x2004` | 20 | RW | Brand | yes | present | `<Dimplex>` observed; metadata only |
| `3/0x2005` | 6 | RW | HeaterType | yes | present | `PLX050E;C;` observed; model metadata |
| `3/0x2006` | 5 | RW | Btpk | yes | present | Protected credential material; blocked |
| `3/0x2007` | 16 | RW | EncKey | yes | present | Protected credential material; blocked |
| `3/0x2008` | 16 | RW | AuthKey | yes | present | Protected credential material; blocked |
| `3/0x200A` | 6 | RW | EpIdentifier | yes | present | `EP4213` observed; `DeviceInfo.model_id` |
| `3/0x2012` | 5 | RW | FirmwareVersion | no | absent | Not available |
| `1/0x300E` | 1 | RW | Esp32OtaEnable | no | present | Raw `00`; writes blocked; not an update capability |
| `0/0x4012` | 1 | R | HeatSetting | no | absent | Not available |
| `4/0x7005` | 1 | R | DsmMode | no | absent | Not available |
| `4/0x7006` | 1 | R | SeasonalBand | no | absent | Not available |
| `4/0x7007` | 2 | R | OffpeakCounter | no | absent | Not available |
| `4/0x7008` | 3 | R | Runtime | no | absent | Not available |
| `5/0x700B` | 3 | RW | ChargeMode | no | absent | Not available |
| `5/0x700E` | 4 | RW | ExternalController | no | absent | Not available |
| `1/0x701F` | 9 | R | RelayStatus | no | absent | Not available |
| `0/0x7020` | 3 | RW | ButtonPressOperation | no | absent | Not available |
| `3/0x7021` | 4 | RW | HeatingModeSetpoint | no | absent | Not available |
| `3/0x7022` | 4 | RW | PresetButtonSetpoint | no | absent | Not available |
| `3/0x7024` | 12 | R | PcbStatus | no | absent | Not available |
| `0/0x7026` | 8 | RW | ActivateProduct | no | absent | Not available |
| `3/0x7029` | 1 | RW | ErrorTone | no | absent | Not available |

## Cross-repository candidate fields and writes

These values/layouts are useful protocol-family evidence from the Sunhouse
ESPHome project and Q-Rad notes, but they are not AM6532 verification:

| Candidate | Source-documented value/layout | Scope and decision |
|---|---|---|
| Boost mode | Sunhouse writes `0/0x1001 = [5,3,0,0,0,0]`, then writes the boost hours to `0/0x100D`; Q-Rad notes call this inferred and record `0x1001` byte 3 as the boost-running flag | SUNHOUSE/Q-RAD candidate only; no AM6532 command |
| Standby mode | Sunhouse comments use `0/0x1001 = [6,3,0,0,0,0]` | Not verified on Q-Rad or AM6532; no command |
| Advance | Sunhouse writes `0/0x100D = [1]` to engage and `[0]` to cancel; Q-Rad confirms read value `1`/`0` in timer modes | Read semantics are Q-Rad evidence; AM6532 write remains unverified |
| Schedule read | Sunhouse writes `0/0x1002 = [0,timerMode,day]`, waits, then reads 31 bytes | Inferred protocol-family layout; AM6532 returned zero buffer only |
| Schedule write | Sunhouse writes `[1,timerMode,day]` plus four periods of `[startH,startM,endH,endM,temp,0]` and four padding bytes | Not verified on AM6532; no calendar entity |
| Clock keep-alive | Sunhouse writes current time to `0/0x0006` every 3 seconds; its byte order is `[seconds,minutes,hours,day-of-week,day,month,year-offset]` | Conflicts with Q-Rad/ConfigR clock layout; do not copy to AM6532 |
| BLE security setup | Sunhouse uses keyboard-only I/O, MITM + bonding, and a passkey callback | Pairing architecture evidence only; no panel-heater field |
| One-central behavior | Q-Rad notes one central at a time; advertising and bond persistence were observed on QRAD100E | Device/profile behavior, not a GATT capability mapping |

## Quantum board-dump evidence

The requested Quantum dump repository contains PCB photographs and two
identical 2 MiB SPI-flash dumps. It does not contain a decoded BLE packet map,
characteristic trace, model capability table, or verified field value. Its
contribution is hardware context only; no field, firmware version, power value,
or command is inferred from the binary contents.

## Protocol inventory

| Capability | Evidence | Status | Integration decision |
|---|---|---|---|
| Stable product identity | LIVE-AM6532: `3/0x2005` returned `PLX050E;C;`; `3/0x200a` returned product code `EP4213`; Home Assistant registry identity is the configured Bluetooth address | REQUIRED | Use the configured panel-heater identity and product code as model metadata; do not use the proxy address as the device identity |
| Device topology | LIVE-AM6532: one panel heater per config entry; no hub/service topology observed | REQUIRED | One config entry and one device per panel heater |
| Current room temperature | LIVE-AM6532: selector `1/0x0008` returned a Celsius room-temperature reading used by the coordinator | REQUIRED | `ClimateEntity.native_current_temperature` |
| Native temperature unit | LIVE-AM6532: display-unit characteristic `0/0x1005` returned Celsius; ConfigR documents Celsius/Fahrenheit display selection | REQUIRED | Keep climate values in native Celsius; expose display-unit selection separately |
| Target temperature | LIVE-AM6532: `0/0x1023` returned a whole-degree Celsius setpoint | REQUIRED | `ClimateEntity.native_target_temperature`; write only whole degrees |
| Target limits | LIVE-AM6532: `0/0x1006` returned minimum and maximum bounds | REQUIRED | `min_temp` and `max_temp` come from the panel heater |
| Target step/precision | LIVE-AM6532: setpoint encoding and readback accept whole degrees only | REQUIRED | Step and precision are one Celsius degree |
| Target range | No low/high pair observed; only one setpoint characteristic | NOT SUPPORTED | Do not advertise `TARGET_TEMPERATURE_RANGE` |
| Current humidity | ConfigR has generic humidity resources for other product families; no AM6532 field was identified | NOT SUPPORTED | No humidity entity or climate humidity feature |
| Target humidity and humidity limits | No AM6532 protocol field identified | NOT SUPPORTED | No humidity control |
| Power/on/off control | No safe AM6532 power command identified; ConfigR's storage-heater profile describes heating modes rather than a generic power switch | UNKNOWN/NEEDS CONFIRMATION | Do not advertise `TURN_ON` or `TURN_OFF` |
| HVAC operating modes | LIVE-AM6532 and controlled AM2086 reads/writes identify Timer Mode, Manual, Eco Mode, and Frost Protect. ConfigR documents Timer as weekly schedule control; the safe Timer/User Timer payload is `01 03`, while arbitrary schedule-buffer writes remain out of scope | SUPPORTED-CONDITIONAL | Map Timer Mode to `HVACMode.AUTO`; map Manual, Eco Mode, and Frost Protect to `HVACMode.HEAT` with translated presets |
| HVAC action | LIVE-AM6532: selector `1/0x0002` returned `010164` while the panel heater was actively heating and `010000` after it became idle; ConfigR and Q-Rad research define byte 1 as the Trac active flag | SUPPORTED-CONDITIONAL | Report `HEATING` or `IDLE` only from the Trac active flag; return no action when the response is unavailable or invalid |
| Presets | LIVE-AM6532/AM2086 verified Manual, Eco Mode, and Frost Protect payloads; Eco and Frost were display-confirmed and all writes were read back | SUPPORTED-CONDITIONAL | Expose translated Manual, Eco, and Frost Protect presets; Timer is represented by `HVACMode.AUTO` |
| Fan modes | ConfigR fan resources are associated with ventilation product families; no AM6532 fan control/status was identified | NOT SUPPORTED | No fan feature |
| Vertical swing | No AM6532 swing field identified | NOT SUPPORTED | No swing feature |
| Horizontal swing | No AM6532 swing field identified | NOT SUPPORTED | No horizontal-swing feature |
| Schedules | CONFIGR-PROFILE documents weekly timer profiles for supported Quantum products. No safe AM6532 schedule read/write mapping has been recovered | UNKNOWN/NEEDS CONFIRMATION | No calendar or schedule entity |
| Occupancy | No AM6532 occupancy field identified | NOT SUPPORTED | No occupancy entity |
| Fault/error state | LIVE-AM6532: `0/0x1011` returned error code `0`; ConfigR documents heater fault-code diagnostics | SUPPORTED-CONDITIONAL | Diagnostic error-code sensor; keep raw/unknown codes visible |
| Element status | CONFIGR-PROFILE names element 1 and element 2 status, but the live `1/0x0002` reads for selectors 1 and 2 mirrored the same global state; this does not prove independent element status | UNKNOWN/NEEDS CONFIRMATION | No separate element entities |
| LCD/controller temperature | LIVE-AM6532: selector `2/0x0008` returned `021d00` (29.0 °C); selector 3 returned zero/unavailable | SUPPORTED-CONDITIONAL | Disabled-by-default diagnostic LCD-temperature sensor |
| Output power | Official Dimplex PLX Series C support documentation defines report-screen field C as output power. On the live PLX050E, Trac selector 1 and 2 both returned `010164`/`020164` at a forced 30 °C target and `010000` after returning idle; the third byte therefore tracks the report-screen output-power value for this model (`100` active, `0` idle) | SUPPORTED-CONDITIONAL | Expose a disabled-by-default diagnostic output-power percentage sensor and retain the raw Trac response; do not treat it as measured watts |
| ConfigR HeatDemand characteristic | ConfigR names `3/0x1024`, but it is absent from the live PLX050E inventory; this is distinct from the Trac output-power byte mapped above | UNKNOWN/NEEDS CONFIRMATION | Do not issue a `3/0x1024` read or expose a second heat-demand entity |
| Rated output | LIVE-AM6532: `3/0x000A` returned `f401980800000000`; first LE16 word is `500`, matching the official PLX050E 500 W rating | SUPPORTED-CONDITIONAL | Disabled-by-default diagnostic rated-power sensor; retain the complete raw payload |
| Calculated power consumption | The output percentage and rated output are both heater-provided values, so `rated_power_w × output_power_percent ÷ 100` is a deterministic estimate of instantaneous electrical power | SUPPORTED-CONDITIONAL | Expose an enabled `POWER`/W measurement sensor for Energy Dashboard power inputs; label it calculated, not metered |
| Cumulative energy | The protocol provides no energy counter or meter total | NOT SUPPORTED | Let users create an HA Integration (Riemann sum) energy sensor from the calculated power entity; do not invent a device-reported energy total |
| Power/energy measurements | LIVE-AM6532 exposes a second `0x000A` word of `2200`, but its PLX050E meaning and measurement semantics are undocumented | UNKNOWN/NEEDS CONFIRMATION | Do not expose instantaneous power from that second word or any direct energy sensor |
| Firmware/software information | ConfigR can display and update firmware for supported products. The local safe read/update protocol is not recovered | UNKNOWN/NEEDS CONFIRMATION | No firmware sensor/update entity |
| Firmware update | No safe local update command is identified | OUT OF SCOPE | Never expose a guessed firmware-write operation |
| Push/subscription | LIVE-AM6532 GATT inventory exposed read/write characteristics but no verified notification stream for the mapped fields | UNKNOWN/NEEDS CONFIRMATION | Poll through one coordinator |
| Polling | LIVE-AM6532 coordinator polls successfully at the integration interval | SUPPORTED-CONDITIONAL | Use the integration-controlled coordinator interval; do not expose it as a user option |
| Command concurrency | The protocol exposes one connected GATT client per heater; the integration serializes each client and shares proxy capacity | SUPPORTED-CONDITIONAL | Serialize per heater and bound shared-proxy slots |
| Runtime device addition/removal | No hub or account device list observed | OUT OF SCOPE | Do not manufacture dynamic-device behavior |
| Authentication expiry | BLE bond/passkey commissioning is required; no expiring account credential exists in the local path | SUPPORTED-CONDITIONAL | Pair once, reuse the bond, and report connection failure without repinning loops |
| Privacy-sensitive fields | Device identifiers and passkeys are sensitive; raw service responses can contain undocumented values | REQUIRED | Redact diagnostics and block known security-sensitive characteristics |

## Additional safe reads verified live

These values were read from the commissioned PLX050E on 2026-11-19 through the
administrator-only raw-GATT action. They are documented here so raw observations
remain separate from implemented Home Assistant semantics.

| Address | ConfigR name/evidence | Live value | Decision |
|---|---|---|---|
| `0/0x1003` | Effective/current setpoint mirror | `1400` at a 20 °C target | Duplicate of the climate target; no second target entity |
| `0/0x1007` | OpenWindowDetection | `010000000000000000` | Configuration switch; disabled-to-enabled readback verified on AM2086; manual confirms global interruption of active modes and Heating Off behavior |
| `0/0x1008` | PreEmptiveHeating | `01` | Configuration switch; disabled-to-enabled readback verified on AM2086; manual confirms Adaptive Start behavior in Timer Mode |
| `0/0x100D` | AdvanceHeating | `00` | Readable status; reversible command not tested on this model |
| `0/0x1016` | Setback | `0008` | Configuration value; unit/meaning not verified |
| `0/0x1017` | Runback | `017c` | Configuration value; encoding not verified |
| `0/0x1027` | Runback/boost countdown | `0000` | No active runback/boost observed |
| `0/0x1028` | Runback/boost setpoint | `1500` | Readable default; not exposed as a duplicate target |
| `0/0x102A` | AwayMode | inactive payload | No Away command or calendar entity without a bounded model-specific test |
| `1/0x0007` | LastKeyPressValue | `06` | UI event diagnostic; too transient for a normal entity |
| `2/0x100C` | RfComms | `01` | Readable configuration flag; control semantics not verified |
| `3/0x2001` selector 1 | SwVersion UI | `010107` | UI revision `1.7` observed; selector 0/2 returned unavailable/placeholder values |
| `3/0x2009` | RF firmware revision | `05` | RF revision `5` observed and combined with UI revision in `DeviceInfo.sw_version` |
| `3/0x200A` | EpIdentifier/product code | `EP4213` | Product code observed; not a user control |
| `3/0x200D` | Board/firmware code | `PGLHS600` | Board code observed; not interpreted as a semantic firmware version |
| `3/0x000B`, `3/0x000C` | Calibration-like fields; exact semantics unknown | Value not retained in the audit capture | Disabled-by-default read-only raw sensors; no write path |
| `1/0x1010` | Unnamed/test-adjacent field | `47` | Disabled-by-default read-only raw sensor; writes blocked |
| `1/0x1020` | `TestMode` | `00` | Disabled-by-default read-only raw sensor; writes blocked |
| `1/0x300E` | `Esp32OtaEnable` | `00` | Disabled-by-default read-only raw sensor; not an update entity |
| `2/0x100C` | `RfComms` | `01` | Disabled-by-default read-only raw sensor; writes blocked |
| `2/0x700F` | Disconnect-address/status candidate | `00` | Disabled-by-default read-only raw sensor; writes blocked |
| `3/0x1022`, `3/0x1029`, `3/0x2003`, `3/0x200B`, `3/0x200C`, `3/0x200F` | Unnamed device values | See complete inventory below | Disabled-by-default read-only raw sensors |

The read-only raw diagnostics above are available as disabled-by-default
sensors. They preserve the hexadecimal payload and GATT address without
assigning units or semantic meaning. They are intentionally snapshots from
platform setup, not live measurements. Protected credential/security fields
remain blocked and are never exposed.

Where the packet layout is established, the same diagnostic entity also adds
decoded attributes while retaining the raw hexadecimal value:

| Address | Decoded attributes |
|---|---|
| `0/0x0006` | `clock` and numeric `clock_flag` |
| `1/0x0001` | `backlight_selector` and `backlight_rgb` |
| `1/0x0007` | numeric `last_key_code` |
| `0/0x100D` | boolean `advance_active` |
| `0/0x1028` | numeric `runback_setpoint_c` |
| `0/0x102A` | `away_active`, `away_until`, and `away_target_temperature_c` |

All other experimental values remain raw because their value mapping is not
established.

## Complete live AM6532 GATT inventory

This is the complete characteristic inventory returned by the commissioned
PLX050E Series C on 2026-11-19. Values are raw hexadecimal payloads unless a
text or selector interpretation is explicitly stated. `present` means the
characteristic exists; it does not mean that its semantics or writes are safe.
Protected values are intentionally not read.

### Group 0 — main control/configuration

| Characteristic | Raw value | Evidence-bound interpretation |
|---|---|---|
| `0x0006` | `141a0b13130301` | ConfigR `SetRtc`; upstream layout decodes panel-heater clock `2026-11-19 19:03`, flag `01`; no clock mapping |
| `0x1001` | `030000000000` | `HeatingMode`; `03` matched verified Eco |
| `0x1002` | `00` × 31 | `ScheduleAndSetTemperature`; buffer present and empty in this read |
| `0x1003` | `1200` | Effective/current-setpoint mirror; currently equals `0x1023` |
| `0x1023` | `1200` | Current target, 18°C |
| `0x1005` | `00` | Temperature-unit value observed as Celsius |
| `0x1006` | `07001e00` | Setpoint bounds 7–30°C from bytes 0 and 2 |
| `0x1007` | `010000000000000000` | Open Window Detection enabled; first-byte boolean layout and write/readback behavior verified on AM2086; official manual describes the resulting Heating Off behavior |
| `0x1008` | `01` | Adaptive Start enabled; one-byte boolean layout and write/readback behavior verified on AM2086; official manual calls this Adaptive Start |
| `0x100D` | `00` | Advance-heating payload; no command mapping |
| `0x1011` | `00` | Error status clear |
| `0x1012`–`0x1015` | protected/unread | Security or undocumented boundary; blocked |
| `0x1016` | `0008` | Setback payload; units unknown |
| `0x1017` | `017c` | Runback payload; units/encoding unknown |
| `0x1027` | `0000` | Runback/boost countdown currently zero |
| `0x1028` | `1500` | Runback/boost setpoint payload; not a second target |
| `0x102A` | `001a0b11300607` | Away payload; first byte inactive, remaining date/time/temperature bytes retained raw |

### Group 1 — identity/multiplexed diagnostics

| Characteristic | Raw value | Evidence-bound interpretation |
|---|---|---|
| `0x0001` | `029eff96` | Backlight payload `[selector,R,G,B]`; no HA control |
| `0x0002` | plain `010000`; selector 1/2 → `010000`/`020000` | ConfigR `Trac`; both element selectors currently idle; third-byte output correlation is model-specific |
| `0x0007` | `06` | ConfigR `LastKeyPressValue`; transient UI event |
| `0x0008` | plain `021d01`; selector 1/2/3 → `011308`/`021d01`/`030000` | TemperatureSensor: room 19.8°C, LCD/controller 29.1°C, core selector unavailable |
| `0x1010` | `47` | Unnamed live field; writes blocked |
| `0x1020` | `00` | ConfigR `TestMode` label exists for the protocol family; AM6532 semantics unverified; writes blocked |
| `0x300E` | `00` | ConfigR `Esp32OtaEnable` label exists; not a verified firmware-update capability; writes blocked |

### Group 2 — settings

| Characteristic | Raw value | Evidence-bound interpretation |
|---|---|---|
| `0x0005` | `000000` | ConfigR `Sound`; no semantic/control mapping |
| `0x100A` | `01` | Unnamed/boolean candidate in upstream notes; not mapped |
| `0x100B` | `05` | Unnamed; not mapped |
| `0x100C` | `01` | ConfigR `RfComms`; readable, writes blocked |
| `0x100F` | `00` | Unnamed; not mapped |
| `0x1018` | `00` | Unnamed; not mapped |
| `0x1019` | `00` | ConfigR `SpConfig`; semantics not verified |
| `0x700F` | `00` | Disconnect address in some ConfigR profiles; read observed, write blocked and not used |

### Group 3 — device information/diagnostics

| Characteristic | Raw value | Evidence-bound interpretation |
|---|---|---|
| `0x000A` | `f401980800000000` | PowerLoading: first LE16 = 500 W; second LE16 = 2200 with unknown meaning |
| `0x1022` | `00000000` | Unnamed; not mapped |
| `0x1050` | `f31000` | Unnamed; not mapped |
| `0x2001` | plain `000000`; selectors 0/1/2 → `000000`/`010107`/`02ffff` | SwVersion: UI selector reports revision 1.7; selector 2 is placeholder; UI revision is mapped to `DeviceInfo.sw_version` |
| `0x2002` | protected/unread | ConfigR `Gdid`; sensitive identifier redacted |
| `0x2003` | `00032d` | Unnamed; not mapped |
| `0x2004` | `<Dimplex>` plus padding | ConfigR `Brand`; manufacturer metadata |
| `0x2005` | `PLX050E;C;` plus padding | ConfigR `HeaterType`; model/series identity |
| `0x2006`–`0x2008` | protected/unread | ConfigR credential/security material; blocked |
| `0x2009` | `05` | RF firmware revision 5 observed and mapped with the UI revision to `DeviceInfo.sw_version`; no firmware-update control is inferred |
| `0x200A` | `EP4213` | ConfigR `EpIdentifier`; product code mapped to `DeviceInfo.model_id` |
| `0x200B` | `7f9d` | Unnamed; not mapped |
| `0x200C` | `1416050d0e00` | Unnamed; possible build data not established |
| `0x200D` | `PGLHS600` | Board/firmware code; raw metadata only |
| `0x1029` | `9eff96` | Same bytes as live backlight colour; semantic relationship unverified |
| `0x200F` | `04e90f0000` | Unnamed/protected-write boundary; not mapped |

### Read-only display heuristics

The diagnostic sensor keeps every payload in the `raw_value` attribute. Where a
shape or ConfigR label supports a reversible display-only interpretation, the
sensor state is rendered as follows; these are not promoted to controls or
semantic device capabilities:

| Address | Display-only rendering | Fallback |
|---|---|---|
| `1/0x0007` | `key code N` | raw hexadecimal |
| `1/0x1010` | `EOL test code N` | raw hexadecimal |
| `1/0x1020`, `1/0x300E`, `2/0x100C` | `on`/`off` for one-byte `0`/`1` | raw hexadecimal |
| `3/0x1029` | RGB bytes as `#RRGGBB` | raw hexadecimal |
| `3/0x200C` | possible timestamp when the six bytes form a valid `20YY-MM-DD hh:mm` value | raw hexadecimal |

The calibration payloads, `3/0x1022`, `2/0x700F`, `3/0x2003`, `3/0x200B`,
and `3/0x200F` remain raw because no defensible display decoding is available.

The selectors above are stateful: the integration must write the selector and
read the response on the same authenticated connection. Plain reads can return
the previously selected value or zeros and must not be interpreted as a fresh
measurement.

## Independent live probe: 2026-11-20

This section records a fresh read performed through the integration's authenticated
`get_gatt_services`, `read_gatt`, and selector write/read actions. No ConfigR
terminology or repository field mapping was used to obtain these observations.
The selector writes only choose which value the panel heater returns; they are not
control commands.

The device exposed 55 Dimplex characteristics across groups 0–3. Every
non-protected characteristic that was read returned successfully. The blocked
characteristics were not probed because they are security/credential or
undocumented boundaries.

| Address | Current raw value | Direct observation |
|---|---|---|
| `0/0x0006` | `141a0b14090301` | Clock-like seven-byte value; changes with wall-clock time, but byte semantics were not independently established |
| `0/0x1001` | `030000000000` | Stable mode payload while the climate entity reports Eco |
| `0/0x1002` | 31 zero bytes | Empty schedule buffer in this read |
| `0/0x1003` | `1000` | Matches the current 16°C target returned by `0/0x1023` |
| `0/0x1023` | `1000` | Current target payload; first byte is 16°C in this live state |
| `0/0x1005` | `00` | Current display-unit value; the integration currently interprets this as Celsius |
| `0/0x1006` | `07001e00` | Bounds payload; current integration interpretation is 7–30°C |
| `0/0x1007` | `010000000000000000` | Open Window Detection enabled; decoded as a verified boolean setting |
| `0/0x1008` | `01` | Adaptive Start enabled; decoded as a verified boolean setting |
| `0/0x100d` | `00` | Stable status/configuration payload; no command semantics inferred |
| `0/0x1011` | `00` | No error value in this sample |
| `0/0x1016` | `0008` | Stable two-byte configuration payload; units remain unknown |
| `0/0x1017` | `017c` | Stable two-byte configuration payload; encoding remains unknown |
| `0/0x1027` | `0000` | No active countdown in this sample |
| `0/0x1028` | `1500` | Separate setpoint-like payload; not treated as the climate target |
| `0/0x102a` | `001a0b11300607` | Inactive-looking date/time payload; semantics remain unproven |
| `1/0x0001` | `029eff96` | Four-byte colour-like payload; no user control inferred |
| `1/0x0007` | `01` | Transient-looking one-byte UI payload; no entity inferred |
| `1/0x1010` | `47` | Unnamed one-byte payload; no semantics inferred |
| `1/0x1020` | `00` | Unnamed one-byte payload; no semantics inferred |
| `2/0x0005` | `000000` | Three-byte payload; no semantics inferred |
| `2/0x100a` | `01` | Unnamed one-byte payload; no semantics inferred |
| `2/0x100b` | `05` | Unnamed one-byte payload; no semantics inferred |
| `2/0x100c` | `01` | Unnamed one-byte payload; no semantics inferred |
| `2/0x100f` | `00` | Unnamed one-byte payload; no semantics inferred |
| `2/0x1018` | `00` | Unnamed one-byte payload; no semantics inferred |
| `2/0x1019` | `00` | Unnamed one-byte payload; no semantics inferred |
| `2/0x700f` | `00` | Unnamed one-byte payload; no semantics inferred |
| `3/0x000a` | `f401980800000000` | Eight-byte power/configuration payload; first two little-endian bytes are 500, second word is 2200, meanings not independently derived here |
| `3/0x000b` | `fafafaf8f7f8f7f7f3f7f8faf7` | 13-byte payload; no stable interpretation inferred |
| `3/0x000c` | `f8f7f6eef7f6f4f6f5f4f6f4f5` | 13-byte payload; no stable interpretation inferred |
| `3/0x1022` | `00000000` | Four-byte payload; no semantics inferred |
| `3/0x1050` | `f31000` | Three-byte payload; no semantics inferred |
| `3/0x2003` | `00032d` | Three-byte payload; no semantics inferred |
| `3/0x2004` | `3c44696d706c65783e0000000000000000000000` | Text-like payload decodes to `<Dimplex>` with zero padding |
| `3/0x2005` | `504c58303530453b433b00000000000000000000000000000000000000000000` | Text-like payload decodes to `PLX050E;C;` with zero padding |
| `3/0x2009` | `05` | Stable one-byte firmware-related payload; no update capability inferred |
| `3/0x200a` | `455034323133` | ASCII payload decodes to `EP4213` |
| `3/0x200b` | `7f9d` | Two-byte payload; no semantics inferred |
| `3/0x200c` | `1416050d0e00` | Six-byte payload; no semantics inferred |
| `3/0x200d` | `50474c4853363030` | ASCII payload decodes to `PGLHS600` |
| `3/0x1029` | `9eff96` | Matches the colour-like bytes in `1/0x0001`; relationship remains unproven |
| `3/0x200f` | `04e90f0000` | Five-byte payload; no semantics inferred |

### Selector-backed observations

The selector exchanges were repeated for selector values 0–4 on the same
authenticated connection. Values not listed as meaningful were returned as
zero/placeholder payloads, not treated as additional capabilities.

| Characteristic | Selector 1 | Selector 2 | Selectors 3–4 |
|---|---|---|---|
| `1/0x0002` | `010000` | `020000` | `030000`, `040000` |
| `1/0x0008` | `011106` → 17.6°C by the existing decoder | `021703` → 23.3°C by the existing decoder | `030000`, `040000` |
| `3/0x2001` | `010107` | `02ffff` placeholder | `030000`, `040000` |

This probe confirms that the selector protocol is stateful and that the
temperature selector currently supplies two distinct live readings. It does not
prove that the `1/0x0002` selector values are independent element telemetry:
all four returned zero activity bytes while the panel heater was idle.

### Controlled target experiment

The panel heater was temporarily changed from the live baseline of Eco/16°C to a
30°C target through the Home Assistant climate action, then restored by writing
the original target and Eco mode payloads. The comparison isolated these
changes:

| Address/state | Baseline | 30°C target | Restored |
|---|---|---|---|
| `0/0x1001` | `030000000000` | `020000000000` | `030000000000` |
| `0/0x1003` | `1000` | `1e00` | `1000` |
| `0/0x1023` | `1000` | `1e00` | `1000` |
| `1/0x0002`, selector 1 | `010000` | `010164` | `010000` |
| `1/0x0008`, plain read | `021703` | `021702` | selector-backed reads remain required |

The target write changed the mode byte from `03` to `02`, changed both target
characteristics together, and caused the Trac response to report a non-zero
output byte while the climate entity reported `heating`. This independently
supports `02` as the target-controlled/manual-like operating state and the third Trac byte as an
activity/output value for this device. It does not establish the meaning of
every byte in the mode or Trac payload.

All other safe characteristics in the full inventory remained unchanged during
this perturbation. They are therefore currently classified as static or
configuration-like observations, not as live measurements. The heater was
restored and verified at Eco/16°C with an idle action.

### Controlled display-unit experiment

The display-unit select was temporarily changed from Celsius to Fahrenheit and
then restored. `0/0x1005` changed from `00` to `01` and back to `00`. The target
and bounds remained `0/0x1023 = 1000`, `0/0x1003 = 1000`, and
`0/0x1006 = 07001e00`. This proves `0/0x1005` is an independently writable
display-unit setting for this heater. It does not change the integration's
native Celsius representation of the device temperature values.

### Controlled mode-payload experiment

The mode payload was temporarily changed from Eco (`03`) to `04`. The heater
returned `0/0x1001 = 040000000000`, changed both target fields to `7°C`
(`0/0x1003 = 0700` and `0/0x1023 = 0700`), returned no error, and reported
idle Trac output. The Home Assistant climate state remained `heat` with no
preset selected. This proves a distinct mode that forces the minimum target on
this device; it does not independently prove the user-facing name or whether
the mode should be exposed as a Home Assistant `frost` preset. That semantic
mapping remains pending physical-display confirmation.

No new control, humidity, fan, swing, schedule, occupancy, energy-counter,
firmware-update, or notification capability was proven by this probe. Unknown
payloads remain raw evidence only and are not converted into Home Assistant
entities or writes.

## Non-BLE cloud/API field catalogue

The following fields come from
[`dimplex-controller-py`](https://github.com/KRoperUK/dimplex-controller-py),
which reverse-engineers the Dimplex Control cloud API. They are included for
completeness because they are part of the requested source set, but they are
**CLOUD-ONLY** evidence. They do not prove a local BLE characteristic, do not
apply to a panel heater without the Dimplex Hub/cloud path, and are not merged into
the AM6532 entity mapping.

### Cloud topology and identity

| Object | Fields documented by the client | Evidence/status |
|---|---|---|
| `Hub` | `HubId`, `HubName`, `FriendlyName`, `SecurityCode`, `AddressLine1`, `AddressLine2`, `TownCity`, `Postcode`, `County`, `Country`, `Latitude`, `Longitude`, `HubRegistrationDate`, `InstallationDate`, `LastTelemDate`, `TimeZoneId`, `TimeZoneName`, `NumberOfZones`, `NumberOfAppliances`, `IsServiceModeEnabled`, `FirmwareVersion`, `ConnectionState`, `HubType`, `BluetoothName`, `PrimaryUserEmail`, `IsDefault`, `RoleName` | CLOUD-ONLY metadata; credentials, email, address and coordinates are privacy-sensitive |
| `Zone` | `ZoneId`, `ZoneName`, `HubId`, `ZoneType`, `RoomType`, `AppName`, `Icon`, `IconColor`, `Appliances` | CLOUD-ONLY topology |
| `Appliance` | `ApplianceId`, `ApplianceType`, `ApplianceModel`, `ZoneId`, `FriendlyName`, `ZoneName`, `Icon`, `IconColor`, `InstallationDate`, `HasConnectivity`, `SecurityCode`, `LastTelemDate`, `SeriesIdentifier`, `FirmwareVersion`, `ProductModelExtensions` | CLOUD-ONLY device metadata; `SecurityCode` is sensitive |
| `ProductModel` | `ProductModelId`, `ProductTypeId`, `ProductModelName`, `ProductTypeName`, `ProductModelExtensions` | CLOUD-ONLY catalogue metadata |
| `UserContext` | `Id`, `EmailAddress`, `Name` | CLOUD-ONLY identity; sensitive |

Cloud null/edge values documented by the client include `.NET` default
datetime `0001-01-01T00:00:00` meaning unset, and
`ActiveSetPointTemperature = 255` meaning no explicit setpoint/follow the
schedule. These sentinel rules must not be applied to BLE payloads without
device evidence.

### Cloud live status

| Status field | Documented meaning/value | AM6532 BLE status |
|---|---|---|
| `HubId`, `ApplianceId`, `ZoneId` | Cloud object references | CLOUD-ONLY |
| `StatusTwo` | Cloud schedule/profile status code | CLOUD-ONLY |
| `ApplianceModes` | Bitfield listed below | CLOUD-ONLY |
| `RoomTemperature` | Room temperature, °C | Separate BLE selector exists; cloud field is not its proof |
| `ActiveSetPointTemperature` | Active target, °C; `255` sentinel follows schedule | Separate BLE target exists; sentinel not reused |
| `NormalTemperature` | Normal/comfort target | CLOUD-ONLY |
| `AwayDateTime`, `AwayTemperature` | Away-until and away setback target | CLOUD-ONLY |
| `BoostDuration`, `BoostTemperature` | Timed boost duration/minutes and target | CLOUD-ONLY |
| `OpenWindowEnabled` | Open-window setting | BLE label/payload exists, but cloud field does not prove its local semantics |
| `EcoStartEnabled` | EcoStart/pre-emptive-start setting | BLE label/payload exists, but cloud field does not prove its local semantics |
| `SetbackEnabled`, `SetbackEnabledInStatusFrame`, `SetbackTemperature` | Setback state and target | CLOUD-ONLY |
| `ComfortStatus` | Cloud comfort status | CLOUD-ONLY |
| `AvailableHotWater` | Usable hot-water amount, litres | CLOUD-ONLY/hot-water products |
| `LockStatus` | Cloud lock status | CLOUD-ONLY |
| `ErrorCode`, `WarningCode` | Cloud fault/warning codes; healthy integer `0` is coerced to text | BLE error byte exists separately |
| `Capacity` | Hot-water tank capacity, litres | CLOUD-ONLY |
| `HeatingUp` | Hot-water heating state | CLOUD-ONLY |
| `IsGreenEnergyActive` | Hot-water green-energy state | CLOUD-ONLY |
| `HygieneFrequency`, `HygieneTemperature` | Hot-water hygiene-cycle settings | CLOUD-ONLY |
| `HolidayNumOfDays` | Hot-water holiday duration | CLOUD-ONLY |
| `DelayedStartTime`, `DsmStatus`, `ModifiedPower` | Cloud/DSM status fields | CLOUD-ONLY |
| `ConnectedChargeProfiles`, `StandaloneChargeProfiles` | Cloud charge-profile payloads | CLOUD-ONLY |

### Cloud mode flags and control values

`ApplianceModeFlag` is a cloud bitfield, not the BLE `HeatingMode` byte:

| Flag | Value | Cloud meaning |
|---|---:|---|
| `TIMER_MODE` | `0x01` | Schedule/timer running |
| `BOOST` | `0x02` | Timed boost; `Time` is minutes |
| `AWAY` | `0x04` | Away setback; `Date` is away-until |
| `HOLIDAY` | `0x08` | Holiday |
| `ADVANCE` | `0x10` | Advance to next period |
| `FROST_PROTECT` | `0x20` | Anti-freeze; cloud app's off behavior |
| `ECO` | `0x40` | Eco mode |
| `MANUAL` | `0x80` | Manual hold |
| `HYGIENE` | `0x100` | Hot-water hygiene |
| `STANDALONE` | `0x200` | Standalone |
| `SAFE_MODE` | `0x400` | Safe mode |
| `SHUTDOWN` | `0x800` | Shutdown |
| `COMMS` | `0x1000` | Communications |
| `NORMAL` | `0x2000` | Hot-water normal |
| `STANDBY` | `0x4000` | Standby |

`ApplianceModeSettings` fields are `ApplianceModes`, `Status` (`0` inactive,
`1` active), `Temperature` (whole-degree short, app range 7–30°C), `Time`
(boost minutes), `Date` (away-until), `StatusTwo`, `NumberOfDays`, and
`Frequency`. Cloud mode bounds are 7–30°C for the normal mode carousel, 7–18°C
for Away, 7°C for Frost Protect, and the default Boost target is 21°C.
Default boost durations documented by the client are 30, 60, 120, and 180
minutes, with 60 minutes as the default.

Other cloud enums and profile values:

| Type | Values |
|---|---|
| `TimerMode` | `USER_TIMER=0`, `MANUAL=1`, `FROST_PROTECTION=2`, `OFF=3`; source warns these are not the same as active mode flags |
| `ScheduleProfile` / `StatusTwo` | `USER_TIMER=0`, `HOME_ALL_DAY=1`, `OUT_ALL_DAY=2` |
| `SetbackStatus` | `INACTIVE=0`, `ACTIVE=1`, `DSM_MODE=2`, `LOCAL_FREQUENCY_CONTROL_ACTIVE=3` |
| `HygieneFrequency` | `OFF=0`, `DAILY=1`, `WEEKLY=7`, `MONTHLY=28` |
| `ApplyProfileToTimerResult` | `SUCCESSFUL=0`, `PROFILE_DOES_NOT_EXIST=1`, `ERROR=2` |

### Cloud schedules, provisioning and energy

| Structure | Fields/layout | Status |
|---|---|---|
| `TimerPeriod` | `DayOfWeek`, `StartTime`, `EndTime`, `Temperature` | CLOUD-ONLY schedule |
| `TimerModeSettings` | `HubId`, `ApplianceId`, `TimerMode`, `TimerPeriods` | CLOUD-ONLY schedule |
| `UserHeatingProfilePeriod` | `UserHeatingProfileId`, `DayOfWeek`, `StartTime`, `EndTime`, whole-degree `Temperature` | CLOUD-ONLY profile |
| `UserHeatingProfile` | `Id`, `ProfileName`, `UserHeatingProfilePeriods` | CLOUD-ONLY named profile |
| `AutomaticProvisioning` | `bottomElementPowerRating`, `topElementPowerRating`, `ratedPower` (kW), `chargeCapacity` (kWh), `chargeElementResistance` (ohms), `powerOffset` | CLOUD-ONLY product metadata; not the BLE `PowerLoading` payload |
| `TsiEnergyReport` | `HubId`, `ApplianceTelemetryData` keyed by appliance ID | CLOUD-ONLY energy report |
| Energy points | Timestamp keys include `timestamp`, `time`, `TS`, `datetime`, `date`, `from`, `start`; primary value keys include `T1`, `ST`, `value`, `kwh`, `energy`, `consumption`, `amount`; secondary keys include `T2`, `value2`, `kwh2`, `energy2` | T1/off-peak and T2/peak registers must remain separate; never sum them |
| Energy summaries | `daily`, `lifetime`, `window`; daily uses local midnight; points are commonly one kWh sample per day | No AM6532 BLE cumulative-energy mapping |

## Phase 1 implementation audit

This is the field-by-field implementation boundary for the current PLX050E
Series C device. “Verified” means the payload was observed on the live heater,
decoded without inference beyond the documented layout, and covered by a
regression test. “Phase 2” means the value or command still needs a deliberate
change confirmed on the physical display before it can become a user-facing
control.

### Climate entity

| Capability/read or write | Evidence | HA surface | Phase 1 decision |
|---|---|---|---|
| Current room temperature | Selector `1/0x0008`, live `19.8 °C`-class payload; parser and coordinator tests | `native_current_temperature` | **Verified / implemented** |
| Native temperature unit | `0/0x1005`, live Celsius; known Celsius/Fahrenheit values | `native_temperature_unit` | **Verified / implemented**; display choice is separately selectable |
| Target temperature | `0/0x1023`, live whole-degree value; bounded write/readback behavior | `native_target_temperature`, `TARGET_TEMPERATURE` | **Verified / implemented** |
| Target min/max | `0/0x1006`, live 7–30 °C | `min_temp`, `max_temp` | **Verified / implemented** |
| Target step/precision | Live setpoint writes and protocol whole-degree representation | `target_temperature_step=1`, `precision=1` | **Verified / implemented** |
| HVAC modes | Only direct heat behavior is verified on this PLX050E | `HVACMode.HEAT` | **Verified / implemented**; no fabricated OFF/COOL/AUTO |
| HVAC action | Trac selector `1/0x0002`, active flag, and PLX output correlation | `HEATING`/`IDLE` | **Verified / implemented**; no temperature-derived action |
| Eco preset | `0/0x1001`, live Eco payload and reversible known write | `PRESET_ECO` | **Verified / implemented** |
| Frost preset | ConfigR terminology and known profile payload; live payload selected 7 °C but physical-display confirmation is pending | None | **Phase 2**; not advertised by the climate entity |
| Other presets/modes | Away, boost, advance, manual, holiday, sleep, comfort, schedule | None | **Phase 2 or cloud-only**; no AM6532-safe local command |
| Fan, swing, horizontal swing | No verified AM6532 capability | None | **Unavailable; not exposed** |
| Humidity and humidity target | No verified AM6532 capability | None | **Unavailable; not exposed** |
| Target range | No verified independent low/high target | None | **Unavailable; not exposed** |
| Explicit turn on/off | No verified independent power command; heat-only mode is not proof of power semantics | None | **Phase 2**; do not advertise `TURN_ON`/`TURN_OFF` |

### Separate entities and device metadata

| Field/read | Evidence | HA surface | Phase 1 decision |
|---|---|---|---|
| Error status | `0/0x1011`, live healthy value and raw bytes | Diagnostic error-code sensor | **Verified / implemented** |
| Rated power | `3/0x000A`, first LE16 = 500 W; matches the PLX050E rating | Disabled diagnostic power sensor | **Verified / implemented** |
| Trac output percentage | `1/0x0002`, third byte and repeated live behavior correlate with output percentage | Disabled diagnostic percentage sensor | **Verified / implemented**, not an energy meter |
| Calculated instantaneous power | Rated power × verified Trac percentage | Enabled `SensorDeviceClass.POWER`, W, measurement state class | **Verified / implemented**; derived power, not cumulative energy |
| LCD/controller temperature | Selector `2/0x0008`, live separate value; selector 3 unavailable | Disabled diagnostic temperature sensor | **Verified / implemented** |
| Display unit control | `0/0x1005`, Celsius → Fahrenheit → Celsius live readback | Config `select` | **Verified / implemented**; physical display wording remains phase 2 |
| Heater model/series | `3/0x2005`, live `PLX050E;C;` | `DeviceInfo.model` | **Verified / implemented** |
| Product code | `3/0x200A`, live `EP4213` | `DeviceInfo.model_id` | **Verified / implemented** |
| UI software revision | Selector `1/3/0x2001`, live `1.7` | `DeviceInfo.sw_version` | **Verified / implemented** |
| RF firmware revision | `3/0x2009`, live `5` | `DeviceInfo.sw_version` metadata | **Verified / implemented**; no update entity inferred |
| Board code | `3/0x200D`, live `PGLHS600`, semantic meaning unresolved | None | **Raw-only; phase 2** |
| Raw operating mode | `0/0x1001`, full bytes retained | Disabled diagnostic raw sensor | **Verified / implemented**; Eco is the only climate-decoded value; Frost remains phase-2 evidence |


### Experimental raw controls

These controls remain raw hexadecimal text entities. Friendly names are
ConfigR-derived hypotheses, not confirmed Home Assistant semantics:

| Entity | Address | Length | Evidence and boundary |
|---|---|---:|---|
| Experimental panel-heater clock | `0/0x0006` | 7 bytes | ConfigR `SetRtc` format; write is available, but no semantic date validation is performed |
| Experimental schedule buffer | `0/0x1002` | 31 bytes | Sunhouse/QRAD layout is inferred; AM6532 schedule-buffer behavior remains unverified |
| Experimental display backlight color | `1/0x0001` | 4 bytes | Reversible color payload observed in the protocol family |
| Existing experimental fields | `0/0x0006`, `0/0x100D`, `0/0x1016`, `0/0x1017`, `0/0x1027`, `0/0x1028`, `0/0x102A`, `2/0x100A`, `2/0x100B`, `2/0x100F`, `2/0x1018`, `2/0x1019` | fixed per field | Raw write/readback trials only; meanings remain experimental |

The verified `2/0x0005` Sound setting is no longer an experimental raw control.
It is a configuration switch with an independent five-minute read schedule.

The controls are for controlled manual testing, not normal automations. Protected
security, credential, OTA, factory-reset, EOL, and undocumented write boundaries
remain excluded.

### Writes audited

| Write | Current status | Boundary |
|---|---|---|
| Setpoint `0/0x1023` | Live bounded whole-degree write/readback and tests | Climate target |
| Eco preset `0/0x1001` | Live known payload and tests | Climate preset |
| Display unit `0/0x1005` | Live reversible write/readback | Configuration select |
| Frost preset `0/0x1001` | Payload is known, but physical-display confirmation is pending | Phase 2 confirmation before broader claim |
| Experimental raw controls | Fixed-width hex writes now available for controlled testing | No semantic validation; meanings and device effects remain experimental |
| Security, credential, OTA, test, factory-reset, EOL and protected fields | Explicitly blocked | Never expose |

### Phase 2 physical confirmations

The next interactive phase is limited to values that cannot be confirmed from
safe reads and regression fixtures alone:

1. Confirm the physical display text and resulting mode/target for Frost.
2. Confirm whether the display-unit select changes only display formatting and
   does not change the native sensor/setpoint semantics.
3. If needed, observe a deliberate active-heating transition to correlate Trac
   output values with display state; do not infer it from temperature alone.
4. Confirm any additional mode or power behavior before adding a control.

No phase-2 item is currently advertised as a verified climate capability.

## Home Assistant mapping

| Home Assistant surface | Capability evidence | Status |
|---|---|---|
| Climate entity | Live current temperature, single target, native Celsius, real limits, whole-degree step, heat-only behavior, and Eco preset | IMPLEMENTED; Frost remains phase 2 and is not advertised |
| Display-unit select | Live display-unit field and ConfigR Celsius/Fahrenheit terminology | IMPLEMENTED |
| Error-code sensor | Live error field and ConfigR fault diagnostics | IMPLEMENTED |
| Raw operating-mode diagnostic sensor | Live raw mode characteristic; Eco is decoded for climate, while the bounded Frost payload remains raw/phase-2 evidence | IMPLEMENTED; diagnostic and disabled by default |
| Rated-power diagnostic sensor | Live `3/0x000A` first LE16 word, verified against PLX050E 500 W documentation; full raw payload preserved as an attribute | IMPLEMENTED; diagnostic and disabled by default |
| LCD-temperature diagnostic sensor | Live selector `2/0x0008` returns a separate 29.0 °C controller/LCD reading; selector 3 is unavailable | IMPLEMENTED; diagnostic and disabled by default |
| Trac output-power diagnostic sensor | Official PLX report-screen semantics plus repeatable live Trac reads correlate the third byte with output power for PLX050E | IMPLEMENTED; percentage diagnostic and disabled by default |
| HVAC action | Live Trac byte 1 distinguishes active heating and idle without deriving from target/current temperature | IMPLEMENTED; reports only `HEATING`/`IDLE` when valid |
| Preset/fan/swing/humidity/range controls | Eco is verified; Frost has a bounded protocol value but still needs physical-display confirmation; remaining controls have no AM6532-safe mapping | PARTIALLY EXPOSED |
| Energy/charge/element sensors | Calculated power is exposed from verified output percentage × rated output; cumulative energy and independent element status remain unverified | PARTIALLY EXPOSED |
| Calendar/schedule | App labels exist, but no AM6532 schedule protocol mapping | NOT EXPOSED |
| Firmware update | App capability is not a recovered local protocol capability | NOT EXPOSED |

## Next evidence required before adding features

A feature can move out of `UNKNOWN/NEEDS CONFIRMATION` only after all of the
following are available:

1. The AM6532 characteristic and payload format are identified from ConfigR
   protocol behavior or an independently repeatable device trace.
2. The value is read repeatedly across at least two meaningful device states.
3. Any command is tested with a bounded, reversible change and readback.
4. The Home Assistant semantic mapping is unambiguous and does not require
   inference from temperature or selected mode.
5. A regression fixture and Home Assistant behavior test cover the mapping.

Until then, the integration intentionally exposes fewer controls than ConfigR.
That is safer than presenting a control whose packet meaning is unknown.
