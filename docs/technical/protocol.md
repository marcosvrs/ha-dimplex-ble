# Bluetooth GATT protocol

This page lists every Bluetooth Low Energy (BLE) Generic Attribute Profile (GATT) characteristic the integration uses. The heaters are Dimplex PLX050E and PLX075E Series C panel heaters. It also lists the characteristics the integration deliberately leaves alone and the ones nobody has decoded. The firmware update channel is described in [Firmware update](firmware-update.md).

## Conventions on this page

The page writes each characteristic as `group/0xNNNN`, the notation of [protocol.py](../../custom_components/dimplex_ble/protocol.py).

- **Group**: the service index `G` in the service universally unique identifier (UUID), from 0 to 3
- **Characteristic**: the 16-bit characteristic number, in hexadecimal
- **ConfigR name**: the parameter class name in Dimplex's ConfigR Android app 3.12.2, from static analysis
- **Bytes**: hexadecimal, in wire order, written as `01 03 00`
- **Little-endian 16-bit**: two bytes, low byte first
- **Verified**: read or written on a real heater, with the date and the heater name
- **ConfigR only**: recovered from ConfigR's code, never confirmed on a heater
- **HVAC mode**: heating, ventilation and air conditioning mode, Home Assistant's climate term
- **UI and RF**: the heater's user interface (display) board and its radio frequency board, each with a firmware revision
- **LCD temperature**: the thermistor reading of the controller behind the liquid crystal display
- **PIN**: personal identification number, here the 6-digit pairing passkey
- **EP number**: the product code that ConfigR calls `EpIdentifier`

Four heaters provide the hardware evidence: AM2086, AM6532, AM8224 and AM1382. AM6532 reports model `PLX050E;C;` and product code `EP4213`; AM2086 reports `EP4213` too.

## UUID scheme

Each characteristic has two possible UUID forms, and the integration accepts either. The function `gatt_uuid_candidates` in [protocol.py](../../custom_components/dimplex_ble/protocol.py) builds both.

| Form | Service UUID | Characteristic UUID |
|---|---|---|
| Bluetooth Special Interest Group (SIG) base | `00000000-000G-1000-8000-00805f9b34fb` | `0000NNNN-0000-1000-8000-00805f9b34fb` |
| GDHV alias | `00000000-000G-474c-4e44-494d504c4558` | `0000NNNN-000G-474c-4e44-494d504c4558` |

The alias suffix `474c4e44494d504c4558` is ASCII for `GLNDIMPLEX`. The lookup in [link.py](../../custom_components/dimplex_ble/link.py) works in this order:

1. The SIG-base service and characteristic
2. The GDHV alias service and characteristic
3. For group 0 only: the SIG-base characteristic UUID in any service

The PLX Series C heaters expose the SIG-base form. Their GATT table has no Device Information Service (`0x180A`). It holds services `0x1800`, `0x1801`, the four vendor services `00000000-0000` to `00000000-0003`, and the firmware update service `00060000-f8ce-11e4-abf4-0002a5d5c51d` ([hardware research](research/dimplex-ble-hardware-research.md)).

## Access rules

The integration checks each characteristic's GATT properties before it touches the value.

- **Read**: only when the characteristic exists and has the `read` property. A missing characteristic yields no value, and the entity that needs it is unavailable.
- **Write**: only when the characteristic has `write` or `write-without-response`. The integration writes with response whenever `write` is present.
- **Selector read**: a write of a selector, then a read of the same characteristic. Both run inside one link operation.
- **Read then write**: some records are read and rewritten inside one link operation. A change made on the heater since the last poll is kept.
- **Metadata**: the `dimplex_ble.get_gatt_services` action returns service and characteristic UUIDs with their properties. It reads no values.

## Connection and pairing

Every operation connects, pairs, runs its GATT sequence and disconnects. The integration never holds a link between operations.

