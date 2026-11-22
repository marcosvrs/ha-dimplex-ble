# Dimplex BLE protocol-field research

**Scope.** This is a field-by-field evidence matrix for the commissioned AM6532 panel heater identified as `PLX050E;C;` (PLX050E Series C). It separates repository facts, live observations already recorded in the repository, ConfigR labels, official Dimplex documentation, and unresolved questions. A ConfigR label is not a PLX050E packet mapping. No field is promoted to a control without repeatable device evidence and reversible readback.

## Evidence sources and limits

| Source | Citation | What it can establish |
|---|---|---|
| Protocol/client implementation | `custom_components/dimplex_ble/protocol.py:9-41,52-70,73-109,125-170,172-248`; `custom_components/dimplex_ble/client.py:54-63,240-287,313-383` | UUID construction, safety gates, decoded fields, selector write/read exchange, and currently polled addresses. |
| Repository live inventory and observations | `docs/capability-matrix.md:9-18,20-59,61-88`; `docs/configr.md:89-126` | The repository's recorded AM6532 reads, payloads, statuses, and implementation boundary. These are **LIVE OBSERVATION** as recorded, not new measurements here. |
| ConfigR extracted artifact | Artifact workspace `/tmp/configr-apk/`; native strings `/tmp/configr-apk/native/configr-utf16-l.txt`, `/tmp/configr-apk/native/all-strings.txt`, `/tmp/configr-apk/native/ConfigR-candidate.dll`; repository interpretation `docs/configr.md:1-14,89-105` | Product-profile terminology and lifecycle/resource labels. The Native AOT extraction does **not** recover a proven AM6532 GATT packet sequence. |
| Official PLXE documentation | `docs/dimplex-manual-research.md:14-32` | PLX050E rating, temperature/mode/error concepts, and explicit limits of the manual. |
| Official PLX report-screen support page | URL recorded in `docs/configr.md:107-114` | Report-screen field C is output power; the repository records correlation with the live Trac response. |

**ConfigR availability:** the extracted workspace is available at `/tmp/configr-apk/`, but the artifact is Native AOT. The repository records recovered resource terminology; no artifact path in the workspace proves a characteristic-number-to-payload mapping for AM6532. Treat all ConfigR-only names below as **CONFIGR FACT / UNKNOWN DEVICE MAPPING**.

## ConfigR artifact references found for the unknown fields

The extracted ConfigR Native AOT string store contains protocol/profile labels
that are relevant to several live raw payloads. The strongest direct evidence is
the constant cluster at
`/tmp/configr-apk/native/assembly-store.strings:184093-184109`:

| ConfigR artifact label | Related live investigation area | What the artifact proves |
|---|---|---|
| `OPENWINDOWDETEC` | `0/0x1007` | ConfigR has an open-window-detection concept; it does not prove this characteristic number or bit layout |
| `EXCESSIVEROOMTEMP` | Room-temperature/fault diagnostics | ConfigR has an excessive-room-temperature concept; no live AM6532 characteristic mapping was recovered |
| `LCD_THERMISTOR` | `1/0x0008` selector 2 | ConfigR has an LCD thermistor concept; it does not prove the selector-2 payload's exact sensor identity |
| `CHARGE_TIMES` | `0/0x1002` / schedule-like data | ConfigR has charge-time configuration; it does not prove the AM6532 zero buffer layout |
| `MIN_SETBACK` | `0/0x1016` and related setback behavior | ConfigR has a minimum-setback concept; it does not prove the payload encoding or characteristic mapping |
| `MANUAL`, `ECO`, `BOOST`, `AWAY` | `0/0x1001` mode payloads | ConfigR has these product modes; it does not prove numeric mode-byte assignments |
| `WIRELESSCOMM`, `RF` | `2/0x100c` and RF metadata | ConfigR has RF communication/status concepts; it does not prove that `01` is the live field's complete meaning |
| `MUTE`, `VSound` | `2/0x0005` candidate sound setting | ConfigR has sound/mute concepts; it does not prove this raw payload's semantics |

The extracted text also contains behavioral descriptions:

- ConfigR describes Advance as moving the next scheduled on/off period.
- ConfigR describes Timer as three weekly profiles with four heating periods per
  day, and describes Setback as a minimum temperature outside scheduled periods.
- ConfigR describes Manual, Eco, Frost, and Boost as distinct heating modes.
- ConfigR exposes RF status, RF version, and nominal output in product diagnostics.

