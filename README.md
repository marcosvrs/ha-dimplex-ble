# Dimplex BLE

Unofficial Home Assistant custom integration that controls Dimplex Bluetooth
panel heaters (tested on the PLX050E Series C) locally, through ESPHome
Bluetooth proxies. No cloud, no Dimplex app.

- [Requirements](#requirements)
- [Setup](#setup)
- [Choosing a Bluetooth proxy](#choosing-a-bluetooth-proxy)
- [Pairing PIN](#pairing-pin)
- [Privacy and logging](#privacy-and-logging)
- [What you get](#what-you-get)
- [Known heater behaviour](#known-heater-behaviour)
- [Known limitations](#known-limitations)
- [Upgrade notes](#upgrade-notes)
- [Troubleshooting](#troubleshooting)

## Requirements

- Home Assistant 2026.11 or newer with the ESPHome integration.
- At least one ESP32-S3 (or other ESP32) running ESPHome as a Bluetooth proxy,
  within Bluetooth range of each heater. Plan for **at most 2 heaters per
  proxy** (see [Known limitations](#known-limitations)).
- The Dimplex phone app closed: the heater accepts one Bluetooth connection at
  a time.

## Setup

[![Open your Home Assistant instance and start setting up Dimplex BLE.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dimplex_ble)

1. **Flash the Bluetooth proxy.** Copy
   [`firmware/example-bluetooth-proxy.yaml`](firmware/example-bluetooth-proxy.yaml)
   and [`firmware/dimplex_pin_responder.yaml`](firmware/dimplex_pin_responder.yaml)
   into the same folder of your ESPHome configuration. Adjust the name, the
   secrets and the board package (the example uses `esp32-generic-s3.yaml`;
   pick the one that matches your ESP32), then install it. The package adds a
   small "PIN responder" that lets Home Assistant answer the heater's PIN
   request. Keep `io_capability: keyboard_only`, `auth_req_mode: sc_mitm_bond`
   and `max_connections: 4` as in the example. For normal use set the
   `logger` level to `INFO`; `DEBUG` is only for investigating problems.
2. **Adopt the proxy in Home Assistant.** Add it under **Settings → Devices &
   services → ESPHome** (an encryption key is recommended). Give it a clear name,
   such as "Living Room Bluetooth Proxy": the setup dialog shows that name.
3. **Install the integration.** In HACS, add this repository as a custom
   repository (type *Integration*), install **Dimplex BLE** and restart Home
   Assistant. Without HACS, copy `custom_components/dimplex_ble` into your
   `config/custom_components` folder and restart.
4. **Add the heater.** Accept the discovered heater (shown with its `AMxxxx`
   name) or use the button above and pick it from the list.
5. **Choose the proxy.** The recommended proxy is preselected; see
   [Choosing a Bluetooth proxy](#choosing-a-bluetooth-proxy).
6. **Enter the PIN.** When you submit, the heater shows a 6-digit PIN on its
   display. Choose **Enter PIN** and type it. Missed it, or the display already
   cleared? Choose **Show PIN again** (or submit the PIN form empty). See
   [Pairing PIN](#pairing-pin).

The heater is added as "Dimplex Heater AMxxxx". To change its proxy later, use
**Reconfigure** on the integration entry.

## Choosing a Bluetooth proxy

The setup dialog lists every proxy that can reach the heater and runs the
Dimplex PIN responder. Each entry shows the proxy's name and at most one tag:

| Tag | Meaning |
| --- | --- |
| **Recommended** | The best choice: a free connection, few existing connections, then the strongest signal. Preselected. |
| **current** | The proxy the heater is paired with now. Keeping it needs no PIN. |
| **⚠ already serves 2 heaters** | Choose another proxy: a third heater on it will not reconnect after Home Assistant restarts. |
| **⚠ full** | No free connection: pairing cannot start there. |

If every proxy has a ⚠ tag, the dialog warns that pairing will probably fail.
Add a proxy near the heater, or free a connection by moving or disabling
another Bluetooth device on that proxy.

Proxies that hear the heater but cannot be used are named in one line under
"Not shown": they are missing the Dimplex firmware (step 1 of
[Setup](#setup)) or are offline in Home Assistant.

The heater stays on the proxy you choose. It never moves to another proxy by
itself, even if that one is closer; moving it means pairing again with the PIN.

## Pairing PIN

- The heater shows its 6-digit PIN only while a pairing is running, for about
  **30 seconds**. Use **Show PIN again** as often as needed.
- The PIN is fixed for each heater: it is the same every time, so you can note
  it down.
- Home Assistant saves the PIN with this heater's settings (in its configuration
  storage, not encrypted) so it can restore the pairing on its own. This
  integration never writes it to logs and removes it from diagnostics; see
  [Privacy and logging](#privacy-and-logging) for one library logger that can.
- The heater has no pairing mode and its Bluetooth is always on. If no PIN
  appears, the proxy could not connect: see [Troubleshooting](#troubleshooting).
- If the heater later rejects the saved PIN, Home Assistant shows a repair:
  "Pair Dimplex Heater AMxxxx again".

## Privacy and logging

- Keep the `aioesphomeapi.connection` logger at `info` (the default). At `debug`
  the ESPHome API library logs every action it sends with its arguments, which
  includes the PIN sent to the proxy's PIN responder. This integration cannot
  redact that library's log lines.
- When that logger is at `debug`, Home Assistant logs a one-time warning at
  startup that the PIN may appear in the log (the warning itself contains no
  PIN).
- Debug logging for `custom_components.dimplex_ble` is safe to enable: it names
  the heater and the proxy, never the PIN.

## What you get

Each heater becomes one device with:

| Entity | Notes |
| --- | --- |
| Climate (named after the heater) | Room temperature (0.1 °C), target temperature in whole degrees, heating/idle action, presets **User Timer**, **Home All Day**, **Out All Day**, **Away**, **Manual**, **Eco**, **Frost Protect**. Heat only. |
| Away return time | When Away ends. Setting it starts Away. Selecting the Away preset needs a future return time here first. |
| Away temperature | What Away holds, 7–18 °C (default 7 °C). Changing it keeps Away on or off. |
| Advance | Flips the current timer period (Heating On ↔ Off) until the next period starts; the heater then turns it off. Only in User Timer, Home All Day or Out All Day. |
| Setback | In timer presets, holds the heater's Setback temperature during Heating Off periods instead of letting the room cool. The Setback temperature itself is set on the heater. |
| Sound | Button sounds on the heater. |
| Adaptive start | Dimplex "Adaptive Start": heats early so a timer period starts at its target. Only affects User Timer and Home All Day. Learns continuously, plans at most 2 hours ahead; turn it on or off for all heaters in a room together. |
| Open window detection | Stops heating ("Heating Off") when the room temperature drops suddenly. Can trigger in drafty or poorly insulated rooms. |
| Display temperature unit | Only changes the heater's own display. Home Assistant always uses your system unit. |
| Estimated power | Rated power × output %, in W. Can be added to the Energy dashboard (Home Assistant then estimates kWh). |
| Error (diagnostic) | "No error", a fault from the Dimplex manual (for example "Open window detected (50)"), or "Code N" for an undocumented code. |
| Operating mode, Rated power, Controller temperature, Output power (diagnostic, disabled) | Enable them on the device page if needed. |
| Temperature offset (installer setting, disabled) | Corrects the room reading, −3.0 to +3.0 °C in 0.1 °C steps. Dimplex reserves it for service staff. |

Sound, Setback and Temperature offset are read every 5 minutes (and right after
you change them), so a change made on the heater's own menu can take up to
5 minutes to show.

Device settings guidance is based on the official
[PLXE Series C manual, Issue 7](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf).
The device page shows the model as, for example, "PLX050E Series C" (the heater
reports `PLX050E;C;`; reading `C` as the series is an inference).

Developer diagnostic: the action **List Bluetooth services (developer)**
(`dimplex_ble.get_gatt_services`) lists the heater's Bluetooth services and
characteristics without reading values. It is only useful when reporting a
problem.

## Known heater behaviour

- **Manual at 19 °C or lower shows Eco.** If you set Manual to 19 °C or lower,
  the heater itself switches to Eco and Home Assistant shows Eco. This is the
  heater's own rule (observed on the PLX050E Series C, not documented by
  Dimplex), not an integration error. Use 20 °C or higher to stay in Manual.
- **Frost Protect** holds 7 °C.
- **Away** returns to the previous mode at the return time.

## Known limitations

- **At most 2 Dimplex heaters per ESP32-S3 Bluetooth proxy.** A proxy that
  already holds 2 heater connections refuses the third heater's connection after
  every Home Assistant restart (ESP32-S3 controller limit,
  [esphome/esphome#22859](https://github.com/esphome/esphome/issues/22859);
  confirmed on hardware, 4 of 4 restarts).
- A proxy has 3 Bluetooth connection slots, shared with every other Bluetooth
  device it connects to (locks, sensors…). Each heater keeps one slot
  permanently, unless its connection option is off (below).
- The pairing belongs to the chosen proxy. Moving a heater to another proxy
  needs the PIN again (**Reconfigure**).
- The PIN is shown for about 30 seconds per pairing attempt.
- While the Dimplex phone app is connected to a heater, Home Assistant cannot
  connect to it.
- Schedules cannot be edited from Home Assistant; there is no on/off; Boost
  and the Setback temperature cannot be set.
- **Heater stops responding (needs a power cycle).** Rarely, after its
  connection drops unexpectedly, a heater's Bluetooth stops completely: no
  receiver hears it until it is switched off and on at the wall. Home Assistant
  then raises the repair "*Heater* stopped responding" (after 60 s of silence;
  not when the proxy itself is offline) and clears it once the heater is heard
  again. If a heater does this often, turn off **Keep the Bluetooth connection
  open** under the heater's **Configure**: each update (every 60 s instead of
  10 s) and each command then connects briefly and disconnects. Controls react
  more slowly. This option is an experiment; it changes the update interval
  together with the connection mode.
- Otherwise, power-cycling a heater is a last resort, not a routine fix.

## Upgrade notes

Upgrading from an earlier version:

- **Breaking:** the Home All Day preset is now `home_all_day` (was Home
  Assistant's `home`). Update automations and scripts that call
  `climate.set_preset_mode` with `preset_mode: home` or check
  `preset_mode == "home"`.
- **Breaking:** the Error sensor's state is now text (`no_error`, `code_50`, …,
  or `Code N`) instead of a number. Update `numeric_state` triggers on it.
- The Operating mode sensor now uses fixed states (`user_timer`,
  `home_all_day`, `manual`, …); unknown payloads show as unknown.
- Raw payload attributes (`raw_bytes`, `power_loading_raw`, `trac_raw`) and the
  switches' explanation attributes were removed. Raw payloads are in the
  diagnostics download.
- Entities that this version no longer creates (old raw sensors, text controls,
  renamed duplicates) are removed from the entity registry at startup. Entity
  IDs of current entities do not change.
- The climate entity's name is now the heater's name (for example "Dimplex
  Heater AM2086"); its entity ID stays the same.
- **Away return time** is enabled for new heaters only. On an existing heater,
  enable it on the device page to use the Away preset.
- The room temperature now shows one decimal.

## Troubleshooting

Start with the [troubleshooting guide](docs/troubleshooting.md). The most common
cases:

- **No proxy offered / "No Bluetooth proxy can reach this heater":** install
  the Dimplex firmware on a proxy near the heater (step 1 of [Setup](#setup)),
  check that the proxy is online, and close the Dimplex phone app.
- **No PIN appears:** the proxy could not connect. Choose another proxy, or free
  a connection on this one.
- **Heater unavailable after a Home Assistant restart:** check how many heaters
  share its proxy (at most 2).
- **Heater unavailable after working:** usually range or a busy proxy, not the
  PIN. Debug logging for `custom_components.dimplex_ble` names the heater on
  every line.

## Removal

Delete the entry under **Settings → Devices & services → Dimplex BLE**. This
removes its entities; it does not change the heater or the proxy firmware.

## How it works

- **Firmware:** stock ESPHome Bluetooth proxy plus the YAML-only package
  [`firmware/dimplex_pin_responder.yaml`](firmware/dimplex_pin_responder.yaml):
  one dormant `ble_client` that answers the heater's passkey request from a PIN
  held in RAM, and two native API actions, `dimplex_pin_arm(address, passkey)`
  and `dimplex_pin_clear()`. No external component, no C++.
- **`link.py`** owns one persistent BLE link per heater, pinned to the proxy
  that holds the bond. It connects only when that proxy sees the heater
  advertising, then arms the responder with the saved PIN and runs the native
  `pair()` to its end before any GATT operation. With a valid bond no PIN is
  asked; a lost bond is re-created automatically. A `pair()` in flight is never
  cancelled: a failed or aborted pairing deletes the bond in ESP-IDF. With
  **Keep the Bluetooth connection open** off, every operation runs that
  sequence and then disconnects; a connect or `pair()` is never cut short.
- **`stall.py`** raises the "stopped responding" repair when a link ended
  without our disconnect and no scanner hears the heater for 60 s.
- **`responder.py`** calls the two actions through the ESPHome entry's own API
  client, never through a Home Assistant action, so the PIN never reaches the
  event bus or the recorder.
- **`errors.py`** is the single error translation point. Only a rejected PIN
  starts reauthentication; timeouts and GATT errors make the heater unavailable
  until the next poll (every 10 s, or 60 s per-poll) or advertisement.
- **`config_flow.py`**: setup, reauthentication and reconfigure share one path.

The evidence-backed protocol and capability boundary is in
[`docs/capability-matrix.md`](docs/capability-matrix.md); ESPHome and BLE
transport findings are in [`docs/esphome-ble-findings.md`](docs/esphome-ble-findings.md).

## Legal notice

This is an unofficial, independently developed integration for local control of
Dimplex products owned or lawfully controlled by the user. It is not affiliated
with, sponsored by, endorsed by, or supported by Dimplex. Dimplex and product
names are used only to identify compatibility.

The repository does not distribute the ConfigR application, decompiled source,
extracted binaries, proprietary assets, Dimplex firmware, credentials, or cloud
access. The project does not bypass authentication or security controls;
pairing uses the PIN displayed by the user's own heater. It is based on
independently written code, observed behaviour of user-owned devices and public
documentation.

No warranty is made that this integration is safe for unattended or
safety-critical heating control. Follow the manufacturer's safety instructions
and local laws. The MIT license applies only to original project code and
grants no rights to Dimplex intellectual property.

## Development

```sh
uv pip install --python .venv/bin/python -r requirements-test.txt
bash scripts/run_quality_gates.sh
```
