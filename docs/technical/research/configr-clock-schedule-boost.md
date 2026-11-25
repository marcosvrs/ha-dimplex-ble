# ConfigR: clock, schedule save, and Boost/Advance byte encodings

Source: the ConfigR Android app (`com.Dimplex.DimplexToolkit`, managed
assembly `managed/0148.dll`), read from its IL. Research only. No device
was written.

Evidence references:

- `IL:n` is a line in `configr-re/configr-il.txt`. This is the existing lossy
  dump, which leaves out locals, arithmetic and branches.
- `F:n` is a line in `configr-re/configr-ilfull.txt`. This is a full dump with
  IL offsets, made for this note by `configr-re/scripts/ildump_full.py`, which
  extends `ildump.py`. To rebuild it:
  `python3 -I scripts/ildump_full.py managed/0148.dll > configr-ilfull.txt`.
  Set `PAT=<regex>` to dump only matching methods.

**VERIFIED** means the code was read directly in the IL. **INFERENCE** means
the claim is a deduction. The IL shows only what ConfigR does. It does not
show what the heater firmware accepts.

## 1. Real-time clock `0/0x0006` and `0/0x0106`

### Byte layout (the same for reads and writes)

| Byte | `0x0006` SetRtc (7 B) | `0x0106` SetRtcWithSeconds (8 B) |
|---|---|---|
| 0 | century, constant `20` (0x14) | century, constant `20` |
| 1 | `Year % 100` | `Year % 100` |
| 2 | month 1–12 | month |
| 3 | day of month 1–31 | day of month |
| 4 | hour 0–23 | hour |
| 5 | minute | minute |
| 6 | `DateTime.IsDaylightSavingTime()` as 0/1 | DST flag 0/1 |
| 7 | — | second |

The payload has no day-of-week byte. VERIFIED from
`ByteArrayHelper::GetBytesForRtcNoSeconds` (IL:655226, F:1399528) and
`GetBytesForRtc` (IL:655203, F:1399453):
`ldc.i4 20; stloc.0 … get_Year; ldc.i4 100; rem; conv.u1 … IsDaylightSavingTime; BitConverter::GetBytes; ldc.i4 0; ldelem.u1`.
The bytes are stored in the order `[loc0, yy, mo, d, h, mi, dst]`, and the 8-byte
form adds `[7] = second`.

- **Local time, not UTC.** VERIFIED. `SetRtc*Parameter::GetBytesFromObject`
  calls `System.DateTime::get_Now` (IL:646850, IL:646886). The drift check
  also uses `get_Now` (F:1318463, IL:617240, `IL_0561 call System.DateTime::get_Now`).
  `UtcNow` appears only in the connection-timeout stopwatch
  (`<startTime>5__6`). No other timezone handling exists. The DST byte is
  `IsDaylightSavingTime()` of the phone's local `Now`.
- **Read decoding.** VERIFIED. `ReadRtcDate` (IL:655105, F:1399159) computes
  the year by joining the digits of bytes 0 and 1 as text (`"20"+"26"`, giving 2026),
  then builds `new DateTime(year, b2, b3, b4, b5, 0)`. Byte 6 is ignored on
  read. `ReadRtcDateWithSeconds` (IL:655315) computes `year = b0 + b1`
  (an arithmetic `add`, so 20+26 = 46, a ConfigR bug), takes the seconds
  from byte 7, and ignores byte 6. Both sides of the comparison are decoded
  the same way, so the drift check still works.
  `SetRtcWithSecondsParameter::SetObjectFromBytes` calls `ReadRtcDate`
  (IL:646879), which drops the seconds.
- **Which characteristic is used.** VERIFIED (`<ReadCharacteristicToTestConnectionAndWriteRealTimeClock>d__33`,
  IL:617023, F:1317980). ConfigR looks for `0x0106` first (lambda `b__33_0`,
  F:1303627, with both base UUID variants). If that is missing, it falls back
  to `0x0006` (`b__33_1`) and sets `isRtcWithSeconds=false`. The AM6532
  exposes only `0x0006`.
