# BLE, ESPHome proxy load balancing, and roaming

Reviewed against current upstream ESPHome, Home Assistant, `habluetooth`, and
`bleak-retry-connector` sources.

## Executive result

There are three different behaviors that are often called roaming:

1. **Advertisement aggregation:** multiple scanners/proxies can hear the same
   device. Home Assistant aggregates those advertisements. This is safe and
   does not require a GATT connection or a bond.
2. **Connection-path selection:** Home Assistant can select one of several
   connectable scanners for a new GATT connection. This is automatic for
   integrations using the normal Home Assistant Bluetooth/Bleak wrapper.
3. **Bonded-device migration:** moving a paired device from one ESPHome proxy
   to another. This is not automatic. The target proxy needs its own usable
   bond; the ESPHome protocol has no bond-replication or cross-proxy migration
   mechanism.

For a Dimplex heater, advertisement aggregation is safe automatically. Runtime
connection-path selection is safe only among currently advertised routes whose
proxy-local bond has already been verified. The integration therefore keeps the
configured proxy for commissioning and uses controlled failover/rebalancing only
after a fresh encrypted connection can be established.

## ESPHome proxy capacity

ESPHome separates advertisement forwarding from active GATT connections:

- `connection_slots` limits simultaneous active GATT connections.
- Advertisements do not consume GATT connection slots.
- ESP32 defaults to three slots and supports up to nine configured slots.
- ESPHome recommends no more than five slots for stability and memory use.
- The configured value must not exceed the ESP32 BLE `max_connections` value.
- A device that stays connected occupies a slot; a short-lived connection frees
  the slot after disconnect.
- There is no cross-proxy scheduler in the ESPHome proxy component.

The proxy API exposes per-proxy allocation state through
`BluetoothConnectionsFreeResponse`: `free`, `limit`, and the allocated device
addresses. This is local to one proxy.

Sources:

- [ESPHome Bluetooth Proxy documentation](https://esphome.io/components/bluetooth_proxy/)
- [ESPHome Bluetooth proxy implementation](https://github.com/esphome/esphome/blob/dev/esphome/components/bluetooth_proxy/bluetooth_proxy.cpp)
- [ESPHome API protocol: connection slots and pairing responses](https://github.com/esphome/aioesphomeapi/blob/main/aioesphomeapi/api.proto#L1948-L2195)

## Home Assistant path selection

Home Assistant owns the scanner set, including ESPHome remote proxies. Its
Bluetooth API exposes every connectable scanner path for an address through
`async_scanner_devices_by_address`. The normal Bleak wrapper then:

1. gathers all connectable paths for the address;
2. ranks them using RSSI and connection-path scoring;
3. excludes paths without a usable connection slot;
4. penalizes paths with in-progress connections, recent failures, or only one
   remaining slot; and
5. connects through the highest-scoring available path.

This is load-aware path selection, not round-robin balancing. RSSI remains the
main input; slot pressure and failure history modify the score. A later new
connection can select a different path if its score is better or the previous
path is unavailable.

Home Assistant also provides reachability diagnostics that report which
scanners see a device and whether connection slots are available.

Sources:

- [Home Assistant Bluetooth integration guidance](https://contributors.home-assistant.io/core/integrations/bluetooth/)
- [Home Assistant Bluetooth API](https://github.com/home-assistant/core/blob/dev/homeassistant/components/bluetooth/api.py)
- [Home Assistant Bluetooth manager](https://github.com/home-assistant/core/blob/dev/homeassistant/components/bluetooth/manager.py)
- [`habluetooth` path scoring](https://github.com/Bluetooth-Devices/habluetooth/blob/main/src/habluetooth/base_scanner.py)
- [`habluetooth` Bleak backend selection](https://github.com/Bluetooth-Devices/habluetooth/blob/main/src/habluetooth/wrappers.py)

The current Home Assistant guidance also says not to reuse a `BleakClient`
between separate connections, to use a connection timeout of at least ten
seconds, and to expect intermittent failures with retry handling.

## Retry behavior is not roaming

`bleak-retry-connector` retries transient connection failures and recognizes
out-of-slot conditions. Its retry loop improves reliability, but it does not
constitute a global load balancer. The candidate backend is resolved by the
Home Assistant Bluetooth wrapper, while ESPHome's connector remains tied to the
selected remote scanner for that attempt.

A retry may use a different path only when the integration resolves candidates
through the normal Home Assistant wrapper. An integration that explicitly
constructs a client from one proxy's connector remains pinned to that proxy.

Source:

- [`bleak-retry-connector` connection and retry implementation](https://github.com/Bluetooth-Devices/bleak-retry-connector/blob/main/src/bleak_retry_connector/__init__.py)

## Pairing and bonds

The ESPHome API identifies the target device address in every connection,
pairing, unpairing, and GATT request. Pairing produces a response from the
proxy that handled the request. The protocol exposes no operation to copy the
resulting bond to another proxy.

Therefore:

- pairing AM6532 through proxy A does not prove that proxy B can reconnect it;
- advertisements from proxy B do not transfer proxy A's bond;
- increasing `connection_slots` does not improve pairing or create bonds; and
- automatic failover to another proxy can fail precisely when the original
  proxy is unavailable, because the new proxy may not be bonded.

A safe proxy migration is an explicit commissioning operation: select the new
proxy, ensure it can reach the heater, perform the device's supported pairing
flow, verify an authenticated read, and only then update the stored route.
Automatic migration must not be used as a recovery retry.

## Current Dimplex integration behavior

The integration keeps the configured `proxy_source` as the commissioning and
bond-ownership route. It now uses Home Assistant's registered Bluetooth paths
for new runtime connections:

- `async_scanner_devices_by_address()` supplies the current connectable
  advertisements.
- RSSI is read from the HA advertisement record (`advertisement.rssi`), which
  is the current Home Assistant `BluetoothScannerDevice` API.
- Home Assistant's native wrapped client already uses this same scanner
  registry and scanner connection-capacity model. The integration keeps the
  source-specific connector only because the ESPHome client exposes the
  required native `pair()` encryption request there; the HA wrapper does not
  expose that ESPHome-specific operation. No proxy firmware routing logic is
  duplicated.
- The connector's current `can_connect()` result is also honored, so a route
  that is registered but temporarily unable to accept connections is skipped
  before any GATT attempt.
- For a fresh connection, known free connection capacity is preferred, then the
  current advertisement RSSI; a live connection is retained until a safe
  operation-boundary handoff is justified.
- A route that drops an operation is deprioritized for the next fresh
  connection, so recovery tries another eligible path before retrying the
  failed proxy. Failed routes are also held out of proactive RSSI rebalancing
  for five minutes while a working route remains available.
- The selected route is retained for the persistent connection and is
  reconsidered after disconnect, operation failure, reload, or restart.
- While connected, the client evaluates current advertisements at operation
  boundaries. After a five-minute stability cooldown, it performs a controlled
  disconnect/reconnect only when another available route is at least 10 dBm
  stronger or the active route is no longer usable.

This is not in-place GATT migration. The client never changes a live
connection's proxy underneath an operation; it closes the connection at a safe
operation boundary, then establishes a fresh encrypted connection. This avoids
breaking device transaction state or creating competing proxy-local bonds.

Each newly established runtime route uses:

1. `connect(pair=False)` to open the GATT link without starting PIN pairing;
2. the native ESPHome `client.pair()` operation to request encryption from an
   existing proxy-local bond; and
3. the first protected GATT read only after that encryption request succeeds.

The second step maps to ESPHome's native `esp_ble_set_encryption()` path. It
does not submit a passkey. Explicit `pair=True` remains limited to intentional
commissioning and reauthentication.

The integration maintains one semaphore per proxy, matching ESPHome's default
three-slot configuration. The semaphore limits Dimplex's own connection
attempts; ESPHome's allocation response remains authoritative because other
Bluetooth integrations may consume slots.

## Two-proxy, three-heater implications

The current layout is valid:

- AM6532 and AM8224 share one proxy: two active GATT clients, below the default
  three-slot limit.
- AM2086 uses the second proxy, subject to that heater being currently
  discoverable and bonded.
- Passive advertisements can be heard by either proxy without consuming slots.
- Active reads and writes use a route that has a usable bond; a stronger RSSI
  alone does not create a bond.

AM6532 and AM8224 were independently verified with authenticated protected
reads through both ESPHome proxies. This proves that those heaters can fail
over between the two routes without a new PIN after each route has been
commissioned. AM2086 remains unverified when it is not advertised; the
integration does not guess a route or invent a bond.

## Safe design boundaries

### Automatic runtime route selection

Automatic selection is safe at a fresh connection boundary or at a controlled
operation boundary where the existing GATT connection is closed before the new
route is selected:

- use currently advertised, connectable paths;
- use registered ESPHome proxies only;
- select the best eligible route at a fresh connection boundary;
- retain the active route during healthy operation until a rebalance threshold
  is met;
- respect reported slot capacity;
- fall back to another candidate only after the first candidate fails; and
- request existing-bond encryption without sending a PIN.

If an alternate path has no usable bond, it fails cleanly and the next route is
tried. The integration does not automatically begin first-time pairing on an
alternate path.

### Explicit commissioning or migration

An explicit pairing flow can select a proxy, perform the supported PIN exchange,
verify a protected read after reconnect, and then persist that proxy as the
configured route. This remains user-driven because ESPHome has no bond export or
cross-proxy bond replication API.

### Not implemented

The integration does not:

- change a proxy underneath an active GATT operation;
- round-robin healthy persistent connections;
- open GATT probes to every proxy periodically just to score RSSI;
- send a PIN automatically during runtime failover; or
- treat advertisement strength as proof of authentication.

These restrictions avoid the previously observed failure mode where arbitrary
alternate-proxy probes disrupted otherwise healthy assigned connections.

## Evidence and remaining questions

**Verified upstream facts:** ESPHome has per-proxy GATT slots; Home Assistant
exposes source-specific scanner paths and allocation state; ESPHome has
per-proxy pairing and encryption operations; no cross-proxy bond-transfer API
was found in the current protocol.

**Verified project facts:** the integration stores the configured proxy for
commissioning; AM6532 and AM8224 have usable bonds on both proxies; AM2086 was
not observed in the available raw scan and therefore is not claimed as
cross-proxy verified.

An independent 120-second raw-advertisement scan on both proxies saw only
AM6532 and AM8224. A direct address-only GATT attempt to AM2086 through the
living-room proxy also failed before authentication. The current evidence
therefore does not justify adding an address-only connection fallback.

The later direct `aioesphomeapi` raw-advertisement test attached independent
API clients to both live proxies. It saw no AM2086 advertisements, then the
existing HA connections began timing out until Home Assistant was restarted.
This does not prove a universal ESPHome single-client limitation, but it is
conclusive operational evidence for this installation: diagnostics must not
attach competing API subscribers to live proxies. The integration therefore
uses only Home Assistant's already-managed scanner registry for route
selection and monitoring.

**Remaining limitation:** a heater must first be commissioned through every
proxy that may be used for automatic failover. Runtime route selection cannot
manufacture a missing bond.

## Project runtime finding: pairing is not a reconnect operation

The installed ESPHome backend defines `connect(pair=True)` as:

1. establish the remote GATT link; then
2. call the ESPHome `BluetoothDevicePair` API.

The previous Dimplex client passed `pair=True` for normal snapshot reads,
experimental reads, and writes. Therefore every lost persistent connection
requested a new pairing operation instead of only reconnecting with the
existing bond. This was a concrete integration defect, not a limitation of
ESPHome roaming.

The client now uses `pair=True` only for the intentional commissioning or
reauthentication operation. Runtime reads, writes, and the post-pair bond
verification use `pair=False`, followed immediately by the native
`client.pair()` encryption request. This keeps the proxy-local bond in use
without repeatedly starting the PIN exchange.
During commissioning, route selection is intentionally narrower than runtime
roaming: the client may use only the proxy that received the address-scoped
pairing arm action. This prevents a stronger advertisement from silently
redirecting a PIN exchange to a second proxy whose relay was not armed.

The live two-proxy test also showed that arbitrary alternate-proxy GATT probes
are unsafe as a health check: while the three heaters were actively connected
through their assigned proxies, probing the opposite proxies timed out and
left the assigned connections unavailable until both proxies were rebooted.
This is evidence against probing unbonded alternate paths during normal
polling. It is not evidence that the alternate proxies lack radio range.

The last observed live allocation after recovery was:

- Main Bedroom proxy: two allocated heater connections, one free slot.
- Living Room proxy: one allocated heater connection, two free slots.

AM6532 and AM8224 have completed explicit commissioning and authenticated-read
verification through both proxies. AM2086 still requires an observed,
authenticated route before it can participate in automatic failover.
The post-cooldown observation then recorded successful coordinator updates for
AM6532 and AM8224 throughout the 15-minute window. No route-switch or
authentication-failure log occurred for either bonded heater. The only failed
entry was AM2086, whose configured address remains `c4:d8:d5:69:c0:86`.

An active pairing flow was separately attempting address
`d0:ef:76:36:dd:be` through the main-bedroom proxy and timed out before
authentication. That address is not stored in any of the three configured
entries, so its relationship to AM2086 remains unproven.
