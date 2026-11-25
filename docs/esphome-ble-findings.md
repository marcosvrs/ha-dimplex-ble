# ESPHome and BLE findings for Dimplex panel heaters

**Status:** engineering record, rewritten 2026-11-24 from the adversarial BLE
review. Source of truth: `docs/investigation-2026-11-24/ledger.md` and
`design-addendum.md` (times below are UTC, 2026-11-24 unless stated). Earlier
revisions of this file described a custom C++ relay component
(`dimplex_passkey_proxy`), MITM prepare/request actions, a runtime PIN relay and a
required ESP-IDF 5.5.5 pin. All of that was removed; see "Rejected" below.

## Scope and terminology

- **Panel heater:** a Dimplex PLXE/PLX050E Series C product advertising
  `AM####<Dimplex>`. Its radio is an Espressif ESP32 (GATT `Esp32OtaEnable`).
- **Proxy:** an ESP32-S3 running stock ESPHome (`bluetooth_proxy`,
  `esp32_ble_tracker`) plus the `firmware/dimplex_pin_responder.yaml` package.
- **Responder:** one dormant `ble_client` in that package that answers the
  heater's passkey request.
- Bluetooth is always on at the heater; there is no heater-side pairing mode.
  Pairing starts when the central connects; the heater then shows a static
  six-digit PIN. A missing PIN display means the central did not reach the
  passkey request (see `AGENTS.md`).

## Architecture

### Firmware (stock ESPHome, no external component, no C++)

`firmware/dimplex_pin_responder.yaml` (spec: design-addendum section A):

- RAM-only globals `dimplex_pin` (1000000 = disarmed) and `dimplex_pin_set_ms`.
- One generic `ble_client` (`dimplex_pin_client`, `auto_connect: false`)
  retargeted at runtime with `set_address()`.
- `on_passkey_request`: fires event `esphome.dimplex_passkey_request` (address
  only, no PIN), waits up to 25 s (below the 30 s SMP timer) for an armed PIN,
  then `ble_client.passkey_reply`. No PIN in log text.