- **Drift rule.** VERIFIED (F:1318455–1318504; IL:617235–617252). The sequence is as follows:
  1. Read the heater's RTC and decode it to `applianceRtcValue`.
  2. Take `Now`, encode it with the same 7- or 8-byte encoder, and decode it
     again to `phoneRtcValue`. This truncates both values to the same
     resolution: whole minutes for `0x0006`.
  3. Compute `(appliance − phone).Duration()`.
  4. Write only if the result is `> TimeSpan.FromSeconds(60)`
     (`op_GreaterThan`, which is strict).

  The write is a single `WriteAsync(phoneRtcbytes)` with a 1000 ms
  cancellation token. No readback follows it. With minute resolution, a
  difference of exactly one minute does not trigger a write. The log says
  "RTC difference <= 1 minute, no update needed".
- The live AM6532 value `141a0b13130301` decodes to 2026‑11‑19 19:03 with
  byte 6 = `01`. Under ConfigR's semantics, byte 6 = 1 means DST is in
  effect, which does not fit November in Europe. See the open questions.

## 2. Schedule save on `0/0x1002` ScheduleAndSetTemperature

### Day record, 31 bytes (one per day and programme)

| Offset | Read response | ConfigR write |
|---|---|---|
| 0 | `0` | **`1`**. The save opcode is set by `WriteTimerModeDetails` (`dayBytes[0]=1`, F:1330406–1330409). The read request is `[0, mode, day]` (F:1321330–1321343) |
| 1 | programme: `1`=Out all day, `2`=Home all day, `3`=User timer | same (`DayData.TimerMode`) |
| 2 | day `0..6`, where 0 = Monday (`EDayOfWeek`) | same (`DayData.Day`) |
| 3+6k .. 8+6k, for k = 0..3 | `sh, sm, eh, em, temp°C, x` | same, and `x` is always `0` |
| 27–30 | pad | `0,0,0,0` |

VERIFIED from `DayData::WriteTo` (IL:636506, F:1357758):

- Periods are sorted by `StartTime` (`OrderBy(b__16_0 → get_StartTime)`).
  At most 4 are written (the loop runs to `ldc.i4 4; clt`).
- For a real period, the code calls `writeTime(start)`, then
  `writeTime(end)` at +2, stores `conv.u1 Temperature` at +4, and stores
  `loc4` at +5. `loc4` is initialised to `0` and never changed, so **byte 5
  is always 0** on write. Byte 5 is also never read (`SetObjectFromBytes`
  reads only bytes 3–7, 9–13, 15–19 and 21–25; IL:636365).
- `writeTime` uses `ByteArrayHelper.GetBytes(TimeSpan)` and produces
  `[Hours, Minutes]`.
- **Unused slots.** If the day has no periods, each slot is
  `[0,0,0,0,21,0]`. Otherwise each unused slot is
  `[endH,endM,endH,endM,21,0]`, where `end` is the end time of the last real
  period, so start equals end and the temperature is the constant `21`
  (`ldc.i4 21; stloc.3`). The firmware's own defaults use the same pattern.
- **Read filter.** A slot becomes a period only if it is not
  (`start==0 && end==0 && temp==0`) and `start != end`. So unused slots are
  recognised by start equal to end.

### Firmware-default records (`Constants::.cctor`, IL:657281, F:1405025–1405042; the arrays are `<PrivateImplementationDetails>` blobs and are byte-identical for all 7 days)

```
Home all day: 00 02 dd | 08 00 15 00 15 00 | 15 00 15 00 15 00 ×3 | 00 00 00 00   (08:00–21:00 @21)
Out all day:  00 01 dd | 07 00 08 1e 15 00 | 11 1e 16 00 15 00 | 16 00 16 00 15 00 ×2 | 00×4   (07:00–08:30, 17:30–22:00 @21)
User timer:   00 03 dd | 06 1e 09 1e 15 00 | 0b 00 0d 00 15 00 | 0f 00 11 00 15 00 | 12 00 16 00 15 00 | 00×4
              (06:30–09:30, 11:00–13:00, 15:00–17:00, 18:00–22:00 @21)
```

### Write sequence (`BLEService/<WriteTimerModeDetails>d__22`, IL:622495, F:1330265)

1. ConfigR always writes **all 7 days** of one programme, in day order
   0..6, using one `WriteAsync` per day. Each write has a 5000 ms
   cancellation token. There is no `Task.Delay` and no commit or apply
   parameter. VERIFIED (loop F:1330392–1330487).
