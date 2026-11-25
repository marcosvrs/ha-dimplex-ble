# Timer programmes

Each heater stores three weekly timer programmes and runs them on its own, even when Bluetooth is down. This page explains how to read, change and restore them from Home Assistant.

## How the programmes work

A programme defines, for each weekday, up to 4 periods with a start time, an end time and a temperature. Outside the periods the heater doesn't heat, unless Setback holds the Setback temperature.

The three programmes are:

- **User Timer**: your own schedule; shown as the **User Timer schedule** calendar
- **Home All Day**: shown as a calendar, disabled by default
- **Out All Day**: shown as a calendar, disabled by default

The heater follows a programme while the climate entity is in **Auto** with that programme's preset. Programmes run on the heater clock, which Home Assistant keeps in sync unless you turn **Sync the heater clock** off.

## Change a programme

`dimplex_ble.set_schedule` replaces the periods of one programme on the days you choose.

```yaml
action: dimplex_ble.set_schedule
data:
  device_id: your_heater_device_id
  programme: home_all_day
  days: [sat, sun]
  periods:
    - start: "08:00"
      end: "22:00"
      temperature: 20
```

Each period needs:

- **start**, **end**: whole minutes, `HH:MM`; the end comes after the start
- **temperature**: whole degrees within the heater's setpoint range (7 to 30 °C on the tested heaters)

Periods on a day must not overlap; at most 4 per day. `periods: []` removes all heating periods from the chosen days.

Home Assistant writes one day at a time and reads it back. The action fails if the heater didn't keep a day. Days already written stay changed.

## When the heater can't be reached

A change saved while the heater is unreachable isn't lost. The action succeeds, Home Assistant keeps the change across restarts and shows the repair **Heater: schedule change waiting**. It writes the change after the next successful update. Days written before the heater dropped the link apply at once; the other days keep their previous periods until then. `get_schedule` shows which days still wait. If the heater then refuses a waiting day, Home Assistant drops it and logs a warning.

## Read a programme

`dimplex_ble.get_schedule` returns `read_at`, the time of the last read, and per programme:

- `heater`: the programme as last read from the heater
- `pending`: waiting changes not yet written
- `backup`: the backup copy kept by Home Assistant

Days are lists of `[start, end, temperature]` periods, so a script can copy one day to others.

## Restore a programme

`dimplex_ble.restore_schedule` writes the backup copy back to the heater, for one programme or every programme with a backup. Use it after a factory reset. The backup belongs to the heater's entry: deleting the entry deletes it, so a replacement heater starts without one. Copy the programmes from `get_schedule` before removing an entry.

The backup starts from the first read that returns all 7 days of a programme and then follows only the changes saved through Home Assistant. A heater reset to its factory programmes therefore never overwrites it. A reset before that first complete read leaves no backup of the earlier programme. Only complete weeks are restored; when one requested programme has no complete backup, nothing is restored.