- API actions `dimplex_pin_arm(address, passkey)` (passkey 1000000 = "target
  only, PIN follows") and `dimplex_pin_clear()`.
- `esp32_ble`: `io_capability: keyboard_only`, `auth_req_mode: bond_mitm` (or
  `sc_mitm_bond`), `max_connections: 4` (3 proxy slots + 1 responder; total <= 9).
- ESPHome >= 2026.11.0 with the default framework. No IDF pin is needed (H9).
  No other BLE customization: `CONFIG_BT_BLE_RPA_SUPPORTED` was dropped on
  2026-11-25 (the heaters use public addresses, `address_type` 0, so the proxy
  never resolves private addresses).

### Home Assistant side

- Connections use the pinned proxy route (`scanner.connector.client`), because
  habluetooth will not pin a source (habluetooth#628, rejected). Connect only
  when the pinned proxy currently sees the heater.
- Link sequence: `dimplex_pin_arm` (target, stored PIN) through the proxy's
  native API client (never `hass.services`, so the PIN never enters the event
  bus) -> connect -> shielded native `pair()` -> GATT. The arm must precede the
  connect (RC-5). One per-proxy security lock is held across connect+pair; the
  responder is cleared in `finally`.
- `pair()` and `connect()` run as their own tasks awaited to completion even
  when HA cancels the caller; the link is closed only after `pair()` resolved
  (RC-2, RC-3). ATT 0x05/0x0F after a successful `pair()` is a transient
  failure, not a reauth. Reauth only for a rejected stored PIN (reason 81).
- Commissioning: flow shows progress, asks for the PIN only when the
  `esphome.dimplex_passkey_request` event arrives (a bonded heater finishes
  without a PIN prompt). Wrong PIN = SMP reason 81, pairing restarts at once.
  PIN is static per heater and stored in the config entry for automatic
  re-pair after a bond loss; it is redacted in diagnostics.
- Reconnect is event-driven off every advertisement
  (`bluetooth.async_register_advertisement_callback`; the regular callbacks skip
  identical payloads, habluetooth 7.1.2 manager.py ~1316-1350).
- Heater settle rule: `HEATER_SETTLE_S = 10` s between a link-down and the next
  connect to the same heater, kept in a shared per-address registry. Basis: a
  reconnect 20-23 ms after a clean disconnect is terminated within ~7 ms
  (rsn 0x13) or answered "Invalid handle" (hardware 17:58:41, 18:24:23), and
  ConfigR help text says to wait ~10 s for the product "to go to sleep".
- Connection mode option: persistent link (default, one proxy slot permanently
  per heater) or per-poll connect/disconnect (60 s interval; first deployed on
  AM8224 17:58). In per-poll mode slow reads (Sound) must reuse the poll
  connection.
- Stall repair: after an abnormal link drop and 60 s without an advertisement
  the integration raises a "heater stopped responding" repair (needs a wall
  power cycle).
- Observability: DEBUG lines with the address on every line (CONNECT/PAIR
  request+result, GATT result, DISCONNECT, ROUTE_NOT_SEEN, RESPONDER_ARM/CLEAR).
  Set `aioesphomeapi.connection: info` on the HA host, because DEBUG logs the
  `ExecuteService` arguments (the PIN). Pre-15:00 logs from the old relay may
  contain PINs.

## Verified root causes

### RC-1: proxy API rejected HA (12:34-13:45)
ESPHome 2026.11.0 limits API clients to 5 (`api_server.cpp:323`); at the limit the
proxy accepts TCP then closes, so HA saw `EncryptionHelloAPIError`. 5 sockets
were held by 17 orphaned `esphome run/logs` processes in the esphome container.
SIGTERM on only those processes restored the proxy within ~60 s. Key hashes
HA==YAML ruled out a key mismatch. Not a code defect.

### RC-2: failed AUTH_CMPL deletes the bond (IDF 5.5.5 source)
`bta_dm_act.c:5021-5038` (RAM) and `btc_dm.c:286-303` (NVS): any failed
AUTH_CMPL except reason 5 removes the bond; no Kconfig to disable (esp-idf#19303).
Reason 102 = SMP_CONN_TOUT, 99 = SMP_RSP_TIMEOUT, 97 = SMP_ENC_FAIL. The old
flow (fire-and-forget encryption, immediate read, disconnect on 0x05/0x0F,
30 s pairing waits cut by HA disconnect) produced repeated 102s. Living-room
showed `success=0 reason=102 bonds=0` at 13:49 with no reboot (bonds deleted at
runtime). Design rule: never abort an in-progress security procedure; await
`pair()` before GATT. Hardware reproduction by reverting was not done.

### RC-3: stale "closing" GATT record after a cancelled connect (AM2086, 15:00-15:15)
Shutting-down HA cancelled an in-flight connect as the link formed:
`L2CA_CancelBleConnectReq - no connection pending` leaves the tcb "closing"
(`gatt_main.c:276-277`, `l2c_ble.c:98-101`); the internal retry keeps the record
(`l2c_link.c:532-544`, ESPHome CONFIG count 10), so every `gattc_open` fails in
<100 ms with status 133 (`gatt_main.c:400-402`, `bta_gattc_act.c:554-559`).
Rebooting only the living-room proxy at 15:12 fixed it (heater advertised again
15:12:54, reconnected 15:15:06), so the state was proxy-side. Rejected
workaround: `CONFIG_BT_GATTC_CONNECT_RETRY_COUNT=0` (the same retry recovered a
normal 0x3e for AM1382 at 15:13:00). Mitigation deployed 15:18: connect runs as
its own bounded (15 s) task even if HA cancels. Residual: HA process death or
API drop mid-connect. Draft upstream report: `docs/upstream-issue-drafts.md`.

### RC-4: third uncached link refused by the controller (16:10, 17:08)
`BT_HCI opcode=0x200d status 0x0d Rejected: Resources` 54 ms after "Connecting v3
without cache" (fixed 7.5 ms interval; `l2c_ble.c:1058-1076`), then status 133.
esphome#22859 (C3/S3 controller, won't-fix 2026-11-18): uncached third link
refused 19/24, cached (MEDIUM 8.75-11.25 ms) never refused. Raising
`max_connections` does not help. After an HA restart the GATT cache is empty so
the first reconnect is uncached: 4/4 refusals for AM2086 as the third heater on
main-bedroom (17:08-17:10). The refusal is local (no SMP, no bond loss). With one
heater removed the connect was accepted (16:13:08).

### RC-5: responder armed after the heater asked for the PIN (16:13)
Link up 16:13:08.5, the heater's Security Request started SMP at once, PASSKEY_REQ
arrived with the responder untargeted, PIN displayed, armed 16:13:10.4 after GATT
discovery, `smp_rsp_timeout` reason 99 at 16:13:38.9. Fix (deployed 16:22): arm
before connect, hold the lock across connect+pair; a contract test asserts the
order action -> connect -> pair.

### Heater settle time
See the settle rule above (17:58:41, 18:24:23 hardware, ConfigR guidance).

### Heater BLE stall (AM8224 14:08 and 17:19; AM2086 17:34)
After an abnormal link loss (AM8224 17:19:19: read of handle 143 hung ~10 s,
error 133, HCI 0x08 supervision timeout) the heater stops advertising until a
wall power cycle. Isolation (AGENTS.md rules): HA kept polling; the proxy held no
link (allocations 1/3; Jarvis hci0 had none); a USB test ESP32 and a phone
(nRF Connect) next to AM8224 heard nothing; both proxies were rebooted 13:58/14:04
and the heater stayed silent until the 14:08 power cycle (so not RC-3); the
heater re-appeared and reconnected via its intact bond after power cycling
(17:35:04). ~50 clean disconnects always re-advertised; 2/2 abnormal losses did
not. No vendor guidance found. Mitigations: stall repair, per-poll option,
settle rule.

### H14: heater keeps bonds with several centrals
AM2086 moved main-bedroom -> living-room at 17:13: `pair()` ok in 0.01 s, no
PIN; the living-room LTK from 15:21 stayed valid after bonding with main-bedroom
(16:52). The heater bond limit is unknown (ConfigR notes the heater evicts bonds
at its limit; do not create bonds repeatedly).

### H9: ESP-IDF 5.5.6 verified (18:15-18:25)
ESPHome 2026.11 default IDF 5.5.6 links `--wrap=smp_check_auth_req` and
`--wrap=btm_sec_clear_ble_keys` (esp32_ble `bluedroid_bond_keys.c`). After an OTA
and 3 entry reloads on living-room: 9/9 LTK re-encryptions, 0 passkey requests,
0 fresh pairings, 0 bond deletions. The 5.5.5 pin is no longer needed
(esp-idf#19286/#19303, fix PR #19322 unmerged; ESPHome PR #22559).

## Experiments that settled the design

| When | Result |
|---|---|
| 13:49 | Native `pair()` on a bonded heater re-encrypts in ~0.1 s, no PIN. Living-room `bonds=0`. H-RACE rejected. |
| 13:58 (E1) | Stock `ble_client` `on_passkey_request` answered a proxy-initiated pairing (`auth success mode=13`). |
| 14:00 (E2) | Bond reuse after HA restart without PIN (0.357 s). Bond survived ESP reboot + OTA (14:06). |
| 15:20 (T-REPAIR) | Bond removed with native UNPAIR; HA re-paired with the stored PIN in 2.07 s, outage ~22 s. Marker `BT_SMP: FOR LE SC LTK IS USED INSTEAD OF STK` = fresh pairing. |
| 16:35-16:53 (R-PIN) | User-typed PIN commissioning passed: wrong PIN -> 81, slow typist -> 30 s timeout then fresh pairing with PIN armed before connect. |
| 17:50 | In User Timer a setpoint is accepted (target reads back); Frost Protect + setpoint switches the heater to Manual. |

Interim baseline 14:09-14:27: 343/343 polls succeeded on four heaters through
two stock proxies (not like-for-like with the 963 failed polls of 12:20-13:36;
sustained soak still required).

## Rejected hypotheses and approaches

- H-RACE (read before encryption completes) rejected for living-room; the real
  harm was bond deletion (RC-2).
- Custom C++ relay, `prepare`/`request` MITM actions, `configure_security_()`
  (redundant with `esp32_ble` YAML settings) and runtime PIN relay: removed.
- Roaming/route ranking/cached-route blocks/backoff ladders: removed; the heater
  is bound to the proxy holding its bond; a move uses a temporary pinned link
  and never unpairs the old proxy.
- `CONFIG_BT_GATTC_CONNECT_RETRY_COUNT=0` (RC-3), raising `max_connections`
  (RC-4), proxy reboot as a reliability fix, "heater only keeps one bond" (H14),
  USB test ESP32 as the stall holder: all ruled out.
- ESPHome has no native passkey path (feature-requests#2013, bleak#1864); hence
  the stock-YAML responder.

## Known limits

- <= 2 heaters per ESP32-S3 proxy for reliable recovery after HA restarts (RC-4;
  the proxy picker warns). Current layout 2+2: main-bedroom AM6532+AM8224,
  living-room AM1382+AM2086. Persistent links use a proxy slot permanently.
- 3 BLE slots per proxy, shared with every other BLE integration; the responder
  is an extra compile-time slot (max 9).
- The PIN window is 30 s (SMP response timer); the responder waits 25 s.
  A typed PIN after a timeout starts a fresh pairing.
- A heater BLE stall needs a wall power cycle.
- Proxy API clients max 5 (RC-1).
- Other discovered heaters (AM5832, AM8922) are unconfigured.

## Operational rules

1. Log captures on the proxy must be bounded (`timeout`) and kept on the host
   volume, not inside the ESPHome container (a container recreation lost the
   17:19 stall log). Host captures: `~/dimplex-logs/`.
2. Never run `esphome run` or `esphome logs` unbounded; `esphome run` always
   with `--no-logs` (RC-1).
3. Never erase NVS or clear bonds routinely; a re-pair is automatic after a lost
   bond but a failed pairing can delete a good bond (RC-2).
4. Use ESPHome >= 2026.11.0 default framework; check with `esphome config`.
5. Validate and compile before flashing; OTA only when the API is reachable.
6. Do not reconnect to the same heater within 10 s of a disconnect.
7. Destructive experiments only on the spare USB ESP32, never production.
8. Recommendation: rotate both proxies' API encryption keys (old keys appeared in
   deleted files and earlier logs).

## References

- [ESPHome Bluetooth Proxy](https://esphome.io/components/bluetooth_proxy/)
- [ESPHome issue 19453](https://github.com/esphome/ESPHome/issues/19453)
- [ESPHome issue 19858](https://github.com/esphome/ESPHome/issues/19858)
- [ESPHome issue 22859](https://github.com/esphome/ESPHome/issues/22859)
- [esp-idf issue 19303](https://github.com/espressif/esp-idf/issues/19303)
- [Dimplex QRAD Smart Bridge research](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/research.md)
- [Dimplex QRAD Smart Bridge backlog](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/backlog.md)
- [Sunhouse/Dimplex ESPHome project](https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant)
- [Dimplex Quantum Storage Heater Dump](https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump)
- [dimplex-controller-py](https://github.com/KRoperUK/dimplex-controller-py)
