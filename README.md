# Dimplex BLE

Dimplex BLE is an unofficial Home Assistant custom integration for [Dimplex PLXE panel heaters](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf) with Bluetooth. It controls each heater locally through an ESPHome Bluetooth proxy: temperature, modes, timer programmes, Away, Boost and the heater's settings. The Dimplex cloud is used only for firmware updates, and only after you sign in.

> [!WARNING]
> The heaters' Bluetooth can stop until the heater is switched off and on at the wall. Every heater in our tests did this at least once. While it is stopped, Home Assistant can't read or change the heater, so an automation step can fail. Put your heating times into the heater's own timer programmes: the heater runs them without Bluetooth.

- [Supported devices](#supported-devices)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Supported functionality](#supported-functionality)
- [Actions](#actions)
- [Data updates](#data-updates)
- [Use cases and examples](#use-cases-and-examples)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Removing the integration](#removing-the-integration)

## Supported devices

The integration supports Dimplex PLXE Series C panel heaters that advertise as `AMxxxx` or `ALxxxx` over Bluetooth.

Tested:

- **PLX050E Series C**: 3 heaters
- **PLX075E Series C**: 1 heater

Other PLXE Series C sizes (PLX100E to PLXC300E) share the manual and controls but are untested. Not supported: Dimplex products that use the Dimplex Control hub, Q-Rad radiators, storage heaters, fan heaters (`FXnnnn`) and fireplaces.

## Prerequisites

You need the following before adding a heater:

- **Home Assistant**: 2026.11 or newer, with the ESPHome integration
- **Bluetooth proxy**: an ESP32 running ESPHome 2026.11.0 or newer with the Dimplex PIN responder, within Bluetooth range of the heater; at most 2 heaters per proxy. See [Set up a Bluetooth proxy](docs/user/bluetooth-proxy.md).
- **The heater's PIN**: the heater shows a 6-digit PIN on its display while Home Assistant pairs; you type it once
- **No phone app connected**: the heater accepts one Bluetooth connection at a time, so close ConfigR or the Dimplex app

## Installation

Install the integration with HACS, then add each heater.

1. In HACS, add this repository as a custom repository of type **Integration**, install **Dimplex BLE** and restart Home Assistant. Without HACS, copy `custom_components/dimplex_ble` into `config/custom_components` and restart.
2. Accept the discovered heater under **Settings** > **Devices & services**, or select [![Add integration](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=dimplex_ble). If a menu appears, choose **Add a heater**.
3. Choose the Bluetooth proxy. The recommended one is preselected.
4. Select **Submit**. The heater shows its PIN for about 30 seconds.
5. Choose **Enter PIN** and type it. If the PIN disappeared, choose **Show PIN again**.

The heater is added as **Dimplex Heater AMxxxx**.

### Setup fields

The setup dialog asks for:

- **Heater**: the heater to add, when you start setup yourself
- **Bluetooth proxy**: the proxy that connects to this heater; the heater stays on it. Tags explain the choice: **Recommended**, **current**, **⚠ already serves 2 heaters**, **⚠ full**.
- **PIN**: the 6-digit code on the heater's display; it's the same every time for that heater

## Configuration

Settings shared by all heaters live in one optional **Dimplex** entry. Without it, heaters use the defaults.

To add it, open **Settings** > **Devices & services** > **Dimplex BLE**, select **Add device** and choose **Dimplex settings for all heaters**. To change it later, select **Configure** on the **Dimplex** entry.

| Option | Default | Description |
| --- | --- | --- |
| **Sync the heater clock** | On | Sets the heater clock to Home Assistant's time when it's more than a minute off. Timer programmes run on the heater clock. |
| **Boost length** | 60 min | How long the Boost preset runs, 1 to 240 minutes |
| **Dimplex email**, **Dimplex password** | Empty | Your Dimplex (ConfigR) account, for firmware updates. Home Assistant keeps a sign-in token, not the password. Leave empty for no updates. |

When Dimplex stops accepting the sign-in, Home Assistant asks you to sign in again. To move a heater to another proxy, select **Reconfigure** on the heater's entry and type the PIN again.

## Supported functionality

Each heater becomes one device. Only the climate entity appears on dashboards by default; the others are settings or diagnostics on the device page.

### Climate

The climate entity shows the room temperature in 0.1 °C steps, whether the heater is heating, and two modes:

- **Heat**: heats to the target you set, in whole degrees. Presets: **Manual**, **Eco**, **Frost Protect**, **Boost**, **Away**.
- **Auto**: follows the heater's timer programme, which sets the temperature. Presets: **User Timer**, **Home All Day**, **Out All Day**, **Boost**, **Away**.

**Boost** heats at the Boost temperature for the Boost length, then returns to the previous mode. **Away** holds the Away temperature until the Away return time, then returns to the previous mode. During Away the target shows and sets the Away temperature. The heaters have no off mode: **Frost Protect** (7 °C) is the lowest.

### Settings

| Entity | Description |
| --- | --- |
| **Advance** | Switches the current timer period between heating and not heating until the next period starts. Only in timer presets. |
| **Setback** | In **Auto**, holds the Setback temperature during periods without heating. |
| **Setback temperature** | What Setback holds, 8 to 19 °C |
| **Boost temperature** | What Boost heats to, within the heater's setpoint range |
| **Away return time** | When Away ends. Setting it starts Away. |
| **Away temperature** | What Away holds, 7 to 18 °C. Can change only while Away is on. |
| **Adaptive start** | Heats early so a timer period starts at its temperature. Set it the same for all heaters in a room. |
| **Open window detection** | Stops heating when the room temperature drops suddenly |
| **Sound** | Button sounds on the heater |
| **Display temperature unit** | The unit on the heater's display only |
| **User Timer schedule** | The User Timer programme as calendar events. **Home All Day** and **Out All Day** calendars are disabled by default. |
| **Firmware** | Installed and latest heater firmware. **Install** updates the heater over Bluetooth in about 15 minutes. Needs the Dimplex sign-in. |
| **Temperature offset** | Corrects the room reading, −3.0 to +3.0 °C. Disabled by default: Dimplex reserves it for service staff. |

### Diagnostics

| Entity | Description |
| --- | --- |
| **Error** | **No error**, a fault from the Dimplex manual such as **Open window detected (50)**, or **Code N** for an undocumented code |
| **Estimated power** | Rated power × output %, in W. The heater has no power meter. |
| **Estimated energy** | Estimated power over time, in kWh, for the Energy dashboard. Time while the heater is unreachable isn't counted. |
| **Operating mode**, **Rated power**, **Controller temperature**, **Output power** | Disabled by default |

Repairs tell you when a heater stopped responding, when a schedule change waits for an unreachable heater, and when the heater rejects the saved PIN.

## Actions

The integration provides these actions, all targeting one heater:

| Action | What it does | Fields |
| --- | --- | --- |
| `dimplex_ble.set_schedule` | Replaces the heating periods of one programme on the chosen days | `device_id`; `programme`: `user_timer`, `home_all_day` or `out_all_day`; `days`: any of `mon` to `sun`; `periods`: up to 4 per day, each with `start` and `end` (`HH:MM`) and `temperature` (°C). `[]` removes all periods. |
| `dimplex_ble.get_schedule` | Returns `read_at`, the time of the last read, and per programme the periods as last read (`heater`), the changes waiting for the heater (`pending`) and the backup copy (`backup`) | `device_id`; optional `programme` (default: all three) |
| `dimplex_ble.restore_schedule` | Writes the backup copy back to the heater. Only complete weeks are restored. | `device_id`; optional `programme` (default: every programme with a backup) |
| `dimplex_ble.get_gatt_services` | Returns the heater's Bluetooth services and characteristics, for bug reports | `device_id` |

`set_schedule` reads each day back from the heater and fails if the heater didn't keep it. If the heater can't be reached, the action still succeeds: Home Assistant keeps the change across restarts, shows a repair, and writes the change as soon as the heater answers. The backup copy starts from the first read of all 7 days and then follows only changes saved through Home Assistant, so a heater reset to factory programmes never overwrites it. See [Timer programmes](docs/user/schedules.md) for the format.

## Data updates

The integration polls each heater once a minute: it connects, reads and disconnects. Commands connect at once.

- **Every minute**: temperature, mode, target, Away, Boost, error and output
- **Every 5 minutes and after a change**: Sound, Setback, Setback temperature, temperature offset and the heater's ID
- **Every 6 hours and after a save**: timer programmes
- **Every 12 hours**: the latest firmware from Dimplex

A change made on the heater's own buttons shows within a minute, or within 5 minutes for the settings above.

## Use cases and examples

The heater runs its timer programmes on its own, so set them once from Home Assistant and keep the heater in **Auto**. Use automations for occasional changes you can afford to miss.

Set a weekday programme:

```yaml
action: dimplex_ble.set_schedule
data:
  device_id: your_heater_device_id
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

Switch to Away when everyone leaves, returning at 07:00 the next day:

```yaml
automation:
  - alias: Heaters to Away when the house is empty
    triggers:
      - trigger: state
        entity_id: zone.home
        to: "0"
    actions:
      - action: datetime.set_value
        target:
          entity_id: datetime.your_heater_away_return_time
        data:
          datetime: "{{ (today_at('07:00') + timedelta(days=1)).isoformat() }}"
```

Setting the return time starts Away; the heater returns to its previous mode at that time, even if Bluetooth stops.

## Known limitations

These limits come from the heaters and the Bluetooth proxies:

- **Bluetooth stops**: after an unexpected disconnect, a heater's Bluetooth can stop until a power cycle. Home Assistant shows the repair **Heater stopped responding**.
- **2 heaters per proxy**: an ESP32-S3 proxy refuses a third heater after every Home Assistant restart ([esphome/esphome#22859](https://github.com/esphome/esphome/issues/22859)).
- **One connection at a time**: while a phone app is connected, Home Assistant can't reach the heater.
- **Pairing belongs to the proxy**: moving a heater to another proxy needs the PIN again.
- **No off mode, no locks**: the heaters accept no Standby command over Bluetooth, and the child lock and heater lock exist only on the keypad.
- **Manual at 19 °C or lower shows Eco**: the heater switches itself to Eco. Use 20 °C or higher to stay in Manual.
- **Schedules aren't edited as calendar events**: use `dimplex_ble.set_schedule`.
- **During a firmware update** the heater can't be controlled. Keep it switched on.

## Troubleshooting

Check the layers in order: the proxy, the Bluetooth connection, then the heater. Re-entering the PIN rarely helps. The [troubleshooting guide](docs/user/troubleshooting.md) covers:

- **No proxy offered**: install the PIN responder on a proxy near the heater and close any phone app
- **No PIN appears**: the connection didn't reach the PIN request; check the proxy, then choose another
- **Heater stopped responding**: switch the heater off at the wall for 5 seconds
- **Unavailable after a restart**: move heaters so no proxy serves more than 2

## Removing the integration

This integration follows standard integration removal: delete each heater's entry, and the **Dimplex** entry if you added one, under **Settings** > **Devices & services** > **Dimplex BLE**. The heaters and the proxy firmware stay unchanged. To remove the PIN responder from a proxy, delete the `dimplex_pin_responder` package from its ESPHome configuration and install it again.

## Legal notice

This is an unofficial, independently developed integration for Dimplex products you own or control. It is not affiliated with, endorsed by or supported by Dimplex. The repository contains no Dimplex software, firmware or credentials. The integration contacts Dimplex only to sign in with your own account and to check for and download firmware. Pairing uses the PIN your heater displays. No warranty is made that the integration is safe for unattended or safety-critical heating control. The MIT license covers only this project's code.

## For developers

How the integration works, the Bluetooth protocol and the development workflow are in [Technical documentation](docs/technical/README.md).
