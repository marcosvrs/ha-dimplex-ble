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
