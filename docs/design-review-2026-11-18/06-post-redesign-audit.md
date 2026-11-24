# Post-redesign UI audit

## Applied findings

- Climate uses only `HVACMode.HEAT`, `TARGET_TEMPERATURE`, and `PRESET_MODE`; unsupported fan, humidity, swing, and power features remain absent (`custom_components/dimplex_ble/climate.py:63-81`).
- Presets now use Dimplex-facing names: User Timer, Home All Day, Away Mode, Manual, Eco Mode, and Frost Protect (`translations/en.json:59-65`).
- Same-mode `HEAT` requests are now no-ops, including while Away Mode is active. Clicking the only HVAC mode no longer silently cancels a preset (`climate.py:204-222`).
- Away return time is a native datetime control. Setting it activates Away with an explicit future deadline; selecting Away without one is rejected (`datetime.py:32-53`, `coordinator.py:163-205`).
- Climate details expose the reported Away deadline and Away target; the datetime entity provides the usable control (`climate.py:156-169`, `translations/en.json:67-80`).
- Sound now exposes translated explanation attributes, matching the existing Adaptive Start and Open Window Detection guidance pattern (`switch.py:46-80`, `translations/en.json:83-119`).
- LCD/controller temperature now declares `TEMPERATURE`, Celsius, diagnostic category, disabled-by-default, and `MEASUREMENT` state class (`sensor.py:105-118`).
- Pairing copy no longer falsely claims that the PIN is not saved; it explains config-entry storage for automatic bond recovery (`translations/en.json:15-20`).
- User Timer, Home All Day, and Away Mode documentation now distinguishes stored schedules from the explicit Away deadline (`README.md:121-139`).

## Deliberately not implemented

- No schedule editor: PLXE Series C `0/0x1002` write semantics are not verified for this device.
- No Away target-temperature write control: the safe local target mapping is not independently verified; the reported target is visible instead.
- No Out All Day preset: the payload is documented by Dimplex but not verified as a safe write on the target model.
- No custom frontend: the integration uses Home Assistant native entity UIs.

## Verification

- Ruff, strict mypy, JSON validation, and pre-commit passed.
- 483 pytest tests passed with 100% module coverage.
- Function coverage: 194/194 (100%).
- Live frontend DOM/screenshot inspection was not performed in this local audit; native HA frontend behavior is based on the supplied current climate documentation.
