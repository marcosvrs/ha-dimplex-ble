# Findings ledger — adversarial BLE review (started 2026-11-24)

Classes: VERIFIED (direct evidence, cite) · EXPERIMENT (controlled result) · INFERENCE · HYPOTHESIS · OPEN.
Raw artifacts (logs, dumps) live outside the repo in the session scratchpad `artifacts/`; never paste secrets here.

## Baseline facts carried over (from docs/esphome-ble-findings.md — re-verify before relying on them)
- Two heaters (AM6532, AM8224) have worked concurrently through one proxy (prior session record).
- Custom firmware: `dimplex_passkey_proxy` exposes 5 API actions: arm / set passkey / cancel / prepare MITM / request MITM.
- `configure_security_()` calls esp_ble_gap_set_security_param (global) on every runtime connect via prepare action — HYPOTHESIS: redundant with `esp32_ble.io_capability/auth_req_mode`.
- transport.py bypasses HA's bluetooth client wrapper and uses `scanner.connector.client` directly, pinned to a proxy source; adds per-proxy asyncio lock and a 30 s cached-route block.

## Phase 1 (in progress)

### Research rule (user directive, 2026-11-24)
Every uncertain point must be researched against official docs, upstream issues/PRs and community reports before a conclusion or change. Each claim needs a citation; unsourced claims stay HYPOTHESIS.
Research streams: installed-source analysis (upstream-capabilities.md) · docs/issues/community (community-research.md).

## Phase 1 results
### Environment (env-inventory.md) — VERIFIED 2026-11-24
- HA 2026.11.3 / habluetooth 7.1.2 / bleak 3.0.2 / bleak-retry-connector 4.7.1 / bleak-esphome 4.0.0 / aioesphomeapi 46.25.0; ESPHome 2026.11.0. Clocks synced (NTP).
- HA custom component is bind-mounted from ~/.docker/openhands/workspace/ha-dimplex-ble and matches the local working tree.
- Proxy firmware is NOT built from the repo component: YAMLs reference `pairing-test-component` (adds clear_bond/clear_all_bonds, `[DEBUG-DIMPLEX-PIN-20261117]` prefix). Main-bedroom flashed source UNKNOWN. bluetooth_proxy settings come from an unpinned remote package `@main` → uncontrolled variable.
- 4 heaters configured (AM6532, AM8224, AM2086, AM1382), 2 proxies (main bedroom 14:C1:9F:21:0E:6A, living room 14:C1:9F:22:4C:DA). Latest log: AM6532 poll OK, other three failing.
- Debug loggers missing: habluetooth, bleak_retry_connector, homeassistant.components.bluetooth wrappers OK; ESPHome device logs not persisted.
- RISK: deployed firmware debug prefix may log PIN-related text — check before sharing logs.

### Custom mechanisms (custom-mechanisms.md) — VERIFIED by code reading; "native equivalent" claims still HYPOTHESIS
- ~70 lifecycle mechanisms. protocol.py is transport-independent (good).
- Persistent link held between 10 s polls (session.py:150-224) → occupies a proxy slot per heater permanently. HYPOTHESIS: contributes to slot exhaustion with 4 heaters on 2 proxies (3 slots each).
- Non-pair connect bypasses establish_connection (transport.py:221-231).
- Coordinator has custom backoff (20–120 s), 30 s advert cooldown, auth ladder (retry, repair on 3rd, 300 s cooldown) vs native UpdateFailed/ConfigEntryAuthFailed/reauth.
- Defects found by reading (unverified at runtime): R-2 client leak if MITM action fails after connect (transport.py:232); R-3 pairing lock leak if task cancelled before start; R-1 _operation_task overwrite; R-4 address-less firmware cancel; write path skips auth classification; read path no timeout; repair blocks poll up to 180 s.
- Error classification by str() matching (errors.py:26-33, transport.py:279-280); wrong PIN labelled "auth required"; ATT codes lost.
- Complexity: lifecycle SLOC 1462 (tree) vs 1233 (HEAD); refactor relocated rather than removed complexity.

