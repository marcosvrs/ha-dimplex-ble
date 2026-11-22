# Dimplex BLE

Standalone Home Assistant custom integration for local Dimplex Bluetooth panel heaters.
It is engineered against the current applicable Home Assistant Quality Scale
requirements; it is not an official Home Assistant Quality Scale-rated integration.

## Legal notice

This is an unofficial, independently developed Home Assistant integration for
local control of Dimplex products owned or lawfully controlled by the user. It
is not affiliated with, sponsored by, endorsed by, or supported by Dimplex.
Dimplex and product names are used only to identify compatibility.

The repository does not distribute the ConfigR application, decompiled source,
extracted binaries, proprietary assets, Dimplex firmware, credentials, or cloud
access. Private research artifacts remain outside the repository and are not
required at runtime. The project does not bypass authentication or security
controls; pairing uses the PIN displayed by the user's own panel heater.

No warranty is made that this integration is safe for unattended or
safety-critical heating control. Follow the panel heater manufacturer's safety
instructions and local laws. The MIT license applies only to original project
code and grants no rights to Dimplex intellectual property.

## Public evidence boundary

The implementation is based on independently written code, observed behavior
of user-owned devices, and public documentation. Private research informed
development but is not a distributed source of truth and must not be treated
as permission to copy proprietary implementation details.

The integration does not depend on ConfigR, its cloud service, undocumented
packet values, or undocumented firmware commands. The exact ConfigR GATT packet
sequence is not recoverable from the artifact and is therefore not guessed.

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
  Normal reads and writes reuse the authenticated connection without invoking
  ESPHome's pairing request. When a fresh connection is required, the proxy is
  asked to re-establish encryption from its existing bond; this is not a new
  PIN request when the bond is valid. Bond repair is not retried in a loop.
- A shared proxy has three integration connection slots, matching ESPHome's
  default active BLE capacity. Additional panel heaters queue until a slot is released.
- The configured proxy remains the runtime route. The integration does not
  silently probe or move a panel heater to another proxy because ESPHome bonds are
  proxy-local. RSSI-based cross-proxy roaming is intentionally not used.