2. Choice of characteristic, VERIFIED (F:1330360–1330366,
   `get_MtuSize; ldc.i4 20; cgt`):
   - If `MtuSize > 20`, ConfigR writes the 31-byte record to `0x1002`.
   - Otherwise it uses the split characteristics: per day, 4 writes of
     9 bytes each, `[1, mode, day] + period k (6 B)`, sent to `0x1012`,
     `0x1013`, `0x1014` and `0x1015` for k = 0..3 (`Skip(3/9/15/21).Take(6)`,
     F:1330646–1330921; UUID lambdas F:1303451–1303502). The pad bytes
     27–30 are not sent in this mode.
3. ConfigR then calls `ReadTimerModeDetails(mode)` to re-read all 7 days.
   The caller `ScheduleDetailsPageViewModel/<SaveSchedulePeriods>d__42`
   (IL:575116, F:1224824–1224876) compares the result with
   `ByteArrayHelper.AreArraysEqual(written.GetBytesFromObject(),
   readback.GetBytesFromObject())`. Both sides are normalised (byte 0
   is 0), so the comparison checks the period content. VERIFIED.
4. ConfigR does not write `0x1001` HeatingMode or any other parameter during
   a schedule save. VERIFIED (the call list of `SaveSchedulePeriods d__42`
   contains only `WriteTimerModeDetails`). A profile write saves Home (2),
   then Out (1), then User (3) through the same routine
   (`WriteBluetoothParameters d__16`, F:1323844–1323968).
5. Editing one day still rewrites the whole week of that programme, because
   the app always sends 7 records. Each record carries its own day index, so
   **INFERENCE**: a single-day write is self-contained on the heater side.
   This needs a device test.

### UI constraints (ConfigR side, not necessarily enforced by the firmware)

| Rule | Value | Evidence |
|---|---|---|
| Max periods per day | 4 (`AllowAddPeriod = !IsDefaultProfile && Count < 4`) | `LoadPeriodsForSelectedDay` IL:574030, F:1222193–1222198 |
| Granularity | 1 minute (Android `MaterialTimePicker`, hour and minute, no step or rounding found) | `MaterialPickers::ShowMaterialTimePicker` F:21281–21299. INFERENCE that no rounding happens elsewhere |
| Start < end | rejects `end < start` and `start == end`. The latest possible end is 23:59, because 24:00 would encode `Hours=0` | `AddEditSchedulePeriodPageViewModel/<SaveAsync>d__76` F:863522–863582 |
| No overlap | rejects any existing `p` with `p.Start < newEnd && newStart < p.End`. Touching periods are allowed | `<SaveAsync>b__76_0` F:862851–862862 |
| Temperature | whole °C: slider `Value` (double) → `conv.i4` → `conv.u1`. Range `min=7, max=30`, narrowed by `SetpointRange` `0x1006` when `checkForMinMax` | F:863712 (`conv.i4`), `InsertPeriodForSelectedDay` F:1222383, `AddSchedulePeriodAsync` F:1222995–1223016 |
| Sorted | the writer sorts by start, so callers may add periods in any order | `DayData::WriteTo` |

## 3. Boost (internal name Runback) and Advance

### Parameter encodings (VERIFIED)

| Param | Read | ConfigR write | Evidence |
|---|---|---|---|
| `0x1017` Runback | `[IsActive bool, Duration]` | `[IsActive?1:0, Duration]` | IL:645978/645988 |
| `0x1027` RunbackTime | `Mins = b1<<8 \| b0` (little-endian u16) | `[(byte)Mins, 0]`. Only the low byte is written | IL:646073/646083, F:1380403–1380436 |
| `0x1028` RunbackSetpoint | `Temperature = b0` (whole °C) | `[(byte)T, 0]` | IL:646035/646042 |
| `0x1023` CurrentSetTemperature | `b0` | `[(byte)T, 0]`, identical to `protocol.encode_setpoint` | IL:642274/642281 |
| `0x1001` HeatingMode | `[Mode, TimerMode, TempSetting, Duration, Temperature]` | 6 bytes, byte 5 = 0 | IL:644240/644255 |
| `0x100D` AdvanceHeating | `b0` | `[mode]` (1 byte) | IL:641528/641535 |

`0x1017` byte 1 has two codecs, chosen by product type
(`RunbackParameter::GetBytesFromEnum` IL:645998 and `GetEnumForByte`
IL:645961, VERIFIED):

