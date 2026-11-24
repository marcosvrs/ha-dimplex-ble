# Dimplex / Glen Dimplex manual research

## Scope and evidence boundary

The repository's live ConfigR evidence identifies the commissioned device as `PLX050E;C;` (recorded in [`docs/capability-matrix.md`](capability-matrix.md)). This report therefore treats **PLX050E Series C** as the directly applicable product documentation, and treats Quantum storage-heater documentation as useful context only. A Quantum manual must not be used to claim that a PLX050E exposes storage charge, core temperature, element status, or Quantum reporting screens.

Evidence labels used below:

- **FACT** — stated in an official Dimplex/Glen Dimplex document.
- **SUPPORTED SEMANTICS** — the document defines the meaning/type of a value, but does not prove that a particular observed BLE field carries it.
- **INFERENCE** — a plausible interpretation of an observed value, requiring protocol/device evidence before implementation.
- **OPEN QUESTION** — not defined by the located manuals.

## Official sources located

1. **PLXE Series C Long Manual, Issue 7**, official Dimplex PDF: [PLXE - Series C Long Manual - Issue 7](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20-%20Series%20C%20Long%20Manual%20-%20Issue%207.pdf). Page 1 explicitly covers `PLX050E`, and pages 6–30 cover the product's operation, settings, service setting, and error codes.
2. **PLXE Series C Product Description, Issue 3**, official Dimplex PDF: [PLXE Series C Spec Sheet - Issue 3](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20Series%20C%20Spec%20Sheet%20-%20Issue%203.pdf). Page 1 gives model-specific output and dimensions.
3. **Quantum Series G.9 Installation and Operating Manual, Issue 2**, official Glen Dimplex product portal PDF: [Quantum G9 Installation User Instructions - Issue 2](https://product-portal.gdhv.com/sites/default/files/2023-01/Quantum%20G9%20Installation%20User%20Instructions%20-%20Issue%202.pdf). Pages 1 and 6–18 cover QM050RF–QM150RF operation, ratings, service-oriented settings, and fault codes.
4. **Quantum Series G.13 Installation and Operating Manual, Issue 3**, official Dimplex PDF: [08/83324/0-3 QMRF Online G13 23-023](https://www.dimplex.co.uk/sites/g/files/emiian551/files/media_import/medias/docus/14/08_83324_0-3%20QMRF%20Online%20G13%2023-023.pdf). Page 6 gives ratings; pages 33, 36–37 give advanced/service settings, wiring labels, and commissioning information.
5. **Quantum Storage Heater Reporting Screen Guide, Series G.6**, official Glen Dimplex product portal PDF: [Quantum Series G.6 Reporting Screen Guide - Issue 1](https://product-portal.gdhv.com/sites/default/files/Quantum%20Series%20G.6%20Reporting%20Screen%20Guide%20-%20Issue%201.pdf). This is the clearest official field dictionary for Quantum diagnostic/report screens, but it is explicitly **Series G.6**, not PLX050E Series C and not the commissioned AM6532 device.
6. Official product/support entry points searched: [Dimplex Manuals and Guides](https://www.dimplex.co.uk/support/manuals-and-guides), [Quantum](https://www.dimplex.co.uk/products/installed-heating/storage-heaters/quantum), and [PLXE Panel Heater](https://www.dimplex.co.uk/products/installed-heating/panel-heaters/plxe-panel-heater). No separate public PLX050E service manual or AM6532-specific protocol manual was found. The PLXE long manual's “Service Settings” section is an installer/service-user section, not a BLE protocol specification.

## Directly applicable PLX050E facts

The PLXE manual is the strongest product match because page 1 lists `PLX050E / PLX075E / PLX100E / PLX125E / PLX150E / PLX200E / PLXC300E / PLX125E SDM`.

- **FACT — temperature control:** page 6 describes a digitally controlled electronic thermostat accurate to ±0.2°C, with a setpoint range of 7–30°C. Page 12 says the minimum room temperature is 7°C, default maximum is 30°C, and 21°C is a common comfortable temperature. Page 13 says the Home Screen shows the current **target room temperature** and operating mode; it does not document a separate raw-room-temperature field on that screen.
- **FACT — modes/status:** pages 14–19 document timer modes, `Heating On`/`Heating Off` in timer profiles, Manual, Eco, and Frost Protect. Page 18 defines Frost Protect as maintaining 7°C. Page 13 says the display text sleeps after inactivity while the panel heater remains active; this is a documented reason for a visually blank display.
- **FACT — service setting:** page 25 says Advanced Settings are intended for trained professionals. Its “Room Temp (Temperature) Offset” is service-personnel-only and permits ±0.2°C adjustment for panels and storage heaters. This is not evidence that a raw BLE number is an offset or that it is safe to expose as a user control.
- **FACT — errors:** page 26 lists codes 20–24 (component thermistor hardware fault), 30/32/33/34 (component overheat), 40 (internal serial communications), 41 (heating element fault), 50 (open window), 52 (heat output disabled), 53 (time loss), 54 (occupancy sensor not detected), 55 (unknown), 60 (wireless module), and 61 (Hub connection). There is no documented “element status” value; only an element-fault code is documented.
- **FACT — output:** the PLXE spec sheet page 1 lists `PLX050E` output as **0.50 kW** (500 W), with dimensions 430 × 450 × 108 mm and weight 4.6 kg. The long manual page 6 gives the same PLX050E 0.50 kW nominal and maximum heat output.
- **FACT — no Quantum storage diagnostics:** unlike the Quantum specification/manual, the PLXE manual/specification does not document a storage-core charge level, core sensor temperature, off-peak charge windows, storage energy, or Quantum Report Screen 1/2/3 fields.

## Quantum diagnostic/status fields found

These are useful for understanding the labels seen in ConfigR resources, but they are not proof that AM6532/PLX050E supports them.

### Reporting Screen Guide (Quantum Series G.6)

The official G.6 guide defines the following:

- **Front Screen, page 1:** active mode, Advance status, desired room-temperature setpoint, and timer-profile heating status (`Heating On`/`Heating Off`).
- **User Info Screen, page 2:** GDID, RF state, test PIN, Open Window (`OW`), Adaptive Start (`AS`), Setback (`SB`), UI/charge-controller/RF software revisions, off-peak availability counter `O` in minutes, daily runtime `R`, and morning/afternoon runtime. The guide explicitly warns that `O` is available off-peak supply minutes, **not heater charge time**.
- **History screens, pages 3–8 and 13–17:** seven-day daily runtime, off-peak energy use (kWh), off-peak availability minutes, tariff periods, maximum/minimum room temperature, programmed heating hours, midnight room temperature/setpoint, maximum core temperature, and adaptive-start equivalent charge time.
- **Report Screen 1, page 9:** line A includes seasonal bands, rated power in watts, and extra-charge hours; line C includes current room-sensor temperature and midnight room-sensor temperature; line D includes programmed heating hours, current-period heat demand, daily energy requirement, and standing loss; line E includes current setpoint, midnight setpoint, and calculated residual energy; line F includes core temperature, midnight core temperature, setback energy requirement, and learned off-peak hours; lines G/H include adaptation and runtime history.
- **Report Screen 2, page 10:** line A is actual/adjusted core temperature; line B is calculated brick temperature and stored-energy value; line C is energy losses; line D is total-energy maximum and energy cap; line E is maximum stored energy and **current charge level (%)**; line F is residual-energy values; line G is standing loss; line H is optimized critical/incremental runtime.
- **Report Screen 3, page 11:** raw room-sensor temperature, additive/subtractive offsets, displayed offset, fixed/user offset, adjusted room temperature, setpoint difference, DSM communication mode (1–4), and communication mode (`0 = standalone`, `1 = comms mode`).

The G.6 guide therefore proves that “charge level” and several temperature/runtime/energy fields existed in that Quantum reporting-screen generation. It does **not** prove those fields or meanings for a PLX050E Series C BLE characteristic.

### Quantum G.9/G.13 manuals

- **FACT — Quantum model scope:** G.9 page 1 and G.13 page 1 cover QM050RF, QM070RF, QM100RF, QM125RF, and QM150RF—not PLX050E.
- **FACT — Quantum ratings:** G.9/G.13 page 6 lists 0.50–1.50 kW nominal output, model input ratings from 936/1020 W through 3024/3300 W, boost outputs, storage capacity, and 11 W fan/0.5 W UI power. The G.13 technical table lists QM100RF input as 2042/2220 W; the separate Dimplex Control catalogue page 10 lists QM100RF input as 2220 W and output as 1000 W.
- **FACT — Quantum service settings:** G.13 page 33 documents Room Temp Offset ±3.0°C, Secondary Heater Mode, Heat Source Setting, charge times, and Factory Reset. These are for trained professionals/service personnel and are not PLX050E semantics.
- **FACT — Quantum faults:** G.9 page 18 and G.13 page 18 list component thermistor, overheat, internal serial communications, heating-element fault, open-window, heat-output-disabled, time-loss, unknown, wireless-module, and Hub-connection faults. Again, this is a fault-code list, not an element on/off status field.
- **FACT — 120° hardware label:** the G.13 circuit diagram on page 36 labels a green “LIMIT STAT (120°)” and a black “LIMIT STAT (125°)”, alongside 140° manual cutout and 145° lower/left/right stats. This is a thermal-protection schematic label, not a documented room/core temperature telemetry value.

## Observed-value mapping

| Observed value | What official manuals support | Classification / conclusion |
|---|---|---|
| `19.4` | Room-temperature values are documented concepts. PLXE page 12 describes room-temperature control; Quantum G.6 pages 9 and 11 define current/raw/adjusted room-sensor temperatures and show decimal examples such as 18.9 and 19.7°C. | **SUPPORTED SEMANTICS, not field proof.** `19.4` is consistent with a Celsius room-sensor/adjusted-room-temperature value. The manuals do not identify a PLX050E BLE field carrying this exact number. |
| `20.0 / 19.4` | PLXE page 13 documents a target room-temperature value on the Home Screen. Quantum G.6 page 1 calls the displayed value the desired setpoint; page 9 separates current room sensor temperature (line C) from current setpoint (line E). | **SUPPORTED SEMANTICS / INFERENCE for pairing.** A pair of target `20.0°C` and room `19.4°C` is plausible, but the official PLX manual does not document that these two values are emitted together or in that order. Keep ConfigR/live characteristic labels as the authority. |
| `7 → 0` | `7°C` is FACT as the PLX/Quantum Frost Protect target (PLXE page 18; Quantum G.9 page 16). `0` is FACT only in specific Quantum G.6 contexts: afternoon runtime can be zero (page 2) and communication mode 0 means standalone (page 11). | **OPEN QUESTION.** No official manual defines a `7→0` transition or pair. Do not call it Frost-to-off, a boolean, or an error without a characteristic label and repeatable state trace. |
| `1 → 0` | Quantum G.6 page 11 documents DSM comms mode values 1–4 and a separate comms-mode value where 0 means standalone and 1 means comms. | **OPEN QUESTION.** The two documented fields could produce 1 and 0 in different positions, but the manuals do not define an arrow/transition representation. No PLX050E mapping is established. |
| blank / empty | PLXE page 13 and Quantum G.9/G.13 page 10 state that display text sleeps/disappears after inactivity while the panel heater remains active. | **SUPPORTED UI explanation only.** A physically blank display can be normal sleep. A blank BLE/raw value is not defined by these manuals and must remain unknown/absent rather than being converted to zero or “off.” |
| `120` | Quantum G.6 page 9 includes an example critical runtime of 120 minutes (Report Screen 1 line B). Quantum G.13 page 36 labels a 120° thermal limit-stat in the circuit diagram. | **AMBIGUOUS.** Both are official meanings in different contexts. There is no basis to choose runtime minutes versus a thermal-stat label for an AM6532 observation. It is not evidence of a live temperature or element status. |
| `500` | PLX050E is explicitly rated 0.50 kW / 500 W (PLXE spec p. 1; long manual p. 6). Quantum QM050RF is also 0.50 kW nominal (Quantum G.9/G.13 p. 6). | **FACT as rated output when the field is model metadata.** It is not evidence of instantaneous power, charge level, or a measured energy value. For the identified PLX050E, 500 W is the documented rating. |
| `2200` | Quantum QM100RF is documented with 2220 W input (G.13 p. 6; Dimplex Control catalogue p. 10). | **INFERENCE only.** `2200` could be a rounded display of 2220 W for a QM100RF, but that is not the identified PLX050E (which is 500 W). No official PLX050E document assigns 2200 to a field. |

## Conclusion for the integration

1. The official PLXE Series C manual confirms the AM6532 identity's product family and supports temperature setpoint, timer/mode, Frost Protect (7°C), display sleep, service offset, and error-code concepts.
2. The official Quantum reporting guide documents rich charge/core/energy/runtime diagnostics, including a current charge-level percentage, but it is Series G.6 Quantum documentation. Quantum G.9/G.13 manuals mention History/Reporting Screens but do not provide a PLX050E protocol map.
3. The only observed value that can be mapped directly as a PLX050E model datum is **500 W rated output**. `19.4` and `20.0/19.4` are semantically consistent with room/setpoint temperatures, but their BLE field mapping remains ConfigR/live-evidence work. `7→0`, `1→0`, blank, `120`, and `2200` remain ambiguous or unsupported as AM6532 meanings.
4. No official manual defines a PLX050E “element status” telemetry field. The safe documented substitute is only the error code `41 = heating element fault`; do not expose an element-status entity from a guessed value.
5. No official manual found defines AM6532/PLX050E charge level, core temperature, storage energy, runtime, or instantaneous power characteristics. Do not promote Quantum diagnostic labels to AM6532 entities without repeatable device-level mapping and readback.

## Remaining questions

- Which exact AM6532/PLX050E characteristic and payload produced each observed value?
- Are `19.4` and `20.0` raw/adjusted room temperature and target setpoint, or values from two unrelated reads?
- Are `7→0` and `1→0` two fields, old/new values, or display formatting of a single payload?
- Does “blank” mean UI sleep at the product, an empty optional field, or a missing/unsupported BLE response?
- Is `120` a runtime (minutes), a thermal-limit label, or another undocumented raw value?
- Is `2200` a rounded/incorrect model rating, an instantaneous measurement, or a protocol value unrelated to power?
- If ConfigR exposes Quantum charge/element labels for a generic profile, does the AM6532 profile actually bind them to a PLX050E characteristic? The manuals provide no answer.

---

# Extension (2026-11-24): pairing, mode definitions, troubleshooting, firmware, identification

Page references in this extension use **PDF page numbers** (the printed page number is one lower), matching the convention above. Local copies of the PDFs were saved for reading under the session scratchpad (`artifacts/dimplex-docs/`); the claims below were checked against extracted text of those files, not against search-engine summaries. Search-engine summaries were used only to locate documents, and one of them (a claim that the PLXE has a "Bluetooth capacitive button, hold 3 s") was traced to the **PLXENC** document and is *not* a PLXE statement (see Q1).

Additional official sources used (all Glen Dimplex / GDHV hosted):

7. **Q-Rad RF Instruction Manual, Series G, Issue 3** (08/82904/0): [QRAD RF Instruction Manual Series G - Issue 3](https://www.dimplex.co.uk/sites/g/files/emiian551/files/media_import/medias/docus/13/QRAD%20RF%20Instruction%20Manual%20Series%20G%20-%20Issue%203.pdf).
8. **PLXENC Series A Short Instructions / Quick Start, Issue 4**: [PLXENC - Series A Short Instructions - Issue 4](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXENC%20-%20Series%20A%20Short%20Instructions%20-%20Issue%204.pdf). PLXENC is the "no onboard controls" variant of the PLXE (p. 3).
9. **FX20VEW Installation and Operating Instructions, Series A, Issue 1** (a ConfigR-paired fan heater, used only as an example of the vendor's ConfigR/PIN label convention): [300004944 FX20VEW Instructions Issue1 SeriesA](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2026-01/300004944%20FX20VEW%20Instructions%20Issue1%20SeriesA.pdf).
10. **ConfigR product page / FAQ**: [dimplex.co.uk ConfigR](https://www.dimplex.co.uk/products/smart-controls/configr) and [dimplex.eu ConfigR](https://www.dimplex.eu/en-de/electrical-heating/waermespeicher/configr).
11. **CapaConnect page / FAQ** (a different Dimplex Wi-Fi product line, Alta/AluRad): [dimplex.co.uk/capaconnect](https://www.dimplex.co.uk/capaconnect).
12. **Dimplex Control compatibility page**: [dimplex.co.uk/dimplexcontrol/compatibility](https://www.dimplex.co.uk/dimplexcontrol/compatibility).
13. **Dimplex Support, "Forgot PIN lock code on my panel heater"**: [support.dimplex.co.uk article](https://support.dimplex.co.uk/forgot-pin-lock-code-on-on-my-panel-heater-how-can-i-reset?journey=consumer).
14. **PLXE Series C Spec Sheet Issue 3** and **PLXE Series B Spec Sheet Issue 1**: [Series C](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20Series%20C%20Spec%20Sheet%20-%20Issue%203.pdf), [Series B](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2024-03/PLXE%20Series%20B%20Spec%20Sheet%20-%20Issue%201.pdf) (the Series B file is image-only; its contents were not text-extractable).
15. **Dimplex Control product catalogue 2021**: [iot_product_catalogue_2021.pdf](https://www.dimplex.co.uk/sites/g/files/emiian551/files/2023-08/iot_product_catalogue_2021.pdf).

Not usable: the URL previously cited for "Identifying Quantum+Hub Series G6 onwards" now returns an HTML page, not the PDF.

## Q1. Bluetooth pairing procedure (PLX/PLXE Series C)

**Headline: no official document located describes a Bluetooth pairing / PIN procedure for the PLXE Series C with an on-board controller.** The PLXE Series C manual (Issue 7, also Issue 6) only says Bluetooth is for software updates; the Series C spec sheet says the same.

- **FACT, PLXE Series C Long Manual Issue 7, PDF p. 7 (printed p. 6) and Spec Sheet Issue 3 p. 1:** the controller lists "Bluetooth for software updates". The manual contains **no** Bluetooth pairing section, no pairing timeout, no bond limit, no "remove pairing" menu item, and no mention of a Bluetooth PIN. The word "PIN" in the manual refers only to the 4-digit **Heater Lock** (PDF pp. 20-22) which is a UI lock, unrelated to BLE security.
- **FACT, same manual, PDF p. 23 (printed 22) and p. 11 (printed 10):** a "Comms" settings item enables/disables "Wireless Connectivity features" and lists "available communications methods"; RF comms must be enabled for Hub connection. The manual does not say whether Bluetooth is among the toggles. The manual does not describe what happens on Factory Reset beyond "restore the default values to all settings" (PDF p. 25), so **the effect of Factory Reset on BLE bonds is not documented**.
- **FACT, same manual, PDF p. 20:** while a Product Lock is active, "any/all wireless communications remain active if enabled". So locking the UI does not disable BLE.
- **Contradiction check against the repo's AGENTS.md assumptions:** the vendor never documents "no separate pairing mode" for the PLXE; it is simply silent. The PLXENC (the controller-less variant, same family) does document a **pairing mode**: PLXENC Short Instructions Issue 4, PDF p. 3: hold the Bluetooth capacitive button 3 seconds, the Bluetooth LED flashes, stays lit once paired, and "If no connection is made within 2 minutes, the product will stop broadcasting". This is PLXENC behaviour; the PLXE Series C has no such button in its documented UI (six touch buttons: Menu, Back, Up/Down, Enter, Advance; PDF p. 12). Do not transfer the 3 s / 2 min rule to the PLXE without device evidence. If the repo's own evidence is that the PLXE is always connectable, that is consistent with the PLXE manual being silent but is not confirmed by it.
- **PIN: fixed-per-unit label PIN is the vendor convention for the ConfigR/BLE product family, but is not stated for the PLXE.**
  - FX20VEW Issue 1 (ConfigR family), PDF pp. 2-3: the product label carries `GDID: <12 hex>`, `ID: FXNNNN <Dimplex>` and `Pin Code: NNNNNN` (6 digits); the app asks for "the Bluetooth PIN" when pairing starts, and the user may scan the QR code or type GDID plus PIN manually. This indicates a **fixed, printed, 6-digit PIN per unit** for that product, and an advertised name of the form `<product code><Dimplex>`.
  - CapaConnect FAQ (different product line, Wi-Fi heaters): the PIN is "a 6 digit number" that is "printed on the rating label or can be read of the products controller" (and troubleshooting says use the PIN on the label at the back). This is the only official text that says a PIN can be *read from the controller display*, which is consistent with the repo's observation that the PLXE shows a six-digit PIN on its display, but it is not about the PLXE.
  - ConfigR product page FAQ and the dimplex.eu ConfigR page: pairing is by GDID (QR or manual); neither page mentions a PIN, a bond limit, or a removal procedure.
  - Not found in official sources: whether the PIN is regenerated per pairing attempt versus fixed; any timeout on the on-screen PIN; the maximum number of paired/bonded centrals; how to remove a bond on the heater; whether the heater stops advertising while connected; any documented connection limit. Queries tried: "Dimplex PLXE Bluetooth pairing PIN", "ConfigR PIN controller pairing", "Dimplex Quantum Bluetooth PIN displayed", "Dimplex ConfigR compatibility list GDID", site searches over dimplex.co.uk / support.dimplex.co.uk / product-portal.gdhv.com / dimplex.eu. The support site's search folders for panel heaters and Dimplex Control were browsed; none had a BLE-pairing article for PLX.
- **Client-side (phone) guidance, ConfigR/CapaConnect (not PLX-specific):** the ConfigR app needs Bluetooth 4.0 (BLE), Android Marshmallow or iOS 10 minimum. CapaConnect troubleshooting: if pairing fails, remove any "Dimplex" entry from the phone's Bluetooth settings and retry; on Android 7+ location services must be on to scan for BLE.
- **FX20VEW, for contrast only (PDF p. 3):** "Pairing Again Later" needs the user to hold the Mode button 5 s; "You only need to hold the button again if pairing with a new phone or device." This implies that at least in that product, an already-paired central reconnects without a button action and a new central needs an explicit enable gesture. It says nothing about the PLXE.
- **PIN-lock reset (unrelated to BLE):** the Dimplex support article lists PLX050E-PLX150E among models whose 4-digit PIN lock can only be reset by contacting Dimplex, who supply an unlock PIN. It does not mention Bluetooth.

## Q2. Operating modes as documented (PLXE Series C Issue 7 unless noted)

- **Mode list (PDF p. 7 / p. 15):** timer modes User Timer, Home All Day, Out All Day; Away; continuous modes Manual, Eco, Frost Protect; additional Boost and Setback. The menu path is Menu > Modes > Timer Mode / Manual Mode / Eco Mode / Frost Protect (PDF p. 15, 18).
- **Manual (PDF p. 18, printed 17):** default 21 C; "can be changed from the Home Screen to any value within the Heater's Temperature Range" (setpoint range 7-30 C, PDF p. 7). **No lower bound such as 19 C is documented for the PLXE Manual mode.**
- **Eco (PDF p. 18):** a **continuous heat mode with its own user-adjustable target**, not a fixed offset and not an ecodesign indicator. Default **19 C**; "can be changed ... to any value in the Heater's minimum temperature range (8-18 C)". Same text in Issue 6. The manual is internally inconsistent: the default (19 C) is outside the stated adjustable range (8-18 C). No document defines an "Eco indicator" or an "Eco <= 19 C" threshold for the PLXE. The Ecodesign table (PDF p. 6) lists no eco-mode parameter.
- **Related-family contrast (Q-Rad RF Series G Issue 3, PDF p. 13, printed 12):** Manual "Default 21 C ... any value within the range **19-30 C**"; Eco "Default **18 C** ... range **8-18 C**"; Frost Protection 7 C, not user definable. The Q-Rad Series G therefore documents a **partition of the setpoint range at the 18/19 boundary between Eco (8-18) and Manual (19-30)**, which is the only official text found that ties a numeric threshold to Eco vs Manual. The PLXE Series C manual dropped the "19-30" wording for Manual and changed Eco's default to 19.
- **Frost Protect (PDF p. 18):** default 7 C, not user definable. Away default also 7 C (PDF p. 17).
- **Timer defaults (PDF p. 15):** User Timer 06:30-09:30, 11:00-13:00, 15:00-17:00, 18:00-22:00 at 21 C; Home All Day 08:00-21:00 at 21 C; Out All Day 07:30-08:00 and 17:30-22:00 at 21 C; four periods per day, per weekday; the setup wizard starts the unit in Out All Day (PDF p. 10). A timer must be selected after being modified (PDF p. 16).
- **Away (PDF p. 17):** temporarily overrides the active mode and then returns to the previous mode; set as hours + a return date + a target temperature (default 7 C). The entered date is the *return* date and Away ends at 23:59 of the day before; hours extend that end; the Back button ends Away early. "Away Mode does not operate in the same way as the other Timers" (PDF p. 15).
- **Boost (PDF p. 19, 23):** activates from the Menu at any time in any timer or continuous mode, with Boost Temp (default 25 C) and Boost Time (30 min, 1, 2, 3, 4 h), restricted by Max Boost Duration; at expiry returns to the previous mode; Back ends it early.
- **Setback (PDF p. 23):** only modifies timer-mode operation; overrides Heating Off periods to hold a constant temperature restricted to 7-18 C.
- **Advance (PDF p. 17):** only in timer modes; toggles the current Heating On/Off period until the next period boundary.
- **Adaptive Start (PDF p. 24):** only affects timer modes; pre-heats so the target is reached when the period starts; learned continuously; two-hour cap on the pre-heat calculation; all heaters in a room should have it on or off together.
- **Open Window Detection (PDF p. 24):** a global setting that can interrupt any active mode and switch the heater to Heating Off; may trigger falsely in drafty or undersized situations; fault code 50 is "open window detected" (PDF p. 26).
- **Keypad locks (PDF pp. 20-22):** Child Lock (hold Back + Enter 3 s, no PIN) and Heater Lock (4-digit PIN, with optional Advance/Boost exceptions). Neither disables wireless communications.
- **Sound (PDF p. 23):** button audio feedback on/off, also asked in the first-run wizard (PDF p. 10).
- **Display units (PDF p. 25):** Advanced Settings (hold Advance + Menu 5 s) include Temp Units, default C, F selectable; values are converted and rounded to the nearest number on the display. Service settings: Room Temp Offset +/-0.2 C (panels), Factory Reset.
- **Display sleep (PDF p. 13):** text disappears after inactivity; the heater stays active.

### Implication for the observed "Manual setpoint <= 19 C reports Eco" behaviour

- No Dimplex document states that the heater reclassifies a Manual setpoint at or below 19 C as Eco, or that Eco is derived from the setpoint. Searches for any such rule in the PLXE manual, spec sheets, Q-Rad Series G manual and Quantum G.13 manual found none (Q-Rad has the closest, a 19-30 / 8-18 partition).
- What the documents do support: (1) Eco is a first-class continuous mode with default 19 C (PLXE) or 18 C (Q-Rad G); (2) the PLXE Eco default of 19 C coincides with the observed threshold, so a firmware rule that maps "continuous mode with setpoint <= 19 C" to Eco, or that stores Manual targets below the Manual floor as Eco, would be consistent with both the PLXE default and the Q-Rad 19-30 Manual range (**INFERENCE**, not documented); (3) the manuals describe Eco's user range as 8-18 C, which does not match a "<= 19 C" cutoff exactly, so the observed 19 C boundary cannot be explained by the manual's stated range alone.
- Design consequence: treat the heater-reported mode as authoritative and do not assume a Manual write at <= 19 C will read back as Manual. Prefer verifying by read-back rather than relying on a documented rule, and do not label this as a vendor-documented behaviour.

## Q3. Official troubleshooting (Bluetooth / reset / limitations)

Not found in official sources for the PLXE Series C: any Bluetooth-not-visible/not-connecting article, recommended power-cycle for BLE, or known BLE limitations. Queries tried: "Dimplex PLXE Bluetooth not connecting", "Dimplex ConfigR troubleshooting Bluetooth", "Dimplex panel heater app connection troubleshooting", plus browsing of the support.dimplex.co.uk panel-heater troubleshooting folder (items were error-code and CPH/DAR articles, none about BLE).
Closest official material, none PLX-specific:
- CapaConnect FAQ: remove "Dimplex" entries from the phone's Bluetooth list and retry; Android location services required; leaving the room during setup may drop the Bluetooth link and require restarting.
- PLXE manual PDF p. 26: fault 60 "Wireless communication module error" and 61 "Hub connection error" (RF module/Hub, not BLE).
- FX20VEW p. 4: a factory reset gesture exists for that product (hold Mode + pull cord over 10 s) and not for PLXE.
- PLXE Factory Reset (PDF p. 25): menu-only, restores defaults; BLE-bond effect not stated.

## Q4. Firmware updates and range/interference

- **FACT (ConfigR page / dimplex.eu ConfigR FAQ):** after pairing over BLE, the ConfigR app's Support screen notifies when an update is available and "update now" starts an over-the-air update over BLE. The PLXE Series C manual and spec sheet say "Bluetooth for software updates", so a BLE OTA path via ConfigR is documented for the family; the PLXE is **not named** on the ConfigR pages located (named products: Dimplex Wall Controller, PLXENC, FX20VEW, FX20WIPX4, AC3W), and the pages defer to a compatibility list that was not reachable.
- **Dimplex Control compatibility page** lists "PLXE Panel Heater (Series B)" PLX050E-PLXC300E as Hub + RFM; it does not list Series C and says Bluetooth is used between the app and the Hub, not to the heater.
- During a CapaConnect software update the display and buttons may be unresponsive for a few minutes (CapaConnect FAQ, different product).
- Not found in official sources: any range, RF power, antenna, or interference guidance for the PLXE BLE radio; no RED declaration of conformity or Bluetooth radio data for PLXE located (Series C spec lists approvals UKCA, CE, BEAB only; the DWC manual carries a generic RED statement). The CapaConnect FAQ gives no distance figure either.

## Q5. Model identification

- **PLXE Series C naming (Long Manual Issue 7 PDF p. 1, spec sheet p. 1):** models PLX050E, PLX075E, PLX100E, PLX125E, PLX150E, PLX200E, PLXC300E, PLX125E SDM; spec sheet item/EAN suffix for PLX050E is 090768 (EAN 5011139+item number). "Series C" is the document/product-generation letter (manual "ISSUE: 7 Series: C", spec "Series C - Issue 3"), and there is also a PLXE **Series B** spec sheet (Issue 1, 2019) and a Dimplex Control compatibility page that lists PLXE **Series B** only. Document issues 6 and 7 of the Series C manual have the same mode text.
- **BLE name / identifiers:** the only official text tying the advertised name format to a label is the FX20VEW label template `ID: FXNNNN <Dimplex>` next to GDID and a 6-digit Pin Code (PDF p. 2). That supports reading `AM####<Dimplex>` as a platform/controller ID of the same pattern (two letters plus four digits plus `<Dimplex>`), but **no document maps `AM####` to PLX/PLXE models**, and the repo's own read of `3/0x2005 = PLX050E;C;` and `3/0x200A = EP4213` is not described in any Dimplex document located. "Series C" appears in the repo's `PLX050E;C;` string as the trailing `C`, consistent with the manual's series letter (INFERENCE).
- **Related ranges:** PLXENC (Series A) is the "same full feature-set as PLXE without on-board controls" and is ConfigR/DWC controlled (Short Instructions p. 3). Q-Rad Series G (RF) uses a very similar controller UI and mode set (see Q2); Quantum G.13 shares the controller family (Child/Heater lock, Boost, Comms menu). Whether Q-Rad or Quantum share the AM-class BLE platform, GATT layout or pairing flow is **not documented** in the official sources found.

## Summary of contradictions / cautions against earlier sections

- The earlier note says pages 14-19 document Eco and Frost Protect without defining Eco; the primary source (PDF p. 18) defines Eco as a continuous mode with default 19 C and user range 8-18 C. This refines rather than contradicts the earlier note.
- No earlier conclusion is contradicted by a primary source. The repo's AGENTS.md statement that the heater has no separate pairing mode is neither confirmed nor contradicted by the PLXE manual (silent); only the PLXENC variant documents a pairing mode (3 s button hold, 2 min advertising timeout).
