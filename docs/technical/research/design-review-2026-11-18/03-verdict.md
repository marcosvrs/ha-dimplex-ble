# Verdict

**REDESIGN the timer/Away control flow, while preserving the native climate surface.** The current climate entity is structurally sound, but Away's hidden 24-hour fallback violates usefulness, understandability, and honesty; Timer Mode also conflates Dimplex's timer family with the specific User Timer payload.

## Highest-leverage moves

1. **Make Away's deadline explicit.** Add a native, visible Away return date/time control or an equivalent explicit action flow. Never silently choose 24 hours when the device requires hours/date/target.
2. **Rename the verified `01 03` preset to “User Timer”.** Keep “Home All Day” as a schedule-selection preset; do not imply it is a countdown.
3. **Expose capability honestly.** Do not add a fake schedule editor until `0/0x1002` schedule read/write semantics are verified for this model. Add a concise entity description or documentation link explaining that schedules remain configured on the heater.
4. **Make Away state visible.** Show the selected return deadline and Away target as read-only state if a native control cannot be added in the same interaction; stale/expired values must not be hidden behind the preset label.
5. **Add icons for all verified presets.** Supply native icon translations for Home All Day, Away, Eco, and User Timer, not only Manual, Timer Mode, and Frost Protect (`icons.json:7-12`).