- When `IsStorageHeater`, `IsQuantumHeater`, `"AC FANHEATER"` or
  `"FX DOWNFLOW"` applies, byte 1 = `100 + minutes/10`: 30→103, 60→106,
  120→112, 180→118, 240→124, 360→136. An unknown value maps to 124.
- Otherwise, byte 1 holds the raw `ERunbackParamTime` value: 30=0, 60=1,
  90=2, 120=3, 240=4, 360=5, 480=6, 180=18.

The live AM6532 reads `0x1017 = 01 7c`, which is active with **124 = 240 min
in the 100+min/10 codec**, and `0x1028 = 15 00`, which is 21 °C.

### How ConfigR starts Boost (the Boost button and the duration picker)

There are two writers:

- `ProductModePageViewModel/<WriteBoostMode>d__262` (IL:562015, F:1195572)
- `ConsumerHomepageViewModel/<WriteBoostMode>d__545` (IL:481920)

Both run the same steps. VERIFIED:

1. Map the duration enum to minutes (`switch`, F:1195603–1195627):
   `0→30, 1→60, 3→120, 18→180, 4→240`. **`2` (90) and `5` (360) fall
   through to 0 minutes.**
2. Clamp the temperature to `SetpointRange.Min..Max`.
3. Run `WriteBluetoothParameters([RunbackTime{Mins}, CurrentSetTemperature{T}])`.
   The list order is `0x1027` first, then `0x1023` (F:1195776–1195809).

ConfigR does **not** write `0x1017`, `0x1028` or `0x1001` to start a
Boost. For `"AC FANHEATER"` only, it sets `HeatingModeParameter.Mode=5` in
its local model after the write succeeds (F:1195987–1195999). It never
transmits that value.

The Boost-button defaults come from `ConsumerHomepageViewModel/<BoostButtonAsync>d__555`
(IL:472308, F:995873–995968):

- The duration is `GetEnumForByte(0x1017.Duration, isStorage||isQuantum||isAc3||isFx20)`.
  If `0x1017` is missing, it defaults to `4` (240 min).
- The temperature is `0x1028.Temperature`, defaulting to 21.

The duration picker offers enums `0, 1, 3, 18, 4` (30/60/120/180/240 min,
F:989794–990782).

### Stop, status and support

- **Stop.** `WriteStopBoostMode d__263` (IL:564118, F:1200390–1200525)
  writes only `0x1027 = [0,0]`. It then waits 1000 ms and calls
  `RefreshParams`. VERIFIED.
- **Boost is active** while `0x1027 Mins > 0`, which calls
  `ApplyHeatingProductBoostState` (F:1013216 context, `get_Mins; ldc 0; cgt`).
  The UI shows `0x1023` as the boost temperature and `0x1027` as the
  remaining time (IL:463538). VERIFIED.
- **Support gating.**
  - The Boost button is enabled when `0x1017` is absent, or present with
    `IsActive = true` (`set_BoostButtonEnabled(Runback==null || Runback.IsActive)`).
  - The Boost button is visible unless product activation is required.
  - The Boost-settings page is hidden for `IsFx20 || IsDmev`.

  No feature flag exists beyond these checks. VERIFIED (F:975363–975370,
  F:1012380–1012390).
- **Boost settings page.** `BoostSettingsPageViewModel` (IL:446301, 446558,
  446749) sets the defaults, not a live boost:
  - `ToggleRunbackAsync` writes `0x1017 [IsActive, current Duration]`.
  - `WriteRunbackDuration` writes `0x1017 [IsActive, GetBytesFromEnum(enum, typeFlag)]`.
  - `WriteRunbackTemperature` writes `0x1028 [T, 0]`.
- **After Boost ends.** The app does not set the post-boost state. It only
  re-reads. Its server-profile emulation (the `IsServerProfile` branch of
  `WriteStopBoostMode`, F:1200189–1200349) zeroes `RunbackTime` and sets
  the setpoint by mode:
  - Timer or Manual: 21, or the Away temperature (default 7) if
    `TimerMode=Away`
  - Eco: 18
  - Frost: 7

  **INFERENCE**: the heater keeps its `0x1001` mode during Boost and
  restores that mode's setpoint when the countdown ends.

### Advance `0x100D`

