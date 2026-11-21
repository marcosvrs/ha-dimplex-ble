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

### Integration module ownership

- `route_policy.py` owns normalized proxy-route ranking, capacity eligibility,
  assignment load, RSSI, and active-route affinity. Pairing-time selection and
  runtime reconnects use the same policy.
- `esphome_proxy.py` is the ESPHome adapter. It owns config-entry lookup, native
  actions, encryption checks, and the passkey relay.
- `commissioning.py` owns one pairing attempt's proxy lock, relay arm, bounded
  wait, cancellation, and cleanup. The config flow owns only presentation and
  bounded retry scheduling.
- `client.py` owns the trusted heater session and protocol reads/writes; it
  consumes route facts and does not own ESPHome action semantics.

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

### Deployment invariant

Both production proxies must resolve the framework pin to ESP-IDF 5.5.5. On
2026-11-21, the main-bedroom YAML had drifted back to the package default
(5.5.6) while the living-room YAML still pinned 5.5.5. This was observable with
`esphome config`, before firmware upload. Restore the explicit pin before
building the main proxy; otherwise the bonded-reconnect regression workaround
is not active.

The passkey action address must be the normalized twelve-hex-digit Bluetooth
address (for example `c4d8d56f6532`), not a colon-separated address. The HA
integration normalizes it before calling ESPHome. Direct diagnostic scripts
must do the same or the relay rejects the action as invalid and the subsequent
authentication attempt times out.

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

### Current-session recovery record

The current-session AM2086 investigation exposed a documentation gap. The
operator reported that an earlier AM8224/main-bedroom recovery required
clearing proxy NVS/BLE state. The repository previously recorded only the
effect and destructive consequences, not that operational history or its exact
command.

The live evidence did not justify repeating a full NVS erase on the
living-room proxy before the operator explicitly authorized destructive
recovery. Its encrypted API was healthy before the test and a targeted
`bluetooth_device_clear_cache(0xc4d8d569c086)` request timed out because the
proxy had no GATT route to AM2086. A non-destructive safe-mode reboot then
completed: the proxy returned online, HA kept both working heater bonds alive
by moving AM8224 to the main-bedroom proxy, the living proxy reported three
of three BLE slots free, and AM2086 remained absent from all three HA scanner
sources.

After explicit authorization, the living-room proxy's HA `Factory reset`
button was tested while it had zero active Dimplex allocations. ESPHome's
factory-reset implementation calls `global_preferences->reset()`, which
invokes `nvs_flash_erase()` for the NVS partition before reboot. The proxy
returned online with its encrypted API and Dimplex pairing services intact.
The main-bedroom proxy retained both AM6532 and AM8224 allocations and
continued successful coordinator updates. AM2086 still did not appear in any
HA scanner source after the NVS erase. This rules out stale living-proxy NVS
or a living-proxy BLE cache as the cause of the AM2086 discovery failure.
The direct connection test was then repeated with the required active
`subscribe_bluetooth_le_advertisements()` subscription, for twenty seconds
before the GATT request and throughout the request. It received zero AM2086
advertisements and the address-scoped connection still timed out. The proxy
remained healthy afterward with three of three BLE slots free.

An independent CoreBluetooth scan from the MacBook was also run for thirty
seconds. It observed 29 other BLE peripherals, including the iPhone and
nearby accessories, but no `AM2086<Dimplex>` or other Dimplex advertisement.
This is independent confirmation that the heater was not advertising in the
RF environment during the test; it is not a proxy scanner failure.
An additional unfiltered 35-second CoreBluetooth scan was run after the
targeted scan. It reported many connectable peripherals and manufacturer
payloads, but no `AM2086`, `Dimplex`, or matching heater advertisement. This
rules out the targeted name/address filter as the reason the Mac test missed
the heater.
The upstream Q-Rad bridge research adds an important discriminator: on a
Q-Rad with Bluetooth enabled, advertising was observed indefinitely after
disconnect, and a wall power cycle restored advertising within one minute.
The same research records one-central-at-a-time behavior. Therefore a silent
AM2086 can mean either that another central (for example ConfigR or a phone)
still owns the link, or that AM2086's radio/firmware is stuck; it is not normal
post-pair behavior established by the reference implementation.



As a control, Home Assistant's normal scanner registry simultaneously contained
`AM6532<Dimplex>` from the main-bedroom proxy at RSSI `-64 dBm` and a separate
nearby `AM3314<Dimplex>` at `-96 dBm`. AM8224 was active as a GATT allocation
on the main-bedroom proxy rather than an advertisement record. Thus the
scanner/proxy path was demonstrably seeing Dimplex peripherals while AM2086
was absent.

