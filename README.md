# Dimplex BLE

Unofficial Home Assistant custom integration that controls Dimplex Bluetooth
panel heaters (tested on the PLX050E Series C) locally, through ESPHome
Bluetooth proxies. No Dimplex app; the Dimplex cloud is used only for firmware
updates, and only after you sign in.

> [!WARNING]
> **Treat every Home Assistant automation for these heaters as unreliable.**
> The heaters are not designed to stay connected over Bluetooth for long: the
> Dimplex app only connects briefly and tells users to power-cycle a heater
> that stops responding. In our testing every heater's Bluetooth stopped at
> least once until it was switched off and on at the wall (see
> [Known limitations](#known-limitations)). While it is stopped, Home
> Assistant can neither read nor change the heater, so an automation step can
> silently not happen.
>
> The most reliable way to heat on time is the heater's **own timer
> programmes**, which run on the heater even when Bluetooth is down: set them
> once with the **Set schedule** action (or the Dimplex app) and select **Auto**.
> Use automations only for occasional changes you can afford to miss. Home
> Assistant therefore never holds the connection: every update connects,
> reads and disconnects, like the Dimplex app.

- [Reliability first](#reliability-first)
- [Requirements](#requirements)
- [Setup](#setup)
- [Choosing a Bluetooth proxy](#choosing-a-bluetooth-proxy)
- [Pairing PIN](#pairing-pin)
- [Privacy and logging](#privacy-and-logging)
- [What you get](#what-you-get)
- [Known heater behaviour](#known-heater-behaviour)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)

## Reliability first

The warning above in practice:

- **Schedules belong on the heater.** Put your weekly heating times into the
  User Timer (or Home All Day / Out All Day) programme with **Set schedule**,
  then leave the heater in **Auto**. The heater follows its programme on its
  own; Home Assistant only shows it as a calendar. Away works the same way:
  once set, the heater itself returns to its previous mode at the return time.
- **Automations are best effort.** A step that writes to a heater fails if the
  heater's Bluetooth has stopped or the link drops; Home Assistant reports the
  error, but nothing retries it later. Do not rely on an automation for
  anything that must happen (for example frost protection while you are away).
- **Brief connections.** Every update (once a minute) connects, reads and
  disconnects, like the Dimplex app; a command connects at once. Changes made
  on the heater itself show up within a minute.
- **Watch for the repair.** When a heater stops responding, Home Assistant
  raises "*Heater* stopped responding" with the power-cycle steps.

## Requirements

- Home Assistant 2026.11 or newer with the ESPHome integration.
- At least one ESP32-S3 (or other ESP32) running ESPHome 2026.11.0 or newer
  as a Bluetooth proxy (older versions can lose the heater's pairing; the
  firmware package refuses to build with them),
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

**Settings for all heaters** (optional): **Add device** → **Dimplex settings
for all heaters** adds one **Dimplex** entry; change it later with its
**Configure**. Without it, heaters use the defaults.

- **Sync the heater clock** (on by default): sets the heater clock to Home
  Assistant's time whenever it is more than a minute off, as the Dimplex app
  does. Timer schedules run on the heater clock, so leave it on unless another
  app manages the clock.
- **Boost length**: how long the Boost preset lasts, 1–240 minutes (default 60).
- **Dimplex email and password** (for firmware updates): your Dimplex (ConfigR)
  account, entered once. Home Assistant keeps only a sign-in token; when
  Dimplex stops accepting it, Home Assistant asks you to sign in again.

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

- At `debug`, the `aioesphomeapi.connection` logger records every action sent
  to a proxy with its arguments. The integration blanks the arguments of its
  PIN action there (`**REDACTED**`), so the PIN never reaches the log.
- Debug logging for `custom_components.dimplex_ble` is safe to enable: it names
  the heater and the proxy, never the PIN.

## What you get

Each heater becomes one device with:

| Entity | Notes |
| --- | --- |
| Climate (named after the heater) | Room temperature (0.1 °C), heating/idle action and two modes. **Heat**: heats to a target you set (whole degrees); presets **Manual**, **Eco**, **Frost Protect**, **Boost**, **Away**. **Auto**: follows the heater's schedule, which sets the temperature; presets **User Timer**, **Home All Day**, **Out All Day**, **Boost**, **Away**. **Boost** heats at the heater's Boost temperature for the Boost length, then the heater returns to the mode it left; the target can be changed while it runs. No Off. |
| User Timer schedule (calendar) | Heating periods of the User Timer programme, as stored on the heater (Home All Day and Out All Day calendars exist but are disabled by default). Read after setup and every 6 hours, and right after a change with **Set schedule**. |
| Away return time | When Away ends. Setting it starts Away. The Away preset reuses the return time set last, if it is still in the future; otherwise set a new one here. |
| Away temperature | What Away holds, 7–18 °C (default 7 °C). Can be changed only while Away is on: the heater starts Away whenever this value is written. During Away the climate's target shows and sets this temperature. |
| Advance | Flips the current timer period (Heating On ↔ Off) until the next period starts; the heater then turns it off. Only in User Timer, Home All Day or Out All Day. |
| Setback | In Auto, holds the Setback temperature during Heating Off periods instead of letting the room cool. |
| Boost temperature | What the Boost preset heats to, within the heater's setpoint range (whole degrees). A running Boost keeps its target; the next Boost uses the new value. |
| Setback temperature | What Setback holds, 7–18 °C (whole degrees). Can be changed whether Setback is on or off. |
| Sound | Button sounds on the heater. |
| Adaptive start | Dimplex "Adaptive Start": heats early so a timer period starts at its target. Only affects User Timer and Home All Day. Learns continuously, plans at most 2 hours ahead; turn it on or off for all heaters in a room together. |
| Open window detection | Stops heating ("Heating Off") when the room temperature drops suddenly. Can trigger in drafty or poorly insulated rooms. |
| Display temperature unit | Only changes the heater's own display. Home Assistant always uses your system unit. |
| Estimated power | Rated power × output %, in W. The heater has no power meter. |
| Estimated energy | Estimated power over time, in kWh; add it to the Energy dashboard. Time while the heater is unreachable or Home Assistant is stopped is not counted. |
| Error (diagnostic) | "No error", a fault from the Dimplex manual (for example "Open window detected (50)"), or "Code N" for an undocumented code. |
| Operating mode, Rated power, Controller temperature, Output power (diagnostic, disabled) | Enable them on the device page if needed. |
| Firmware | Installed display firmware and Dimplex's latest, checked every 12 hours; **Install** updates the heater over Bluetooth (about 7 minutes). Needs the Dimplex sign-in. |
| Temperature offset (installer setting, disabled) | Corrects the room reading, −3.0 to +3.0 °C in 0.1 °C steps. Dimplex reserves it for service staff. |

Sound, Setback, Setback temperature and Temperature offset are read every 5 minutes (and right after
you change them), so a change made on the heater's own menu can take up to
5 minutes to show.

Device settings guidance is based on the official
[PLXE Series C manual, Issue 7](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf).
The device page shows the model as, for example, "PLX050E Series C" (the heater
reports `PLX050E;C;`; reading `C` as the series is an inference).

**Set schedule** (`dimplex_ble.set_schedule`) replaces the heating periods of
one programme on the chosen days: pick the heater, the programme, the days and
up to 4 periods (start, end, temperature). `periods` is required; an empty
list (`periods: []`) removes all heating periods from those days. Each day is read back from the heater afterwards; the action fails if
the heater did not keep it. Example:

```yaml
action: dimplex_ble.set_schedule
data:
  device_id: <your heater>
  programme: user_timer
  days: [mon, tue, wed, thu, fri]
  periods:
    - start: "06:30"
      end: "08:00"
      temperature: 21
    - start: "17:00"
      end: "22:00"
      temperature: 21
```

If the heater can't be reached when you save (its Bluetooth has stopped, or it
is out of range), the action still succeeds: Home Assistant keeps the change,
even across restarts, shows the repair "*Heater*: schedule change waiting", and
writes and verifies it as soon as the heater answers again. Until then the
heater keeps running its previous schedule.

**Get schedule** (`dimplex_ble.get_schedule`) returns each programme as the
heater held it at the last read (`read_at`; schedules are read every 6 hours
and after every save), the changes still waiting for the heater, and the backup
copy
(per day, as `[start, end, temperature]` periods), for example to copy one day
to others in a script.

**Restore schedule** (`dimplex_ble.restore_schedule`) writes Home Assistant's
backup copy back to the heater (one programme or all three), for example after
the heater was reset or replaced. The backup is taken from the first complete
read of the heater (later reads only fill in days it doesn't know) and then
follows only the changes saved through Home Assistant, so a reset heater's
factory schedule never overwrites it. Only complete weeks are restored.

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
- **Boost from Manual, Eco or Frost Protect shows as Heat** (the heater reports
  Manual while boosting) and returns to the previous mode when Boost ends.
  After Boost runs out by itself, the previous mode can take up to a minute to
  show. Boost lasts at most 240 minutes; Boost is not offered during Away.

## Known limitations

- **At most 2 Dimplex heaters per ESP32-S3 Bluetooth proxy.** A proxy that
  already holds 2 heater connections refuses the third heater's connection after
  every Home Assistant restart (ESP32-S3 controller limit,
  [esphome/esphome#22859](https://github.com/esphome/esphome/issues/22859);
  confirmed on hardware, 4 of 4 restarts).
- A proxy has 3 Bluetooth connection slots, shared with every other Bluetooth
  device it connects to (locks, sensors…). A heater uses one slot only while
  it updates or runs a command.
- The pairing belongs to the chosen proxy. Moving a heater to another proxy
  needs the PIN again (**Reconfigure**).
- The PIN is shown for about 30 seconds per pairing attempt.
- While the Dimplex phone app is connected to a heater, Home Assistant cannot
  connect to it.
- Schedules are shown as calendars and changed with the **Set schedule**
  action, not by editing calendar events. There is no on/off; the Setback and
  Boost temperatures are set on the heater.
- **Heater stops responding (needs a power cycle).** Rarely, after its
  connection drops unexpectedly, a heater's Bluetooth stops completely: no
  receiver hears it until it is switched off and on at the wall. Home Assistant
  then raises the repair "*Heater* stopped responding" (after 60 s of silence;
  not when the proxy itself is offline) and clears it once the heater is heard
  again. No app is needed, and no app can reach a stalled heater either. To
  power-cycle it, as the Dimplex app words it: "If your installation
  has a mains isolator switch: switch it off, wait at least 5 seconds, then
  switch it on. If there is no isolator switch: switch off the circuit breaker
  or remove the fuse supplying the unit, wait at least 5 seconds, then switch it
  on."
- Otherwise, power-cycling a heater is a last resort, not a routine fix.
- **Firmware updates** come from Dimplex and need the Dimplex sign-in. During
  an update Home Assistant cannot control the heater; keep it switched on and
  its proxy online. If an update stops, Home Assistant says why and you can
  install again. Updates are never installed automatically.

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
- **`link.py`** owns the BLE link to one heater, pinned to the proxy that
  holds the bond. It connects only when that proxy sees the heater
  advertising, then arms the responder with the saved PIN and runs the native
  `pair()` to its end before any GATT operation. With a valid bond no PIN is
  asked; a lost bond is re-created automatically. A `pair()` in flight is never
  cancelled: a failed or aborted pairing deletes the bond in ESP-IDF. Every
  operation runs that sequence and then disconnects; a connect or `pair()` is
  never cut short.
- **`stall.py`** raises the "stopped responding" repair when a link ended
  without our disconnect, or the proxy no longer sees the heater, and no
  scanner hears it for 60 s.
- **`responder.py`** calls the two actions through the ESPHome entry's own API
  client, never through a Home Assistant action, so the PIN never reaches the
  event bus or the recorder.
- **`errors.py`** is the single error translation point. Only a rejected PIN
  starts reauthentication; timeouts and GATT errors make the heater unavailable
  until the next poll (every 60 s) or advertisement.
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
access. Firmware is downloaded from Dimplex with the user's own Dimplex
account. The project does not bypass authentication or security controls;
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