`ConsumerHomepageViewModel/<WriteAdvance>d__547` (IL:481519) and
`ProductModePageViewModel/<WriteAdvance>d__264` write
`AdvanceHeating = [advanceMode]`, with no other parameter. The callers pass
`0` or `1` (a toggle of the current value, F:993565–993569). The write is
skipped when the value is already equal. After the write, the code calls
`RefreshParams`. VERIFIED.

## Hardware results (AM2086 and all four heaters, 2026-11-25)

- **Clock:** read `[20, 26, 11, 25, h, m, flag]` on all four. AM6532 (set by
  ConfigR) was 2 min off with flag 1; AM2086, AM1382 and AM8224 were 138–144
  min slow with flag 0. Writing `encode_clock(now)` fixed all four (read back
  03:44–03:45, flag 1). Flag = `dst() != 0` of the local time; HA's tzdata
  gives Europe/Dublin a negative winter DST, matching ConfigR's flag 1.
  Whether the heater acts on the flag is unknown; held links recheck hourly.
- **Boost:** `0x1027 = minutes` then `0x1023 = °C` starts it; the countdown
  ran (30 → 29 after a minute). `0x1001` byte 3 becomes a duration band
  (1: ≤30 min, 2: ≤60, 3: ≤120, 4: ≤180, 5: ≤240). From Frost Protect the
  mode reads Manual (`02 00 00 0n`), from User Timer it stays `01 03 00 0n`.
  `0x1027 = 0` ends it and the previous mode and setpoint return within 1.5 s;
  a 1-minute boost that ran out returned to Frost Protect / User Timer within
  the next minute. 1–240 min are accepted; 241, 255 and 300 are ignored
  (nothing starts). Answers open questions 2 (irrelevant: minutes are written
  directly) and 4 (no: max 240).
- **Schedule save:** single-day writes work and do not change the mode; see
  `schedule-format.md`. Answers open question 1.

## Candidate hardware tests (ordered; each one is a single bounded write with readback)

| # | Purpose | Write | Expect |
|---|---|---|---|
| 1 | Start a 30-min boost at 22 °C | `0x1027 = 1e 00`, then `0x1023 = 16 00` | `0x1027` counts down from 30; the display shows boost |
| 2 | Cancel the boost | `0x1027 = 00 00` | `0x1027 = 0000`; `0x1023` goes back to the mode setpoint |
| 3 | Set the clock (only if drift > 60 s) | `0x0006 = 14 yy mm dd hh mi dst` from local time, e.g. `14 1a 0b 19 0a 1e 00` (2026‑11‑25 10:30, no DST) | read back the same, apart from byte 6 behaviour |
| 4 | Schedule save, User timer, Monday only (31-B MTU) | `0x1002 = 01 03 00 06 1e 09 1e 15 00 0b 00 0d 00 15 00 0f 00 11 00 15 00 12 00 16 00 15 00 00 00 00 00` | read `[00 03 00]` and get the same periods |
| 5 | Unused-slot form | `01 03 00 07 00 09 00 15 00 09 00 09 00 15 00 09 00 09 00 15 00 09 00 09 00 15 00 00 00 00 00` | Monday reads back as 1 period, 07:00–09:00 |
| 6 | Advance on and off (already verified) | `0x100D = 01` / `00` | — |

## Open questions needing a device test

1. Does a single-day `0x1002` write work, or must all 7 days be sent the way
   ConfigR sends them? Does saving a programme that is not active change the
   current mode?
2. Does the panel heater store `0x1017` byte 1 in the 100+min/10 codec, as
   the live `7c` suggests? If so, ConfigR treats a `PLX…` appliance
   (not Storage/Quantum/AC/FX) as raw-enum, its Boost-button default
   duration of 124 maps to 0 minutes, and the button would write
   `0x1027=0`. Write `0x1027` minutes directly instead, which does not
   depend on this codec.
3. Is `0x0006` byte 6 really DST? The live value is `01` in November. Does
   the heater shift its own clock when this byte changes?
4. Are `0x1027` writes above 255 minutes honoured? ConfigR never writes the
   high byte.
5. Does the firmware enforce 1-minute schedule granularity, or does it round
   (for example to 10 or 15 minutes)? Does it accept periods that overlap or
   are not sorted?
6. Are the split characteristics `0x1012`–`0x1015` reachable at all? They
   were blocked or unread on the AM6532. The proxy MTU decides which path
   ConfigR would use.
