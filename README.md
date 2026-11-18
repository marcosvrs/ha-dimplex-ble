# Dimplex BLE

Model-neutral Home Assistant integration for Dimplex BLE heaters. Home Assistant's Bluetooth integration aggregates local adapters and ESPHome Bluetooth proxies; each discovered physical heater gets one config entry and one device. No heater MAC addresses, room names, or per-heater credentials are configured in YAML.

## Scope

- Native `climate` entity for the heater-reported Celsius target and, when available, room temperature.
- Heater-reported minimum/maximum setpoint range; target writes are whole-degree Celsius and are read back from the heater. The integration does not change operating mode as a side effect.
- Display-temperature-unit `select` for the observed Celsius/Fahrenheit setting.
- Error-code `sensor` that retains every numeric value, including unknown codes, plus the exact raw status bytes in an attribute. No unknown code is discarded or assigned an invented meaning.
- Raw operating-mode bytes are available as a diagnostic entity, disabled by default until their interpretation is established.
- `get_gatt_services`, `read_gatt`, and administrator-only `write_gatt` response actions expose service metadata and raw characteristic bytes without adding model-specific entities for unverified fields.

Raw actions accept Dimplex service groups 0–3 and a 16-bit characteristic ID in decimal (for example, `0x1023` is `4131`). Payloads and responses are hexadecimal. The integration blocks the characteristics already identified as unsafe, including device-security material and writes to the Cypress bootloader services. `get_gatt_services` returns metadata only; it does not read characteristic values.

The GATT client resolves the documented Bluetooth SIG and GDHV vendor UUID bases. GDHV lookup has unit coverage but has not been verified against a live GDHV-based heater.

## Actions and examples

The integration provides no conditions or triggers. Use Home Assistant's native climate action for target temperature, or the response actions for raw GATT access. The `device_id` is selected from the device registry; it is not a heater MAC.

Read an unmapped error/status value (`0x1011`, decimal `4113`):

```yaml
action: dimplex_ble.read_gatt
data:
  device_id: YOUR_DEVICE_ID
  service_group: 0
  characteristic: 4113
response_variable: raw_status
```

`raw_status.value` contains the bytes as hexadecimal. To set a climate target, call `climate.set_temperature` for that heater's climate entity; the integration writes the target characteristic and then reads heater state back.

The administrator-only write action uses the same device, group, and characteristic fields, plus a hexadecimal `payload`. For example, group `0`, characteristic `4101` (`0x1005`), payload `"01"` requests Fahrenheit display. The returned response includes the written bytes and a readback when that characteristic is readable. Do not write undocumented values unless you can safely verify their effect on the heater.

## Supported devices

Models observed during protocol research include QRAD100E Series D and PLX050E Series C. The integration discovers devices from their advertisements, not from a per-heater allowlist. Unverified characteristics remain raw and are not given invented meanings.
## Installation

1. Install `custom_components/dimplex_ble` under Home Assistant's `custom_components` directory.
2. Restart Home Assistant.
3. Open **Settings → Devices & services → Add integration → Dimplex BLE**, or accept Home Assistant's Bluetooth discovery.
Accepting Home Assistant's Bluetooth discovery **Add** immediately arms the selected ESPHome proxy and starts pairing through that scanner's source-specific connector, before the PIN form appears. The encrypted ESPHome API actions relay the PIN. The upstream ESPHome Bluetooth Proxy still handles scanning and GATT; the external component only adds the missing passkey callback.

The manual flow lists each distinct proxy-reachable heater with its advertised name and signal strength; the PIN form repeats the selected advertised name so the user can identify the heater while pairing. Multiple proxy advertisements for one heater remain one choice. The integration stores the selected proxy source and uses Home Assistant's scanner-by-source connector directly, so the bond and subsequent GATT traffic stay on that controller. A heater's bond is local to the controller that paired. Proxy roaming remains unverified and must not be assumed.

## Removal

In **Settings → Devices & services**, open the Dimplex BLE config entry and choose **Delete**. Remove each heater entry separately. This removes the integration entry and its entities; it does not modify ESPHome proxy firmware or heater settings.

## Bluetooth proxy and pairing

The standard ESPHome Bluetooth Proxy handles advertisements and GATT connections, but its pairing request has no passkey field and the stock BLE client does not handle the passkey-request event. Vanilla firmware therefore cannot complete first-time PIN pairing by itself.

This repository includes a minimal ESPHome external component, `components/dimplex_passkey_proxy`, for that missing security callback. When Home Assistant starts setup, it arms the relay for one address and starts the proxy's BLE pairing request before asking for the PIN. In a live test the proxy reached the passkey-request state, but the heater did not display a PIN and authentication timed out; this display trigger remains unverified. Do not submit a guessed PIN. The relay accepts the PIN later through the encrypted ESPHome native API and handles either arrival order; it replies only to the matching heater and keeps the address/PIN state in RAM for at most 60 seconds. It clears state after the reply, authentication completion, explicit cancellation, or expiry. Home Assistant refuses proxies without all three pairing actions and an encrypted API. Pairing attempts are serialized per proxy and time out after 60 seconds. After pairing, normal discovery and GATT traffic still use the standard ESPHome Bluetooth Proxy. Each heater stays bound to the proxy that owns its bond; bonds are not shared between proxies.

