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

For a Dimplex heater, only the first two are generally safe automatically. The
current integration deliberately pins each heater to its configured proxy for
runtime connections because the bond belongs to the proxy that performed
pairing.

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

The integration intentionally differs from generic Home Assistant path
selection:

- `proxy_source` is stored in each config entry.
- `ble_device_from_source()` resolves the heater through that exact proxy.
- The client uses that proxy's connector and does not silently roam.
- One semaphore is maintained per proxy, currently matching ESPHome's default
  three-slot configuration.
- The semaphore limits Dimplex integration concurrency only; it does not know
  about slots consumed by other Home Assistant integrations.
- A persistent client connection is reused while healthy and released on
  operation failure or unload.

This protects proxy-local bonding and prevents a reconnect from accidentally
being attempted through a different controller. It also means the integration
currently does not benefit from Home Assistant's generic cross-proxy path
scoring for runtime Dimplex connections.

## Implications for two proxies and three heaters

The current layout is valid:

- AM6532 and AM8224 share one proxy: two active GATT clients, below the default
  three-slot limit.
- AM2086 uses the second proxy: one active GATT client.
- Passive advertisements can be heard by either proxy without consuming slots,
  but active reads and writes use the configured bond-owning proxy.

Other active Bluetooth integrations may consume the remaining slot on the
shared proxy. The integration's local semaphore cannot account for those
external consumers; ESPHome's actual allocation response and proxy logs are the
authoritative capacity view.

## Safe design options

### Recommended now: proxy affinity

Keep the configured proxy for already-paired Dimplex heaters. On a temporary
failure, retry and reconnect through the same proxy. Mark the entity
unavailable if that proxy cannot reach the device. Do not probe an unverified
alternate proxy during normal runtime.

### Safe load balancing: assignment only

For a new heater before pairing, the integration ranks eligible proxies by:

1. passkey-relay support;
2. an available ESPHome connection slot;
3. remaining proxy capacity;
4. the number of existing Dimplex entries assigned to that proxy; and
5. RSSI as the final tie-breaker.

The allocation value is read from Home Assistant's registered scanner, so the
selection uses the proxy's actual reported slot state rather than only the
integration's local semaphore. The selected route is persisted after pairing.
This balances new assignments without moving an existing bond.

### Explicit migration: possible, but user-driven

A migration flow could let the user choose another proxy, perform a fresh
pairing, verify a protected read, and only then save the new `proxy_source`. It
must not assume that a proxy-side `UNPAIR` operation erases the heater's own
bond or that a new proxy can recover the old bond without a PIN.


### Not recommended: automatic runtime roaming

Do not automatically switch a bonded Dimplex heater to another proxy merely
because that proxy has stronger RSSI or a free slot. This can produce repeated
PIN prompts, failed encryption, stale availability, and competing bonds.

## Evidence and remaining questions

**Verified upstream facts:** ESPHome has per-proxy GATT slots; Home Assistant's
generic Bluetooth wrapper scores available scanner paths; the ESPHome API has
per-proxy pairing and allocation messages; no cross-proxy bond-transfer API was
found in the current protocol.

**Project-specific fact:** the Dimplex integration stores and enforces a
proxy-specific route because the current pairing design requires the same proxy
to own the authenticated connection.

**Open implementation question:** whether future `bleak-esphome` or ESPHome
versions add portable bond export/import. Until an upstream API and a verified
Dimplex migration sequence exist, runtime roaming should remain disabled.

## Project runtime finding: pairing is not a reconnect operation

The installed ESPHome backend defines `connect(pair=True)` as:

1. establish the remote GATT link; then
2. call the ESPHome `BluetoothDevicePair` API.

The previous Dimplex client passed `pair=True` for normal snapshot reads,
experimental reads, and writes. Therefore every lost persistent connection
requested a new pairing operation instead of only reconnecting with the
existing bond. This was a concrete integration defect, not a limitation of
ESPHome roaming.

The client now uses `pair=True` only for the intentional first-time pairing
operation. Runtime reads, writes, and the post-pair bond verification use
`pair=False`. This keeps the proxy-local bond in use without repeatedly
starting the PIN exchange.

The live two-proxy test also showed that arbitrary alternate-proxy GATT probes
are unsafe as a health check: while the three heaters were actively connected
through their assigned proxies, probing the opposite proxies timed out and
left the assigned connections unavailable until both proxies were rebooted.
This is evidence against probing unbonded alternate paths during normal
polling. It is not evidence that the alternate proxies lack radio range.

Current live allocation after recovery:

- Main Bedroom proxy: two allocated heater connections, one free slot.
- Living Room proxy: one allocated heater connection, two free slots.

The safe next step for roaming is explicit per-heater commissioning through a
second proxy, followed by authenticated-read verification. Runtime load
balancing must only choose among routes proven to own a usable Dimplex bond.
