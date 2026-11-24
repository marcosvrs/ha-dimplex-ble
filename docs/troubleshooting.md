# Troubleshooting

Work through the layers in order: Home Assistant, the Bluetooth proxy, the
Bluetooth connection, and only then the heater. Do not re-enter the PIN or
reconfigure the heater until the earlier layer works.

## Facts about pairing

- The heater's Bluetooth is always on. It has no pairing mode and no Bluetooth setting.
- Pairing starts when Home Assistant connects through the proxy. Only then does
  the heater show its 6-digit PIN, for about 30 seconds.
- A heater that is already paired reconnects without a PIN.
- No PIN on the display means the proxy could not connect. It does not mean the
  heater's Bluetooth is off.

## The setup dialog finds no proxy

- Install the Dimplex firmware on a proxy near the heater (README, Setup step 1).
  Proxies without it are named under "Not shown".
- Check that the proxy is online under **Settings → Devices & services → ESPHome**.
- Close the Dimplex phone app: while it is connected, the heater does not
  advertise.
- Move the proxy closer, wait a minute and submit again.

## No PIN appears, or "Pairing didn't start"

- The proxy refused or lost the connection. Choose another proxy, especially
  if the current one is tagged "⚠ already serves 2 heaters" or "⚠ full".
- Another heater may be pairing on the same proxy at the same time; wait for
  it to finish.
- The PIN disappeared before you typed it: choose **Show PIN again**.
- "Wrong PIN": check the display and type it again. The PIN is the same every
  time for this heater.

## Repair: "*Heater* stopped responding"

1. Switch the heater off and on at the wall. Home Assistant reconnects without a
   PIN and clears the repair once it hears the heater.
2. If it happens again, open the heater's **Configure** and turn off **Keep the
   Bluetooth connection open**: each update then connects briefly (every 60 s,
   so controls react more slowly).

Why: the repair appears only when the heater's connection ended without Home
Assistant disconnecting it and then no proxy heard the heater for 60 seconds,
while its proxy stayed online. On hardware (2026-11-24) heaters were silent to
every receiver, including a phone scan, after such a drop until power-cycled;
after a normal disconnect they always advertised again within seconds.

## A heater is unavailable

1. Are all heaters on the same proxy unavailable? Then check the proxy: is it
   online in Home Assistant, and does it still report other Bluetooth devices?
2. Only this heater, right after a Home Assistant restart? Count the heaters on
   its proxy: with 3 or more, one is refused after every restart. Move one to
   another proxy with **Reconfigure** (max 2 per proxy).
3. Is the Dimplex phone app connected to it? Close the app.
4. Is the heater still in range? An independent Bluetooth scan near the heater
   (for example a phone app) should show its `AMxxxx` name while Home Assistant
   is not connected to it.
5. Only if the heater is not visible to any scanner: power-cycle that one heater
   as a last resort, then wait for Home Assistant to reconnect (no PIN needed).

An unavailable heater is not a PIN problem: Home Assistant keeps retrying and
asks for the PIN (with a repair) only when the heater rejects the saved one.

## Moving a heater to another proxy

The pairing belongs to the proxy. Use **Reconfigure**, choose the other proxy and
enter the PIN. The heater switches only after pairing on the new proxy worked;
the old proxy is left untouched.

## What to collect before reporting a failure

Record:

- panel-heater name and stable Bluetooth address;
- selected ESPHome proxy and its online state;
- whether an independent scan sees the advertisement;
- whether the PIN appeared on the display;
- the first connection, GATT, authentication, or route error;
- whether another panel heater on the same proxy continued working;
- whether the failure recovered without reconfiguration.

Do not include PINs, API keys, tokens, cookies, or ESPHome secrets in logs or issue reports.

Keep `aioesphomeapi.connection` at `info`. At `debug` that library logs every
ESPHome action with its arguments, so the PIN Home Assistant sends to the proxy's
responder can appear in the log; the integration cannot redact it and warns at
startup when that logger is at `debug`. Debug logging for
`custom_components.dimplex_ble` is safe: it never logs the PIN. If you did enable
`aioesphomeapi.connection` debug logging, remove the PIN from the log before
sharing it.

## Do not use these as routine fixes

- Re-entering the PIN on every reconnect.
- Reconfiguring a healthy entry because one poll failed.
- Looking for a pairing mode or a Bluetooth setting on the heater: there is none, Bluetooth is always on.
- Power-cycling the heater. Use it only as a last resort, after every step above.
- Clearing all proxy NVS bonds for a single-device failure.
- Treating a single GATT status as proof that the panel heater is defective.
- Adding a second custom polling or roaming loop to the ESPHome proxy.
- Calling the `dimplex_pin_arm` action from automations or scripts: a Home Assistant service call stores its data, including the PIN, in the recorder.