The AM2086 entry was not created by a partial discovery. Its stored creation
time is `2026-11-20T22:41:01Z`, and the integration's `async_pair()` creates
an entry only after a PIN pairing plus a fresh authenticated reconnect both
return the heater identity. The device registry received the PLX050E/EP4213
metadata at `22:41:24Z`. The raw pairing log was rotated away, so this is
strong storage/code evidence of a previously successful bond, not a retained
pairing-success log. The later absence is therefore a bond/runtime-loss
event, not proof that AM2086 has never paired.
The recorder timeline makes the failure order explicit: AM2086 was last
available at `2026-11-20T22:48:09Z`, while AM6532 remained available until
`22:49:35Z` and AM8224 until `22:57:22Z`. AM2086 therefore failed before the
later shared HA/proxy outage; it was not merely collateral damage from that
outage.


The observed timeline is consistent with a post-pair runtime/bond failure:
the living proxy firmware compiled at `22:13:27Z`, the AM2086 entry was
created at `22:41:01Z`, Home Assistant restarted at `02:47:27Z`, and the
first retained failure was immediately after that restart because no current
AM2086 advertisement route existed. The later safe-mode reboot and full living
proxy NVS erase did not restore advertisements. No evidence implicates the
main-bedroom proxy or either working heater.

The next recovery operation is physical and cannot be performed by HA:
terminate any ConfigR/phone Bluetooth connection to AM2086, leave its
Bluetooth setting enabled, power-cycle the radiator, wait up to one minute,
and verify that `AM2086<Dimplex>` is visible before starting HA pairing. The
Q-Rad research records advertising returning within a minute after a wall
power cycle, and ConfigR's recovered lifecycle requires the current central to
disconnect before another connection. If AM2086 remains absent from both
proxy and Mac scans after that operation, the remaining fault is inside the
heater's Bluetooth/radio state or hardware and cannot be repaired through the
proxy.

The earlier AM8224 erase command is not recoverable from the repository; it
must not be reconstructed by guess. The verified current recovery operation
was the living proxy's HA `Factory reset` action, and it is destructive:
affected heater bonds require PIN pairing again.


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

### 2026-11-21 proxy API recovery

After the Home Assistant restart, both ESPHome API sessions failed: the
main-bedroom proxy returned `EOF received`, and the living-room proxy dropped
the connection immediately after the encrypted hello. HA consequently marked
AM6532, AM8224, and AM2086 unavailable. The configured Noise-PSK fingerprints
matched the current proxy YAML for both devices, so the failure did not prove a
stored-key mismatch; TCP port reachability was also insufficient evidence.

The current YAML was compiled and uploaded over encrypted OTA to both proxies.
OTA succeeded without erasing the NVS partition. The two already-bonded heaters
then produced successful coordinator updates again through the main-bedroom
and living-room routes. AM2086 still had no advertisement route and remained
unavailable. This separates the transient proxy API failure from the AM2086
fault: repairing the proxies restores existing bonds, but cannot make a
non-advertising heater discoverable.

The pre-factory living-proxy NVS backup contained the AM2086 address inside the
Bluetooth GATT-cache region. It was restored through the proxy's USB serial
connection as a controlled experiment, followed by an HA config-entry reload.
AM2086 still had no route and remained unavailable. The proxy's current NVS
backup was then restored, so the experiment did not leave the living proxy in
the older cache state. A cache entry is therefore not sufficient to wake or
connect to this heater.

HA contains an `innr SP 120` socket in the Single Bedroom area, but it was
unavailable. Both normal switch service calls and a direct Zigbee On/Off
command failed because the device had no route; reloading ZHA did not restore
it. It cannot be used as a remote AM2086 power-cycle path. A fresh Mac
CoreBluetooth scan after these experiments still found no Dimplex
advertisement, while macOS listed AM2086 only as a cached, disconnected device.

The official PLXE Series C manual adds a device-side hypothesis that cannot be
tested remotely: `Settings -> Comms` controls wireless-connectivity features,
and the manual lists Bluetooth as being for software updates. It also defines
fault code `60` as a wireless-communication-module error and `61` as a hub
connection error. The same manual identifies `PLX050E` as the model family and
documents a device-side factory reset (`Advance` + `Menu` for five seconds,
then `Factory Reset` -> `Yes`). This is not proof that AM2086 has a disabled
setting or a hardware fault, but it is the only remaining device-side recovery
path after the remote BLE and NVS tests. Source: Dimplex, *PLXE Panel Heater
Models ... Series C, Issue 7*, pp. 6, 11, 23, 25-26.

The official ConfigR product page lists PLXENC as its panel-heater product and
requires checking the compatibility list before connection. The AM2086,
AM6532, and AM8224 metadata all identify them as `PLX050E;C;`, not PLXENC.
The PLXE Series C manual describes Bluetooth as intended for software updates,
while the older PLX050E manual says the Bluetooth connection is “currently
without function.” This means ConfigR/PLXENC pairing behavior must not be
treated as an official PLX050E contract. It does not explain why AM6532 and
AM8224 currently work with the reverse-engineered protocol, but it does make
AM2086's device-side wireless state or module variant the remaining
model-specific variable.

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
