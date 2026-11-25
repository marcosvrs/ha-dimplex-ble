# Dimplex PLXE Series C BLE hardware and "stops advertising" fault research

Research date: 2026-11-24. Web and local files only; no live-system changes.

## Scope and evidence labels

Question: do other Dimplex users report the same fault (heater stops answering, link ends with supervision timeout HCI 0x08, then never advertises again until power-cycled at the wall), and what BLE chip/firmware is inside the PLXE Series C?

- **VERIFIED**: read in a primary source or in this repo's captured data (cited).
- **INFERENCE**: reasoned from verified facts; not proven.
- **NOT FOUND**: searched, nothing located (queries listed at the end).

Fault facts supplied by the coordinator (not independently verified here): 3 of 4 units stalled over several days (AM8224, AM2086, AM6532); hypothesis that stalls occur on links held at a fixed 7.5 ms interval. **New data point (coordinator, 2026-11-24 17:34:50): AM2086 (C4:D8:D5:69:C0:86) stalled while its link was IDLE, with no request in flight, and was not power-cycled; a phone running nRF Connect next to it sees nothing.** This weakens any theory in which our own request or a specific GATT operation triggers the stall.

## 1. BLE silicon vendor: the heaters' public addresses are Espressif

| Address | OUI | Registered to | Source |
|---|---|---|---|
| C4:D8:D5:6F:65:32, C4:D8:D5:6F:8C:2E, C4:D8:D5:69:C0:86 | C4:D8:D5 | Espressif Inc. | Wireshark manuf (IEEE-derived) |
| 24:4C:AB:07:BD:82 | 24:4C:AB | Espressif Inc. | Wireshark manuf (IEEE-derived) |
| 8C:4B:14:70:F8:D2 (neighbour) | 8C:4B:14 | Espressif Inc. | IEEE MA-L registry, direct |
| D0:EF:76:36:89:22 (AM8922, neighbour) | D0:EF:76 | Espressif Inc. | Wireshark manuf; maclookup.app |

- **VERIFIED (IEEE, primary):** the IEEE MA-L CSV from `https://regauth.standards.ieee.org/standards-ra-web/rest/assignments/download/?registry=MA-L&format=csv` lists `MA-L,8C4B14,Espressif Inc.,"Vistra Corporate Services Centre, Wickhams Cay II Road Town Tortola VG VG1110"`. Caveat: my download of this CSV was truncated at about 970 KB, so C4D8D5, 244CAB and D0EF76 could not be read in the IEEE file itself (standards-oui.ieee.org returned an HTTP 418 block page).
- **VERIFIED (secondary, derived from the IEEE registry):** Wireshark's `manuf` file, `https://www.wireshark.org/download/automated/data/manuf`, lists C4:D8:D5, 24:4C:AB, 8C:4B:14 and D0:EF:76 all as "Espressif Inc." maclookup.app independently agrees for C4:D8:D5 and D0:EF:76 ([maclookup.app/macaddress/c4d8d5](https://maclookup.app/macaddress/c4d8d5), [maclookup.app/macaddress/d0ef76](https://maclookup.app/macaddress/d0ef76)).
- **VERIFIED (repo capture):** the GATT dumps contain a ConfigR-named characteristic `1/0x300E` = `Esp32OtaEnable` (value `00`), see `docs/capability-matrix.md` lines 110 and 218. This independently says the radio side is an ESP32.
- **VERIFIED (repo capture):** the GATT dumps in `captures/plx050e-seriesC-*.json` contain no Device Information Service (0x180A): services are 0x1801, 0x1800, four vendor `00000000-000N-1000-8000-00805F9B34FB` services and `00060000-F8CE-11E4-ABF4-0002A5D5C51D`. Advertisements carry no service UUIDs and no manufacturer data. Chip variant, module name and BT controller library version are therefore **not exposed over GATT**. The only firmware-ish strings are `3/0x2009` RF revision `05` and `3/0x200D` `PGLHS600` (`docs/capability-matrix.md` lines 212 and 214).
- **INFERENCE:** the Espressif addresses are public (not random), as expected for an ESP32 factory MAC used as the BLE public address. The exact SoC (ESP32, S3, C3, C6) cannot be determined from the OUI. Three different Espressif OUIs across five units suggest units from different production batches, not necessarily different chips.

