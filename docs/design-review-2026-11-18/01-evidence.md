# Evidence

## Structural

- The climate module exposes one HVAC mode, `HEAT`, six presets, a target-temperature control, and HVAC action (`custom_components/dimplex_ble/climate.py:66-84, 87-165`).
- The preset selector is a single native HA control. It has no parameterized duration/date step (`climate.py:280-299`).
- Away activation silently selects an existing future expiry or falls back to `now + 24 hours` and a 7 °C target (`climate.py:192-210`).
- No datetime/number entity exists for Away return date, Away hours, or Away target. Existing select support is display temperature unit only (`select.py:18-60`).
- Timer schedules are not exposed. The repository explicitly records schedule writes as unverified/out of scope (`docs/capability-matrix-source-research.md:32`, `docs/capability-matrix.md:610-617`).

## Protocol and product semantics

- The official PLXE Series C manual identifies User Timer, Home All Day, Out All Day, and Away as Timer Modes; User Timer, Home All Day, and Out All Day have four configurable daily periods and a Select/Modify/Preview flow (manual Issue 7, pages 15–16).
- The manual says Away Mode is temporary, restores the previously active mode, and requires hours, return date, and target temperature; the Back button ends it early (manual Issue 7, pages 17–18).
- Timer Mode target changes while a heating period is active are temporary for that instance, not permanent schedule edits (manual Issue 7, page 14).
- The current translation labels the custom preset `timer_mode` as “Timer Mode”, although the verified payload is the User Timer submode (`translations/en.json:57-64`, `protocol.py:21-29, 247-271`).

## Copy and honesty

- “Away” is presented as a normal climate preset, but selecting it can create a hidden 24-hour expiry (`climate.py:192-210`). This is a label-to-behavior mismatch: the user cannot see or choose the return time in the same interaction.
- “Home All Day” is not a countdown. It selects the stored all-day schedule; the UI should not imply a duration field. The current label is honest but lacks explanatory context.
- “Timer Mode” is ambiguous because Dimplex uses it as the family name while the verified `01 03` payload is specifically User Timer. The current label obscures that distinction.
- The existing climate translations provide names but no per-preset explanation (`translations/en.json:57-64`). Native HA climate preset menus have no standard description field per option.

## Weight/friction

- No integration-specific frontend JavaScript is shipped. The native HA climate UI is reused.
- The primary friction is interaction semantics, not bundle size: Away requires at least one hidden policy decision (24-hour fallback) and schedule editing is absent by design.

## Accessibility and states

- Native HA climate controls provide the platform's standard keyboard/focus/disabled behavior; this audit did not inspect a live DOM.
- The integration returns `None` for unknown/missing mode data and marks entities unavailable without a trusted snapshot (`climate.py:87-115, 132-157`).
- No dedicated empty/loading/error explanation exists for a missing Away return time because no such control currently exists.
