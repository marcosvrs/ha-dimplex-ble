# Handoff

```text
/make-plan Redesign the Dimplex panel heater timer and Away controls while preserving the native Home Assistant climate surface.

Primary user: Home Assistant user controlling a Dimplex PLXE Series C panel heater locally.
Primary task: Select a documented heating behavior and target/return time without hidden defaults or misleading climate semantics.
Constraints: Native HA entities; no custom frontend; official Dimplex names; no unverified schedule writes; preserve stable entity IDs; HA climate semantics remain heat-only.

Current evidence:
- Dimplex documents User Timer, Home All Day, Out All Day, and Away as Timer Modes.
- Away requires hours, return date, and target temperature and can be ended early.
- Current climate.py exposes a single preset selector and silently falls back to a 24-hour Away expiry.
- Verified payload 01 03 is User Timer, but the current label is Timer Mode.
- Schedule field 0/0x1002 is not verified for safe PLXE Series C writes.

Preserve:
- Native climate target-temperature control and heat-only HVACMode.HEAT (`custom_components/dimplex_ble/climate.py:66-84`).
- Stable translated presets and device readback (`climate.py:132-157`, `translations/en.json:57-64`).

Discard or change:
- Hidden `now + 24 hours` Away fallback (`climate.py:192-210`).
- The ambiguous Timer Mode label for the verified User Timer payload.

Priority:
1. Make Away's return date/time explicit through a native datetime entity or an equivalent native HA action flow.
2. Rename the verified 01 03 preset to User Timer; retain Home All Day as a schedule-selection preset, not a countdown.
3. Surface the Away deadline/target and explain that schedule editing is unavailable until the model's schedule protocol is verified.
4. Add icons for all verified presets.

Deliverables:
- State model for Away deadline, target, activation, expiry, and early cancellation.
- Native HA UI flow with no hidden duration default.
- Migration behavior for existing entries and stable unique IDs.
- BDD black-box tests for activation, explicit deadline, expiry, cancellation, restart, and unavailable state.
- Translation and icon updates.
- Documentation that distinguishes Timer Mode, User Timer, Home All Day, Out All Day, and Away.

Do not:
- Map any Dimplex timer mode to HVACMode.AUTO.
- Invent a schedule editor or write 0/0x1002 without model-specific evidence.
- Add a custom frontend or duplicate the native climate controls.
``` 
