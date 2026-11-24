# Upstream issue drafts (not filed)

Ready-to-file drafts from the 2026-11-24 investigation. Source of truth:
`docs/investigation-2026-11-24/ledger.md` (cited as "ledger L<line>"). Raw logs
and the per-issue analysis files (`am2086-133.md`, `hci-0x0d.md`) live in the
session scratchpad, not in the repo; attach sanitized excerpts when filing and
redact device MACs and any PIN. Verify each claim against the current upstream
`main` before filing, and search for duplicates first.

Common environment: ESPHome 2026.11.0, ESP32-S3 (two proxies), Home Assistant
2026.11.3, aioesphomeapi 46.25.0, bleak-esphome 4.0.0; peripheral: Dimplex PLXE
panel heater (Espressif ESP32 radio, static-PIN LE Secure Connections bonding).
ESP-IDF 5.5.5 for (a) at the time of capture (15:00-15:15 UTC); 5.5.6 with
ESPHome's bond-key wrap was verified later (ledger L256-258).

---

## (a) espressif/esp-idf: cancelling an LE connect while the link forms leaves the GATT tcb "closing"; every later `gattc_open` returns 133 until reboot

**Title:** Bluedroid: `L2CA_CancelBleConnectReq` racing link establishment leaves GATT tcb in "closing"; all later GATTC opens fail with status 133 until reboot

**Environment:** ESP-IDF v5.5.5 (also check master), ESP32-S3, Bluedroid, ESPHome
2026.11.0 `bluetooth_proxy` (CONFIG `BT_GATTC_CONNECT_RETRY_COUNT` = 10).

**Minimal reproduction**
1. Central: `esp_ble_gattc_open()` to a peripheral that is advertising.
2. Cancel the open (`esp_ble_gattc_close` / API client disconnect) at the moment
   the link is being established (we hit it when the host application
   was shut down mid-connect, 2026-11-24 15:00:59-15:01:00 UTC).
3. Log shows `L2CA_CancelBleConnectReq - no connection pending`.
4. Every subsequent `esp_ble_gattc_open()` to that peer fails within <100 ms
   with `status=133` (reason 0x85), indefinitely. Peripheral stops advertising
   (probably because the orphan link is held by the central; inference).
5. Rebooting only the central restores everything (peripheral advertised again
   at 15:12:54, reconnected 15:15:06).

**Evidence (ledger L148-154)**
- A cancel after the link already formed leaves the tcb marked "closing"
  (`gatt_main.c:276-277`, `l2c_ble.c:98-101`).
- The internal connect retry keeps the record (`l2c_link.c:532-544`; ESPHome sets
  retry count 10).
- Each later open then fails fast (`gatt_main.c:400-402`,
  `bta_gattc_act.c:554-559`).
- Setting `CONFIG_BT_GATTC_CONNECT_RETRY_COUNT=0` is not a fix: the same retry
  recovered a normal 0x3e connect failure for another peer at 15:13:00.
- Not explained by the peripheral: it recovered immediately after the central
  reboot, and other peripherals on the same central were unaffected.

**Expected:** a cancelled connect cleans up the tcb whichever side of the race
the link lands on, and later opens to the same address proceed normally.
**Actual:** tcb stays "closing", 100% of later opens fail with 133 until reboot.

**Suggested fix:** in `gatt_le_connect_cback` (`gatt_main.c`), when a link-up
arrives for a tcb in the "closing" state, complete the close (disconnect the new
link and clear the tcb) instead of ignoring it; alternatively make
`L2CA_CancelBleConnectReq` report "no connection pending" so
`GATT_CancelConnect` can disconnect an already-formed link. Workaround we ship:
never cancel an in-flight connect (run it as its own task to its natural end).

---

## (b) esphome/esphome: `bluetooth_proxy` "Conn param update failed, status=19" (HCI 0x2013 Invalid Param) on every connection; links stay at 7.5 ms

**Title:** bluetooth_proxy: connection parameter update rejected on every connection (`status=19`, HCI 0x2013), link keeps the 7.5 ms FAST interval