Merge this fragment into the existing ESPHome proxy configuration. Do not duplicate existing `api`, `esp32_ble_tracker`, or `bluetooth_proxy` blocks; `path` must resolve inside the ESPHome container:

```yaml
external_components:
  - source:
      type: local
      path: /path/to/ha-dimplex-ble/components
    components: [dimplex_passkey_proxy]

api:
  encryption:
    key: !secret api_encryption_key
  actions:
    - action: arm_dimplex_pairing
      variables:
        address: string
      then:
        - lambda: |-
            id(dimplex_pairing_relay).arm_pairing(address.c_str());
    - action: set_dimplex_pairing_passkey
      variables:
        address: string
        passkey: string
      then:
        - lambda: |-
            id(dimplex_pairing_relay).set_pending_passkey(address.c_str(), passkey.c_str());
    - action: cancel_dimplex_pairing
      then:
        - lambda: |-
            id(dimplex_pairing_relay).cancel_pairing();

esp32_ble:
  io_capability: keyboard_only

dimplex_passkey_proxy:
  id: dimplex_pairing_relay
```

Keep the existing `esp32_ble_tracker` and `bluetooth_proxy: active: true` configuration. `keyboard_only` allows the ESP32 central to answer the heater's passkey challenge. ESP32 proxies default to three active GATT slots; pairing is single-flight per proxy, while later heater reads and writes use short-lived connections. See [ESPHome API actions](https://esphome.io/components/api/#user-defined-actions), [external components](https://esphome.io/components/external_components/), and [Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/).

## Update and control behavior

Each heater has an independent coordinator with a 10-second polling interval. It reads only the mapped, known-safe fields and selects the room-temperature value through the heater's selector protocol. It does not read the known timeout/security characteristics. If a heater is offline, its entities remain registered and become unavailable; setup does not fail solely because a radiator is out of range.

The climate entity is heat-only and does not publish `hvac_action`: the available BLE value is not yet a reliable representation of actual heating activity. Setpoint writes do not silently change Manual/Timer/Eco mode; use the raw write action for other settings and confirm their behavior on the heater.

## Troubleshooting

- **No heater appears:** Confirm Home Assistant Bluetooth is working and the ESPHome Bluetooth Proxy is added to Home Assistant through the ESPHome integration. Keep the proxy within BLE range and make sure it supports active GATT connections.
- **A heater is discovered but unavailable:** The integration uses the selected proxy to preserve the controller-local bond. Check that proxy is online and within BLE range; the integration does not silently switch to another controller.
- **The bond-owning proxy was replaced or reset:** Bonds are not transferable between ESP32s. The replacement must pair separately. Whether the heater retains multiple controller bonds or replaces the old one is unverified; do not assume automatic failover.
- **First pairing fails:** Confirm the selected ESPHome proxy has this external component, its encrypted native API actions, and `io_capability: keyboard_only`. A stock/vanilla proxy alone cannot accept the PIN through HA.
- **Pairing diagnostics:** In Developer Tools > Actions, run `logger.set_level` with `custom_components.dimplex_ble: debug`. The level resets when HA restarts. The native PIN form cannot show a live countdown; it explains the observed ~30-second heater PIN window. Each proxy pairing request times out after 60 seconds, then retries after a 1-second delay while the setup flow remains open. Closing the flow cancels the active pairing and retry task. Passkeys are not logged.
- **Raw reads fail:** The heater may be unbonded, out of range, or lacking that characteristic. The integration blocks known unsafe characteristics and does not read Cypress bootloader services.
- **Several heaters share proxies:** ESP32 proxies default to three active GATT connection slots. Setup chooses the strongest passkey-capable route, and each heater stays on its bond-owning proxy. HA's route scoring still applies to other Bluetooth clients. Moving a Dimplex heater between proxies requires verified support for multiple controller bonds.

The error-code sensor reports the numeric value and raw bytes. No human-readable mapping is guessed; record an unknown value and the heater display when investigating a fault.

## Development

```sh
uv venv --python 3.14
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/pre-commit install --hook-type pre-commit --hook-type pre-push
bash scripts/run_quality_gates.sh
```

The pre-commit hook runs Ruff and strict mypy on relevant changes. The pre-push hook runs all hooks, tests, 100% statement/function coverage checks, and the 99% mutation-score gate. GitHub CI runs the same quality script and Home Assistant `hassfest` on every push and pull request.

The test gate requires 100% executed statements and 100% of defined integration functions to be exercised; branch and line thresholds are not configured. The mutation score gate is 99%. The suite uses Home Assistant's custom-component harness and mocked GATT clients; when a host C++ compiler is available, pytest also builds and runs the relay lifecycle harness against the production C++ source. Automated tests never write to a physical heater.

## Quality-scale status

CI enforces strict mypy, Ruff's async checks, the test and mutation gates, and Home Assistant `hassfest`. This BLE-only integration makes no HTTP requests, so the Platinum `inject-websession` rule does not apply. Async-dependency use still needs code review; automated checks cannot prove every I/O path is non-blocking. This repository does not claim an officially accepted Platinum tier, which requires Home Assistant's quality-scale review.