These descriptions occur in the localized resource text at
`assembly-store.strings:188627-188696`,
`:188808-188822`, and `:188990-189112`. They are useful semantic references,
but the extracted artifact does not contain a readable characteristic-address
table connecting those names to the live AM6532 GATT groups.

Therefore the current conclusion is:

**Yes, ConfigR references the concepts behind several unknown values. No, the
available ConfigR extraction does not directly map those concepts to the
unknown characteristic addresses.** Numeric mapping still requires controlled
device experiments or a recovered/decompiled protocol implementation.

## Controlled raw-write trials: 20 November 2026

The following live tests used the administrator-only raw-GATT action. Each
mutated value was immediately restored, then re-read after a delay. These tests
establish write acceptance/readback behavior only; they do not assign a field
meaning from a single payload.

| Address | Trial | Immediate result | Delayed restore | Conclusion |
|---|---|---|---|---|
| `0/0x1007` | `010000000000000000 → 000000000000000000` | Trial read back exactly | Restored exactly | Writable configuration payload; open-window semantics still unknown |
| `0/0x1008` | `01 → 00` | Trial read back exactly | Restored exactly | Writable configuration payload; pre-emptive-heating semantics still unknown |
| `0/0x100d` | `00 → 01` | Read back `00` | `00` | No persistent state change observed; command/status meaning remains unknown |
| `0/0x1016` | `0008 → 0009` | Trial read back exactly | Restored exactly | Writable two-byte payload; units and setback semantics still unknown |
| `0/0x1017` | `017c → 017d` | Trial read back exactly | Restored exactly | Writable two-byte payload; encoding and runback semantics still unknown |
| `0/0x1027` | `0000 → 0000` | Read back exactly | Unchanged | No mutation attempted; countdown behavior remains unknown |
| `0/0x1028` | `1500 → 1600` | Trial read back exactly | Restored exactly | Writable setpoint-like payload; temporary-mode semantics still unknown |
| `0/0x102a` | Same-value write | Read back exactly | Unchanged | No mutation attempted; Away semantics remain untested |
| `2/0x0005` | `000000 → 010000` | Trial read back exactly | Restored exactly | Writable payload; sound/mute semantics remain unknown |
| `2/0x100a` | `01 → 00` | Trial read back exactly | Restored exactly | Writable payload; semantics remain unknown |
| `2/0x100b` | `05 → 06` | Trial read back exactly | Restored exactly | Writable payload; semantics remain unknown |
| `2/0x100f` | `00 → 01` | Trial read back exactly | Restored exactly | Writable payload; semantics remain unknown |
| `2/0x1018` | `00 → 01` | Trial read back exactly | Returned to `00` after delay | Delayed persistence/readback behavior; semantics remain unknown |
| `2/0x1019` | `00 → 01` | Trial read back exactly | Returned to `00` after delay | Delayed persistence/readback behavior; semantics remain unknown |

Writes to every protected candidate (`0/0x1012–0x1015`,
`1/0x1010`, `1/0x1020`, `1/0x300e`, `2/0x100c`, `2/0x1030`,
`2/0x1033`, `2/0x1034`, `2/0x700f`, `3/0x000a`, `3/0x1010`,
`3/0x1037`, and `3/0x2002`, `3/0x2006–0x2008`, `3/0x200f`) were rejected
by the integration validation layer before reaching the panel heater. No protected
payload was sent.

## Service and UUID groups

| Group/service | Known address form | Safe/protected status | Evidence and open question |
|---|---|---|---|
| Group 0 | `00000000-0000-1000-8000-00805f9b34fb` (or GDHV candidate); characteristic `0000xxxx-0000-1000-8000-00805f9b34fb` | Group accepted by `validate_raw_access`; individual addresses below determine read/write safety. | **FACT:** `protocol.py:73-98`. Main control/configuration fields are in this group. |
| Group 1 | `...0001...` | Group accepted; individual addresses below. Selector fields require write-then-read. | **FACT:** `protocol.py:73-98`; **FACT:** `client.py:240-262,272-275`. |
| Group 2 | `...0002...` | Group accepted; `2/0x100C` is readable but write-blocked; other protected writes listed below. | **FACT:** `protocol.py:20-40`; live inventory confirms groups 0–3 (`docs/capability-matrix.md:124-126`). |
| Group 3 | `...0003...` | Group accepted; identity/rated output and metadata are readable; several security/bootloader/update addresses are blocked. | **FACT:** `protocol.py:14-40`; live inventory confirms presence. |

