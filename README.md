# Dimplex BLE

Standalone Home Assistant custom integration for local Dimplex Bluetooth heaters.
It is engineered against the current applicable Home Assistant Quality Scale
requirements; it is not an official Home Assistant Quality Scale-rated integration.

## Source of truth

Connection and pairing lifecycle decisions are derived from the extracted official
ConfigR application artifact. The artifact is a Native AOT application, so its
readable evidence is limited to recovered managed resources and native strings.
Those resources establish the Advanced Verification lifecycle:

1. Try the normal product verification path.
2. If cloud verification is unavailable, use local Bluetooth verification.
3. Switch the product off before local verification.
4. Refresh the Bluetooth connection; the iOS flow also tells the user to forget the
   phone's Bluetooth bond.
5. After local verification, power the product off and on before continuing.

The integration follows those lifecycle boundaries but does not depend on ConfigR,
its cloud service, undocumented packet values, or undocumented firmware commands.
The exact ConfigR GATT packet sequence is not recoverable from the artifact and is
therefore not guessed.

## Architecture

- Home Assistant owns discovery, the selected ESPHome Bluetooth route, GATT
  connection lifecycle, retries, availability, and teardown.
- The only ESPHome customization is the minimal BLE passkey callback relay;
  scanning, connections, bonding, reconnection, and capacity remain stock
  ESPHome Bluetooth Proxy behavior.
- `api.max_connections: 16` is only an API-session headroom setting for Home
  Assistant's proxy and management connections; it does not increase or replace
  ESPHome's BLE connection capacity.
- Pairing is serialized per proxy and is an explicit commissioning operation.
  Normal reads and writes reuse the authenticated connection. When a fresh
  connection is required, the proxy is asked to re-establish encryption from
  its existing bond; this is not a new PIN request when the bond is valid.
  Bond repair is not retried in a loop.
- A shared proxy has three integration connection slots, matching ESPHome's
  default active BLE capacity. Additional heaters queue until a slot is released.
- The selected proxy is stored with the config entry. The integration does not
  silently move a heater to another proxy because a bond belongs to its controller.

## Installation

1. Install the `custom_components/dimplex_ble` directory.
2. Install the minimal `components/dimplex_passkey_proxy` component in the ESPHome
   Bluetooth Proxy that will own the heater connection.
3. Keep the proxy's standard `esp32_ble_tracker` and `bluetooth_proxy` components.
4. Configure the proxy's encrypted API actions for
   `arm_dimplex_pairing`, `set_dimplex_pairing_passkey`, and
   `cancel_dimplex_pairing`, plus the stock `esp32_ble` settings
   `io_capability: keyboard_only` and `auth_req_mode: bond_mitm`.
5. Restart Home Assistant and add **Dimplex BLE**.

The config flow selects a reachable heater and its proxy, arms pairing, then asks
for the six-digit PIN shown by the heater. The PIN is forwarded to the selected
proxy and is not stored.

## Entities and controls

Each configured heater creates:

- one heat-only `climate` entity with native Celsius temperature, heater-reported
  current temperature, target temperature, target limits, the verified Eco preset,
  and directly reported `HEATING`/`IDLE` HVAC action when available;
- one configuration `select` for the heater-reported display unit;
- one diagnostic error-code `sensor`;
- one disabled-by-default diagnostic raw operating-mode `sensor`;
- one disabled-by-default diagnostic rated-power `sensor`; its raw
  `PowerLoading` payload is retained as an attribute, while the second
  undocumented word is not interpreted as measured power;
- one disabled-by-default diagnostic LCD/controller-temperature `sensor`;
  its complete Trac response is retained as an attribute;
- one disabled-by-default diagnostic output-power `sensor` in percent; its
  complete Trac response is retained as an attribute;
- one enabled calculated power-consumption `sensor` in watts, computed as
  `rated_power × output_power_percent ÷ 100`. It has the standard `POWER` device
  class and `MEASUREMENT` state class, so it is available as a power input in
  Home Assistant's Energy dashboard.

Raw GATT actions exist for inspection and controlled administrator-only writes.
Known security-material and bootloader characteristics are blocked. Unknown
characteristics are not promoted to Home Assistant entities.

## Runtime behavior

The coordinator polls every ten seconds and shares one client per config entry.
A failed connection or GATT operation closes the connection; coordinator entities
become unavailable until a fresh snapshot succeeds. Repeated failures are handled
by Home Assistant's coordinator without publishing stale live values. Commissioning
automatic retries are bounded to three attempts.

The integration exposes only the verified Eco preset. Frost has a known bounded
protocol payload but remains phase 2 until the physical display state is
confirmed. HVAC action comes only from the heater's Trac response; it is not
inferred from temperature or selected mode. The integration exposes calculated
instantaneous power but no device-reported cumulative energy counter. To add
kWh tracking to the Energy dashboard, create a Home Assistant Integration
(Riemann sum) sensor from the calculated power-consumption entity. It does not
expose Boost/Away/Manual/Timer presets, fan modes, swing, humidity, schedules,
firmware updates, or other capabilities that the device has not exposed and
that the ConfigR evidence has not verified.

## Troubleshooting

- **No heater found:** verify Home Assistant Bluetooth and the selected ESPHome
  proxy are online, connectable, and within range.
- **Pairing times out:** confirm the heater is switched off before the local
  verification attempt, the proxy has the passkey relay, and the PIN is entered
  only when the heater displays it. If the heater-side verification state is
  stale, follow the ConfigR recovery boundary: power it off, wait, and power it on.
- **Entities unavailable:** check the selected bond-owning proxy and the
  integration diagnostics. The integration will not silently roam to another
  controller.
- **Several heaters use one proxy:** the integration queues connections beyond
  the three available slots. Other Bluetooth integrations also consume proxy
  capacity.

## Removal

Delete the config entry from **Settings → Devices & services**. This removes the
Home Assistant entities and entry; it does not erase heater state or change proxy
firmware.

## Development

```sh
uv pip install --python .venv/bin/python -r requirements-test.txt
bash scripts/run_quality_gates.sh
```

The ConfigR-derived lifecycle evidence is recorded in
[`docs/configr.md`](docs/configr.md). The evidence-backed protocol and
Home Assistant capability boundary is recorded in
[`docs/capability-matrix.md`](docs/capability-matrix.md). The local artifact
workspace used for analysis is `/tmp/configr-apk/` and is not part of the
repository.