**Environment:** ESPHome 2026.11.0, ESP32-S3, `bluetooth_proxy` with active
connections, BLE peripheral = Dimplex PLXE heater. Related: esphome/esphome#22859
(C3/S3 controller refuses a third uncached link at the fixed 7.5 ms interval,
won't-fix 2026-11-18).

**Minimal reproduction**
1. Flash a stock `bluetooth_proxy` with `active: true`.
2. Connect to any peripheral through the proxy API without a cached GATT
   service table ("Connecting v3 without cache").
3. Observe in the proxy log the `Conn param update failed, status=19` line
   (HCI error 0x13 / 0x2013 reported for the parameter update) after each connect, and
   that the link interval remains 7.5 ms.

**Evidence (proxy logs, ESPHome 2026.11.0, 2026-11-24 UTC):** the failure
appears on every proxy and heater; 44 occurrences in the 17:35–18:30 host
captures alone. Verbatim pairs (the HCI rejection and ESPHome's report):

```text
[17:48:20.168][D][esp-idf:000][BTU_TASK]: W (6525329) BT_HCI: opcode=0x2013, status= 12: Invalid Param
[17:48:20.325][W][esp32_ble:742][BTC_TASK]: [C4:D8:D5:69:C0:86] Conn param update failed, status=19
[17:56:49.794][D][esp-idf:000][BTU_TASK]: W (7034964) BT_HCI: opcode=0x2013, status= 12: Invalid Param
[17:56:49.794][W][esp32_ble:742][BTC_TASK]: [24:4C:AB:07:BD:82] Conn param update failed, status=19
```

Code path (ESPHome 2026.11.0 compiled sources): `tracker_connect_()` sets FAST
(fixed 7.5 ms) as preferred parameters for `V3_WITHOUT_CACHE` before
`gattc_open` (`bluetooth_connection_bluedroid.cpp:155-165`); after service
discovery `handle_search_cmpl_` sends `esp_ble_gap_update_conn_params` with
MEDIUM (`:407`, logged "medium conn params") — that is the HCI 0x2013 the local
controller rejects; GAP status 19 = `ESP_BT_STATUS_ERR_ILLEGAL_PARAMETER_FMT`
(`esp_bt_defs.h:123`). The same rejection occurs on the `update_connection_params`
path ("custom conn params", `:341-343`, `bluetooth_connection_hub.cpp:140`).
The related, ledger-verified facts: uncached connects use ESPHome's FAST fixed
7.5 ms interval, cached connects use MEDIUM 8.75-11.25 ms; with two links held
a third uncached connect is rejected locally with
`BT_HCI opcode=0x200d status 0x0d Rejected: Resources` 54 ms after "Connecting"
(`l2c_ble.c:1058-1076`), 4/4 on main-bedroom (ledger L181-205). Cached never
refused (esphome#22859).

**Expected:** the connection parameter update is accepted (or not attempted when
it can only fail), so the link can move off 7.5 ms and the controller needs
fewer resources per link.
**Actual:** the update is rejected on every connection and links stay at 7.5 ms,
which is the interval the controller refuses at the third link.

**Suggested fix:** check which parameters `bluetooth_proxy` sends (interval,
latency, supervision timeout) for validity against the Core spec limits
(supervision timeout > (1 + latency) * interval * 2) and send valid values or
skip the update when already requested; consider using the MEDIUM interval for
uncached connects (the cached path never fails). Mention #22859 in the report
and say whether this is the same root cause or an additional contributor.

---

## (c) esphome/esphome feature request: passkey request/reply over the native API for `bluetooth_proxy`

**Title:** Feature request: expose BLE passkey request and reply through the native API (bluetooth_proxy), so a remote client can pair devices that display a PIN

**Context:** esphome/feature-requests#2013; aioesphomeapi 46.25.0 and
bleak-esphome 4.0.0 have no passkey message in `api.proto` (ledger L48); `PAIR`
only calls `esp_ble_set_encryption(ESP_BLE_SEC_ENCRYPT)` and has no passkey
handler, so a PIN-display peripheral hangs ~30 s then fails (reason 99/102).
bleak#1864 (passkey agent) is BlueZ-only and stalled (ledger L37).

**Use case:** Dimplex panel heaters (static six-digit PIN shown on the display,
LE Secure Connections, MITM, bonding) cannot be commissioned through a stock
proxy from Home Assistant without out-of-band YAML.

**Current workaround (stock YAML, no external component)** in
`firmware/dimplex_pin_responder.yaml`: `esp32_ble` with
`io_capability: keyboard_only`; one dormant `ble_client` (`auto_connect: false`,
retargeted with `set_address()`); `on_passkey_request` fires a
`homeassistant.event`, waits for a RAM-only global PIN, then
`ble_client.passkey_reply`; API actions `dimplex_pin_arm(address, passkey)` and
`dimplex_pin_clear()`. Verified on hardware: the `ble_client` answers a
passkey requested during a proxy-initiated `PAIR` (13:58), pairings complete
(`auth success mode=13`), user-typed PIN commissioning works (16:35-16:53), a
removed bond is re-created automatically (15:20). Costs: one extra compile-time
connection slot (total `max_connections` <= 9), a YAML package on every proxy,
and the integration must call user actions on the proxy's API client.

**Proposed API:** (1) device -> client message `BluetoothDevicePasskeyRequest {
address }` (and optionally a numeric-comparison variant); (2) client -> device
`BluetoothDevicePasskeyReply { address, passkey, accept }`, valid only for the
pairing the same API client started; (3) feature flag in
`BluetoothProxyFeatureFlags`; (4) aioesphomeapi/bleak-esphome plumbing so bleak's
pairing agent callbacks can answer it. Security: never log the PIN; time-box the
pending request (SMP response timer is 30 s).

**Alternatives considered:** a custom C++ relay component (we had one; removed
as unmaintainable across ESPHome releases, e.g. esphome#18198 rewrote the ESP32
proxy), a firmware-baked PIN (fails first-commissioning UX because the heater
shows the PIN only once pairing starts).