### Docs / issues / community (community-research.md) — cited; [M]=maintainer [U]=user [C]=code
- No native remote-passkey path in ESPHome API/bleak-esphome (feature-requests#2013 open; bleak#1864 stalled, BlueZ-only). Custom passkey relay = GENUINE upstream limitation (pending source confirmation).
- Community pattern for passkey via ESP: ble_client on_passkey_request + passkey_reply (HA eq3btsmart docs, philcluff Dimplex bridge). Bond store is global per ESP (keyed by peer) → HYPOTHESIS: ble_client-created bond reusable by bluetooth_proxy (needs experiment).
- habluetooth will NOT pin to a source (habluetooth#628, [M] rejected). Direct ESPHomeClient use for bond locality = justified deviation; alternative: only one proxy active per heater.
- ESP-IDF 5.5.6 bond-key-in-RAM deletion bug: esp-idf#19286/#19303, fix PR #19322 unmerged; ESPHome PR #22559 (2026.11.0) wraps for 5.5.6–6.0. 5.5.5 unaffected. Pin can likely be removed on 2026.11.0 (needs hardware test).
- !! esp-idf#19303: a FAILED pairing can auto-delete a good central bond by default. HYPOTHESIS H-BONDLOSS: spurious pair/repair attempts (e.g. timeout misclassified as auth, or MITM re-encrypt after connect) destroy valid bonds → cascade of "auth required". Must check logs for "Deleting unpaired device" / bond count drops.
- eq3-custom-fw#3 [U]: HA dropping link ~50 ms after first insufficient-auth status kills in-progress pairing (reason 102).
- ESPHome encryption is not auto-started on connect for bonded peers ([C]+[U]) → some explicit encryption request is needed; whether native `pair()` on bonded peer suffices is OPEN.
- esphome#18198 (2026.8.0) rewrote ESP32 proxy onto BluedroidGattClient — custom component hooks must be rechecked against 2026.11.0.
- Slot-leak fixes present in installed versions: bleak-esphome #422/#423/#425 (4.0.0), esphome#22077 (2026.11.0). Open: core#187087, bleak-esphome#438/#430.

### Installed-source capabilities (upstream-capabilities.md) — VERIFIED in code unless marked
- Q1 No passkey message in api.proto / aioesphomeapi / bleak-esphome (46.25.0 / 4.0.0 / ESPHome 2026.11.0). Corroborated by community research. → relay of PIN to ESP32 is a GENUINE limitation.
- Q2 PAIR = esp_ble_set_encryption(ESP_BLE_SEC_ENCRYPT); returns paired=true immediately if already paired. No passkey handler in stock proxy → ~30 s hang, error 99/102 (INFERENCE).
- Q3 esp32_ble applies io_capability/auth_req_mode/key sizes at BLE init; nothing resets them → firmware configure_security_() REDUNDANT when YAML has sc_mitm_bond (only diff: CSRK dropped from key mask). Proxies on bond_mitm differ → fix YAML instead.
- Q4 Proxy never encrypts by itself; native pair() on a bonded peer re-encrypts with stored LTK without PIN → request_mitm_encryption replaceable by native pair() (needs hardware confirmation of MITM level / no PIN prompt).
- Q5 Stock ble_client: on_passkey_request + passkey_reply(static), auto_connect:false; security events dispatched to all clients by address → a dormant ble_client can answer the passkey for a bluetooth_proxy PAIR (runtime UNTESTED). Cost: +1 compile-time slot per ble_client (max 9). Prior art: phurth/ha-onecontrol pairing_test.yml, ESPHome delta_wallbox.
- Q6 HA wrapper can't pin (corroborates habluetooth#628). Per-proxy connect serialization not provided upstream. establish_connection stale cleanup is BlueZ-only. _release_connection_no_wait redundant (disconnect already sent) — INFERENCE.
- Q7 Pair errors: aioesphomeapi returns int error (78 passkey fail, 80, 81, 88, 99, 102); bleak-esphome flattens to BleakError("Pairing failed due to error: N"); timeout is builtin TimeoutError → classify by type, not str.

## Candidate architecture A (to be tested, NOT decided)
Stock ESPHome firmware (no external component) + `esp32_ble: auth_req_mode sc_mitm_bond, io_capability keyboard_only` + one dormant `ble_client` (auto_connect:false) per heater with static `passkey_reply` from secrets. HA: native `connect(pair=True)` for commissioning, `pair()` after connect for re-encryption; no PIN in HA; error classification by type/code.
Discriminating experiments needed: E1 ble_client answers passkey for proxy PAIR; E2 bond reusable via proxy plain connect + pair() without PIN; E3 bond survives ESP reboot / HA restart; E4 slot accounting with dormant ble_clients.

### Log forensics 12:20–13:36 UTC (failure-timelines.md, episodes/E01–E41) — VERIFIED from HA logs; no proxy logs exist
- Routing: AM6532 → main-bedroom (healthy). AM8224 → main-bedroom (INFERENCE), never seen advertising, 387 failed polls (CONNECT no reply 15 s → error 256).
- C1 Living-room proxy API: connect then closed within 16–33 ms, EncryptionHelloAPIError (63) / EOF (2), from 12:34:32 after HA restart, still failing → AM2086/AM1382 route unavailable (42). EARLIEST unexpected event for those heaters now.
- C2 GATT 0x05/0x0F on first read 0.15–0.9 s after connected:true (74). Recurs after successful pairing (AM2086 paired 12:52:46, 0x0F at 12:53:33).
  HYPOTHESIS H-RACE: runtime path calls request_mitm_encryption (async esp_ble_set_encryption, no completion wait) and then reads immediately → read precedes encryption. Competing: H-BONDLOSS (bond not persisted / deleted), H-IDF (5.5.6 RAM-key bug if a proxy actually runs 5.5.6), H-LEVEL (encryption level not MITM).
- C3 PAIR with no PairingResponse for 30 s (18) / 9 failed PIN pairings; error 102 mostly trailing after HA DISCONNECT.
- Repair path fired 9× in ~1 h; 8/18 pairings succeeded. Pairing churn → per esp-idf#19303 failed pairings may delete good bonds.
- Not seen: disconnect-ack timeouts, slot exhaustion (free=0), unsolicited disconnects.
- Gaps: 790/963 failed polls silent (instant fail, no reason); no passkey/auth/SMP/bond/encryption-level/disconnect-reason visibility; session lines lack MAC.

## Root cause RC-1 — living-room proxy API rejected HA (CONFIRMED, fixed operationally 13:45 UTC)
- Mechanism (ESPHome 2026.11.0 api_server.cpp:323, api/__init__.py: esp32 max_connections default 5): at limit the proxy accepts TCP then closes → HA sees EncryptionHelloAPIError.
- Evidence: 5 ESTAB sockets host→192.168.50.175:6053 held by 17 orphaned `esphome run/logs` processes in marcosvr-esphome-1 (from earlier agent sessions, Nov 23 + today 11:30/12:18). API key hashes HA==YAML (ruled out key mismatch).
- Experiment: SIGTERM only those processes (one variable) → HA reconnected to living-room within ~60 s, advertisements for AM2086/AM1382 (+ unconfigured AM5832) flowing.
- Not a code defect. Prevention: diagnostic log captures must always be bounded (`timeout`), never `esphome run` without `--no-logs`.

## E-RACE experiment (13:49–13:52 UTC) — H-RACE REJECTED for living-room; native pair() VALIDATED on bonded heater
- Change: transport non-pair path awaits native client.pair() instead of fire-and-forget request_mitm_encryption (backup /config/dimplex-rollback/transport.py.20261124 sha 7e8e6bd8…).
- living-room: pair() blocks 30.0 s → TimeoutError; proxy log "authentication complete success=0 reason=102 bonds=0". → living-room bond store EMPTY (H-BONDLOSS confirmed for this proxy).
- main-bedroom: AM6532 re-encryption via pair(): success=1 reason=0 bonds=2, ~0.1 s, no PIN. → native pair() suffices for bonded re-encryption (Q4 verified on hardware).
- "BTM_SetEncryption busy, enqueue request" on both proxies at connect → something (heater security request accepted by ESPHome, or prepare action) starts security before pair(); OPEN.
- Living-room firmware flashed 12:49 (build dir mtime; ESP uptime agrees), HA reported successful pairings ~12:52, bonds=0 by 13:49 with no reboot → bonds deleted at RUNTIME.
  HYPOTHESIS H-ABORT-DELETE: Bluedroid deletes a bond when encryption/pairing is aborted (HA disconnect after 0x05/0x0F during set_encryption) — cf. esp-idf#19303. Competing: H-CLEAR (clear_bond actions present in flashed debug firmware, called by an out-of-band tool). HA logs show no clear action calls.
- Leftover destructive configs exist: living-room-bluetooth-proxy-clear.yaml (clear_bond/clear_all_bonds actions), tmp-main-nvs-reset.yaml (nvs_flash_erase on boot).
- AM8224: never advertises; connect gets no reply (error 256). Separate track — needs independent scan; do NOT blame the heater yet.

## Amendment 2026-11-24 (user): priority = comprehensive pruning/refactor of entire component; stability > simplicity; native reuse preferred but challenged.

## 13:54 UTC
- AM1382/AM2086 entries temporarily disabled (disabled_by=user; backup .storage/core.config_entries.bak-20261124) to stop unanswered pairing attempts (heater rate-limit risk). RESTORE after experiment.
- EXPERIMENT: after HA restart, AM6532 native pair() on bonded heater → paired in 0.367 s, no PIN, poll success. Bond reuse across HA restart VERIFIED (n=1 restart).
- AM8224 (main-bedroom): proxy "Connecting v3 without cache" → no link in 15 s → reason 0x100; proxy slots free (used=1/3), so proxy is NOT holding a stale link. No proxy sees its advertisement. Open: heater off / out of range / connected to another central. Needs independent scan or physical check (user). Not attributed to heater yet.

## E1 — stock ble_client answers proxy-initiated passkey (CONFIRMED on hardware, 13:58 UTC)
- living-room flashed OTA with living-room-bluetooth-proxy-vanilla.yaml (no external component, no custom actions; 2 dormant ble_clients auto_connect:false, passkey_reply from !secret; esp32_ble max_connections 5 = 3 proxy slots + 2 ble_client). Rollback image: /config/rollback-20261124/living-room-bluetooth-proxy.bin (running build compiled 2026-11-23 20:49:17).
- HA transport: removed runtime prepare_mitm action; awaits native pair() after connect.
- AM1382: proxy PAIR → "ble_client on_passkey_request, replying" → "auth success mode=13" (SC+MITM+bond). HA never handled the PIN. Polls succeed.
## E2 — bond reuse without PIN after HA restart (CONFIRMED, 14:00 UTC): AM1382 re-encrypted with LTK in 0.357 s, no passkey request.

## RC-2 mechanism — bond deletion on failed AUTH_CMPL (VERIFIED in IDF v5.5.5 source, idf-bond-deletion.md)
- bta_dm_act.c:5021-5038 (RAM) + btc_dm.c:286-303 (NVS): any failed AUTH_CMPL except reason 5 removes the bond. No Kconfig/API to disable.
- 102=SMP_CONN_TOUT (link lost while SMP active), 99=SMP_RSP_TIMEOUT, 97=SMP_ENC_FAIL. A failed new pairing also deletes an old bond.
- Old flow (fire-and-forget encryption + immediate read + disconnect on 0x05/0x0F; 30 s pairing waits cut by HA disconnect) produced repeated reason-102 AUTH_CMPL → bond deletion → more "auth required" → more pairing churn. Consistent with bonds=0 on living-room. Hardware reproduction by reverting: NOT yet done (optional; now cheap since ble_client re-pairs automatically).
- Design rules: never abort an in-progress security procedure; await pair() before GATT; a lost bond is re-created natively by ble_client on the next pair().
- Peer Security Request with valid bond → LTK encryption automatically (smp_act.c:462); "BTM_SetEncryption busy" = heater SR already started encryption (INFERENCE).
- esp_ble_get_bond_device_num counts NVS-mirrored config. ESP_GAP_BLE_KEY_EVT is dropped by esp32_ble (custom component's KEY_EVT logging never fired).
- Note: a third build `dimplex-ble-proxy` (ESPHome 2026.10.2, IDF 5.5.6 WITHOUT bond-key wrap) exists in build dir from Nov 16 — confirm no device runs it.

## 14:03–14:06 UTC
- AM2086 commissioned automatically via living-room ble_client (auth success mode=13). AM1382 + AM2086 bonded and polling concurrently through one stock proxy.
- main-bedroom flashed OTA to main-bedroom-bluetooth-proxy-vanilla.yaml (rollback /config/rollback-20261124/main-bedroom/). After reboot AM6532 auth success mode=13 with NO passkey request → bond survives ESP reboot + OTA (n=1).
- Both production proxies now run stock ESPHome 2026.11.0 (no custom component). Bounded 1 h log captures: /tmp/e3-{main-bedroom,living-room}.log in esphome container.
- AM8224 still not advertising (proxy connect attempts unanswered). User reports power-cycling historically restored it. Plan: one observed power-cycle under the new stack.
- User: a third ESP32 on the MacBook USB is for testing/debugging only — use for destructive experiments (H-ABORT reproduction) and independent scans; never production.
- Target architecture drafted (artifacts/target-architecture.md); Codex adversarial review in progress.

## Requirement R-PIN (user, 2026-11-24)
The heater shows its PIN only once pairing starts; for a NEW heater the user reads it from the display and types it into the HA UI during that pairing. Commissioning must therefore be: HA starts pair() → display shows PIN → user enters PIN in HA → PIN reaches the ESP32 while the pairing waits.
- Evidence the PIN is static per heater: PINs stored in secrets days earlier completed fresh pairings of AM1382/AM2086 today (n=2); prior art says fixed per unit. → storing the PIN after first commissioning enables automatic re-pair after bond loss.
- Consequence: firmware-baked-PIN-only (variant A) fails first-commissioning UX. Open research (runtime-pin-entry.md): stock-ESPHome path for HA→ESP PIN during pairing (templated passkey_reply + wait_until + text/number entity, or YAML api action lambda), timing budget (SMP/aioesphomeapi 30 s?), secrecy, per-heater YAML vs runtime address.

## Architecture decisions after Codex adversarial review (codex-architecture-review.md)
1 ACCEPT: pair() shielded from cancellation; link close only after pair() resolved; close serialized on link lock (RC-2).
2 ACCEPT: ATT 0x05/0x0F after successful pair() = transient UpdateFailed, not ConfigEntryAuthFailed. Reauth only on hardware-confirmed credential pairing codes (H8 wrong-PIN test).
3 ACCEPT: keep one per-proxy security lock through pair() (ESP serializes connects, not SMP; "BTM_SetEncryption busy" evidence).
4 PARTIAL: advertisement-gated connect kept; reconnect is event-driven (bluetooth advertisement callback) not timer-polled. Verify H6 (re-advertise after disconnect) on every heater.
5 ACCEPT as documented: persistent link per heater; current 2 heaters/proxy leaves 1 slot. Measure in soak.
6 ACCEPT: reconfigure/move uses a temporary link pinned to the selected proxy; commit source only after success; never unpair the old proxy as part of a move.
7 ACCEPT: unload = stop coordinator, await, then single serialized close.
8 REJECT: drop private _release_connection_no_wait; no ack timeouts in sample; revisit if soak shows slot leak.
9 MOOT: R-PIN keeps the PIN in HA.
10 ACCEPT: invalidate cached client on proxy API disconnect / scanner change; add proxy-reboot test.
11 ACCEPT: consistent capacity numbers (max_connections = 3 + N ble_clients ≤ 9).
12 ACCEPT: keep SOUND on its slow schedule.

## Interim baseline (stock firmware + awaited native pair(), persistent links) 14:09–14:27 UTC
- Polls: AM6532 80/80, AM8224 91/91, AM1382 94/94, AM2086 78/78 (343/343, 0 failures). No re-encryptions needed (links persisted), no GATT 0x05/0x0F, no auth failures, no passkey requests.
- Compare pre-change sample 12:20–13:36: 963 failed polls, 41 failure episodes (failure-timelines.md). Different conditions (proxy API outage, empty bond store) — not a like-for-like reliability claim; sustained soak still required.
- .gitignore restored from HEAD (an earlier session overwrote it with an ESPHome template that dropped Python ignores). Deliverable firmware moved to tracked /firmware.

## Refactor deployed 15:00–15:01 UTC
- Firmware v2 (firmware/dimplex_pin_responder.yaml: 1 generic dormant ble_client + globals + api actions dimplex_pin_arm/clear; max_connections 4) OTA'd to both proxies. Rollback: /config/rollback-20261124/vanilla-{living-room,main-bedroom}/.
- Integration refactor deployed (link.py/responder.py/errors.py; transport/session/commissioning/esphome_proxy/pairing/route_policy/exceptions + C++ component deleted). Rollback: /config/dimplex-rollback/integration-interim.
- Metrics (refactor-metrics.md): integration SLOC 3372→1893, lifecycle SLOC 2293→885, try 45→19, asyncio sleep/timeout 7/5→0/0, radon ≥B blocks 33→12, tests 6125→2492 SLOC. ruff/mypy/pytest(303, 100% cov)/hassfest pass. OPEN: `mutmut run` segfaults at interpreter shutdown (exit 139) on the new tree (2/3 runs; 0/1 on HEAD) → CI quality gate would fail. Must resolve before any push.
- Security: aioesphomeapi.connection DEBUG logs ExecuteService args (would log PIN). Set `aioesphomeapi.connection: info` in HA configuration.yaml on Jarvis. Old HA logs from before today 15:00 may contain PIN arguments sent by the old relay (docker logs / home-assistant.log*). Local scratchpad copies checked: no PIN values.
- Result after restart: AM1382, AM6532, AM8224 reconnect via bond (pair ok) and poll OK.
- NEW FAILURE F-133: AM2086 every connect rejected by proxy in 40–100 ms: "Connection open failed, status=133", reason 0x85. Preceded by proxy "hcif disc complete hdl 0x1 rsn 0x3e" at 15:01:07. HA handles via native ConfigEntryNotReady backoff. Investigation delegated (am2086-133.md). No proxy reboot (would mask cause).

## RC-3 — IDF stale "closing" GATT record after a cancelled connect (CONFIRMED, am2086-133.md)
- Trigger (15:00:59–15:01:00): shutting-down HA cancelled an in-flight AM2086 connect just as the link formed → `L2CA_CancelBleConnectReq - no connection pending` → GATT tcb left "closing" (gatt_main.c:276-277, l2c_ble.c:98-101); Bluedroid's internal retry (ESPHome sets CONFIG count 10; l2c_link.c:532-544) kept the record → every later gattc_open failed in <100 ms status 133 (gatt_main.c:400-402, bta_gattc_act.c:554-559); heater stopped advertising (INFERENCE: orphan link held by proxy).
- Discriminating experiment 15:12: reboot ONLY the living-room proxy → AM2086 advertised again within seconds (15:12:54) and reconnected via bond after HA's native setup retry (15:15:06). → stuck state was proxy-side; heater recovers from link loss correctly.
- Rejected workaround: CONFIG_BT_GATTC_CONNECT_RETRY_COUNT=0 — the same internal retry recovered a normal 0x3e for AM1382 at 15:13:00.
- Fix in our layer: connect() runs as its own task awaited to its native end (bounded 15 s) even when HA cancels us (same rule as pair()); cancelled-after-success connect is closed cleanly. Tests: test_cancel_during_connect_* . Deployed 15:18; restart produced no 133.
- Residual: HA process death / API drop mid-connect and bleak-esphome's own 15 s timeout cancel remain possible triggers (narrow window). Upstream report to ESP-IDF needed (gatt_le_connect_cback should close a link arriving for a "closing" tcb).
- AM8224 earlier silence is NOT explained by RC-3: both proxies rebooted 13:58/14:04 and it stayed silent until power-cycle 14:08. USB test ESP32 (scanner fw, compiled 11:40 today) ruled out (out of range, not a client). Cause OPEN (other central / heater-side).

## T-REPAIR — automatic recovery after confirmed bond loss (PASS, 15:20–15:21 UTC)
- Fault injection: native ESPHome API UNPAIR for AM1382 on living-room proxy from a separate short-lived client (bluetooth_proxy.cpp:407-418 removes the bond by address). Proxy: HCI disconnect rsn 0x13 → 0x16, slot freed (15:20:44).
- Recovery without user: HA reconnect 15:21:04 → RESPONDER_ARM (stored PIN) → "BT_SMP: FOR LE SC LTK IS USED INSTEAD OF STK" (fresh SC pairing marker; absent on bonded re-encryption, e.g. 15:13:07) → auth success mode=13 → PAIR_RESULT ok 2.07 s → polls OK. Outage ~22 s.
- Confirms H-RESP: generic ble_client retargeted at runtime via set_address() answers a passkey for a bluetooth_proxy-owned pairing.
- Tool note: aioesphomeapi bluetooth_device_unpair from the side client timed out waiting for the response although the proxy executed it (test-tool limitation).
- Observability gap: firmware logs unanswered passkey requests only; add an "answered" line (next firmware batch).

## Command/write-path validation via HA UI session (15:25–15:45 UTC, user-authorized on AM2086; OWD toggle on AM1382)
- All writes read back from the heater on the next poll: presets manual/frost_protect/home/user_timer/eco/away; setpoint 18–22; Adaptive Start off/on; OWD off/on; Sound on/off; display unit °F/°C; Away return datetime (deadline 16:40 read back exactly). Call latencies 0.35–8.6 s. AM2086 restored to baseline (eco, 18 °C, celsius, adaptive on, OWD on, sound off); datetime entity re-disabled (user's choice).
- REGRESSION FOUND+FIXED: Adaptive Start (PREEMPTIVE_HEATING 0/0x1008) missing from snapshot reads in the uncommitted tree (present at HEAD) → switch unavailable. Restored in link.py READ_SNAPSHOT_FIELDS; regression test test_snapshot_reads_every_verified_setting (proven to fail without the fix). Deployed 15:31.
- DEVICE BEHAVIOUR (verified, reproducible n=2): in Manual, writing setpoint ≤19 °C makes the heater report Eco mode (0x1001=03); 20–22 °C stays Manual. Integration writes only 0/0x1023. Not an integration defect. Cause INFERENCE: firmware eco threshold.
- Entry reload: new setup hits ROUTE_NOT_SEEN before the heater re-advertises → ConfigEntryNotReady → native retry connected ~5 s later (connect 11.4 s, pair 0.19 s).
- Registry: 17 orphaned disabled experimental raw sensors per heater family remain from earlier uncommitted pruning (not this refactor) — user may delete in UI.
- CI: mutmut segfault (CPython finalize GC, no extension frames) mitigated by documented `[tool.mutmut] process_isolation = "forkserver"`; 3/3 full gate runs exit 0. Exact corrupted object not identified.
- Discovered unconfigured heaters: AM5832, AM8922 (ownership unconfirmed — not touched).

## Reconfigure UX defects found on hardware (15:45–15:56 UTC, user at AM2086)
- D1: reconfigure listed no proxy — a connected heater does not advertise and the flow listed proxies before releasing the link. Fixed (release first) 15:47.
- D2: after release, the advertisement wait timed out (15:53:51→15:54:21) although the heater advertised: habluetooth 7.1.2 manager.py ~1316-1350 skips async_register_callback/async_process_advertisements dispatch for identical payloads (VERIFIED in source). Native fix: bluetooth.async_register_advertisement_callback (HA api.py:222, habluetooth manager.py:1454) fires on every advertisement. Same root cause affects the coordinator's advertisement-driven refresh (explains AM2086's ~2 min recovery via setup retry instead of callback).
- D3: chosen proxy already had a valid bond → pair() ok 0.01 s, no PIN displayed, yet flow showed the PIN form. Fix: firmware fires `esphome.dimplex_passkey_request` (address only; forwarded unconditionally per esphome/manager.py:351-373); flow shows a progress step and only asks for the PIN when that event arrives; finishes directly if pair() completes first.
- Firmware 16:00: responder package adds the event + "passkey request answered" log; flashed to both proxies (rollback /config/rollback-20261124/dimplex_pin_responder.v2.yaml). All heaters recovered, no status 133.
- Official docs (docs/dimplex-manual-research.md): PLXE manual has no BLE pairing procedure; sibling Dimplex lines use a fixed 6-digit label PIN (supports static PIN); PLXE Eco default 19 °C matches observed ≤19 °C→Eco (inference); no official BLE troubleshooting/power-cycle guidance.

## 16:10–16:22 UTC — two more hardware findings during reconfigure to main-bedroom
### RC-4 (platform limit, CONFIRMED by log + upstream): third uncached link refused by the controller
- main-bedroom held AM6532+AM8224; third connect (AM2086, uncached "v3 without cache" → ESPHome FAST fixed 7.5 ms interval) → `BT_HCI opcode=0x200d status 0x0d Rejected: Resources` 54 ms after Connecting → status 133 (hci-0x0d.md; l2c_ble.c:1058-1076).
- Upstream: esphome/esphome#22859 (C3/S3 controller, won't-fix 2026-11-18): uncached third link at fixed 7.5 ms refused 19/24; cached (MEDIUM 8.75–11.25 ms) never refused. Raising MAX_ACT/max_connections does not help.
- Experiment: AM8224 entry temporarily disabled → only AM6532 on main-bedroom → AM2086 connect accepted (16:13:08). Consistent with RC-4.
- Design consequence: runtime reconnects must be cached (already: dangerous_use_bleak_cache unless fresh); commissioning (uncached) should target a proxy holding < 2 links; document ≤2 uncached-capable / prefer ≤2 persistent links per proxy. Cached third link still to be verified (re-enable AM8224 after AM2086 moves).
### RC-5 (CONFIRMED): responder armed after the heater already requested the passkey
- 16:13:08.5 link up → heater Security Request starts SMP immediately → PASSKEY_REQ while responder untargeted (no trigger, no event, no log) → PIN on display → 16:13:10.411 armed (after GATT discovery) → 16:13:38.969 smp_rsp_timeout reason 99.
- Fix: arm responder (target ± PIN) BEFORE connect, holding the per-proxy security lock across connect+pair, clear in finally. Contract test asserts order action→connect→pair. Deployed 16:22. (T-REPAIR earlier passed only because the arm happened to precede the passkey request.)
### D4 (16:25): reconfigure offered only the nearest proxy
- Causes (code): options were computed from pre-release history (nearest proxy only) so the wait was skipped; and the wait returned on the first responder advertisement. Fix: repairs always collect fresh advertisements after release, until every responder proxy reported or DISCOVERY_WINDOW (5 s) after the first; responder.responder_sources(). Regression test test_reconfigure_offers_a_farther_proxy_heard_after_the_nearest. 312 tests, 100% cov. Deployed 16:33.

## R-PIN acceptance test — user-typed PIN commissioning (PASS, 16:35–16:53 UTC, AM2086 moved living-room → main-bedroom)
- Discovery window: main-bedroom offered after the D4 fix.
- Wrong PIN (in window, 16:50:58): responder answered → SMP reason 81 → "pin_rejected" → fresh target-only pairing restarted at once (display shows PIN again). H8 CONFIRMED: wrong PIN = 81 (twice: 16:36:40, 16:50:59).
- Slow typist: pairing timed out 30.00 s (TimeoutError, transient) → user typed PIN later → responder armed with PIN BEFORE connect → "passkey request answered" → fresh SC pairing (BT_SMP LTK marker) → auth success mode 13 → "Reconfiguration was successful" (16:52:34) → polls OK via main-bedroom.
- UX gap noted: after a timeout the display is blank; the static PIN must be remembered or a wrong PIN entered to re-show it. Follow-up: blank submit restarts pairing ("show PIN again").
- PIN typed only by the user; never in HA logs (aioesphomeapi.connection at INFO).

## Standing principle (user, 2026-11-24): UI/UX is always in scope
Every user-facing change needs: plain language (no internal jargon), a preselected recommendation, warnings stating consequence + remedy, no dead ends (always a way forward: retry / show PIN again / look again), explicit limitations and boundaries. Verify in the real UI (browser screenshots), not only tests. Independent UX review of all user-facing surfaces (flows, repairs, entity names/attributes, errors, README, troubleshooting) before final sign-off.
In progress: "Show the PIN again" menu CTA; proxy selection with Recommended + per-proxy limitation warnings + 10 listed edge cases.

## RC-4 CONFIRMED deterministic after HA restart (17:08–17:10 UTC)
- HA restart empties the in-memory GATT service cache → first reconnect is uncached even though link requests cache=True → proxy "Connecting v3 without cache" (fixed 7.5 ms) → with 2 links already held: 0x200d Rejected: Resources, 4/4 for AM2086 as third heater on main-bedroom. (Earlier AM8224 third-link success at 16:53 was the lucky minority.)
- Refusal is local (no SMP) → no bond deletion; but heater stays unavailable until a slot frees.
- PRACTICAL LIMIT: ≤2 Dimplex heaters per ESP32-S3 proxy for reliable recovery after HA restarts (ESPHome fixed uncached interval; esphome#22859 won't-fix). Proxy picker warning must say "will not reconnect after Home Assistant restarts" for proxies already holding 2 heaters. Remediation: move AM2086 back to living-room (2+2).
- New UI deployed 17:07 (menu CTA "show PIN again", proxy Recommended + warnings). 329 tests.
- UX rule refined (user, 17:20): dialogs = one short sentence + the choice + a short tag/warning only when relevant. No bullets/paragraphs, no MAC/dBm, friendly proxy names; details in README via "?" help link.

## H14 ANSWERED (17:13 UTC): heater keeps bonds with ≥2 centrals
- AM2086 reconfigured main-bedroom → living-room: PAIR_RESULT ok 0.01 s, no passkey request, no PIN — the living-room LTK (created 15:21) stayed valid after the heater bonded with main-bedroom (16:52). Flow's bonded path finished without the PIN form. Layout restored to 2+2 (main-bedroom: AM6532, AM8224; living-room: AM1382, AM2086).

## ESPHome dashboard pruned (user request, ~17:25 UTC)
- /config now: living-room-bluetooth-proxy.yaml, main-bedroom-bluetooth-proxy.yaml (both = former -v2 production configs with packages/dimplex_pin_responder.yaml), secrets.yaml. Dashboard shows 2 devices, both online.
- Archived (reversible) to /config/archive/2026-11-24-prune/: 17 stale YAMLs (tmp-*, *-vanilla, *-clear, recovery, api-only, nvs-reset + .h, old canonical files), their dashboard storage JSONs, pairing-test-component/, stale build dimplex-ble-proxy (IDF 5.5.6 build). Rollback images remain in /config/rollback-20261124/.
- Not removable from inside the container: /config/components is a bind mount from ~/.docker/openhands/workspace/ha-dimplex-ble/components (unused by current firmware) — remove from the ESPHome compose service (needs container recreate).
- Note: HA's custom_components/dimplex_ble is also bind-mounted from the openhands workspace — deployment source of truth should move to a deliberate path.
- ESPHome cleanup (user-approved, ~17:35 UTC): permanently deleted archive/ (today's prune + old atom-echo yaml), rollback-20261124/ (firmware rollback images — rollback path now = recompile from git-tracked firmware/), .esphome/external_components, 2 stale package caches, dashboard-jobs + atom-echo caches, 49 untracked tmp_*.py/clear_dimplex_bonds.py/.bak files (27 contained plaintext API keys). Removed the openhands components bind mount from ~/docker-compose.yml (backup docker-compose.yml.bak-20261124) and recreated the esphome container. Local repo esphome/ probe folder (26 untracked files) deleted. /config now: 2 device YAMLs, packages/, secrets.yaml (+ dashboard dotfiles, .git).
- ESPHome scope = Dimplex work only (user). Also removed stale .esphome/file, 2 stale idedata, empty platformio; re-initialised the Device Builder git repo with one clean commit of the current files (old 90-commit history contained deleted probe configs with plaintext API keys). Dashboard: 2 devices online. RECOMMENDATION: rotate both proxies' API encryption keys (old keys appeared in plaintext in deleted files/history and in earlier HA logs/sessions).

## 17:24–17:30 UTC
- UX release deployed (386 tests): preset translations fixed (home → home_all_day BREAKING), climate name = device, Away return time CONFIG + enabled by default, frozen unique_ids + orphan cleanup, model "PLX050E Series C" (inference), tenths room temp, flow_title, minimal flow copy, actionable errors, ENUM operating mode, error-code states from manual p.26, README/troubleshooting rewrite. Live unique_id check: all Adaptive Start switches use suffix preemptive_heating → no duplicates. User automations do not use preset 'home' or the error sensor.
- User automation "Dimplex heaters: presence-based Away Mode" was silently broken since the refactor (bare preset away rejected — explicit deadline required; continue_on_error hid it). With user approval: now sets each heater's Away return time to now+13 h (activates Away; 12 h refresh extends it). Backup ~/automations.yaml.bak-20261124. AM2086 return-time entity enabled. Hardware test on AM2086 via execute_script: preset away, away_until 2026-11-25T06:28Z (minute resolution) → restored to frost_protect.
- AM8224 SILENT AGAIN: 17:19:19 snapshot GATT read handle 143 hung ~10 s → error 133 → bleak-esphome connected=False error=8 (HCI 0x08 supervision timeout = heater stopped answering) → ROUTE_NOT_SEEN from 17:19:39 on, proxy holds no link (1/3 used, not AM8224). Same symptom as morning (fixed by power cycle at 14:08). Proxy-side log of the event LOST: capture lived in the esphome container I recreated ~17:21 (lesson: keep captures on the host volume). Pending discriminating test: independent phone BLE scan next to AM8224 (advertising → range/radio path; silent → heater BLE stack stopped).

## AM8224 episode 2 isolation (17:30–17:35 UTC)
- Independent scanners: USB test ESP32 at the Mac heard only AM5832 (inconclusive: range unknown); user's phone (nRF Connect) NEXT TO AM8224 heard nothing → heater-side BLE stall CONFIRMED (heater stops advertising after the link dies). Satisfies AGENTS.md isolation: HA kept polling, proxy released the link, two independent scanners silent.
- User power-cycled AM8224 ~17:34 → 17:35:04 event-driven reconnect (cached) → bond intact (pair 0.01 s) → polls OK.
- Trigger under investigation (am8224-stall.md): handle 143 read hang → supervision timeout.
- Host-side 12 h proxy captures started: ~/dimplex-logs/{living-room,main-bedroom}-20261124T1735.log (survive container recreation; bounded by timeout).

## Climate semantics test (AM2086, 17:50 UTC) — decides HVACMode.AUTO question
- User Timer + setpoint 20 → heater accepted it, stays User Timer, target reads back 20 (timer mode HAS a writable target) → HA's AUTO ("you cannot set a temperature in this mode") does NOT fit; keep HEAT + presets (decision: no AUTO). Whether the write is a temporary override or changes the schedule's comfort value is not yet observed.
- Frost Protect + setpoint 20 → heater switches itself to Manual at 20 (device behaviour; same as a wall thermostat). Document; no rejection needed.
- Restored AM2086 to Frost Protect 7 °C.

## Heater BLE stall — status (17:40–17:50)
- AM2086 stalled independently at 17:34:50 while idle (not power-cycled by user; only AM8224 was). Ruled out holders: both proxies (allocations), Jarvis hci0 (no connections). Heater accepts ≥2 bonds (H14), so "only one paired device" is ruled out. Heater radio = Espressif ESP32 (OUIs C4:D8:D5, 24:4C:AB, 8C:4B:14, D0:EF:76; GATT `Esp32OtaEnable`). No public user reports or vendor fix found (dimplex-ble-hardware-research.md). Clean disconnects (~50) always re-advertised; abnormal link losses 2/2 → silence until wall power-cycle.
- User approved: (2) Repair issue "heater stopped responding" after abnormal drop + 60 s without advertisement; (3) A/B: per-poll connect/disconnect mode (options flow) on AM8224 only; (4) research RF firmware (no vendor contact). AM2086 power-cycled by user → reconnected 17:48:20.

## 17:56–18:00 UTC — stall repair + connection-mode option deployed (403 tests); A/B started
- AM8224 switched to per-poll (keep_connected=false) at 17:58:32 via options flow. Cycle verified: connect (cached) → pair 0.1 s → snapshot → clean DISCONNECT reason=per_poll, every 60 s (~6–8 s per cycle). Other three heaters persistent (control group). NOTE: per-poll also uses a 60 s interval (confound documented).
- Defect found: in per-poll mode the slow-schedule Sound read opened a SECOND connection 20 ms after the poll's clean disconnect (cached path, mtu=23) → read 2/0x0005 handle 98 → ATT 0x01 Invalid handle → Sound switch unavailable. Other heaters (persistent) read Sound OK at 17:56. Fix planned: fold the Sound read into the poll connection in per-poll mode (no back-to-back reconnects). Retry at ~18:03:41 will show whether the failure persists.
- ConfigR RE: toolchain installed via brew (apkeep 1.1.0, jadx 1.5.7, android-commandlinetools) + build-tools 35 (apksigner) via sdkmanager. Package com.dimplex.dimplextoolkit (likely). Waiting for user to run ~/Downloads/configr-play/fetch-configr.sh (official Google Play via apkeep; credentials only in user's terminal).

## 18:05 UTC — ConfigR acquired from Google Play
- apkeep 1.1.0 direct Google Play download of com.Dimplex.DimplexToolkit (case-sensitive; APKPure's lowercase ID gives "Invalid app response"): base + config.arm64_v8a + config.xxhdpi + config.en + .dm → ~/Downloads/configr-play/20261124T180544Z/. Earlier failures: reused single-use oauth_token cookie (only issued after the EmbeddedSetup consent "I agree", URL ends #close). Lessons added to ~/.agents/skills/android-google-play-analysis/SKILL.md ("Field notes"). Static RE in progress → docs/configr-firmware-analysis.md.
- AFTER ANALYSIS: delete ~/Downloads/configr-play/.apkeep.ini and have the user remove the "Android Device" session (Google Account → Security → Your devices); the oauth cookie was pasted in chat once (single-use, already consumed).
- AM8224 per-poll: Sound read recovered 18:03:34 (the 17:58 Invalid-handle was a back-to-back reconnect 20 ms after disconnect). Fix in progress: read Sound inside the poll connection in per-poll mode.

## UI walkthrough (18:12–18:20 UTC, AM6532 device page + climate more-info)
- Good: model "PLX050E Series C", firmware "UI 1.7 / RF 5", translated states (Frost Protect, No error, Celsius), climate title = device name, honest "Idle", all 6 preset names (Away, Home All Day, User Timer, Manual, Eco, Frost Protect) with icons.
- Fixed (not yet deployed): Manual preset icon mdi:hand doesn't exist → mdi:hand-back-right; model_id raw "PLX050E;C;" shown in parentheses → "PLX050E"; Away return time showed a stale past deadline (heater keeps last value) → shown only while Away is active. 406 tests, 100% cov.
- IDF pin test in progress: living-room YAML without `version: 5.5.5` → ESPHome 2026.11 default IDF 5.5.6 (+bond-key wrap); compiling.
- User request: ConfigR entity-gap inventory added to the RE task.

## H9 — ESP-IDF 5.5.6 (ESPHome 2026.11 default + bond-key wraps) VERIFIED on living-room (18:15–18:25 UTC)
- Build links -Wl,--wrap=smp_check_auth_req and --wrap=btm_sec_clear_ble_keys + esp32_ble/bluedroid_bond_keys.c.
- OTA flash kept both bonds; 4 reconnect cycles per heater (flash + 3 entry reloads): 9/9 LTK re-encryptions, 0 passkey requests, 0 fresh pairings, 0 bond deletions → 5.5.5 pin no longer needed. Next: main-bedroom + firmware/example YAML without the pin.
## Heater settle time (VERIFIED twice + vendor guidance)
- Reconnect 20–23 ms after a clean disconnect → heater accepts then terminates within ~7 ms (rsn 0x13) or answers "Invalid handle" (17:58:41, 18:24:23). ConfigR help text: wait ~10 s after a link ends for the product "to go to sleep"; ConfigR caps retries. → rule: no reconnect to the same heater within 10 s of a disconnect.
## ConfigR 3.12.2 static RE (docs/configr-firmware-analysis.md, docs/configr-entity-gap.md)
- No firmware bundled; per-appliance "latest firmware" (version, URL, min app version) from cloud API mobileapi.gdhv-iot.com (Azure AD B2C login); ZIP with release_config.json + images fetched by plain GET (likely pre-signed blob, INFERENCE). Update offered when cloud version > stored version; UI version read over BLE (3/0x2001); EP number (3/0x200A) checked.
- OTA: ESP32 path if 00060001-F8CE-11E4-ABF4-0002A5D5C51D present (MTU 247, 1/0x300E=1, Cypress-bootloader-style frames over ESP32), else Cypress PSoC path (1/0x100E); Zigbee EFR32 for other products. PLXE likely ESP32 path (has 0x300E).
- ConfigR BLE: autoConnect=false, 10 s × 3 attempts, explicit disconnect always, no keep-alive (clock write once if >60 s off), asks before reconnect after loss; heater evicts bonds when its bond limit is reached (don't create bonds repeatedly).
- 73 parameter classes inventoried; high-value missing: Boost, Advance, Setback, Out All Day/Holiday, schedules, temperature offset, min/max setpoint, away temperature. Do-not-expose: PIN lock, factory reset, test modes, activation, pairing mode, OTA/keys.

## 18:26–18:33 UTC
- H9 complete: main-bedroom also on IDF 5.5.6 (ESPHome default + bond-key wraps); after OTA all four heaters re-encrypted (0 passkey requests, 0 fresh pairings, 0 auth failures). Both proxies now unpinned. firmware/example-bluetooth-proxy.yaml pin removed.
- Heater settle rule (HEATER_SETTLE_S = 10 s, shared per-address registry) deployed 18:29 (412 tests).
- Docs refreshed: esphome-ble-findings.md rewritten (888 stale lines → current record), capability matrix corrected, docs/upstream-issue-drafts.md (esp-idf RC-3, esphome status=19 with verbatim evidence, passkey API feature request).
- Play credential ~/Downloads/configr-play/.apkeep.ini deleted. USER TODO: remove the "Android Device" sign-in (Google Account → Security → Your devices).
- scripts/soak_report.py added (per-heater polls, connects, pair times/codes, GATT fails, error=8 link losses, ROUTE_NOT_SEEN, settle waits, stall repairs). Formal soak starts after the entity release deploy.
- In progress: high-value missing entities from ConfigR gap (Boost, Out All Day, Away temperature, offset, limits; schedules out of scope).

## New entities — hardware verification on AM2086 (18:42–18:58 UTC); restored: Frost Protect 7 °C, Away temp 7, Advance off, Setback off, offset 0.0, limits 7/30, installer entities re-disabled
- Out All Day preset (0/0x1001 = 01 01 …): set + read back + restored. PASS.
- Away temperature (0/0x102A byte 6): heater ENTERS AWAY on ANY write of the Away record — active byte 0 with a future deadline (18:44) AND with deadline = now (18:48). Hypothesis "future deadline arms Away" REJECTED. Design: writable only while Away is active; refused otherwise ("Turn on Away first…"), verified live (stayed Frost). While active: temp 10→7, deadline preserved. PASS.
- Advance (0/0x100D): refused outside timer modes (clear message); in User Timer on/off read back. PASS.
- Setback (0/0x1016 on/off byte): on/off read back. PASS.
- Temperature offset (2/0x1019 signed tenths): +0.2, +1.0, +3.0, −3.0, 0.0 all read back → ±3.0 verified; PLXE manual's ±0.2 is not a device limit. PASS.
- Setpoint limits (0/0x1006): 5 writes ACKed, never applied (read-back 7/30, climate limits unchanged) → effectively read-only over BLE → entities removed (pruning in progress); climate keeps reading the limits.
- Boost not implemented (duration encoding inference only).

## Final review, CI parity, push (19:00–19:40 UTC)
- Codex final adversarial review: 7 findings, all fixed (shared-responder clear only by the lock-holding pairing; flow keeps/awaits pair task before close; arm inside try/finally; coordinator Away lock; reauth only on code 81; DEBUG-logging PIN warning + docs; climate Away attrs hidden when inactive). 507 tests.
- Setpoint limit entities removed (writes ACKed, not applied).
- CI parity on a clean worktree checkout of commit e3ff1a0: fresh uv venv (Python 3.14, requirements-test.txt), ruff format check, run_quality_gates.sh (pre-commit incl. check-yaml --unsafe for ESPHome tags — would have failed CI before, fixed), pytest 507 / 100%, functions 229/229, mutmut 2171 killed / 552 survived / 84 timeout of 2807 (advisory), hassfest 0 invalid. NOT verifiable locally: HACS validation (GitHub action).
- Pushed branch refactor/stock-esphome-native-ble (e3ff1a0) after the repo pre-push hook passed. No PR opened.
- Formal soak started 2026-11-24T19:24:06Z on final code + IDF 5.5.6 firmware on both proxies; AM8224 per-poll (A/B), others persistent. Measure with scripts/soak_report.py.

## Soak interim (19:24:06–21:14 UTC, 1 h 50 min)
- `scripts/soak_report.py` over HA logs: polls ok/failed AM1382 548/1, AM2086 466/1, AM6532 451/3, AM8224 (per-poll) 102/0. All 5 failures are `ROUTE_NOT_SEEN` at 19:24:16–19:24:30, i.e. the first polls after the HA restart before the first advertisement; from 19:24:31 on: 0 failed polls.
- 0 pairing failures, 0 GATT failures, 0 abnormal link losses (`error=8`), 0 stall repairs, 0 settle waits.
- Main-bedroom proxy capture (IDF 5.5.6), same window: 101 `auth success` (AM8224 per-poll re-encryptions), 0 auth failures, 0 `[E]`, 0 status 133 / 0x200d / reason 0x08 / 0x3e; 101 clean disconnects (reason 0x00, initiated by HA); `Conn param update failed, status=19` on every connect (upstream draft (b), no functional effect observed).
- Baseline for comparison (12:20–13:36, old code): 963 failed polls, 41 failure episodes. 24 h checkpoint due 2026-11-25T19:24Z.
- Docs: firmware updates stay out of the integration (fully local); README limitation + troubleshooting section point to the ConfigR app.

## Heat/Auto decision (21:25–21:40 UTC, AM2086)
- Test via HA services: Home All Day → t=21; User Timer → t=21; set_temperature 22 → User Timer t=22 (accepted); Home All Day → t=21; User Timer again → **t=21**. The setpoint written in a timer programme is a temporary override, dropped when the programme is selected again; the schedule's own value is unchanged. (Behaviour at a period boundary not observed.)
- HA climate docs (core/entities/climate.mdx): AUTO = "follows a schedule ... You cannot set a temperature in this mode"; presets change how a mode behaves. → Timer programmes moved to `HVACMode.AUTO` (presets user_timer/home_all_day/out_all_day/away, no TARGET_TEMPERATURE); continuous modes are `HVACMode.HEAT` presets (manual/eco/frost_protect/away). Away counts as Heat. Auto resumes the last seen programme (default User Timer). Supersedes the 17:50 "no AUTO" decision.
- HA core validates `set_preset_mode` against the current `preset_modes` and `set_temperature` against supported features (verified in HA 2026.10.4 sources and live: "Preset mode manual is not valid…", "does not support action climate.set_temperature").
- Bug found live: after `set_hvac_mode`, the debounced refresh left the old mode in the state for up to 10 s, so a following `set_preset_mode` of the new mode was rejected. Fix: `async_write_mode` now awaits a non-debounced `async_refresh()`; verified live (auto+home_all_day and heat+frost_protect back to back).
- Deployed 21:32 and 21:36 (HA restarts, logged in ~/dimplex-logs/RESTARTS); rollback /config/dimplex-rollback/dimplex_ble-pre-auto-20261124.tar. AM2086 restored to Frost Protect.
- User automations: new "Main Bedroom heater: weekday morning warm-up" (AM6532; Mon–Fri 05:00 Heat→Manual→21 °C, 07:30 Heat→Eco; skipped in Away). "presence-based Away Mode": set_hvac_mode heat inserted before its Eco step (backup /config/automations.yaml.bak-pre-auto-20261124). Its scene restore works with Auto (reproduce_state calls set_hvac_mode before set_preset_mode).

## Read-only schedules (21:40–21:58 UTC)
- ConfigR IL decoded by a static-analysis agent (no device/network): one 31-byte (programme, day) record per read; select with `[0, programme, day]`, then read; save = byte 0 = 1 for all 7 days (not implemented). Details: docs/schedule-format.md.
- Implemented: protocol `encode_schedule_request`/`parse_schedule_day`, link reads (persistent: own operation every 6 h; per-poll: inside the poll), diagnostics, 3 calendars per heater (User Timer enabled, Home/Out disabled by default after the 12-calendar month view proved crowded). 532 tests, 100 %.
- Live (21:48): all four heaters returned 3 × 7 valid days; defaults match ConfigR's Constants tables; AM6532 Monday User Timer customised (05:30–07:30, 16:00–17:00, 21:00–23:00). Modes unchanged afterwards. Test-only artefact found: pytest-freezer broke HA climate cached properties during setup; test now patches dt_util.now instead.
- HA restarts for deploys: 21:32, 21:36, 21:48, 21:53, 21:55 (~/dimplex-logs/RESTARTS); each showed the usual 1–4 ROUTE_NOT_SEEN polls per heater at start and one AM6532 15 s connect timeout at 21:49 (recovered at 21:49:45). Soak metrics must exclude these windows.
- Live registry: the 8 Home/Out calendars created before the default change were disabled (disabled_by=user).

## Peak-time automation and presence compatibility (2026-11-25 03:00–03:30 UTC, AM2086)
- Requirement (user): peak rate 17:00–19:00 every day; heaters to Frost Protect, previous state back at 19:00; coming home during peak keeps Frost Protect; leaving during peak goes to Away and stays in Away after 19:00.
- Rejected design: in-memory `scene.create` snapshot with "fall back to Auto after a restart". AM1382, AM2086 and AM8224 normally sit in Frost Protect, so the fallback would switch them to Auto. Chosen: persistent helper `input_text.dimplex_peak_time_saved_states` (JSON `{amXXXX: [hvac, preset, temperature]}`, 255 chars; HA restores it after a restart).
- New automation "Dimplex heaters: peak-time Frost Protect" (`dimplex_peak_time_frost_protection`). Triggers 17:00 (start), 19:00 (end), HA start (start if within the peak and nothing saved; end if outside it and something saved; 2 min delay for the heaters to reconnect). Start saves only heaters that are available and not in Away/Frost Protect, then Heat → Frost Protect. End restores only heaters still in Frost Protect (manual changes during peak win; Away stays), then clears the helper.
- DEVICE BEHAVIOUR (verified): after Frost Protect, `set_preset_mode eco` returns Eco at the heater's own Eco temperature (18 °C), not the saved 16 °C. Restore therefore also writes the saved temperature for Manual and Eco when it differs. Manual ≤ 19 °C reporting Eco reproduced again (Manual 21 → write 16 → Eco 16).
- Restore round trips on AM2086 via a temporary script with the same steps: Eco 16, Manual 21, Auto + Home All Day all → Frost Protect 7 → back exactly. Phase template checked for the 6 trigger/restart cases.
- Presence automation ("presence-based Away Mode"): the coming-home branch now checks the peak window. During peak: heaters go Heat → Frost Protect (leaves Away) and heaters with nothing saved get `[heat, eco, null]` in the helper, so 19:00 gives what coming home would have given; states saved at 17:00 are kept. Outside the peak: unchanged (scene restore, then Eco). The leaving branch is unchanged: Away on top of Frost Protect, untouched by the 19:00 step.
- Verified on AM2086: Away + saved Manual 21 → end step → stays Away, helper cleared; Away → coming-home-during-peak steps → Frost Protect 7, helper `{am2086: [heat, eco, null]}` → end step → Eco 18.
- Backup: /config/automations.yaml.bak-pre-peak-compat-20261125. Temporary test scripts deleted; AM2086 restored to Frost Protect 7 °C.
- Known limit: a heater still unavailable 2 min after an HA restart is not restored by the restart path.

## Clock, Boost, schedule save, upstream filing (2026-11-25 03:20–04:20 UTC)
- Temporary probe service (`dimplex_ble.debug_gatt`, raw read/write/select inside one link operation, `validate_raw_access` still applied) deployed to Jarvis only, never committed; rollback /config/dimplex-rollback/dimplex_ble-pre-probe-20261125.tar. HA restarts 03:29:54, 03:43:24, 04:02:15 (~/dimplex-logs/RESTARTS).
- ConfigR IL (static agent, docs/configr-clock-schedule-boost.md): clock `[20, yy, mm, dd, hh, mi, dst]` local time, written once per connection when drift > 60 s; Boost = write RunbackTime minutes then the setpoint; schedule save = byte 0 = 1.
- ROOT CAUSE of AM2086 heating at 21:26 in Home All Day (08:00–21:00): heater clocks were slow. 03:31 UTC reads: AM6532 03:29 flag 1 (set by ConfigR); AM2086 01:14, AM1382 01:07, AM8224 01:12, flag 0 (138–144 min slow). Not an integration or schedule-decoding fault.
- Clock sync implemented (option "Sync the heater clock", default on, in setup, reconfigure and options): read on each new connection and hourly on a held link, write `encode_clock(now)` when more than 60 s off, offset in diagnostics. DST flag = `dst() != 0`; HA's tzdata (Jarvis) gives Europe/Dublin −1 h in winter → 1, matching ConfigR; the Mac's tzdata gives 0 (tests use explicit zones). Deployed 03:43: all four set ("−139/−144/−2/−138 min off"), read back 03:44–03:45 flag 1.
- Boost on AM2086 (probe): from Frost Protect `0x1027=1e00`, `0x1023=1600` → mode `020000010000`, setpoint 22, countdown 30→29; `0x1027=0000` → Frost 7 within 1.5 s. 1-min boost expired → Frost within a minute. From User Timer → `010300010000`, expiry → User Timer. Byte 3 = duration band (≤30:1, ≤60:2, ≤120:3, ≤180:4, ≤240:5). 1–240 min accepted; 241/255/300 ignored. 0x1028 = 21 °C.
- Boost implemented as climate preset (Heat and Auto lists, not in Away), length option (1–240, default 60), temperature from 0x1028; any other preset/mode ends Boost first (0x1027 = 0, 1.5 s settle, refresh). Live via HA services: Frost → Boost 21 → set 22 → Frost 7; User Timer → Boost (Auto, target 21) → Heat (Manual) → Frost; raw read confirmed no Boost left.
- Schedule save (probe): Monday User Timer only → Monday changed, Tuesday and mode unchanged; empty-day form accepted; backup restored byte for byte. `dimplex_ble.set_schedule` implemented (device, programme, days, ≤4 periods; validates before writing, writes each day, reads each back, updates calendars, fails on mismatch). Live: Monday 06:00–08:00 20 °C + 17:00–22:00 21 °C → calendar showed both at once; restored factory Monday via the action, byte-identical.
- Test environment limit: newest pytest-homeassistant-custom-component (0.13.375) pins HA 2026.10.4, whose ClimateEntity reads `_attr_temperature_unit`; the entity uses 2026.11's native unit API, so a full-setup climate entity cannot load in tests (entity tests call it directly). Not a product bug; live HA 2026.11.3 is fine.
- Upstream (#10): (a) filed as espressif/esp-idf#19495 (v5.5.5 reproduction, v5.5.6/master line refs, "latest version" box left unticked and explained). (b)/(c) not posted: ESPHome forms ask for the reporter's own words and AI analysis only in `<details>`; corrected drafts in docs/upstream-issue-drafts.md for the user.
- main fast-forwarded to 22942a4 (CI green on that commit); the pre-push hook had failed only because it ran on uncommitted work in the tree.
- Codex adversarial review (needs-attention, 3 medium), each challenged:
  1. Boost/Away race: VALID but narrower than stated. Climate calls are serialized (`PARALLEL_UPDATES = 1`), but Away can start from the return-time datetime entity outside that queue. Fix: `async_start_boost` holds the Away lock and rechecks `away_active` (Away writes update the snapshot at once). Also closed the reverse case: entering Away (any path, e.g. the presence automation's datetime write) while Boost runs ends Boost first. Regression tests for both.
  2. Partial multi-day save: VALID problem, proposed rollback REJECTED (it needs more writes over the link that just failed; re-running the action is idempotent). Fix: write and read back day by day, keep the confirmed days in the calendars, re-read all programmes in the background after a failure, and report "saved N of M days".
  3. Fractional temperatures truncated by `Coerce(int)`: VALID. Now 21.0 is accepted, 21.9 / "21.5" / True are rejected before any write.
  589 tests, 100 % lines, 266/266 functions.
- Codex post-refactor review: (1) "settings completion uses key presence, not decoded value" — REJECTED: `_read_optional` returns None (key present) for an absent characteristic, so completion still works and the entity stays unavailable on a decoded None; requiring a decoded value would make a heater that lacks a setting re-read forever. On this hardware GATT readability is static per connection, so a once-decoded setting cannot later return None (a transient failure is a GATT error, caught and retried). (2) "diagnostics expose the household heating schedule" — FIXED: the diagnostics schedule block now exports only the per-day period count (None when unread), not the exact times and temperatures, since diagnostics are often attached to public issues (the PIN and addresses were already redacted). 521 tests, 100 %.

## AM1382 stall (2026-11-25 08:19 UTC)
- Held link, polls ok every ~12 s until 08:18:56. 08:19:06 poll stalled 11 s, then `GATT ... handle=60 error=133`; the proxy reported `connected=False error=8` (supervision timeout) right after our release. From 08:19:27 every poll: ROUTE_NOT_SEEN. A 20 s listen on every HA Bluetooth receiver heard no AM1382 advertisement; the living-room proxy's allocations list only AM2086 (no orphan link). Same signature as the AM8224/AM2086 heater-side stalls (abnormal link loss, then silent to all receivers). Recovery: power-cycle at the wall (or close the Dimplex app if a phone is connected).
- BUG found: no "stopped responding" repair. The timeout surfaced first as a GATT read error; `_operation` released the client for it, so the later link-down counted as ours and the stall window never started. Fix: a GATT failure that ends the link also starts the stall window (hearing the heater ends it). Regression tests for silent and still-heard heaters.
- 08:56 UTC (user decision): all four heaters switched to per-poll (keep-connected off) via the options flow; this ends the held-link vs per-poll A/B. Evidence at the switch: every heater stalled at least once while held (AM8224, AM2086, AM6532 earlier; AM1382 today); AM8224 per-poll since 2026-11-24 19:24 with no stall. ConfigR never holds a link either. AM6532 changed to Frost Protect at 08:49:08 without any HA context (heater buttons or the Dimplex app), noted in case the app was in use near the AM1382 stall.