- **Poll**: once every 60 seconds. Sound, Setback, temperature offset and the appliance ID are read every 5 minutes and after a change. Timer programmes are read after setup and every 6 hours.
- **Settle time**: the integration waits 10 seconds after a heater's last link went down before it connects again. A reconnect 20 to 23 ms after a disconnect made the heater drop the link or answer "Invalid handle" (2026-11-24, [ledger](research/investigation-2026-11-24/ledger.md)).
- **Service cache**: normal operations connect with the cached GATT table. The cached path runs at a maximum transmission unit (MTU) of 23 bytes. The firmware update connects without the cache to get MTU 517; see [Firmware update](firmware-update.md#link-and-mtu).
- **Proxy**: each heater is pinned to one ESPHome Bluetooth proxy, which holds the heater's bond.

### Pairing and the passkey

The heater starts pairing itself. Its Bluetooth is always on, and it has no separate pairing mode.

1. The central connects through the proxy.
2. The heater sends a Security Request at once, which starts the Security Manager Protocol (SMP) exchange.
3. The heater shows a 6-digit passkey on its display and asks the proxy for it.
4. The PIN responder on the proxy (`firmware/dimplex_pin_responder.yaml`) answers with the passkey that Home Assistant armed before the connect.
5. The proxy stores the bond. Later connects re-encrypt with the stored key and show no passkey.

The passkey is static per heater. The integration stores it in the heater's config entry and also sends it as `SecurityCode` in the firmware lookup ([Firmware update](firmware-update.md#firmware-lookup)). Dimplex accepts it there, and the AM2086 firmware update on 2026-11-25 ran on that lookup. A missing passkey on the display means the central did not reach the passkey request. The pairing design and its evidence are in [ESPHome BLE findings](research/esphome-ble-findings.md).

## Characteristic reference

The table lists every characteristic the integration reads or writes. Details for the multi-byte records follow in their own sections.

| Address | ConfigR name | Size | Access | Encoding | Integration use | Evidence |
|---|---|---|---|---|---|---|
| `0/0x0006` | SetRtc | 7 | Read, write | See [Clock](#clock) | Clock sync, diagnostics offset | Verified on all four heaters, 2026-11-25 |
| `0/0x1001` | HeatingMode | 6 | Read, write | See [Heating mode](#heating-mode-and-presets) | Climate HVAC mode and presets, Operating mode sensor | Verified on AM6532 and AM2086, 2026-11-24 |
| `0/0x1002` | ScheduleAndSetTemperature | 31 | Selector read, write | See [Timer programmes](#timer-programmes) | Three calendars, `get_schedule`, `set_schedule`, `restore_schedule` | Read verified on all four heaters, 2026-11-24; save verified on AM2086, 2026-11-25 |
| `0/0x1005` | TemperatureUnit | 1 | Read, write | `00` Celsius, `01` Fahrenheit | Temperature unit select | Fahrenheit write and readback verified on AM6532 ([capability matrix](research/capability-matrix.md)) |
| `0/0x1006` | SetpointRange | 4 | Read | `[min °C, 0, max °C, 0]`; AM6532 reads `07 00 1e 00` (7 to 30 °C) | Climate minimum and maximum; range check for every setpoint, Boost temperature and schedule temperature | Read verified on AM6532, 2026-11-19; writes acknowledged but not applied on AM2086, 2026-11-24 |
| `0/0x1007` | OpenWindowDetection | 9 read, 1 written | Read, write | Read: byte 0 `00` off or `01` on, then 8 zero bytes. Write: one byte `00` or `01` | Open window detection switch | Write and readback verified on AM2086 ([capability matrix](research/capability-matrix.md)) |
| `0/0x1008` | PreEmptiveHeating | 1 | Read, write | `00` off, `01` on | Pre-emptive heating switch (Adaptive Start in the manual) | Write and readback verified on AM2086 ([capability matrix](research/capability-matrix.md)) |
| `0/0x100D` | AdvanceHeating | 1 | Read, write | `00` off, `01` on. ConfigR also defines `02` to `06`, `0x0C` to `0x0F` and `0x81` to `0x8D`; the integration reads those as unknown | Advance switch, refused outside a timer programme and during Away | Verified on AM2086, 2026-11-24 |
| `0/0x1011` | ErrorStatus | 1 | Read | Unsigned integer. `0` is no error; codes 20 to 61 follow the PLXE Series C manual, Issue 7, page 26 | Error code sensor | `00` read on AM6532, 2026-11-19 |
| `0/0x1016` | Setback | 2 | Read, then write | See [Setback](#setback) | Setback switch, Setback temperature number | Verified on AM2086: on and off 2026-11-24, temperature 2026-11-25 |
| `0/0x1023` | CurrentSetTemperature | 2 | Read, write | `[°C, 0]`, whole degrees; read as little-endian 16-bit; `0` reads as unknown | Climate target temperature; Boost start | Whole-degree writes verified ([capability matrix](research/capability-matrix.md)) |
| `0/0x1027` | RunbackTime | 2 | Read, write | Boost minutes left, little-endian 16-bit | Boost preset | Verified on AM2086, 2026-11-25 |
| `0/0x1028` | RunbackSetpoint | 2 | Read, write | `[°C, 0]`; `0` or a nonzero second byte reads as unknown | Boost temperature number; Boost start | Verified on AM2086, 2026-11-25 |
| `0/0x102A` | AwayMode | 7 | Read, then write | See [Away](#away) | Away preset, Away return time, Away temperature | Verified on AM6532 and AM2086, 2026-11-24 |
| `1/0x0002` | Trac | 3 | Selector read | Selector `1`. Reply `[1, active, output %]`: active `00` or `01`, output 0 to 100 | Climate HVAC action, Output power sensor, Calculated power sensor, Estimated energy sensor | Verified on AM6532: `01 01 64` while heating, `01 00 00` idle |
| `1/0x0008` | TemperatureSensor | 3 | Selector read | Selector `1` room, `2` LCD. Reply `[selector, whole °C, tenths 0 to 9]` | Climate current temperature (selector 1), LCD temperature sensor (selector 2) | Verified on AM6532, 2026-11-19: room `01 13 08` (19.8 °C), LCD `02 1d 01` (29.1 °C) |
| `1/0x300E` | Esp32OtaEnable | 1 | Write during an update only | `01` update mode, `00` normal | Firmware update | See [Firmware update](firmware-update.md#update-mode-switch) |
| `2/0x0005` | Sound | 3 | Read, write | `[on, tone, volume]`. The integration accepts only `[00 or 01, 00, 00]` and writes `[on, 00, 00]` | Sound switch | Verified ([capability matrix](research/capability-matrix.md)) |
| `2/0x1019` | SpConfig | 1 | Read, write | Signed tenths of °C: a byte up to 100 is `+b/10`, a larger byte is `(b − 256)/10`. The integration writes within ±3.0 °C in 0.1 °C steps | Temperature offset number, disabled by default | ±3.0 °C verified on AM2086, 2026-11-24 |
| `3/0x000A` | PowerLoading | 8 | Read | See [Power loading](#power-loading) | Rated power sensor; Calculated power and Estimated energy sensors | First word verified on AM6532, 2026-11-19 |
| `3/0x2001` | SwVersion | 3 | Selector read | Selector `1` (UI). Reply `[1, major, minor]` | Installed firmware version on the device page and the Firmware update entity | Verified on AM2086 and AM6532: `01 01 07` (1.7); `01 05 00` (5.0) on AM2086 after the update of 2026-11-25 |
| `3/0x2002` | Gdid | 6 | Read | See [Identity](#identity) | Appliance ID for the firmware lookup | Verified on AM2086, 2026-11-25 |
| `3/0x2005` | HeaterType | ASCII, zero padded | Read | `PLX050E;C;` | Device model `PLX050E Series C` | Verified on AM6532, 2026-11-19 |
| `3/0x2009` | none | 1 | Read | RF firmware revision, one unsigned byte | Device page firmware text | `05` on all four heaters |
| `3/0x200A` | EpIdentifier | 6 | Read | ASCII product code, `EP4213` | Firmware file check | Verified on AM6532, 2026-11-19 |
| `00060001-f8ce-11e4-abf4-0002a5d5c51d` | Esp32CommandParameter | 506 read at idle | Write, notify during an update only | Update frames | Firmware update | See [Firmware update](firmware-update.md#ble-update-sequence) |

## Selector characteristics

Four characteristics multiplex several values behind one UUID. The integration writes a selector, then reads the selected value in the same link operation.

| Address | Selector written | Value read | Integration use |
|---|---|---|---|
| `1/0x0008` | `01 00 00` | `[01, °C, tenths]` | Room temperature |
| `1/0x0008` | `02 00 00` | `[02, °C, tenths]` | LCD temperature |
| `1/0x0002` | `01 00 00` | `[01, active, output %]` | Heating action and output power |
| `3/0x2001` | `01 00 00` | `[01, major, minor]` | UI firmware version |
| `0/0x1002` | `[00, programme, day]` | 31-byte day record | Timer programmes |

A reply whose first byte differs from the selector is discarded. A temperature reply with a tenths byte above 9 is discarded. ConfigR also defines `3/0x2001` selector `0` (CC) and `2` (RF); AM6532 answers `00 00 00` and `02 ff ff` to them ([capability matrix](research/capability-matrix.md)). Selector 3 of `1/0x0008` reads `03 00 00` and is not used.

## Heating mode and presets

`0/0x1001` holds the operating mode. The integration writes a 6-byte payload `[mode, programme, 0, 0, 0, 0]`.

| Byte | Meaning |
|---|---|
| 0 | Mode: `01` timer, `02` manual, `03` eco, `04` frost protect. ConfigR also defines `05` Boost and `06` Standby |
| 1 | Timer programme while byte 0 is `01`: `01` Out All Day, `02` Home All Day, `03` User Timer, `05` Away. ConfigR also defines `04` Holiday |
| 2 to 5 | ConfigR: temperature setting, duration, temperature. During Boost, byte 3 holds a duration band |

The written payloads are verified on hardware (2026-11-24):

| Preset | Payload | HVAC mode |
|---|---|---|
| `manual` | `02 00 00 00 00 00` | Heat |
| `eco` | `03 00 00 00 00 00` | Heat |
| `frost_protect` | `04 00 00 00 00 00` | Heat |
| `user_timer` | `01 03 00 00 00 00` | Auto |
| `home_all_day` | `01 02 00 00 00 00` | Auto |
| `out_all_day` | `01 01 00 00 00 00` | Auto |

The climate entity maps the modes as follows:

- **Heat**: presets `manual`, `eco`, `frost_protect`, `boost` and `away`. The target temperature is writable.
- **Auto**: presets `user_timer`, `home_all_day`, `out_all_day`, `boost` and `away`. The programme sets the temperature. A setpoint written during a programme is dropped when the programme is selected again (AM2086, 2026-11-24).
- **Switching to Auto**: resumes the last programme the heater reported, User Timer by default.
- **Unknown programme byte**: shown as the `user_timer` preset.
- **Before a mode write**: the integration ends a running Boost. If Away is active, it writes the Away record with byte 0 `00` and the new mode in one link operation.
- **Observed**: Manual at 19 °C or less reads back as Eco ([ledger](research/investigation-2026-11-24/ledger.md), 2026-11-25).

## Clock

`0/0x0006` holds the heater's local time. The heater runs its timer programmes on its own clock. The integration keeps that clock on Home Assistant's local time, as ConfigR does.

| Byte | Meaning |
|---|---|
| 0 | Century, constant `20` (`0x14`) |
| 1 | Year modulo 100 |
| 2 | Month, 1 to 12 |
| 3 | Day of month |
| 4 | Hour, 0 to 23 |
| 5 | Minute |
| 6 | Daylight saving time (DST) flag: `1` when the local zone's `dst()` is not zero |

- **When**: the integration reads the clock once on every new connection, before the operation's own reads.
- **Write**: when the heater is more than 60 seconds off and the **Sync the heater clock** option is on. It writes whole minutes and does not read back.
- **Failure**: a failed or unreadable clock read never fails the operation. The check runs again on the next connection.
- **DST flag**: Europe/Dublin has a negative winter DST in the time zone database, so the flag is `1` in November. That matches the heater ConfigR had set.
- **Evidence**: on 2026-11-25 three heaters were 138 to 144 minutes slow. One write fixed each of the four heaters ([clock research](research/configr-clock-schedule-boost.md)).

## Boost

Boost uses `0/0x1027` for the minutes left and `0/0x1028` for the temperature. Boost heats to a set temperature for a set time, then the heater returns to its previous mode by itself. ConfigR calls it Boost and its code calls it Runback.

- **Start**: write `0/0x1027` = minutes as little-endian 16-bit, then `0/0x1023` = the Boost temperature as `[°C, 0]`. Both writes run in one link operation. The length comes from the **Boost length** option, 60 minutes by default.
- **Accepted lengths**: 1 to 240 minutes. The heater ignores 241, 255 and 300 and does not start Boost.
- **Running**: `0/0x1027` counts down once a minute. The climate shows the `boost` preset while it is above 0.
- **Mode byte during Boost**: from a continuous mode, `0/0x1001` reads `02 00 00 0n`. From a programme, it keeps `01 0p 00 0n`. Byte 3 is a band: `1` up to 30 minutes, `2` up to 60, `3` up to 120, `4` up to 180, `5` up to 240.
- **End**: write `0/0x1027` = `00 00`. The previous mode and setpoint return within 1.5 seconds; the integration waits 1.5 seconds before it continues.
- **Boost temperature**: `0/0x1028` = `[°C, 0]`. The number entity writes it within the heater's setpoint range. A running Boost keeps its target; the next Boost uses the new value. On AM2086 the display showed 21 °C for `15 00`, and a written `17 00` showed 23 °C (2026-11-25).
- **Away**: the integration refuses to start Boost during Away and ends Boost before it starts Away. Boost during Away was never tested.
- **Not used**: `0/0x1017` Runback and mode `05`. ConfigR does not write them to start Boost either.

ConfigR writes only the low byte of `0/0x1027` followed by `00`. For 1 to 240 minutes this is the same payload as the integration's little-endian 16-bit value. Evidence: [clock, schedule and Boost research](research/configr-clock-schedule-boost.md) and the [ledger](research/investigation-2026-11-24/ledger.md).

## Setback

`0/0x1016` controls Setback. Setback holds a fixed temperature during the Heating Off periods of a timer programme.

| Byte | Meaning |
|---|---|
| 0 | `00` off, `01` on |
| 1 | Temperature, whole °C, 7 to 18 |

- **Read**: on the 5-minute settings schedule. AM6532 read `00 08` (off, 8 °C) on 2026-11-19.
- **Write**: the switch changes byte 0 and the number changes byte 1. Each write reads the live record first and keeps the other byte, inside one link operation.
- **Evidence**: on AM2086, on and off read back on 2026-11-24. On 2026-11-25 the display showed 8 °C for `08`, and a written `0F` showed 15 °C. The range 7 to 18 °C comes from the PLXE manual, page 23.

## Away

`0/0x102A` holds the Away record. Away holds a fixed temperature until a return time. The record is 7 bytes.

| Byte | Meaning |
|---|---|
| 0 | `00` inactive, `01` active |
| 1 | Year minus 2000 |
| 2 | Month |
| 3 | Day |
| 4 | Minute |
| 5 | Hour |
| 6 | Away temperature, whole °C |

The minute comes before the hour. AM6532 read `00 1a 0b 11 30 06 07` on 2026-11-19: inactive, 2026-11-17 06:48, 7 °C.

- **Any write enters Away**: byte 0 `00` with a future return time entered Away. So did a return time equal to now (AM2086, 2026-11-24 18:44 and 18:48).
- **Start Away**: the climate `away` preset or the Away return time entity writes byte 0 `01` and the return time. The integration reads the live record first and keeps its temperature.
- **Return time for the preset**: the last return time still in the future. Without one, the preset is refused and the return time entity must be set.
- **Away temperature**: writable only while Away is active, 7 to 18 °C. The integration rewrites the live record with the same return time. The bounds come from the Dimplex cloud client; the 7 °C default is in the PLXE manual, page 17.
- **Leave Away**: the record with byte 0 `00`, then the new heating mode, in one link operation. The mode write ends Away.
- **Malformed record**: when the live temperature is outside 7 to 18 °C, the integration writes 7 °C, so a rewrite never produces an unheated Away.
- **Stale record**: the heater keeps the last record after Away ends. The integration shows the return time and temperature only while byte 0 is `01`.

## Timer programmes

`0/0x1002` gives access to the timer programmes. The heater stores three weekly programmes: Out All Day (`01`), Home All Day (`02`) and User Timer (`03`). The characteristic holds one programme day of 31 bytes at a time.

| Offset | Meaning |
|---|---|
| 0 | `00` select or read, `01` save |
| 1 | Programme, `01` to `03` |
| 2 | Day, `00` Monday to `06` Sunday |
| 3 + 6i, i = 0 to 3 | Period i: start hour, start minute, end hour, end minute, temperature °C, `00` |
| 27 to 30 | Padding, `00` |

- **Read**: write `[00, programme, day]`, then read 31 bytes. The integration reads 21 days per heater and discards a record whose programme or day differs from the request.
- **Unused slot**: start equals end. The integration skips such slots when it reads.
- **Save**: write the day record with byte 0 `01`, then select and read the same day back. The heater accepts one day per write and leaves the other days and the mode unchanged.
- **Save layout**: periods sorted by start, whole minutes, start before end, no overlap, at most 4. Unused slots repeat the last end time as start and end with 21 °C. An empty day uses `00 00 00 00 15 00` four times.
- **Temperature**: whole degrees within the heater's setpoint range (`0/0x1006`).
- **Failure**: a day that reads back different fails the action. When the heater is unreachable, the days not yet saved wait on disk and are written after the next successful poll.
- **Evidence**: factory defaults on all four heaters decode as ConfigR's tables (2026-11-24). Single-day save and restore read back byte for byte on AM2086 (2026-11-25). Layout and history: [schedule format](research/schedule-format.md).

## Identity

Four group 3 characteristics identify the heater. None of them is ever written.

- **`3/0x2002` appliance ID**: ConfigR `Gdid`, the Glen Dimplex ID (GDID). 6 bytes, stored byte-reversed. The label form is the 6 bytes reversed and printed as 12 uppercase hexadecimal digits. An all-zero value reads as unknown. The integration reads it on the 5-minute settings schedule and uses it only for the firmware lookup. AM2086's value matched its label on 2026-11-25. Diagnostics never show it.
- **`3/0x2005` model**: ASCII `PLX050E;C;` with zero padding. The integration shows `PLX050E Series C`. Reading the single letter as the product series is an inference.
- **`3/0x200A` product code**: ASCII `EP4213`. The firmware update refuses an image whose EP number does not match it.
- **`3/0x2009` RF revision**: one byte, `05` on all four heaters. The device page shows it with the UI version from `3/0x2001`.

## Power loading

`3/0x000A` holds the element ratings. The heater has no power meter. The integration estimates power from the rated power and the Trac output percentage.

| Bytes | Meaning |
|---|---|
| 0 to 1 | Element 1 power, watts, little-endian 16-bit |
| 2 to 3 | Element 2 power in ConfigR; meaning on PLXE unknown |
| 4 to 7 | `00` |

- **Rated power**: the first word. AM6532 reads `f4 01 98 08 00 00 00 00`: 500 W, which matches the PLX050E rating. The second word reads 2200 and is not used.
- **Calculated power**: rated power × output percentage (`1/0x0002` byte 2) ÷ 100.
- **Estimated energy**: the calculated power integrated over time, in kWh. Time without a successful poll is not counted.

## Firmware update characteristics

Two characteristics carry the firmware update: `1/0x300E` (update mode) and `00060001-f8ce-11e4-abf4-0002a5d5c51d` (frames and replies). The integration touches them only inside an update. [Firmware update](firmware-update.md) owns their encoding and sequence.

## Characteristics the integration never writes

The integration has no code path that writes these characteristics. Some of them it never reads either.

| Address | ConfigR name | Reason |
|---|---|---|
| `2/0x1030` | FactoryReset | Resets the heater. Absent from the AM6532 inventory; never probed |
| `3/0x2006` | Btpk | Security material, 5 bytes. Never read or written |
| `3/0x2007` | EncKey | Security material, 16 bytes. Never read or written |
| `3/0x2008` | AuthKey | Security material, 16 bytes. Never read or written |
| `2/0x1033` | Security | PIN lock state and 32 random bytes. Absent on AM6532 |
| `0/0x1012` to `0/0x1015` | none (ConfigR's split schedule writes for MTU 20 or less) | Never read: a read of `0/0x1012` drops the link on PLXE heaters |
| `0/0x1006` | SetpointRange | Read only. Five writes on AM2086 were acknowledged and never applied (2026-11-24) |
| `0/0x1001` mode `06` | HeatingMode Standby | A Standby write was accepted and ignored on AM2086 (2026-11-25). No climate Off mode exists |
| `3/0x2002` | Gdid | Read only. ConfigR marks it read and write |
| `3/0x000A` | PowerLoading | Read only. Installer setting |
| `2/0x100C` | RfComms | Not read or written. A write could cut the radio link to a hub |
| `1/0x1020` | TestMode | Factory test. Not read or written |
| `1/0x1010` | EolTest | Factory test. Not read or written |
| `0/0x7026` | ActivateProduct | One-time activation that leaves the product in manual mode. Absent on AM6532 |
| `0/0x7020` | ButtonPressOperation | Button mapping, includes a pairing trigger. Absent on AM6532 |
| `0/0x1017` | Runback | Not needed for Boost. AM6532 reads `01 7c` |
| `1/0x100E` | OtaEnable | The older, non-ESP32 update path. Absent on AM6532 |

Sources: [ConfigR entity gap](research/configr-entity-gap.md) and the [capability matrix](research/capability-matrix.md).

## Unknown characteristics

The AM6532 inventory of 2026-11-19 holds these characteristics with no decoded meaning. The integration does not read them.

- **`0/0x1003`**: mirrors `0/0x1023`, `12 00` at an 18 °C target
- **`1/0x0001`**: `[selector, R, G, B]`, a backlight colour candidate
- **`1/0x0007`**: ConfigR LastKeyPressValue, a transient key event (`Auto=21`, `Off=22`, `Manual=23`)
- **`2/0x100A`, `2/0x100B`, `2/0x100F`, `2/0x1018`**: one byte each
- **`2/0x700F`**: a disconnect address in some ConfigR profiles
- **`3/0x1022`, `3/0x1029`, `3/0x1050`**: unnamed; `3/0x1029` repeats the backlight bytes
- **`3/0x2003`, `3/0x200B`, `3/0x200C`, `3/0x200F`**: unnamed
- **`3/0x2004`**: ConfigR Brand, `<Dimplex>` with padding
- **`3/0x200D`**: `PGLHS600`, a board or software code
- **`0/0x1001` bytes 2 to 5**: ConfigR names them; the heater's use outside Boost is unknown
- **`3/0x000A` bytes 2 to 3**: the second power word

Raw values: [capability matrix](research/capability-matrix.md#complete-live-am6532-gatt-inventory).

## Where the archive differs from the code

The research archive records the state at the time of writing. The code is the current reference where the two disagree.

- **GDHV alias group**: [ConfigR entity gap](research/configr-entity-gap.md) gives the alias service as `000(G+1)`. The code uses `000G` for both the service and the characteristic.
- **Away record size**: the [capability matrix](research/capability-matrix.md) catalogue lists `0/0x102A` as 8 bytes. The code parses and writes 7 bytes, which matches the 7-byte AM6532 read.
- **Away activation**: the capability matrix says activation requires a future return time. Any write enters Away; the code follows the ledger result of 2026-11-24.
- **Clock cadence**: the capability matrix and the clock research mention an hourly check on a held link. Held links were removed on 2026-11-25; the code checks once per connection.
- **Schedule MTU**: [schedule format](research/schedule-format.md) says proxies use a 517-byte MTU. In the code only the uncached update connect gets 517; cached connects stay at 23.
- **Setting status**: the capability matrix lists `0/0x100D`, `0/0x1016`, `2/0x1019` and `0/0x1028` as read only or unverified. All four are now written and verified.
- **`3/0x2002`**: the entity gap and the capability matrix say "keep blocked". The code reads it for the firmware lookup and never writes it.
- **Passkey and label code**: [OTA research](research/ota-research.md) section A.3 says the pairing PIN is not the label code. The code sends the passkey as the label code, and Dimplex accepts it.