## 2. Regulatory and teardown evidence

- **VERIFIED:** Glen Dimplex holds FCC ID **2AP2M4106**, "Bluetooth Low Energy Module", single modular, 2402-2480 MHz, granted 2019-01-30, model/product code 4106. Public exhibits include internal photos, test report and user manual; block diagram and schematics are confidential/unavailable. Source: [fccid.io/2AP2M4106](https://fccid.io/2AP2M4106). The exhibit pages returned HTTP 403 to my fetcher, so the chip on that module is **not read**.
- **NOT FOUND / INFERENCE:** nothing ties FCC ID 2AP2M4106 to the PLXE Series C. It is a 2019 module of unknown chip; Espressif MACs on the PLXE units suggest the PLXE radio is a different or ESP32-based design. Do not assume 4106 is in the heater.
- **NOT FOUND:** FCC/ISED/UKCA/RED declaration naming PLXE Series C or an "RF 5" board, and any public teardown or board photo identifying the BLE chip (see queries below). The repos named in the brief were not fetched in a way that produced a teardown.
- **VERIFIED:** PLXE Series C documents list "Bluetooth for software updates" (already recorded in `docs/dimplex-manual-research.md`, Q4 section). Dimplex's own FAQ says ConfigR performs BLE over-the-air updates ([ConfigR page](https://www.dimplex.co.uk/products/smart-controls/configr)); the heater's `Esp32OtaEnable` characteristic is consistent with an ESP32 OTA path (INFERENCE).

## 3. Firmware and app release notes

- **VERIFIED:** the ConfigR iOS app listing ([apps.apple.com/us/app/id1497696142](https://apps.apple.com/us/app/id1497696142)) shows version history 2.8.0 to 3.12.2 (2024-11 to 2026-11). Almost every entry says only "Bug fixes and stability improvements." 3.6.2 added "Quick Connect and firmware update features for installers." No entry mentions Bluetooth disconnects, advertising, or a heater-side RF firmware fix. The page shows no review text ("hasn't received enough ratings").
- **NOT FOUND:** any published heater RF firmware changelog (RF 5 to a later release), or any Dimplex support article about heater Bluetooth stops, resets or required power-cycles for PLXE.
- **VERIFIED (related, different product):** the PLXENC Series A short instructions document a pairing window: if no connection is made within 2 minutes the product stops broadcasting ([PLXENC Series A Short Instructions Issue 4](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXENC%20-%20Series%20A%20Short%20Instructions%20-%20Issue%204.pdf)). Already noted in `docs/dimplex-manual-research.md` (lines 117 and 177). INFERENCE worth stating: if the PLXE Series C shares RF firmware logic with PLXENC (same family, same ESP32 module family), an advertising timeout that is never re-armed would match "never advertises again until power-cycled". This is unproven; the repo's own evidence is that PLXE Series C advertises continuously in normal operation.
- Dimplex support articles found are about app pairing ("GDID/PIN invalid", "unable to pair hub"), not a stuck radio: [GDID/PIN invalid](https://glendimplexservice.freshdesk.com/support/solutions/articles/79000136835-when-trying-to-add-a-heater-to-dimplex-control-app-it-says-gdid-pin-is-invalid-what-do-i-do-). The Dimplex Hub guidance recommends removing old Bluetooth bonds and clearing the Android Bluetooth cache ([Unable to pair my hub](https://help.gdhv.co.uk/support/solutions/articles/79000136579-unable-to-pair-my-hub-how-do-i-resolve-)). Relevant only as generic bond hygiene.

## 4. User reports of the same fault

- **NOT FOUND.** No app-store review, forum post, Reddit thread, Trustpilot/Which?/AVForums item or GitHub issue was located describing a Dimplex heater that stops advertising after a Bluetooth disconnect and recovers only after being switched off at the wall. Search engines returned mostly Dimplex support pages and manuals; the search tool is US-only and did not surface Reddit, Trustpilot or Google Play review text. Absence here is weak evidence only (the heater BLE user base is small and few people monitor advertising).
- Related Dimplex GitHub items seen, none matching: [KRoperUK/dimplex-controller-hass #312](https://github.com/KRoperUK/dimplex-controller-hass/issues/312) (Quantum schedule not updating, cloud), [AntonioJMM/dimplex-sierra-ble](https://github.com/AntonioJMM/dimplex-sierra-ble) (fireplaces; BLE disconnected after each operation). The specific repos in the brief (philcluff/dimplex-qrad-smart-bridge, JYewman Sunhouse, Lammerink/Dimplex-Optimyst-BLE, zjeeraar/flame-connect, bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump) were not individually opened.

## 5. Espressif-side known issues (the heater is probably an ESP32 peripheral)

None of these is confirmed to be the Dimplex fault; they show the failure class exists on ESP32-based peripherals.

1. **Advertising stops and never restarts on ESP32 peripherals (VERIFIED existence, cause unconfirmed).**
   - [espressif/esp-idf #513](https://github.com/espressif/esp-idf/issues/513): advertising stops after several connect/disconnect cycles (2017 report).
   - [esp32.com t=15301](https://esp32.com/viewtopic.php?t=15301) (IDF v3.3): disconnect event seen, restart call returns OK, device not visible.
   - [arduino-esp32 #12437](https://github.com/espressif/arduino-esp32/issues/12437): Bluedroid, device invisible after disconnect; a later commit references an advertising-restart race.
   - [OffbandMesh/meshcore-firmware #1828](https://github.com/OffbandMesh/meshcore-firmware/issues/1828) (2026-10): NimBLE-Arduino 2.x ESP32 companion stopped advertising while idle and unconnected for about 3 hours; only a radio reboot or phone Bluetooth toggle recovered it; cause not confirmed, no fix; a 16 h instrumented run did not reproduce. This parallels the new idle-stall data point.
2. **BT controller silently stops until reboot (VERIFIED existence, scanner side).** [esp-idf #18217](https://github.com/espressif/esp-idf/issues/18217): on ESP-IDF 5.5.2 (original ESP32) the controller silently stopped delivering scan results after hours to days, HCI command timeout `0x200c`, recovered only by reboot while Wi-Fi and app kept running. Fixed in 5.5.3 (closed completed 2026-02-20). INFERENCE: a controller-level wedge that spares the host app and the rest of the device would explain "heating/display unaffected but radio dead" in the heater, if its BT controller library has a similar defect. Whether the heater's IDF version is affected is unknowable from outside.
3. **Controller abandons LL_TERMINATE_IND after 6 connection events (VERIFIED, different direction).** [esp-idf #19189](https://github.com/espressif/esp-idf/issues/19189) (C3/S3, open; Espressif shared a test controller build 2026-11-17; no released fix named): when an ESP32-C3/S3 is the central and disconnects, a peer using peripheral latency can miss the terminate and wait for supervision timeout. This concerns our S3 proxy as central; it does not describe the heater stalling, but it can create spurious supervision-timeout disconnects on the heater side when a link is torn down by the proxy. ESPHome carries a workaround ([esphome/esphome #20232](https://github.com/esphome/esphome/pull/20232), per the esp-idf issue text).
4. **7.5 ms interval is ESPHome's fixed "fast" setting.** [esphome/esphome #22859](https://github.com/esphome/esphome/issues/22859) (closed not planned, 2026-11-18): `FAST_*_CONN_INTERVAL` is 0x06 (7.5 ms) and `MEDIUM_*` is 0x07-0x09 (8.75-11.25 ms) in `ble_client_state.h`; on an ESP32-C3 with two 7.5 ms links a third connect was refused HCI 0x0D in 19 of 24 tries, none refused with the 7.5-11.25 ms range. This matches the coordinator's observation (7.5 ms vs 8.75-11.25 ms) as a proxy-side scheduling difference; it is about the central's controller, not a documented heater bug. [esp-idf #2813](https://github.com/espressif/esp-idf/issues/2813) (2018) says data loss was worse at 7.5 ms (user report, not vendor-confirmed).
5. **NOT FOUND:** any Espressif errata, release note or advisory that says "peripheral stops advertising after a supervision-timeout disconnect" or "controller hangs at 7.5 ms interval". The ESP-IDF v4.4.6 notes mention a "BLE disconnection delay on peripheral when slave latency is not zero" fix (seen via search summary, not read in full).

## 6. Actionable takeaways

- **Fix path is on Dimplex's side:** the heater's RF firmware (Espressif BT controller) is the likely stalled component. A user cannot update it except via ConfigR OTA; raise it with Dimplex support citing model PLX050E/PLX075E Series C, "UI 1.7 / RF 5", product code EP4213, and ask whether an RF firmware newer than 5 exists. INFERENCE: the ConfigR app update path (Quick Connect and firmware update, 3.6.2) is the only sanctioned route.
- **Treat the interval hypothesis as unproven.** The idle stall on AM2086 means no request needs to be in flight. Still cheap to test: avoid a fixed 7.5 ms interval (request 8.75-11.25 ms or 30-50 ms). Caution from #22859: slower intervals lengthen service discovery (about 5x slower) and some peripherals drop links that look inactive, so do not slow connection parameters below what the heater's idle timeout tolerates.
- **Reduce exposure:** fewer long-lived idle links (connect, read, disconnect) in case the controller wedges after long link life; keep one central per heater; test with a different proxy chip (classic ESP32 vs S3) to rule out #19189 style terminate loss.
- **Recovery without wall access:** none found. Power-cycle at the wall (or breaker) is the only observed recovery. INFERENCE: an automatic smart-plug power cycle would be a workaround but is electrical work on a fixed-wired panel heater and is out of scope here.
- **Isolation per AGENTS.md:** do not attribute the fault to the heater until the independent phone scan next to the heater is repeated during a stall (already done for AM2086 per coordinator: heater silent), which does isolate the HA/ESPHome/proxy side. That observation is the strongest evidence in this file that the heater radio itself stalls.

## Queries tried (and results)

- IEEE OUI lookup via standards-oui.ieee.org (blocked, HTTP 418), regauth CSV (partial), Wireshark manuf (complete), maclookup.app searches for C4:D8:D5 and D0:EF:76.
- "Dimplex PLXE Series C panel heater FCC ID Bluetooth module Espressif": found FCC 2AP2M4106 only.
- "Dimplex Quantum Q-Rad bluetooth heater stops advertising disconnect switch off at the wall": no matching reports.
- "Dimplex Control app Bluetooth heater not found disconnects review OR reddit OR forum": only Dimplex support pages.
- "Dimplex ConfigR bluetooth heater ... github issue disconnect not advertising": no matching issue.
- "Dimplex ConfigR app ... what's new Bluetooth": App Store history, no BLE notes; Google Play ConfigR page not located.
- esp-idf searches for advertising not restarting after disconnect, supervision timeout, and 7.5 ms interval: results in section 5.
- Not done: Reddit, Trustpilot, Which?, AVForums, Hotukdeals, Mumsnet directly; fccid.io exhibit PDFs (403); the named Dimplex GitHub repos individually.

## 7. RF firmware versions and updates

Research date: 2026-11-24. Web only; nothing downloaded or executed. Reddit is blocked for the search tool (domain refused), so Reddit, Trustpilot, Which?, Mumsnet and Hotukdeals could not be queried directly.

### 7.1 Is there an RF firmware newer than "RF 5"?

- **NOT FOUND (no).** No Dimplex/GDC page, PDF, product bulletin, app release note or community post names any heater RF/UI firmware release, changelog or "RF version" later than 5 (or any version at all, apart from our own captures: `3/0x2009` = `05`, QRAD100E Series D also reads `05` in [philcluff docs/ble-protocol.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md)). Queries listed in 7.6.
- **VERIFIED:** ConfigR iOS version history ([apps.apple.com/us/app/id1497696142](https://apps.apple.com/us/app/id1497696142)), 3.1.10 to 3.12.2 (2026-11-12): every entry is "Bug fixes and stability improvements" except 3.6.2 (2026-05-28): "New Quick Connect feature for Installers New Connect for Firmware update for Installers", "Now supports dMEV High and Low Flow products". No entry names Bluetooth, disconnects, PLXE or a heater firmware fix. A mirror (apkpure, unverified) lists 3.8.0 as adding Air Curtain (AC3) support. Google Play "What's new" for `com.Dimplex.DimplexToolkit` was not retrievable.
- **INFERENCE:** two units of the same family (QRAD100E Series D, PLX050E Series C) both report RF 5, so RF 5 is at least a widely shipped build; whether a later one exists is unknowable without the app.

### 7.2 Update path

- **VERIFIED (vendor):** [Dimplex ConfigR page](https://www.dimplex.co.uk/products/smart-controls/configr): "the App will prompt the user on the 'Support' screen that a firmware update is available" after BLE pairing; "Pressing update now on this screen will initiate OTA update over BLE." ConfigR 3.6.2 added an installer "Connect for Firmware update". So delivery is over BLE from the phone (ConfigR), not via the Dimplex Hub/Control cloud. A Freshdesk/Dimplex text seen in search says the display/buttons may be unresponsive for a few minutes during a firmware update (secondary; the Edel article I fetched did not contain it).
- **VERIFIED (repo captures + philcluff):** GATT has `1/0x100E OtaEnable`, `1/0x300E Esp32OtaEnable`, `3/0x2012 FirmwareVersion` (absent on our PLXE), and two Cypress OTA bootloader services `00060000-F8CE-11E4-ABF4-0002A5D5C51B/C` ([philcluff docs/research.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/research.md), ConfigR decompile).
- **Hub/RFM is a different radio:** PLX050E-PLXC300E Series B+ use the Dimplex Hub plus RF Module for Control ([compatibility](https://www.dimplex.co.uk/dimplexcontrol/compatibility)); Dimplex says a Hub update may improve connectivity. That is the Hub, not the heater's BLE firmware.
- **Official text on Bluetooth stops, not discoverable, power cycle:** **NOT FOUND** for PLXE. Support articles cover app pairing only: remove old "Dimplex" entries from the phone Bluetooth list, Android location on ([GDID/PIN invalid](https://glendimplexservice.freshdesk.com/support/solutions/articles/79000136835-when-trying-to-add-a-heater-to-dimplex-control-app-it-says-gdid-pin-is-invalid-what-do-i-do-), [Hub pairing](https://help.gdhv.co.uk/support/solutions/articles/79000136579-unable-to-pair-my-hub-how-do-i-resolve-)). Other product lines (403BTB, Plinth) have an explicit button-held pairing mode ([manual](https://manualshive.com/en/manual/dimplex/403btb/2503825?page=4)); the PLXE does not, per AGENTS.md.

### 7.3 FCC grantee 2AP2M

- **VERIFIED:** [fccid.io/2AP2M](https://fccid.io/2AP2M) lists exactly one FCC ID for grantee 2AP2M (Glen Dimplex, Barn Road, Dunleer, Co. Louth): **2AP2M4106**, "Bluetooth Low Energy Module", DTS, Part 15C, 2402-2480 MHz, 0.000791 W conducted, single modular, granted and applied 2019-01-30, TCB TUV Rheinland NA, test lab TUV Rheinland Hong Kong ([fccid.io/2AP2M4106](https://fccid.io/2AP2M4106)). Exhibits: Users Manual (108.7 KB), internal/external photos, test report, RF exposure; block diagram and schematic are confidential. Exhibit PDFs were not fetched (403 earlier, and download rule), so BLE connection limits/timeouts in the user manual are **NOT FOUND**.
- **NOT FOUND:** the products using module 4106 (no host-device grants under 2AP2M). Mapping to PLXE is not possible from FCC pages; module is modular-approved for "portable" use, so it could be in any Dimplex smart product.

### 7.4 Community reports

- **NOT FOUND** for PLXE Series C or Q-Rad: no Hotukdeals, Mumsnet, Trustpilot, Which?, AVForums, diynot, Screwfix/Toolstation or Amazon item about Dimplex Bluetooth drop/"not found"/wall switch recovery surfaced; the tool is US-only and Reddit is blocked. Weak evidence only.

### 7.5 GitHub

- philcluff/dimplex-qrad-smart-bridge: 0 issues. Most relevant prior art ([README](https://github.com/philcluff/dimplex-qrad-smart-bridge)): ESP32-S3 node polls every 30 s and "has held its connection for hours with no keep-alive" (ble-protocol.md); bond and Bluetooth setting survive a wall-switch power cut, radiator advertises again "within a minute" (QRAD100E, 2026-09-11); advertising persisted indefinitely ~10 min after disconnect. "Radiator Connected" off "usually means the radiator was switched off at the wall"; RSSI better than -75 dBm solid, worse than -85 dBm drops; "Clear BLE Bonds" button; one central only. No stall report, so our idle-stall on PLXE is not reproduced there (different model: Q-Rad needs Comms > Bluetooth enabled).
- JYewman Sunhouse (SSHE heater): 0 issues. README: ESP32 writes the clock characteristic every 3 s "as a keep-alive"; "Clear BLE Bonds", restart ESP32 and heater if failing. Workaround candidate, but unrelated to heater-side advertising loss.
- KRoperUK/dimplex-controller-py: 20+ issues, all cloud API (none BLE). Lammerink/Dimplex-Optimyst-BLE: 2 open, fireplaces (secure-failed error 1284; extended advertising on ENG68). zjeeraar/flame-connect-home-assistant and bobthecooldad/...Dump: no issues; zjeeraar says "power-cycle the fireplace" when pairing fails, fireplace accepts one BLE client only.
- **Chip identification (conflicting):** the heaters' public MACs are Espressif OUIs and the GATT has `Esp32OtaEnable` (section 1), but philcluff states the Q-Rad BLE radio is a **Cypress PSoC 4 BLE** module, from the presence of the Cypress OTA bootloader services (UUID `...F8CE-11E4-ABF4-0002A5D5C51B/C`) and the Quantum Hybrid UI board teardown. Our PLXE captures also expose `00060000-F8CE-11E4-ABF4-0002A5D5C51D` (section 1). INFERENCE: the GATT stack/firmware lineage is Cypress-style (possibly a PSoC-derived service table ported to or co-resident with an ESP32); the exact SoC (ESP32 vs ESP32-C3 vs PSoC 4) is **unresolved**. bobthecooldad's repo holds board photos and a SPI-flash dump (`Smart MX25L16066 .bin`, Macronix 2 MB flash, Quantum Hybrid UI board `GD_Quantum_Hybrid_UI_XLE_v4_5` per philcluff); images and binaries were not downloaded here, so the radio chip on it is not read.

### 7.6 Queries tried

- "Dimplex ConfigR app Google Play what's new heater firmware update"; "Dimplex PLXE firmware update RF version ConfigR installer"; "Dimplex Quantum Q-Rad RF firmware update Dimplex Hub software update firmware release notes freshdesk"; "FCC ID 2AP2M Glen Dimplex Bluetooth grantee"; "Dimplex heater Bluetooth ConfigR can't find / not connecting / not discoverable reddit OR diynot OR avforums"; "Dimplex PLXE Series C Bluetooth heater not showing up app switch off at the wall power reset review"; Reddit/Trustpilot/Hotukdeals/Mumsnet domain-filtered search (refused: reddit.com not accessible).
- Fetched: ConfigR vendor page, App Store listing, fccid.io 2AP2M and 2AP2M4106, Freshdesk Edel article, GitHub READMEs/issues for the six repos (via gh).
- Not done: filetype:pdf searches for "RF version", Google Play page, Dimplex installer/trade portals (login), Dimplex DE/NL/IE sites beyond search hits.

### 7.7 Most actionable

Ask Dimplex support (or an installer with ConfigR 3.6.2+, "Connect for Firmware update") to read the heater's Support screen for an available OTA, quoting UI 1.7 / RF 5 / EP4213; in HA, an advertising loss cannot be fixed from our side, so keep the existing detection plus a notification. Also try, cheaply, a periodic cheap write (clock/keep-alive as Sunhouse does) only as a test of the idle-stall theory.
