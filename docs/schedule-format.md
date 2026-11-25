# Timer programme schedules (`0/0x1002`)

Read since 2026-11-24, saved since 2026-11-25 (`dimplex_ble.set_schedule`). Static analysis of ConfigR 3.12.2
(`ConfigR.dll` IL, method names below) plus live reads of four PLX Series C
heaters. **VERIFIED** = read in the IL or observed on hardware;
**INFERENCE** = consistent, not proven.

## Record

The characteristic holds **one (programme, day) record of 31 bytes** at a time
(VERIFIED: `ScheduleAndSetTemperatureParameter` size 31, `DayData.ReadFrom`
copies 31 bytes; the earlier "0x1F mask" note was the record length, not a mask).

| Offset | Meaning |
|---|---|
| 0 | Operation flag: `0` = select/read, `1` = save (ConfigR sets it to 1 only in `WriteTimerModeDetails`) |
| 1 | Programme: 1 Out All Day, 2 Home All Day, 3 User Timer |
| 2 | Day: 0 Monday … 6 Sunday (`EDayOfWeek`) |
| 3 + 6·i (i = 0..3) | Period: start hour, start minute, end hour, end minute, temperature (whole °C), 0 |
| 27–30 | Padding (0) |

At most 4 periods per day. ConfigR fills unused slots with
`start = end = last end time`, temperature 21, and drops any slot whose start
equals its end when reading (`DayData.SetObjectFromBytes`). Periods are sorted
by start time.

## Read (what the integration does)

For each programme and day, inside one GATT operation: write the 3 bytes
`[0, programme, day]`, then read 31 bytes (`BLEService.ReadTimerModeDetails`,
MTU > 20 path). The integration only ever builds the `0` form
(`encode_schedule_request`) and rejects a record whose programme or day differs
from the request. A plain read without a selection returned 31 zero bytes on
AM6532 earlier (no selection yet; INFERENCE).

Cadence: once after setup and every 6 hours (persistent link: over the held
link; per-poll mode: inside the next poll's connection). 21 select+read pairs
per heater.

## Verified on hardware (2026-11-24 21:48 UTC)

All four heaters returned 3 × 7 valid days. Factory defaults decode exactly as
ConfigR's `Constants` tables (User Timer 06:30–09:30, 11:00–13:00, 15:00–17:00,
18:00–22:00; Home All Day 08:00–21:00; Out All Day 07:00–08:30, 17:30–22:00; all
21 °C). AM6532's Monday User Timer is customised (05:30–07:30, 16:00–17:00,
21:00–23:00). Heater modes and settings were unchanged after the reads.

## Save

Write the day record with byte 0 = 1 (VERIFIED on AM2086, 2026-11-25). The
heater accepts **one day per write**: saving Monday of User Timer changed only
Monday, left Tuesday and the heater mode unchanged, and the next `[0, p, d]`
read returned exactly the written periods. The empty-day form (four
`00 00 00 00 15 00` slots) and a restore of the backed-up factory record also
read back byte for byte. ConfigR itself writes all 7 days, then reads them
back; the integration writes only the chosen days, then reads each back and
fails the action when one differs.

Encoding (ConfigR `WriteTimerModeDetails`): periods sorted by start, whole
minutes, start before end, no overlaps, at most 4; unused slots repeat the last
end time as start and end with 21 °C (00:00 on an empty day). Each period's
last byte is 0. The split characteristics `0x1012`–`0x1015` (ConfigR's MTU ≤ 20
fallback) stay blocked; proxies use a 517-byte MTU.

## Open questions

- The heater clock was 2 h 17 min – 2 h 24 min slow on three heaters
  (2026-11-25 03:31 UTC), which explains AM2086 heating at 21:26 in Home All
  Day (its clock showed about 19:09). The integration now sets the clock like
  ConfigR (local time, when more than 60 s off), so the calendars and the
  heater agree.
- Behaviour at a period boundary after a setpoint override in a programme.
