# ESPHome and BLE findings for Dimplex heaters

**Status:** verified findings from the local Jarvis deployment and controlled
ESPHome/API tests. This is an operational engineering record, not a claim that
ESPHome upstream guarantees every behavior across future releases.

## Scope and terminology

- **Heater:** a Dimplex Quantum/storage heater exposing the `AM####<Dimplex>` BLE
  name.
- **Proxy:** an ESP32-S3 running ESPHome's standard `bluetooth_proxy` and
  `esp32_ble_tracker` components.
- **Relay:** the only custom ESPHome component,
  `dimplex_passkey_proxy`, which forwards a one-time Dimplex passkey request
  and reply.
- **HA route:** one Home Assistant Bluetooth scanner route. A heater's bond is
  owned by the proxy route used during pairing.

## Verified architecture boundary

Home Assistant owns discovery, route selection, GATT connection lifecycle,
coordinator polling, retries, availability, and teardown. ESPHome remains the
Bluetooth scanner/proxy and GATT transport.

The custom firmware does **not** implement a second scanner, a second GATT
client, a custom polling loop, or Dimplex protocol reads/writes. The custom
firmware only adds the BLE authentication callback relay required because the
heater's six-digit PIN is entered by the user while the ESP32 acts as the
Bluetooth central.

`api.max_connections` controls concurrent ESPHome API clients (HA, diagnostics,
and management tools). It does not increase the ESP32's BLE link capacity.
The standard proxy currently exposes three active BLE connection slots.

## Dimplex pairing facts

- Bluetooth stays enabled on the heater.
- There is no separate heater-side "pairing mode" to enable.
- Pairing starts when the central initiates the connection and authentication.
- The heater displays the PIN only during that authentication attempt.
- A missing PIN display means the central did not reach the passkey request; it
  is not evidence that Bluetooth is disabled.
- The passkey relay is needed for first pairing or bond repair only. A valid
  bonded reconnect does not request a new PIN.
- Pairing is serialized per proxy so a second PIN cannot replace the first
  proxy-scoped pending request.

These facts are encoded in `AGENTS.md` and must not be replaced with advice to
 toggle Bluetooth or put the heater into a non-existent pairing mode.

## Direct ESPHome API test requirement

A direct `aioesphomeapi` probe must subscribe to Bluetooth advertisements before
requesting a GATT connection and must keep that subscription active until the
GATT session ends:

```python
await client.connect(login=True)
advertisement_unsubscribe = client.subscribe_bluetooth_le_advertisements(
    on_advertisement
)
disconnect = await client.bluetooth_device_connect(...)
# use GATT connection
disconnect()
advertisement_unsubscribe()
await client.disconnect()
```

If the advertisement subscription is omitted, ESPHome sees no active API
Bluetooth subscriber and tears down the connection. The observed log sequence
was:

```text
disconnect while backend idle, err=-1
Pending open cancelled
Disconnected, reason=0x100
```

This was a test-harness error, not a Dimplex rejection, radio-range problem,
or proxy-capacity limit. Home Assistant normally owns the required subscription,
so a bare direct probe does not model the HA runtime.

## Controlled connection results

The following were observed with the production-style proxy and a subscribed
API client:

| Test | Result |
|---|---|
| AM8224 through stock proxy path | GATT connection, MTU 517, seven services |
| First AM8224 pairing through the passkey relay | Authentication success, bond created, seven services discovered |
| Bonded AM8224 reconnect | Successful without a PIN |
| AM6532 through the same proxy | Successful |
| AM6532 and AM8224 concurrently through one proxy | Both connected; each reported MTU 517 and seven services |
| HA config flow for AM8224 | Created the entry after the cached-route fix and PIN submission |
| HA restart with both bonds present | Both entries reconnected without PIN or reauthentication |

Therefore one ESP32 proxy can serve both tested heaters. The tested proxy's
three BLE slots leave one slot for another concurrent BLE link, although other
HA Bluetooth integrations can consume the same proxy capacity.

## Firmware settings that are evidence-backed

The stable proxy builds use:

```yaml
esp32_ble:
  io_capability: keyboard_only
  auth_req_mode: bond_mitm

esp32:
  framework:
    version: 5.5.5
    sdkconfig_options:
      CONFIG_BT_BLE_RPA_SUPPORTED: "n"
```

The explicit ESP-IDF 5.5.5 pin is a deployment workaround. ESP-IDF 5.5.6
showed a bonded-reconnect regression in the relevant timeframe in upstream
tracking; it must not be removed until a newer version is tested against these
heaters. `CONFIG_BT_BLE_RPA_SUPPORTED: "n"` is retained because the verified
production proxy used it successfully; it is not a license to add more BLE
customization.

