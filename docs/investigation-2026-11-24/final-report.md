# Final report: Dimplex BLE review, root causes and refactor (2026-11-24)

Evidence for every claim is in [`ledger.md`](ledger.md); design in
[`design-addendum.md`](design-addendum.md). **VERIFIED** = observed on hardware or
in upstream source; **INFERENCE** = consistent with evidence, not proven.

## 1. Verified native capabilities (used as-is)

| Need | Native mechanism |
|---|---|
| Connect through a chosen proxy | `async_scanner_by_source(...).connector.client` + bleak-retry-connector `establish_connection` (habluetooth cannot pin a route; maintainer declined) |
| Pair / re-encrypt | bleak `pair()` → ESPHome API `PAIR` → `esp_ble_set_encryption` |
| Answer the heater's PIN | stock ESPHome `ble_client.on_passkey_request` + `passkey_reply` (YAML only) |
| Tell HA the PIN is needed | stock `homeassistant.event` (`esphome.dimplex_passkey_request`) |
| Hand the PIN to the proxy | stock API `actions:` called on the ESPHome entry's API client |
| Wait for the heater | `bluetooth.async_register_advertisement_callback` (fires on every advertisement; the deduping APIs do not) |
| Bond survives failed re-encryption | ESP-IDF 5.5.6 + ESPHome 2026.11 default wraps: 9/9 re-encryptions, 0 bonds lost |
| UI | HA climate/number/switch/datetime/select entities, config/options/reauth/reconfigure flows, repairs, translations, icons |

## 2. Upstream limitations (not worked around in code beyond the minimum)

- ESPHome native API has no passkey message → PIN answered on the ESP32
  (draft (c) in `docs/upstream-issue-drafts.md`; esphome/feature-requests#2013).
- ESP32-S3 controller refuses a third uncached link at 7.5 ms (esphome#22859,
  won't fix) → documented limit of 2 heaters per proxy.
- `Conn param update failed, status=19` on every connect (draft (b)); no
  functional effect observed.
- Cancelling a connect while the link forms poisons the GATT record until reboot
  (draft (a), ESP-IDF).
- Dimplex firmware is cloud-only via ConfigR (account + registered appliance);
  the integration stays local and only shows the installed version.

## 3. Root causes

| ID | Symptom | Root cause (VERIFIED) | Fix |
|---|---|---|---|
| RC-1 | Living-room proxy unreachable, `EncryptionHelloAPIError` | API `max_connections` exhausted by 17 orphaned `esphome logs/run` processes | killed; rule: bounded captures, `run --no-logs` |
| RC-2 | Bond lost after a failed reconnect | IDF deletes the bond on any failed `AUTH_CMPL` except reason 5 | never abort SMP; IDF 5.5.6 wraps |
| RC-3 | status 133 on every connect until proxy reboot | cancelled in-flight connect leaves GATT tcb "closing" | never cancel a connect (run to its natural end) |
| RC-4 | Third heater on a proxy never connects after HA restart | controller `0x200d Rejected: Resources` at fixed 7.5 ms | ≤2 heaters per proxy, picker warns |
| RC-5 | PIN prompt missed / pairing hangs | heater starts SMP on connect; responder armed too late | arm before connect |
| — | Reconnect rejected right after disconnect | heater needs settle time (ConfigR waits ~10 s) | `HEATER_SETTLE_S = 10` |
| — | Heater invisible to every receiver after an abnormal link loss | heater-side BLE stall (AM8224, AM2086; phone scan confirms) | Repair issue + power-cycle; per-poll option as experiment |

## 4. Rejected hypotheses (selection)

- "The heater refuses a second central" — rejected: heaters keep bonds with ≥2
  centrals (H14).
- "Away temperature only activates Away with a future deadline" — rejected by a
  second test: any write of the Away record enters Away.
- "Setpoint limits are writable" — writes ACKed, never applied.
- "PLXE offset range is ±0.2 °C (manual)" — device accepts ±3.0 °C.
- "Pin IDF 5.5.5 to protect bonds" — not needed on 5.5.6 (H9).
- "AM8224 is simply broken" — isolated with a test ESP32 and a phone: the
  stall follows abnormal link loss on two heaters, not one unit.

## 5. Removed workarounds

Custom C++ ESPHome component `dimplex_passkey_proxy` (294 lines + 206 lines of
C++ tests/stubs), `esphome_proxy.yaml`, the HA-side relay/commissioning/route
policy/pairing modules, the custom client and exception hierarchy, the text
entity used for PIN entry, the IDF version pin, setpoint-limit numbers, the
components bind mount on Jarvis, stale ESPHome devices/files.

## 6. Remaining custom code and justification

| Code | Lines | Why it exists |
|---|---|---|
| `link.py` | 549 | Pinned proxy route, per-proxy security lock, settle rule, never-cancel connect (RC-2/3/5); nothing upstream offers these |
| `responder.py` | 146 | Arms/clears the YAML PIN responder over the proxy's API client (no native passkey API) |
| `errors.py` | 90 | Classifies SMP/ATT codes (81 = wrong PIN) for reauth vs retry |
| `stall.py` | 114 | Repair issue for the heater-side BLE stall |
| `firmware/dimplex_pin_responder.yaml` | 110 | Stock components only; replaces the C++ relay |
| `protocol.py` | — | Dimplex parameter encoding (no library exists) |

## 7. Metrics (main → branch)

| Metric | Before | After |
|---|---|---|
| Lifecycle Python (connect/pair/relay modules) | 1210 lines | 899 lines (`link`, `responder`, `errors`, `stall`) |
| Custom ESP32 C++ | 294 (+206 test) | 0 |
| Integration total | 4547 lines | 4315 lines, with 6 more controls and the stall repair |
| Tests | 7781 lines | 5608 lines; 507 tests, 100% line coverage, 229/229 functions |
| Mutation (advisory) | — | 2171 killed / 552 survived / 84 timeout of 2807 |
| Failed polls | 963 in 76 min, 41 episodes (12:20–13:36) | 5 in 110 min, all in the first 15 s after an HA restart; 0 afterwards (soak interim) |

## 8. Hardware results

- PIN commissioning from the HA UI: correct PIN, wrong PIN (retries with
  "show PIN again"), slow entry, removed bond re-created — PASS.
- Re-encryption on IDF 5.5.6: 9/9 plus 101/101 in the soak; 0 bond losses.
- Controls on AM2086: Advance, Setback, Adaptive start, Open window, Sound,
  Away temperature/return time, offset ±3.0 °C — PASS; setpoint limits removed.
- Soak interim (19:24–21:14 UTC): 1567/1572 polls ok; 0 pairing, GATT, link-loss
  or stall events; AM8224 per-poll 102/102 connects with clean disconnects.
  24 h checkpoint: 2026-11-25 19:24 UTC.
- Independent adversarial review (Codex): 7 findings, all fixed and tested.

## 9. Open items

- 24 h soak result and the per-poll vs persistent comparison (needs days).
- Trigger of the heater BLE stall (heater firmware; not fixable locally).
- Boost (encoding unverified), schedules (not implemented).
- Filing upstream drafts (user decision).
- User decisions: rotate proxy API keys, purge pre-15:00 HA logs, move the HA
  integration folder off the old workspace mount, configure or ignore AM5832 and
  AM8922, remove the "Android Device" Google sign-in.
