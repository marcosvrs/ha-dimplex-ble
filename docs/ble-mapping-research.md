# Third-party BLE mapping research (Glen Dimplex heaters)

Date: 2026-11-25. Scope: unofficial documentation and third-party code that maps
the BLE GATT interface used by ConfigR (`com.Dimplex.DimplexToolkit`) and
related Glen Dimplex apps. The main question was which characteristic, if any,
carries the 12-hex appliance ID (GDID) and the 6-digit label PIN ("security
code"). This is desk research: nothing was written to a heater.

No PIN, passkey, GDID value, key or credential is reproduced here. Two sources
publish their own unit's passkey in the clear (noted below). Those values were
deliberately left out.

Legend:
- **VERIFIED**: the cited source states it. The source's own evidence level is
  given (live capture, decompile, or assertion).
- **INFERENCE**: my reading across sources. It is not stated by any source.
- **NOT FOUND**: searched for, with no source found.

## 1. Sources found

| # | Source | Product / app | Basis | Commit / date read |
|---|---|---|---|---|
| S1 | [philcluff/dimplex-qrad-smart-bridge](https://github.com/philcluff/dimplex-qrad-smart-bridge), esp. [`docs/ble-protocol.md`](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md), [`docs/research.md`](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/research.md), [`docs/backlog.md`](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/backlog.md), [`captures/`](https://github.com/philcluff/dimplex-qrad-smart-bridge/tree/main/captures) | QRAD100E Series D, QRAD200E Series D. ConfigR v3.8.0 decompile | Live GATT dumps, watch sessions and a ConfigR parameter table | `5091078`, 2026-09-20 |
| S2 | [JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant](https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant) | Sunhouse SSHE storage heater. Dimplex Remo / inteliHeat app | ESPHome config reversed from the app | `7cd0271`, 2026-01-24 |
| S3 | [glenntucker/Heaters](https://github.com/glenntucker/Heaters), esp. [`research/PROTOCOL.md`](https://github.com/glenntucker/Heaters/blob/main/research/PROTOCOL.md) | Creda "Intelirad" PSLFS6 panel heaters. InteliApp (`com.heatstore.inteliapp`, BLE library `com.eyespyfx.gdble`) | Decompile plus live GATT on 2 units | `198a591`, 2026-11-24 |
| S4 | [JeroenLind/Dimplex-Control](https://github.com/JeroenLind/Dimplex-Control), esp. [`docs/register-map.md`](https://github.com/JeroenLind/Dimplex-Control/blob/main/docs/register-map.md), [`docs/design.md`](https://github.com/JeroenLind/Dimplex-Control/blob/main/docs/design.md) | Dimplex Opti-myst fireplace (CAS400LNH family), `FI7539<Dimplex>`, firmware `EP4120` | Live reads/writes and traffic of the original remote | `57f4749`, 2026-11-24 |
| S5 | [KRoperUK/dimplex-controller-py](https://github.com/KRoperUK/dimplex-controller-py) | Dimplex Control cloud API (hub), not BLE | Decompiled Dimplex Control app API | `28fdc66`, 2026-11-15 |
| S6 | [bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump](https://github.com/bobthecooldad/Dimplex-Quantum-Storage-Heater-Dump) | Quantum Hybrid UI board | PCB photos plus SPI flash dump; no GATT map | 2025-10-08 |
| S7 | [Glen Dimplex support: "GDID/PIN is invalid"](https://glendimplexservice.freshdesk.com/support/solutions/articles/79000136835-when-trying-to-add-a-heater-to-dimplex-control-app-it-says-gdid-pin-is-invalid-what-do-i-do-) | Dimplex Control app (hub/RF) | Vendor support article | read 2026-11-25 |
| S8 | [FX20VEW Instructions, Issue 1, Series A (PDF)](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2026-01/300004944%20FX20VEW%20Instructions%20Issue1%20SeriesA.pdf) | ConfigR-paired fan heater | Vendor manual (label template) | already analysed in `docs/dimplex-manual-research.md` |
| S9 | [HA community: Dimplex Q Rad Electric Heaters](https://community.home-assistant.io/t/dimplex-q-rad-electric-heaters/145867) | Q-Rad / Dimplex Control | Forum, 2019 to 2025-08-13 | no technical BLE content |

Searches with no Dimplex-relevant result are listed in section 6.

## 2. Primary goal: serial number / GDID and the 6-digit security code

### 2.1 GDID (12-hex appliance ID)

- **VERIFIED (S1, ConfigR decompile):** ConfigR names characteristic `0x2002`
  in service `…-0003-…` **`Gdid`**, 6 bytes, listed as RW in ConfigR's
  parameter table
  ([ble-protocol.md, "Parameter names from Dimplex's ConfigR app"](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md#parameter-names-from-dimplexs-configr-app)).
- **VERIFIED (S1, live QRAD100E D):** the GATT dump shows `0x2002` with
  properties `read` only. The value is redacted as `REDACTED_GDID`
  ([capture JSON](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/captures/qrad100e-seriesD-gatt-2026-09-11.json)).
  The doc says: "The unit's Glen Dimplex ID, shown big-endian on the debug
  screen under the name prefix" and "The 12-digit ID is 2002 byte-reversed"
  ([ble-protocol.md, "Radiator debug screens"](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md#radiator-debug-screens-2026-09-11)).
  So the characteristic holds 6 bytes little-endian, and the label/screen form
  is those bytes reversed and printed as 12 hex digits.
- **VERIFIED (S4, Opti-myst fireplace):** `0x2002` (handle 0x0089) reads
  6× `00` on that unit
  ([register-map.md §8](https://github.com/JeroenLind/Dimplex-Control/blob/main/docs/register-map.md#8-other-registers)).
  The characteristic is therefore not populated on every product.
- **VERIFIED (S8):** the ConfigR-family label carries `GDID: <12 hex>` next to
  a 6-digit "Pin Code". **VERIFIED (S7):** Dimplex Control adds a heater by GDID
  plus PIN, by QR scan or typed manually.
- **INFERENCE:** the user's 12-hex label value (QR field 1) is the GDID. On a
  PLX050E it should equal `3/0x2002` read byte-reversed. This is untested on a
  PLXE. The date-plus-series-plus-unit reading of the 12 hex digits is not
  documented by any source found.

### 2.2 The 6-digit "security code" / PIN

No source names a characteristic that exposes the label PIN in readable form.
What the sources do say:

- **VERIFIED (S1, ConfigR decompile):** ConfigR defines three
  credential-like parameters in service `…-0003-…`: `0x2006` **`Btpk`** (5 bytes),
  `0x2007` **`EncKey`** (16 bytes) and `0x2008` **`AuthKey`** (16 bytes), all
  listed as RW. It also defines `0x1033` **`Security`** (33 bytes, service
  `…-0002-…`). ConfigR runs an extra `SecurityParameter` (1033) handshake "on
  products that expose it"
  ([ble-protocol.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md#decoders-worth-having)).
- **VERIFIED (S1, live QRAD100E D):** `0x2006`, `0x2007`, `0x2008` and `0x200F`
  have **no GATT properties** (empty property list, no value). `0x1033` is
  absent on the Q-Rad D. The author's rule is "Never write to … the
  property-less characteristics `2006`-`2008` and `200f`"
  ([CLAUDE.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/CLAUDE.md)).
- **VERIFIED (S1):** "Passkey appears fixed per radiator rather than rolling:
  the living room unit showed the same six digits on every pairing attempt
  (2026-09-14)" ([ble-protocol.md, Connection](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md#connection)).
- **VERIFIED (S2, Sunhouse SSHE):** the README says the BLE PIN "is printed on a
  sticker inside the heater's front panel or in your documentation", and the
  ESPHome config hardcodes it as a static passkey
  ([README](https://github.com/JYewman/Sunhouse-Dimplex-Storage-Heater-ESPHome-Home-Assistant/blob/main/README.md)).
  The repo publishes the author's PIN, which is not reproduced here.
- **VERIFIED (S4, Opti-myst):** "Legacy pairing with a fixed 6-digit passkey" and
  "a fixed 6-digit code per fireplace"
  ([design.md](https://github.com/JeroenLind/Dimplex-Control/blob/main/docs/design.md)).
  The repo publishes its default passkey, which is not reproduced here.
- **VERIFIED (S3, Creda Intelirad, decompiled InteliApp):** on that family the
  BLE PIN is **derived from the MAC address**: the last 3 MAC bytes as 6 hex
  digits, a fixed per-digit substitution, then reversed. InteliApp
  reportedly auto-pairs only if characteristic `0x200D` exists ("Not yet
  verified" by the author for that condition). The derived PIN bonded live on
  2 PSLFS6 units
  ([PROTOCOL.md, Security / pairing](https://github.com/glenntucker/Heaters/blob/main/research/PROTOCOL.md#security--pairing)).
  That is a Creda / InteliApp product. No source says ConfigR or the PLXE uses
  the same derivation.
- **NOT FOUND:** any source that reads the label PIN from a characteristic, or
  that decodes `Btpk`/`EncKey`/`AuthKey`/`Security`.

**INFERENCE (unverified, flagged as speculation):**
1. The name `Btpk` reads naturally as "Bluetooth passkey". 5 bytes is enough
   for a 6-digit number plus a flag/length byte, or for ASCII digits minus one.
   On the Q-Rad it has no read property, so a central probably cannot read it
   back. It may be a factory provisioning slot.
2. In three Glen Dimplex BLE families (Q-Rad per S1, Sunhouse per S2, Opti-myst
   per S4), the BLE passkey is fixed per unit. S8 prints a 6-digit "Pin Code"
   on the same label as the GDID and calls it the Bluetooth PIN. Together these
   make it plausible that the PLXE's on-screen BLE PIN equals the label's
   6-digit code, but no source confirms this for PLXE.
   - Local check for this (not done by me): compare the displayed PIN at the
     next pairing with the label code, without recording either value.
   - Caveat: S7 uses the same GDID + PIN for Dimplex Control **hub/RF**
     registration. The label code may serve the RF join, BLE, or both.
3. The Creda MAC-derived scheme (S3) gives a second hypothesis that can be
   checked offline against the PIN shown on screen. It comes from a different
   app and product line, so treat it as unlikely, not as established.

### 2.3 Serial-number-like strings elsewhere

- **VERIFIED (S4):** on the Opti-myst fireplace, `0x200D` holds an 8-char ASCII
  string (example `F000B5E0`) that the author calls the "Serial number". The
  author adds that it is "also present in the advertisement while idle"
  ([register-map.md §7](https://github.com/JeroenLind/Dimplex-Control/blob/main/docs/register-map.md#7-identification-read-only-constant)).
- **VERIFIED (S3):** on Creda PSLFS6, `0x200D` = `"PSLFS6\0\0"`. InteliApp names
  it "Software modification" and maps that value to its PLXE type.
- **VERIFIED (S1):** on QRAD100E D, `0x200D` = `"PGPHS600"`, which the author
  calls the "Board/firmware code".
- **INFERENCE:** the PLX050E value `PGLHS600` fits the S1/S3 "board / software
  modification" meaning and is not a serial number. Only on the Opti-myst does
  the field look per-unit.

### 2.4 Cloud API (for completeness, not BLE)

- **VERIFIED (S5):** the Dimplex Control cloud `Appliance` model has
  `ApplianceId`, `SecurityCode`, `SeriesIdentifier` and `FirmwareVersion`
  fields, and the Hub model also has `SecurityCode`
  ([models.py](https://github.com/KRoperUK/dimplex-controller-py/blob/main/dimplex_controller/models.py)).
  The cloud therefore stores a per-appliance "SecurityCode". That is the term the
  user saw, and it fits the label PIN registered through the hub (S7). This is a
  cloud field. It says nothing about GATT.

## 3. Secondary goal: other characteristics

Short ids. Service index S in `00000000-000S-1000-8000-00805f9b34fb`.
"ConfigR name" is from S1's decompile of ConfigR v3.8.0. Values are from S1
(QRAD100E D) unless marked S4 (Opti-myst) or S3 (Creda).

| Char | Svc | ConfigR name (S1) | Third-party observations | Status in sources |
|---|---|---|---|---|
| `0x2002` | 3 | Gdid (6, RW) | QRAD: read-only property, per-unit, byte-reversed = 12-digit ID on the debug screen. S4: all zero | VERIFIED |
| `0x2003` | 3 | none | QRAD `00 04 00` (R/W). S4 `00 00 00` | unknown |
| `0x2006` | 3 | Btpk (5) | QRAD: no properties | name VERIFIED, meaning unknown |
| `0x2007` | 3 | EncKey (16) | QRAD: no properties | name VERIFIED |
| `0x2008` | 3 | AuthKey (16) | QRAD: no properties | name VERIFIED |
| `0x2009` | 3 | none | QRAD `05` = debug screen "RF Rev 005". S4 `05` (author: "hardware revision?") | QRAD VERIFIED via screen |
| `0x200B` | 3 | none | QRAD `e5 26`. S4 `8e 16` (LE16 5774) | unknown in all sources |
| `0x200C` | 3 | none | QRAD `14 13 03 0f 0f 23` ("Build date/time?"). S4 6× `00`. S1 also lists it as a candidate for "UI Rev 018" | unknown |
| `0x200D` | 3 | none | see §2.3 | product-dependent |
| `0x200F` | 3 | none | QRAD: no properties. "Probably write-only. Do not write blind." | unknown |
| `0x2001` | 3 | SwVersion (3, R) | selector: write `[n,0,0]` (0 controller, 1 UI, 2 RF), read `[n,major,minor]`. S4 fireplace uses it as a "handshake" (`010200`→`010702`); author does not know the purpose | VERIFIED (S1); S4 consistent with selector |
| `0x2004` | 3 | Brand (20) | `"<Dimplex>"` zero-padded (S1, S4) | VERIFIED |
| `0x2005` | 3 | HeaterType (6) | QRAD `"QRAD100E;D;"`; S4 empty | VERIFIED |
| `0x200A` | 3 | EpIdentifier (6) | QRAD `EP4099`; S4 `EP4120` (called "Firmware version" by S4) | VERIFIED |
| `0x2012` | 3 | FirmwareVersion (5) | absent on QRAD | name only |
| `0x1012`–`0x1015` | 0 | none | QRAD: 10× `00` each, R/W, unknown. **S4 (fireplace): schedule slots P1–P4 of the day selected through `0x1002`**, header `00 03 <day>` + 5 bytes + `00 00`; writes not tested | S4 VERIFIED (read structure) on Opti-myst only |
| `0x1029` | 3 | none | QRAD `b9 ff 96`, same as default backlight colour in `1/0x0001` | unknown |
| `0x000A` | 3 | PowerLoading (8) | QRAD `[e1 LE16, e2 LE16, 0…]` = debug screen G 1000 / H 2200. S4 `ff ff ff ff 00…` | QRAD VERIFIED |
| `0x000B` | 3 | none | QRAD int8 table `fb…f9…` ("Calibration curve?"). S4 13× `0x50` (`"PPPP…"`, "probably empty") | unknown |
| `0x000C` | 3 | none | QRAD int8 table `f9…f6…f1…`. S4 13× `0x50` | unknown |
| `0x1033` | 2 | Security (33) | absent on QRAD | name only |
| `0x300E` | 1 | Esp32OtaEnable (1) | absent on QRAD | name only |
| `0x100E` | 1 | OtaEnable (1) | QRAD `00` | name only |
| `0x700F` | ? | Disconnect | "ConfigR writes `[1]` here to make the radiator drop the link cleanly. Not present on the Q-Rad D." | name/usage VERIFIED (S1 decompile) |
| `0x0106` | 0 | SetRtcWithSeconds (8, W) | S4: "This is the register to write to set the clock" on the fireplace | VERIFIED (S4) |
| `0x1050` | 3 | none | QRAD `f3 10 00`. S4 constant 8-byte values | unknown |
| `0x4012` | 0 | HeatSetting (1, R) | S4: heater power `01` low / `03` high, R/W on the fireplace | VERIFIED (S4) |

S1 also notes a vendor UUID base for newer GDHV products:
`0000XXXX-000S-474C-4E44-494D504C4558`, whose tail is ASCII `GLNDIMPLEX`.
The Q-Rad uses the SIG base
([ble-protocol.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md#parameter-names-from-dimplexs-configr-app)).

### 3.1 Differences from the mapping this repo already uses

- **OTA service UUID differs by product.** **VERIFIED (S1 capture):** the
  QRAD100E D exposes **two** Cypress bootloader services,
  `00060000-f8ce-11e4-abf4-0002a5d5c51b` (write, notify) and
  `…-0002a5d5c51c` (write, notify, write-without-response). Neither is the `…c51d`
  seen on the PLX050E. S1 calls these "Cypress PSoC OTA bootloader. Do not
  touch." **NOT FOUND:** any Dimplex-specific source for `…c51d` /
  `00060001-…-c51d`. GitHub code search for that UUID returned only unrelated
  projects.
- **Radio chip.** **VERIFIED (S1 research.md, S6):** the Q-Rad / Quantum Hybrid
  UI board uses a Cypress **CY8C4248LQI-BL** (PSoC 4 BLE) module
  ([research.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/research.md)).
  S3 states that Creda advertised names starting `BL` mean "in bootloader".
  **INFERENCE:** `0x300E Esp32OtaEnable` in ConfigR implies that some GDHV
  products use an ESP32 radio. Which radio the PLXE uses is not stated by any
  third-party source.
- **Advertised-name prefixes.** **VERIFIED (S3, InteliApp):** a 2-letter prefix
  gives the product type: `AL` Aluminium, `AH`, `EC` Convector, `SZ`, `CV`, `FI`
  Fire, `QB`, `WC` Wall Controller, `BL` bootloader. `AM` is not in that list.
  **VERIFIED (S1):** the Q-Rad name prefix is per unit and "isn't simply the
  MAC" (one of two units matched its MAC tail).
- **Mode enum.** **VERIFIED (S3, InteliApp):** `1001[0]` values: 5 Boost,
  6 Standby (off), 7 Back-to-last-mode. `1001[3]` is a boost-duration enum.
  `0x100D` also has timed-boost values. On PSLFS6, mode 5 and the timed-boost
  values of `0x100D` were **ignored**. S1 never saw 5/6 on the Q-Rad.

## 4. BLE reliability, stalls and firmware notes

- **VERIFIED (S1):** one central at a time. Advertising persisted 10 min after
  disconnect at about 2 adverts/s. The Bluetooth setting and the bond both
  survive a power cut. The ESPHome node polls every 10 s and "has held its
  connection for hours with no keep-alive"; a truly idle link is untested.
  Without `esp_ble_set_encryption()` after discovery, reads fail with GATT
  status 15 and no passkey prompt appears. A failed pairing needs a forced
  disconnect after 45 s so that a fresh pairing starts
  ([ble-protocol.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/docs/ble-protocol.md#connection),
  [CLAUDE.md](https://github.com/philcluff/dimplex-qrad-smart-bridge/blob/main/CLAUDE.md)).
- **VERIFIED (S2):** the Sunhouse config writes the clock characteristic every
  3 s as a keep-alive "to maintain connection". ESP32 security settings:
  `io_capability: keyboard_only`, `ESP_LE_AUTH_REQ_SC_MITM_BOND`.
- **VERIFIED (S3):** InteliApp's connect sequence is: connect, wait 1 s,
  discover, MTU 276, bond on auth error, rediscover, write RTC, then reads.
  Connecting to two heaters at once "sometimes fails first time with status
  147; a retry works".
- **VERIFIED (S4):** the fireplace uses legacy pairing with no stored bond, so it
  re-pairs on every connection. One connection at a time.
- **Firmware versions seen:** QRAD100E D RF `05` (`0x2009`), UI "Rev 018" (S1).
  Creda PSLFS6 "firmware 51.0" (S3). Opti-myst `0x2009` = `05`, `EP4120` (S4).
- **NOT FOUND:** any third-party report of the heater radio stalling or going
  silent (invisible to all scanners until a power-cycle), or of a firmware fix
  for it. No source mentions radio stalls on Glen Dimplex BLE products.

## 5. Summary of answers

| Question | Answer |
|---|---|
| Characteristic holding the 12-hex appliance ID | `3/0x2002` (ConfigR `Gdid`, 6 bytes; screen/label form = bytes reversed). VERIFIED on QRAD100E D (S1); not checked on PLXE |
| Characteristic exposing the 6-digit label PIN | NOT FOUND. Candidates by name only: `3/0x2006 Btpk`, `3/0x2007 EncKey`, `3/0x2008 AuthKey`, `2/0x1033 Security`. On the Q-Rad the first three have no GATT properties (not readable) |
| Is the BLE passkey fixed per unit? | Yes on Q-Rad (S1), Sunhouse (S2, printed on a sticker) and Opti-myst (S4). Derived from the MAC on Creda Intelirad (S3). PLXE: not documented |
| `0x200D` meaning | Board / software-modification code on Q-Rad and Creda; a per-unit "serial" only on Opti-myst (S4) |

## 6. Searches with no Dimplex-relevant result

- GitHub code search: `DimplexToolkit`, `Dimplex ConfigR`, `PLX050E`, `AM2086`
  (only this repo); `QRAD100E` (this repo, plus S1 via references); `Dimplex
  bleak`, `Dimplex gatt`, `PGLHS600`, `PGPHS600`, `SecurityParameter Dimplex`,
  `Btpk` (no Dimplex hits); `EP4213`, `eyespyfx`, `gdble`,
  `00060001-f8ce-11e4-abf4-0002a5d5c51d`, `0002a5d5c51d` (unrelated hits only);
  `<Dimplex>` (found S4).
- GitHub repo search: `dimplex ble`, `dimplex bluetooth`, `dimplex qrad`,
  `dimplex quantum`, `dimplex esphome`, `glen dimplex`, `dimplex heater`,
  `configr`. New relevant hits were S3 only.
  [projectthirty3/home-assistant-glen-dimplex](https://github.com/projectthirty3/home-assistant-glen-dimplex)
  covers DuoCool fans and is not relevant.
  [AntonioJMM/dimplex-sierra-ble](https://github.com/AntonioJMM/dimplex-sierra-ble)
  is a Tuya BLE fireplace (service `0xFD50`), a different protocol.
- GitHub issue search `dimplex bluetooth`: nothing beyond this repo.
- Web: "Dimplex BLE gatt heater reverse engineering", "Dimplex Toolkit ConfigR
  bluetooth Home Assistant", "Dimplex GDID bluetooth characteristic",
  "Dimplex Q-Rad bluetooth ESPHome passkey", "reddit Dimplex panel heater
  bluetooth", "Dimplex Remo app BLE protocol", "Dimplex Opti-myst/Q-Rad/Quantum
  bluetooth ESP32 bridge". None returned a Dimplex BLE mapping beyond S1–S4. The
  HA Q-Rad thread (S9) has no BLE content; its latest post (2025-08-13) quotes
  Dimplex as having "no plans to add Matter support, or support for any other
  third-party smart home system".
- No Wireshark/btsnoop dissector, Gist, blog post or Reddit thread for Glen
  Dimplex BLE was found.