The proxy must expose only these Dimplex user actions:

- `arm_dimplex_pairing(address)`
- `set_dimplex_pairing_passkey(address, passkey)`
- `cancel_dimplex_pairing()`

An attempted `clear_dimplex_bond(address)` action failed compilation because the
current relay component has no `clear_bond` method. Bond clearing is therefore
not part of the supported proxy API. Do not add that action to a YAML file unless
the component implements it and the behavior is separately verified.

## Bonds and NVS

The ESP32 NVS contains state needed for API/BLE operation, including bonds. An
NVS erase is a destructive diagnostic operation:

1. It removes existing heater bonds from the proxy.
2. Existing HA entries can then report authentication failure.
3. Every affected heater must be paired again.

Do not erase NVS as a routine reconnect or troubleshooting step. Prefer an HA
reauthentication flow and inspect the proxy/API connection first.

## Home Assistant route selection

A Dimplex discovery callback can arrive from the host adapter while the selected
ESPHome proxy's route is absent from that one discovery snapshot. The observed
failure was:

```text
No passkey-capable ESPHome route
(connectable_candidates=1, candidates_with_source=1, matching_entries=0)
```

The proxy route was still present in HA's scanner registry. The integration now
falls back to that registry when the current snapshot has no eligible route. It
still requires a loaded, encrypted ESPHome config entry with all three pairing
actions; a host adapter or incomplete proxy is never accepted.

The selected proxy source is persisted with the heater config entry. The
integration does not silently move a heater to another proxy, because the bond
belongs to the controller that performed pairing.

## ESPHome management observations

- Stale `esphome logs` clients left running in the ESPHome container produced
  `BAD_INDICATOR errno=11` noise and API churn. Stop old log clients before
  interpreting proxy behavior.
- The running proxy must be checked through the encrypted API, not only by ping
  or TCP port reachability. API reachability alone does not prove that the
  custom user actions are present.
- After OTA, query the API's `UserService` list and verify the three Dimplex
  actions. Then reload or enable the matching HA ESPHome config entry so HA
  registers the corresponding service names.
- The living-room proxy was online but its HA entry was user-disabled and its
  previous firmware did not expose the relay actions. It was rebuilt with the
  tested settings, flashed over OTA, verified through the API, and enabled in
  HA. The HA service names then appeared.
- One transient main-proxy API reset was observed while enabling the second
  proxy; both radiator coordinators recovered automatically within the next
  refresh cycle. No further errors were observed in the subsequent verification
  window.

## Operational commissioning checklist

1. Validate the YAML and compile before flashing.
2. Use the tested ESP-IDF 5.5.5 configuration for these ESP32-S3 proxies.
3. Flash over OTA only when the device's API is reachable; use USB recovery if
   OTA is unavailable.
4. Verify the encrypted API reports the proxy identity and three Dimplex
   `UserService` actions.
5. Verify the HA ESPHome config entry is enabled and loaded.
6. Verify HA exposes the three proxy action services.
7. Start pairing from HA; enter the PIN only when the heater displays it.
8. Confirm service discovery and one successful HA snapshot before considering
   the heater commissioned.
9. Restart HA and confirm bonded reconnect without a new PIN.
10. Keep the proxy's BLE slot usage in mind when adding other BLE integrations.

## Known limitations and open verification

- The IDF 5.5.5 workaround is retained pending a verified upstream replacement.
- The three-slot limit is the tested ESPHome configuration, not a guarantee that
  every future ESPHome release keeps the same limit.
- A successful short soak proves the tested route and bonds work; it does not
  prove months of uninterrupted operation. Long-duration monitoring remains a
  separate reliability exercise.
- The living-room proxy is commissioned and ready, but a new living-room heater
  has not been paired merely by flashing the proxy.

## References

- [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/)
- [ESPHome issue 19453](https://github.com/esphome/ESPHome/issues/19453)
- [ESPHome issue 19858](https://github.com/esphome/ESPHome/issues/19858)
- [ESPHome issue 22859](https://github.com/esphome/ESPHome/issues/22859)
- [Dimplex QRAD Smart Bridge research](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/research.md)
- [Dimplex QRAD Smart Bridge backlog](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/backlog.md)
- [Sunhouse/Dimplex ESPHome project](https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant)
- [Dimplex Quantum Storage Heater Dump](https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump)
- [dimplex-controller-py](https://github.com/KRoperUK/dimplex-controller-py)
