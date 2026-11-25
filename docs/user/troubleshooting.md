# Troubleshooting

Most problems are in the proxy or the Bluetooth connection, not the heater. Check the layers in order: Home Assistant, the proxy, the connection, then the heater. Re-entering the PIN or reconfiguring rarely helps.

## How pairing works

The heater's Bluetooth is always on; it has no pairing mode. Pairing starts when Home Assistant connects through the proxy, and only then does the heater show its PIN, for about 30 seconds. A paired heater reconnects without a PIN. No PIN on the display means the connection didn't reach the PIN request: check Home Assistant, the proxy and the proxy choice before the heater.

## The setup dialog finds no proxy

The heater must be heard by a proxy running the PIN responder.

1. Install the PIN responder on a proxy near the heater: see [Set up a Bluetooth proxy](bluetooth-proxy.md). Proxies without it are listed under **Not shown**.
2. Check that the proxy is online under **Settings** > **Devices & services** > **ESPHome**.
3. Close ConfigR or the Dimplex app: a connected heater stops advertising.
4. Move the proxy closer, wait a minute and submit again.

## No PIN appears

The proxy refused or lost the connection.

- **Proxy busy**: choose another proxy, especially one not tagged **⚠ already serves 2 heaters** or **⚠ full**
- **Another heater pairing**: wait until it finishes
- **PIN gone before you typed it**: choose **Show PIN again**
- **Wrong PIN**: type it again from the display; it's the same every time

## Heater stopped responding

This repair appears when a heater's connection ended unexpectedly and no proxy heard the heater for 60 seconds while its proxy stayed online. On the tested heaters this meant the heater's Bluetooth had stopped, and only a power cycle restarted it. First check that other Bluetooth devices on the same proxy still report.

1. Switch the heater off at its isolator switch, or at the circuit breaker or fuse, for at least 5 seconds.
2. Switch it on. Home Assistant reconnects without a PIN and clears the repair once it hears the heater.

No phone app can reach a stalled heater either.

## A heater is unavailable

An unavailable heater isn't a PIN problem: Home Assistant keeps retrying and asks for the PIN only when the heater rejects the saved one.

1. If every heater on the same proxy is unavailable, check the proxy: is it online, and does it still report other Bluetooth devices?
2. If only this heater is unavailable right after a restart, count the heaters on its proxy. With 3 or more, move one with **Reconfigure**.
3. Close any phone app connected to the heater.
4. Scan near the heater with a phone Bluetooth app: it should list `AMxxxx` while Home Assistant isn't connected.
5. If no scanner hears the heater, power-cycle it as described above.

## A firmware update stopped

Home Assistant shows why the update stopped; the **Firmware** entity shows the version the heater reports. Check that the heater is switched on and its proxy is online, then select **Install** again. If Home Assistant says the heater still reports the old version after an update, wait a minute and check the **Firmware** entity before installing again.

## Collect information for a bug report

Include:

- the heater name (`AMxxxx`) and its proxy
- whether the proxy is online and other devices on it work
- whether a phone scan sees the heater
- whether the PIN appeared
- the first error in the log
- whether it recovered without reconfiguring

Enable debug logging for `custom_components.dimplex_ble`: every line names the event and the heater, never the PIN. Don't share PINs, API keys, tokens or ESPHome secrets.