## Internals (for developers)

### Advertisement checks

A heater may not remain discoverable to a second scanner while an authenticated
proxy connection is active. If Home Assistant completes snapshots, that proves
the route; do not read the second scanner's silence during that window as a new
outage. Run an independent scan before reconnecting or after the route is idle.
No Home Assistant code or PIN entry can bypass a missing radio advertisement.

### PIN window

The PIN window is the Bluetooth SMP response timer (30 s on both the ESP and the
heater) and cannot be extended from Home Assistant. If the PIN is typed late,
the flow retries once with the PIN already armed on the proxy.

### Why a failed pairing loses the bond

ESP-IDF (Bluedroid, verified in v5.5.5 `bta_dm_act.c` and `btc_dm.c`) deletes the bond for a peer, from RAM and NVS, whenever an authentication attempt with that peer completes unsuccessfully (any reason except 5). That includes:

- a wrong or unanswered PIN (reasons 81, 99);
- a link that drops while the security procedure is still running (reason 102), for example because the central disconnected mid-`pair()`;
- a failed new pairing over an existing good bond.

Consequences for the integration:

- `pair()` is always awaited to its native end. Cancellation, reload, unload and Home Assistant stop wait for it before closing the link.
- Before every pairing the responder is armed with the stored PIN, so a deleted bond is re-created on the next poll without user action.
- Only a PIN-rejection code starts reauthentication, which stops polling, so a wrong stored PIN does not cause a loop of failed pairings.

Never use a debug firmware that clears bonds, and never erase the proxy NVS to fix one heater.

### Bonded panel heater becomes unavailable after working normally

First distinguish a transport failure from an authentication failure. Enable debug logging for `custom_components.dimplex_ble`; every line starts with an event name and the heater address:

`ROUTE_NOT_SEEN`, `CONNECT_REQUEST`/`CONNECT_RESULT`, `RESPONDER_ARM`/`RESPONDER_CLEAR` (never the PIN), `PAIR_REQUEST`/`PAIR_RESULT code=<SMP reason> <seconds>`, `GATT ... att=<ATT status>`, `DISCONNECT reason=...`, `DISCONNECTED ... link down`.

- `ROUTE_NOT_SEEN`: the pinned proxy does not see the heater; nothing is attempted until it advertises again.
- A connection timeout, `PAIR_RESULT code=99/102`, or a GATT error (including ATT 0x05/0x0F right after a successful pairing) is transient: the heater is unavailable until the next successful poll.
- Only `PAIR_RESULT code=81` (wrong PIN, hardware-confirmed in ledger test H8) starts reauthentication. Codes 78, 80 and 88 were never observed on hardware and are treated as transient.

For the proxy side, turn on the ESPHome integration's device-log forwarding; the stock `ble_client` logs the authentication result and reason.

Do not clear every BLE bond or factory-reset a proxy to fix one panel heater.

### Stale ESPHome API client after a proxy reconnect

When a proxy's native API connection drops and comes back (proxy reboot, Wi-Fi loss, or Home Assistant restarting the ESPHome entry), ESPHome registers a new Bluetooth scanner object with a new API client. A BLE client object built on the old scanner still points at the dead API connection, and its `is_connected` flag is only cleared if the disconnect callback was delivered.

The link therefore checks on every operation that the scanner for its proxy is still the same object it connected through; if not, it drops the old client and connects again through the new one. If you see `DISCONNECT reason=stale` after a proxy reboot, that is this path working.

A related operational trap: an ESPHome proxy accepts only a limited number of native API clients. Leftover `esphome logs`/`esphome run` processes can occupy them; Home Assistant then fails to connect (`EncryptionHelloAPIError`) and every heater on that proxy disappears. Always bound diagnostic log captures.

### Proxy capacity

ESPHome owns the proxy's Bluetooth connection slots (`connection_slots`, default
3). Each configured heater keeps one persistent link (unless **Keep the
Bluetooth connection open** is off), and other BLE integrations
share the same slots. The responder's `ble_client` needs one more controller
connection (`max_connections: 4`), not a proxy slot. On an ESP32-S3 a third
heater link is refused ("Rejected: Resources") after every Home Assistant
restart (esphome/esphome#22859; hardware 2026-11-24, 4/4), so keep at most 2
heaters per proxy.

The full evidence record for transport lifecycle, proxy capacity, bonding, and recovery is in [`esphome-ble-findings.md`](esphome-ble-findings.md).