## Installation
[![Open your Home Assistant instance and start setting up Dimplex BLE.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dimplex_ble)

1. Install the `custom_components/dimplex_ble` directory.
2. Install the minimal `components/dimplex_passkey_proxy` component in the ESPHome
   Bluetooth Proxy that will own the panel heater connection.
3. Keep the proxy's standard `esp32_ble_tracker` and `bluetooth_proxy` components.
4. Configure the proxy's encrypted API actions for
   `arm_dimplex_pairing`, `set_dimplex_pairing_passkey`, and
   `cancel_dimplex_pairing`, plus the stock `esp32_ble` settings
   `io_capability: keyboard_only` and `auth_req_mode: bond_mitm`.
5. If manually copying into a running Home Assistant instance, restart Home
   Assistant, then add **Dimplex BLE**. Marketplace installs can load the
   config flow without a restart.

The config flow selects a reachable panel heater and its proxy, arms pairing, then asks
for the six-digit PIN shown by the panel heater. The PIN is stored in the Home Assistant
config entry and is never logged or included in diagnostics. That lets the
integration repair a lost proxy bond automatically, with a bounded attempt and
cooldown; a missing or invalid PIN still requires the normal reauthentication flow.

## Entities and controls

Each configured panel heater creates:

- one heat-only `climate` entity with native Celsius temperature, panel-heater-reported
  current temperature, target temperature, target limits, the verified Eco preset,
  and directly reported `HEATING`/`IDLE` HVAC action when available;
- one configuration `select` for the panel-heater-reported display unit;
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


- one configuration `switch` for **Adaptive Start**; it is an independent
  panel-heater setting, not an HVAC mode or preset;
- one configuration `switch` for **Open Window Detection**; it is an independent
  panel-heater setting, not a temperature or occupancy sensor.

### Device setting guidance

The explanations shown in the entity details are based on the official [PLXE
Series C manual, Issue 7](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf),
which explicitly covers the PLX050E Series C:

- **Adaptive Start** is a continuously learning function
  that models the room's heating characteristics and starting temperature. It
  only changes Timer Mode operation and starts heating before the user-defined
  Heating On time so the target temperature is reached when that period starts.
  The manual enforces a two-hour limit on the pre-heating calculation and
  recommends enabling it on every panel heater in the same room.
- **Open Window Detection** is a global setting that can interrupt any active
  mode. When the panel heater detects an open window, it switches to **Heating Off**.
  Dimplex warns that sudden temperature decreases in drafty, poorly insulated,
  or undersized-room conditions can trigger it incorrectly.

Home Assistant has no generic long-form entity-help field rendered directly in
the standard more-info card. The integration therefore exposes these translated
explanations as entity attributes; open the entity's details view from the
more-info dialog's menu to see the **What it does**, **How it works**, **When it
applies**, and **Important note** entries.

Raw GATT actions exist for inspection and controlled administrator-only writes.
Known security-material and bootloader characteristics are blocked. Unknown
characteristics are not promoted to Home Assistant entities.

## Runtime behavior

The coordinator polls every ten seconds and shares one client per config entry.
A failed connection or GATT operation closes the connection; coordinator entities
become unavailable until a fresh snapshot succeeds. Repeated failures are handled
by Home Assistant's coordinator without publishing stale live values. If a stored
PIN is available, a missing bond triggers one address-scoped automatic repair
attempt, limited to thirty seconds and followed by a five-minute cooldown.
Commissioning retries are bounded to three attempts.

The integration exposes only the verified Eco preset. Frost has a known bounded
protocol payload but remains phase 2 until the physical display state is
confirmed. HVAC action comes only from the panel heater's Trac response; it is not
inferred from temperature or selected mode. The integration exposes calculated
instantaneous power but no device-reported cumulative energy counter. To add
kWh tracking, add each calculated power sensor directly under Home Assistant's
Energy dashboard → Individual electrical devices; Home Assistant creates the
required kWh energy sensor. If custom integration settings are needed, create a
native Integration (Riemann sum) sensor first and add that sensor instead. Both
methods estimate energy from the calculated power and do not recover historical
values.
expose Boost/Away/Manual/Timer presets, fan modes, swing, humidity, schedules,
firmware updates, or other capabilities that have not been verified on the
supported device.

## Troubleshooting

- **No panel heater found:** verify Home Assistant Bluetooth and the selected ESPHome
  proxy are online, connectable, and within range.
- **Pairing times out:** confirm the panel heater is switched off before the local
  verification attempt, the proxy has the passkey relay, and the PIN is entered
  only when the panel heater displays it. If the panel-heater-side verification
  state is stale, follow the documented recovery boundary: power it off, wait, and
  power it on.
- **Entities unavailable:** check the selected bond-owning proxy and the
  integration diagnostics. The integration will not silently roam to another proxy.
- **Several panel heaters use one proxy:** the integration queues connections beyond
  the three available slots. Other Bluetooth integrations also consume proxy
  capacity.

## Removal

Delete the config entry from **Settings → Devices & services**. This removes the
Home Assistant entities and entry; it does not erase panel-heater state or change proxy
firmware.

## Development

```sh
uv pip install --python .venv/bin/python -r requirements-test.txt
bash scripts/run_quality_gates.sh
```

The evidence-backed protocol and Home Assistant capability boundary is recorded
in [`docs/capability-matrix.md`](docs/capability-matrix.md). The complete
ESPHome and BLE transport, pairing, bonding, proxy-capacity, and commissioning
findings are recorded in [`docs/esphome-ble-findings.md`](docs/esphome-ble-findings.md).
Private research artifacts are intentionally excluded from the repository.