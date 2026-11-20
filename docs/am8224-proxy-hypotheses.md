# AM8224 Bluetooth-proxy investigation

**Date:** 2026-11-20  
**Scope:** AM8224 (`C4:D8:D5:6F:8C:2E`) through the main ESPHome proxy
(`14:C1:9F:21:0E:6A`).

## Corrected root cause

AM8224 is compatible with the ESPHome Bluetooth proxy. The earlier direct
`aioesphomeapi` probes were invalid: they requested a GATT connection without
first subscribing the API client to Bluetooth advertisements. ESPHome's
Bluetooth proxy keeps its active connection subscriber tied to that
advertisement subscription. Without it, the proxy immediately tears down the
connection:

```text
disconnect while backend idle, err=-1
Pending open cancelled
Disconnected, reason=0x100
```

This was a test-harness failure, not an AM8224 radio, range, capacity, or
two-radiator limitation.

## Verified evidence

| Test | Result |
|---|---|
| ESP32-side scan | AM8224 is seen as connectable, `PUBLIC`, with its Dimplex name. |
| Vanilla ESPHome proxy, no advertisement subscription | Connection is torn down immediately; this reproduces the false failure. |
| Vanilla ESPHome proxy, advertisement subscription active | AM8224 connects, discovers services, and reports MTU 517. Seven services were returned. |
| Production proxy with Dimplex relay, advertisement subscription active | AM8224 connection opened; the relay received the passkey request; PIN reply succeeded; authentication reported `success=1`, `reason=0`, `bonds=1`; service discovery completed. |
| Bonded reconnect without PIN | Reconnected successfully and returned seven services. |
| AM6532 through the same proxy | The same subscribed connection path is available; any current failure is a stale bond/HA-entry repair issue, not proxy capacity. |

The successful subscribed test used the same public address type reported by the
ESP32 scanner. Changing the address type did not explain the earlier failure.

## What was misleading

The failed low-level probes used `bluetooth_device_connect()` directly but did
not call `subscribe_bluetooth_le_advertisements()`. HA's Bluetooth integration
normally owns that subscription, so those probes did not model the HA runtime.
The immediate `reason=0x100` was the proxy cancelling the unowned connection,
not a Dimplex rejection.

The repeated `BAD_INDICATOR errno=11` messages came from stale `esphome logs`
clients left running in the ESPHome container during earlier experiments. The
ESPHome tooling container was restarted before the clean verification.

## Pairing and firmware findings

- The production proxy is built with ESP-IDF 5.5.5.
- ESP-IDF 5.5.6 has a documented bonded-reconnect regression in this timeframe:
  successful encryption can clear LE bond keys, breaking a later reconnect.
  Keep the 5.5.5 workaround until the upstream fix is adopted and verified.
- Erasing the proxy NVS partition clears API/BLE bond state. That is a
  diagnostic recovery operation, not a normal pairing step. It invalidates
  existing heater bonds and causes HA entries such as AM6532 to require repair.
- The Dimplex passkey relay is needed only for first pairing. The verified
  reconnect did not need a new PIN.

## HA discovery-route failure and fix

The first HA discovery attempt did fail for a separate, real reason:

```text
No passkey-capable ESPHome route
(connectable_candidates=1, candidates_with_source=1, matching_entries=0)
```

That callback came from a Bluetooth discovery snapshot containing only the
host-adapter route. The ESPHome proxy route was present in Home Assistant's
scanner registry but absent from that snapshot. The config flow therefore
rejected AM8224 before pairing could start, even though the proxy could reach
the heater.

`pairing.select_proxy_source()` now falls back to the scanner registry when the
current discovery snapshot has no eligible route. It still requires a loaded,
encrypted ESPHome entry exposing all three Dimplex pairing actions; a host
adapter or incomplete proxy is never accepted.

The fix was deployed and exercised end to end:

1. HA selected the cached main-bedroom proxy route for AM8224.
2. The flow started PIN pairing without another proxy or firmware change.
3. PIN `577143` created the AM8224 config entry.
4. AM6532 and AM8224 both reported successful coordinator refreshes through
   the same proxy.
5. Restarting HA reconnected both bonded heaters without another PIN or
   authentication-repair error.

The earlier direct-probe root cause and the HA discovery-route race are
independent. Both are now covered: direct probes retain the advertisement
subscription, and HA discovery can recover a valid cached proxy route.

## Source references

- <https://esphome.io/components/bluetooth_proxy/>
- <https://github.com/esphome/ESPHome/issues/19453>
- <https://github.com/esphome/ESPHome/issues/19858>
- <https://github.com/esphome/ESPHome/issues/22859>
- <https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/research.md>
- <https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/backlog.md>
- <https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant>
- <https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump>
- <https://github.com/KRoperUK/dimplex-controller-py>