## Field matrix: documented safe reads and implemented mappings

Statuses: **FACT** = source states it directly; **LIVE OBSERVATION** = repository-recorded device result; **INFERENCE** = interpretation requiring further evidence; **UNKNOWN** = no proven mapping.

| Address | ConfigR/repository label | Payload / observed value | Read/write safety | Current conclusion and verification question |
|---|---|---|---|---|
| `0/0x1001` | Operating mode | Raw payload retained as hex; mode `03` accepted for Eco and `04` selected the ConfigR Frost target | Read polled; Eco is the only exposed write | **LIVE OBSERVATION:** `03 00 00 00 00 00` selected Eco/18°C and was display-confirmed; `04 00 00 00 00 00` selected 7°C but still needs display confirmation. Manual/Timer/Boost/Away remain phase 2. |
| `0/0x1003` | ConfigR effective/current setpoint mirror | `1400` at 20°C target | Readable; no write mapping established | **LIVE OBSERVATION:** duplicate-looking 20°C mirror (`capability-matrix.md:67-70`). UNKNOWN whether independent effective setpoint or duplicate. |
| `0/0x1005` | Display unit | Celsius/Fahrenheit values and reversible writes verified live; native climate readings remain Celsius | Readable and implemented as a configuration select | **LIVE OBSERVATION/CONFIGR FACT:** `00` read as Celsius, `01` was written/read back as Fahrenheit, then restored to `00`; this does not change the climate native unit. |
| `0/0x1006` | Temperature limits | Payload contains minimum and maximum at bytes 0 and 2; live bounds correspond to PLXE 7–30°C | Readable; no write | **FACT:** decoder requires ≥4 bytes and rejects inverted bounds (`protocol.py:195-203`). Verify meaning of bytes 1/3 and whether limits vary by mode. |
| `0/0x1007` | OpenWindowDetection | `010000000000000000` | Readable; no safe write mapping | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:70`). Configuration semantics, bit layout, and model-safe command remain UNKNOWN. |
| `0/0x1008` | PreEmptiveHeating | `01` | Readable; no safe write mapping | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:71`). Verify whether `01` means enabled and whether writes are reversible. |
| `0/0x100D` | AdvanceHeating | `00` | Readable; command untested | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:72`). Verify state transitions and payload length. |
| `0/0x1011` | Error/fault code | `00` live; raw bytes retained | Readable and implemented diagnostic | **LIVE OBSERVATION:** error code 0 (`capability-matrix.md:43`). **FACT:** PLXE manual documents fault concepts/codes (`dimplex-manual-research.md:27-31`). Verify all observed nonzero codes and whether little-endian interpretation is universal. |
| `0/0x1012–0x1015` | Security/material or undocumented fields | Not read | **BLOCKED READ and WRITE** (`protocol.py:14-18,20-23`) | **FACT:** known hazardous/security boundary. Do not probe without independently recovered safe semantics. |
| `0/0x1016` | Setback | `0008` | Readable; no write | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:73`). Unit/encoding and relationship to target are UNKNOWN. |
| `0/0x1017` | Runback | `017c` | Readable; no write | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:73`). Encoding and duration/unit UNKNOWN. |
| `0/0x1023` | Target/setpoint | Whole-degree Celsius, encoded as `[degree, 0]`; live target observed | Readable; verified writes only whole-degree payload | **LIVE OBSERVATION:** single target, whole-degree precision (`capability-matrix.md:28-31`). **FACT:** encoder rejects fractional/non-finite values and emits two bytes (`protocol.py:125-132`). Verify whether byte 2+ ever carries metadata. |
| `0/0x1027` | Runback/boost countdown | `0000` | Readable; no write | **CONFIGR label + LIVE OBSERVATION:** no active runback/boost (`capability-matrix.md:75`). Verify countdown unit and transition under a bounded mode test. |
| `0/0x1028` | Runback/boost setpoint | `1500` | Readable; not exposed as second target | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:76`). Verify whether it is a temporary target and how it changes. |
| `0/0x102A` | AwayMode | Inactive payload (exact bytes not recorded) | Readable; command blocked by evidence boundary | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:77`). Verify payload, calendar dependence, and reversible activation before any write. |
| `1/0x0002` selector 1/2 | ConfigR Trac; repository uses active flag and output percentage | `010164` / `020164` at forced 30°C; `010000` idle; selector 2 mirrors global state | Selector requires write `[selector,00,00]` then read; not a normal independent write | **LIVE OBSERVATION:** byte 1 correlates with active/idle and byte 2 with PLX report-screen output power (100/0) (`capability-matrix.md:36,46`; `client.py:272-275`). **INFERENCE:** byte meanings are model-specific correlation, not ConfigR HeatDemand. Verify across intermediate output levels and selector independence. |
| `1/0x0007` | LastKeyPressValue | `06` | Readable; no write | **CONFIGR label + LIVE OBSERVATION:** transient UI event (`capability-matrix.md:78`). Verify event code dictionary and whether reads race with user input; do not make normal entity. |
| `1/0x0008` selector 1 | Room temperature | Selector response format `[1, whole°C, tenth]`; e.g. repository records 19.4°C | Selector requires write-then-read; read-only response decoded | **LIVE OBSERVATION:** current room temperature (`capability-matrix.md:26,45`). **FACT:** decoder validates selector and tenth 0–9 (`protocol.py:160-169,205-207`). Verify raw-vs-adjusted sensor semantics and behavior in Fahrenheit display mode. |
| `1/0x0008` selector 2 | LCD/controller temperature | `[2, 0x1d, 0x00]` = 29.0°C in recorded observation | Selector requires write-then-read; read-only response decoded | **LIVE OBSERVATION:** separate 29.0°C diagnostic; selector 3 unavailable (`capability-matrix.md:45`). Verify sensor identity and selector 3/other selectors without assuming a temperature meaning. |
| `1/0x0008` selector 3 | Unknown temperature selector | Zero/unavailable | Selector exchange only | **LIVE OBSERVATION:** unavailable (`capability-matrix.md:45`). UNKNOWN whether unsupported, zero, or state-dependent. |
| `1/0x1010` | Undocumented/protected | Not read/written | **BLOCKED WRITE** (`protocol.py:24-26`) | Do not infer from neighboring fields. Read status is not blocked by current list, but no safe semantic is documented; live probing requires separate approval/evidence. |
| `1/0x1020` | Undocumented/protected | Not written | **BLOCKED WRITE** (`protocol.py:24-26`) | Semantics UNKNOWN. |
| `1/0x300E` | Undocumented/protected | Not written | **BLOCKED WRITE** (`protocol.py:24-26`) | Semantics UNKNOWN. |
| `2/0x100C` | RfComms | `01` | Readable; **BLOCKED WRITE** | **CONFIGR label + LIVE OBSERVATION** (`capability-matrix.md:79`; `protocol.py:27`). Verify whether value is radio enabled/linked state; never write on current evidence. |
| `2/0x1030`, `0x1033`, `0x1034`, `0x700F` | Undocumented/protected | Not written | **BLOCKED WRITE** (`protocol.py:27-31`) | Semantics UNKNOWN; no guessed mappings. |
| `3/0x000A` | PowerLoading / rated output metadata | `f401980800000000`: LE16 500, second word `2200`, remaining bytes present | Readable; **BLOCKED WRITE** | **LIVE OBSERVATION:** first word 500 W matches official PLX050E 0.50 kW (`capability-matrix.md:48,51`; `dimplex-manual-research.md:30-32`). Second word meaning UNKNOWN; do not call it measured power. |
| `3/0x1010`, `0x1037` | Undocumented/protected | Not written | **BLOCKED WRITE** (`protocol.py:33-34`) | Semantics UNKNOWN. |
| `3/0x2001` selector 1 | SwVersion UI | `010107` (UI revision 1.7); selectors 0/2 unavailable/placeholder | Selector read; no write mapping | **LIVE OBSERVATION** (`capability-matrix.md:80`). Verify selector dictionary and whether revision is user-relevant metadata. |
| `3/0x2002`, `0x2006`, `0x2007`, `0x2008` | Security material / bootloader or undocumented | Not read or written | **BLOCKED READ and WRITE** (`protocol.py:16-18,35-38`) | **FACT:** protected boundary. Never expose raw values. |
| `3/0x2005` | Model identity | ASCII `PLX050E;C;` | Readable and polled | **LIVE OBSERVATION:** stable product identity (`capability-matrix.md:24`; `protocol.py:179-184`). Verify behavior across firmware revisions and trailing NUL/space handling. |
| `3/0x2009` | RF firmware revision | `05` | Readable; no write | **LIVE OBSERVATION:** revision 5 (`capability-matrix.md:81`). Verify encoding and whether it belongs in device metadata. |
| `3/0x200A` | EpIdentifier/product code | `EP4213` | Readable; no write | **LIVE OBSERVATION:** product code (`capability-matrix.md:82`). Meaning is metadata only; verify stable scope across units. |
| `3/0x200D` | Board/firmware code | `PGLHS600` | Readable; no write | **LIVE OBSERVATION:** board code (`capability-matrix.md:83`). Do not interpret as semantic firmware version without vendor evidence. |
| `3/0x200F` | Undocumented/protected | Not written | **BLOCKED WRITE** (`protocol.py:39`) | Semantics UNKNOWN. |
| `3/0x1037` | Undocumented/protected | Not written | **BLOCKED WRITE** | Semantics UNKNOWN; no firmware/update inference. |

## Live inventory refresh: 19 November 2026

The following values were read from the commissioned `PLX050E;C;` over the
administrator-only `read_gatt` action during this investigation. Selector rows
were obtained by the required write-then-read exchange. These are observations,
not automatic semantic mappings.

| Address | Live payload | Classification | Evidence-bound conclusion |
|---|---|---|---|
| `0/0x0006` | `141a0b13130301` | LIVE OBSERVATION + upstream layout | ConfigR/Upstream layout decodes this as panel-heater clock `2026-11-19 19:03`, flag `01`; it was about three minutes behind the HA host clock. Read-only observation; no clock entity or write was added. |
| `0/0x1001` | `030000000000` | LIVE OBSERVATION + ConfigR label | Current mode payload is Eco; the user previously confirmed the display. No new mode command was issued. |
| `0/0x1002` | 31 zero bytes | LIVE OBSERVATION + ConfigR label | Schedule buffer is present but empty/inactive in this read. No schedule structure or calendar mapping is claimed. |
| `0/0x1003` and `0/0x1023` | `1200` / `1200` | LIVE OBSERVATION | Effective-setpoint mirror and target currently agree at 18°C; equality alone does not prove they are the same field semantically. |
| `0/0x1005` | `00` | LIVE OBSERVATION | Current display-unit value is Celsius under the existing integration mapping. |
| `0/0x1006` | `07001e00` | LIVE OBSERVATION | Bounds decode to 7–30°C under the tested decoder. |
| `0/0x1007` | `010000000000000000` | LIVE OBSERVATION + ConfigR label | Open-window-detection payload is readable; bit layout and enabled semantics remain unknown. |
| `0/0x1008` | `01` | LIVE OBSERVATION + ConfigR label | Pre-emptive-heating payload is readable; `01` is not promoted to a boolean control without a state transition test. |
| `0/0x100d` | `00` | LIVE OBSERVATION + ConfigR label | Advance-heating payload is readable and currently clear; no command was issued. |
| `0/0x1011` | `00` | LIVE OBSERVATION | Error status is clear. |
| `0/0x1016`, `0/0x1017`, `0/0x1027`, `0/0x1028` | `0008`, `017c`, `0000`, `1500` | LIVE OBSERVATION + ConfigR labels | Setback/runback/countdown/setpoint family is present; units and current behavioral meaning on PLX050E remain unverified. |
| `0/0x102a` | `001a0b11300607` | LIVE OBSERVATION + ConfigR label | Away payload is currently inactive (`00`); date/time bytes are retained only as raw evidence. |
| `1/0x0001` | `029eff96` | LIVE OBSERVATION + upstream observation | Backlight-color payload follows the documented `[selector,R,G,B]` shape; no HA control is exposed. |
| `1/0x0002` selectors 1/2 | `010000` / `020000` | LIVE OBSERVATION | Both element selectors currently report inactive; the third-byte output-power correlation remains model-specific and documented separately. |
| `1/0x0007` | `06` | LIVE OBSERVATION + ConfigR label | Last-key-press event currently reads `06`; it is transient and not an entity. |
| `1/0x0008` selectors 1/2/3 | `011308`, `021d01`, `030000` | LIVE OBSERVATION | Selector 1 = 19.8°C room reading, selector 2 = 29.1°C LCD/controller reading, selector 3 = zero/unavailable. |
| `1/0x1010`, `1/0x1020`, `1/0x300e` | `47`, `00`, `00` | LIVE OBSERVATION + protected-write boundary | Values are readable, but EOL/test/OTA semantics are not promoted and writes remain blocked where configured. |
| `2/0x0005`, `2/0x100a`, `2/0x100b`, `2/0x100c`, `2/0x100f`, `2/0x1018`, `2/0x1019`, `2/0x700f` | `000000`, `01`, `05`, `01`, `00`, `00`, `00`, `00` | LIVE OBSERVATION + ConfigR labels where available | These values are raw configuration/status evidence. `2/0x100c` is RfComms and remains write-blocked; no other meaning is claimed. |
| `3/0x000a` | `f401980800000000` | LIVE OBSERVATION + ConfigR label | PowerLoading first word is 500 W; second word is 2200 and remains semantically unknown. |
| `3/0x1022`, `3/0x1050`, `3/0x2003` | `00000000`, `f31000`, `00032d` | LIVE OBSERVATION | No authoritative semantic mapping recovered. |
| `3/0x2001` selectors 0/1/2 | `000000`, `010107`, `02ffff` | LIVE OBSERVATION + ConfigR label | SwVersion selector 1 reports UI revision 1.7; selectors 0 and 2 are zero/placeholder values. |
| `3/0x2004` | `3c44696d706c65783e` plus padding | LIVE OBSERVATION + ConfigR label | Brand payload is `<Dimplex>`; manufacturer metadata already supplies Dimplex. |
| `3/0x2005` | `PLX050E;C;` plus padding | LIVE OBSERVATION + ConfigR label | Stable model/series identity; already polled. |
| `3/0x2009`, `3/0x200a`, `3/0x200b`, `3/0x200c`, `3/0x200d`, `3/0x1029`, `3/0x200f` | `05`, `EP4213`, `7f9d`, `1416050d0e00`, `PGLHS600`, `9eff96`, `04e90f0000` | LIVE OBSERVATION | RF revision and product code are promoted to `DeviceInfo.sw_version`/`model_id`; board/build/colour/undocumented values remain raw-only |

No protected read was attempted. The full raw values are not sufficient by
themselves to justify new controls, schedules, firmware metadata, or energy
counters.

## ConfigR concepts with no proven AM6532 field

ConfigR resources reportedly name Auto/Timer, Manual, Eco, Frost, Boost, Away, weekly timers, charge level, element 1/2 status, total energy, firmware information, and `HeatDemand` at `3/0x1024` (`docs/configr.md:89-105`; `docs/capability-matrix.md:35,41,44,47,52`). The live PLX050E inventory did not expose `3/0x1024`, and the repository explicitly distinguishes it from the Trac output-power byte (`docs/capability-matrix.md:46-47`). These are **CONFIGR FACT / UNKNOWN AM6532 MAPPING**, not safe fields. No field should be guessed for:

- generic power on/off;
- Manual, Timer, Boost, or Away commands;
- schedules/calendar;
- fan, swing, humidity, occupancy;
- charge level, core temperature, element status, cumulative energy;
- firmware read/update;
- notifications/subscriptions.

Official PLXE documentation supports temperature, modes, Frost Protect at 7°C, errors, and 500 W rating, but explicitly does not document Quantum storage diagnostics (`docs/dimplex-manual-research.md:23-32,72-78`).

## Protected-access inventory

The implementation currently blocks reads for `0/0x1012–0x1015` and `3/0x2002`, `0x2006`, `0x2007`, `0x2008`. It blocks writes for those plus `1/0x1010`, `1/0x1020`, `1/0x300E`, `2/0x100C`, `2/0x700F`, `2/0x1030`, `2/0x1033`, `2/0x1034`, `3/0x000A`, `3/0x1010`, `3/0x1037`, and `3/0x200F` (`custom_components/dimplex_ble/protocol.py:14-40`). **FACT:** this is a conservative policy, not proof that every blocked field has the same underlying purpose. The source comments identify credential/security or unsafe/unknown fields; no protected payload is reproduced here.

## Candidate mappings requiring live verification

1. `0/0x1001`: enumerate all mode codes with bounded, reversible tests; confirm readback payload length and display text for Manual/Timer/Boost/Away.
2. `0/0x1007`, `0/0x1008`, `0/0x100D`, `0/0x1016`, `0/0x1017`, `0/0x1027`, `0/0x1028`, `0/0x102A`: record payloads across at least two meaningful states; test only reversible, model-specific writes after identifying exact encoding.
3. `1/0x0002`: repeat Trac selector 1/2 at several intermediate heating outputs; verify byte 1 active flag, byte 2 percentage, selector independence, and whether any fourth-or-later bytes carry additional status.
4. `1/0x0008`: repeat selectors 1–3 while changing room temperature, target, and display unit; establish raw versus adjusted/LCD sensor semantics.
5. `1/0x0007`: capture a controlled key-press sequence and determine whether `06` is a stable code or transient event.
6. `2/0x100C`: correlate `RfComms=01` with radio link conditions; do not write because it is blocked.
7. `3/0x000A`: compare multiple PLX050E units/firmware versions; determine the second word `2200` and all remaining bytes without treating it as instantaneous power.
8. `3/0x2001`, `0x2009`, `0x200A`, `0x200D`: compare units and firmware updates to establish metadata encodings.
9. ConfigR `3/0x1024 HeatDemand`: verify inventory across the exact model and authenticated session before any read; current evidence says absent, so no probe is justified by the label alone.
10. Notifications: inspect characteristic properties and controlled subscription behavior for mapped fields; current evidence supports polling only (`docs/capability-matrix.md:54-55`).
11. Any candidate command must satisfy the repository's evidence rule: characteristic/payload identified, repeated state observations, bounded reversible readback, unambiguous semantics, and regression fixture (`docs/capability-matrix.md:107-120`).

## Bottom line

The strongest device-level mappings are: identity `3/0x2005`; product code `3/0x200A`; UI revision selector 1 of `3/0x2001`; RF revision `3/0x2009`; rated output first LE16 word of `3/0x000A`; target `0/0x1023`; limits `0/0x1006`; error `0/0x1011`; display unit `0/0x1005`; current room temperature selector 1 of `1/0x0008`; LCD/controller temperature selector 2 of `1/0x0008`; Eco and bounded Frost mode payloads on `0/0x1001`; and model-specific Trac active/output correlation on selector reads of `1/0x0002`. Everything else is either metadata, raw inspection, protected, ConfigR-only terminology, or an open question. No ConfigR artifact evidence currently proves an AM6532 GATT packet mapping beyond the repository's separately recorded live observations.

## Experimental HA controls

The integration exposes the following safe, fixed-width raw fields as text controls.
Their names are **hypotheses from ConfigR terminology**, not confirmed device
meanings:

| --- | --- | ---: | --- |
| Experimental panel-heater clock | `0/0x0006` | 7 bytes | ConfigR SetRtc format; live read and write-capable characteristic |
| Experimental schedule buffer | `0/0x1002` | 31 bytes | Sunhouse/QRAD layout inferred; AM6532 schedule semantics unverified |
| Experimental display backlight color | `1/0x0001` | 4 bytes | Reversible color payload observed in QRAD and present on AM6532 |
| Experimental open-window detection | `0/0x1007` | 9 bytes | Reversible first-byte write/readback |
| Experimental pre-emptive heating | `0/0x1008` | 1 byte | Reversible write/readback |
| Experimental advance heating | `0/0x100D` | 1 byte | Write was not persistent |
| Experimental setback | `0/0x1016` | 2 bytes | Reversible write/readback |
| Experimental runback | `0/0x1017` | 2 bytes | Reversible write/readback |
| Experimental runback countdown | `0/0x1027` | 2 bytes | No-op trial only |
| Experimental runback setpoint | `0/0x1028` | 2 bytes | Reversible write/readback |
| Experimental away mode | `0/0x102A` | 7 bytes | Same-value write only |
| Experimental sound / mute | `2/0x0005` | 3 bytes | Reversible write/readback |
| Experimental setting 0x100A | `2/0x100A` | 1 byte | Reversible write/readback |
| Experimental setting 0x100B | `2/0x100B` | 1 byte | Reversible write/readback |
| Experimental setting 0x100F | `2/0x100F` | 1 byte | Reversible write/readback |
| Experimental setting 0x1018 | `2/0x1018` | 1 byte | Trial value reverted after delay |
| Experimental SP configuration | `2/0x1019` | 1 byte | Trial value reverted after delay |

The controls accept hexadecimal payloads of exactly the advertised byte length. The
integration does not claim semantic validation, and it refreshes the device after a
write. Do not use them in automations until the displayed panel-heater behavior and
multiple readback states establish a meaning.
