# Set up a Bluetooth proxy for Dimplex heaters

Home Assistant reaches each heater through an ESP32 running ESPHome as a Bluetooth proxy. This guide installs the proxy firmware with the Dimplex PIN responder, adds it to Home Assistant and explains how to choose a proxy for each heater.

## Install the proxy firmware

The proxy runs stock ESPHome plus a small package that answers the heater's PIN request on Home Assistant's behalf.

1. Copy [`firmware/example-bluetooth-proxy.yaml`](../../firmware/example-bluetooth-proxy.yaml) and [`firmware/dimplex_pin_responder.yaml`](../../firmware/dimplex_pin_responder.yaml) into the same folder of your ESPHome configuration.
2. Add `api_encryption_key`, `wifi_ssid` and `wifi_password` to your ESPHome secrets. The key protects the PIN that Home Assistant sends to the proxy, and the package makes firmware uploads require it too. A key that Home Assistant provisions at adoption doesn't work: the package needs it in the YAML.
3. If your board isn't an ESP32-S3, replace `esp32-generic-s3.yaml` in the package URL with the matching file from [esphome/bluetooth-proxies](https://github.com/esphome/bluetooth-proxies).
4. Install the firmware from the ESPHome dashboard.

The proxy needs ESPHome 2026.11.0 or newer: older versions can lose the heater's pairing, and the package refuses to build with them. Include the package after the stock proxy package, as the example does: the Bluetooth settings it needs then take precedence.

## Add the proxy to Home Assistant

Home Assistant discovers the proxy through the ESPHome integration.

1. Open **Settings** > **Devices & services** and add the discovered ESPHome device, or add the ESPHome integration with the proxy's address.
2. Enter the API encryption key from your secrets.
3. Give the proxy a name that says where it is, such as **Living Room Bluetooth Proxy**. The heater setup dialog lists proxies by this name.

## Choose a proxy for each heater

The setup dialog lists every proxy that hears the heater and runs the PIN responder, and preselects the best one. Each proxy shows at most one tag:

| Tag | Meaning |
| --- | --- |
| **Recommended** | The first in the list. Proxies are ordered by: a free connection and fewer than 2 heaters or open connections; then the heater's current proxy; then the strongest signal. |
| **current** | The proxy the heater uses now. Keeping it needs no PIN. |
| **⚠ already serves 2 heaters** | 2 or more heaters, or open Bluetooth connections, on this proxy. A third heater won't reconnect after Home Assistant restarts. |
| **⚠ full** | No free connection, so pairing can't start |

Proxies that hear the heater but can't be used are listed under **Not shown**: they lack the PIN responder or are offline.

A heater stays on the proxy you choose, even if another proxy is closer. To move it, select **Reconfigure** on the heater's entry and type the PIN.

## Plan proxy capacity

Each proxy has 3 Bluetooth connection slots, shared with every other Bluetooth device it connects to. A heater uses a slot only while it updates or runs a command: 5 seconds per update at the median, 15 seconds at the 95th percentile (778 updates, 25 Nov 2026).

Keep at most 2 heaters per ESP32-S3 proxy. With 3, the proxy refuses one heater after every Home Assistant restart ([esphome/esphome#22859](https://github.com/esphome/esphome/issues/22859)).

## Keep the PIN private

Home Assistant stores the PIN you type in its configuration storage so it can restore the pairing without you. A heater added on a proxy that was already paired with it stores no PIN; if that pairing is lost, Home Assistant asks for the PIN. The PIN never appears in this integration's logs or diagnostics, and the integration blanks it from the `aioesphomeapi.connection` debug log.

Don't call the `dimplex_pin_arm` proxy action from automations or scripts: Home Assistant records action data, including the PIN.
