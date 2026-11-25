"""Dimplex BLE identifiers and byte encoding/decoding."""

from dataclasses import dataclass
from datetime import datetime, time
from math import isfinite
from typing import Final

type GattAddress = tuple[int, int]
OPEN_WINDOW_DETECTION: Final[GattAddress] = (0, 0x1007)
PREEMPTIVE_HEATING: Final[GattAddress] = (0, 0x1008)
HEATING_MODE: Final[GattAddress] = (0, 0x1001)
SETPOINT: Final[GattAddress] = (0, 0x1023)
HEATER_TYPE: Final[GattAddress] = (3, 0x2005)
PRODUCT_CODE: Final[GattAddress] = (3, 0x200A)
RF_VERSION: Final[GattAddress] = (3, 0x2009)
POWER_LOADING: Final[GattAddress] = (3, 0x000A)
ERROR_STATUS: Final[GattAddress] = (0, 0x1011)
DISPLAY_UNIT: Final[GattAddress] = (0, 0x1005)
# Selector reads: write the selector, then read (room 1 / LCD 2, Trac 1, UI 1).
ROOM_TEMPERATURE: Final[GattAddress] = (1, 0x0008)
TRAC: Final[GattAddress] = (1, 0x0002)
UI_VERSION: Final[GattAddress] = (3, 0x2001)
# Dimplex appliance id (ConfigR "Gdid"), stored byte-reversed: AM2086 reads
# 3a00c0230401 for the label's 010423C0003A (2026-11-25).
GDID: Final[GattAddress] = (3, 0x2002)
# Firmware update: 1 = update mode (display image), 0 = normal (ConfigR
# Esp32OtaEnableParameter); the update frames go to OTA_COMMAND.
OTA_ENABLE: Final[GattAddress] = (1, 0x300E)
OTA_COMMAND_UUID: Final = "00060001-f8ce-11e4-abf4-0002a5d5c51d"
AWAY_MODE: Final[GattAddress] = (0, 0x102A)
SOUND: Final[GattAddress] = (2, 0x0005)
# ConfigR AdvanceHeatingParameter, SetpointRange, SetbackParameter and
# SpConfigParameter (docs/configr-entity-gap.md, rows 2, 3, 6 and 7).
ADVANCE_HEATING: Final[GattAddress] = (0, 0x100D)
SETPOINT_RANGE: Final[GattAddress] = (0, 0x1006)
SETBACK: Final[GattAddress] = (0, 0x1016)
ROOM_TEMPERATURE_OFFSET: Final[GattAddress] = (2, 0x1019)
# ConfigR ScheduleAndSetTemperature: one (programme, day) record per read,
# selected by writing [0, programme, day] first; byte 0 = 1 saves that one day
# (hardware 2026-11-25, docs/schedule-format.md).
SCHEDULE: Final[GattAddress] = (0, 0x1002)
SCHEDULE_RECORD_LENGTH: Final = 31
SCHEDULE_PERIODS: Final = 4
SCHEDULE_DAYS: Final = 7
# ConfigR fills unused slots with this temperature (Constants.cctor defaults).
SCHEDULE_FILLER_C: Final = 21
# Boost (ConfigR "Boost", internally Runback): writing the minutes to
# RunbackTime and then the setpoint starts it; writing 0 minutes ends it and the
# heater returns to its previous mode by itself. 1-240 min are accepted, longer
# values are ignored (hardware 2026-11-25, docs/configr-clock-schedule-boost.md).
BOOST_REMAINING: Final[GattAddress] = (0, 0x1027)
BOOST_TEMPERATURE: Final[GattAddress] = (0, 0x1028)
BOOST_MAX_MINUTES: Final = 240
# ConfigR SetRtc: [20, year % 100, month, day, hour, minute, DST flag], local
# time; ConfigR writes the phone clock once per connection when it is more than
# 60 s off (docs/configr-clock-schedule-boost.md).
CLOCK: Final[GattAddress] = (0, 0x0006)
CLOCK_PAYLOAD_LENGTH: Final = 7
CLOCK_CENTURY: Final = 20
SOUND_PAYLOAD_LENGTH: Final = 3
AWAY_MODE_PAYLOAD_LENGTH: Final = 7
SETBACK_PAYLOAD_LENGTH: Final = 2

# Away target bounds of the Dimplex cloud client (7-18 degC); the PLXE manual
# gives the 7 degC default (p. 17) (docs/capability-matrix.md).
AWAY_TEMPERATURE_MIN_C: Final = 7
AWAY_TEMPERATURE_MAX_C: Final = 18
# Room-temperature offset: the Quantum manual (same controller family) documents
# +/-3.0 degC; ConfigR's encoding itself spans -15.5 to +10.0 degC.
TEMPERATURE_OFFSET_LIMIT_C: Final = 3.0

HEATING_MODE_TIMER = 0x01
HEATING_MODE_MANUAL = 0x02
HEATING_MODE_ECO = 0x03
HEATING_MODE_FROST_PROTECT = 0x04
TIMER_SUBMODE_OUT = 0x01
TIMER_SUBMODE_HOME = 0x02
TIMER_SUBMODE_USER_TIMER = 0x03
TIMER_SUBMODE_AWAY = 0x05

PRESET_MANUAL = "manual"
PRESET_HOME_ALL_DAY = "home_all_day"
PRESET_OUT_ALL_DAY = "out_all_day"
PRESET_AWAY = "away"
PRESET_USER_TIMER = "user_timer"
PRESET_FROST_PROTECT = "frost_protect"
PRESET_BOOST = "boost"


class DimplexProtocolError(ValueError):
    """A requested GATT value or payload is invalid."""


@dataclass(frozen=True, slots=True)
class HeaterSnapshot:
    """Decoded values from one read-only panel-heater poll."""

    model: str | None
    setpoint_c: int | None
    minimum_temperature_c: int | None
    maximum_temperature_c: int | None
    current_temperature_c: float | None
    error_code: int | None
    error_status_hex: str | None
    operating_mode_hex: str | None
    display_unit: int | None
    rated_power_w: int | None = None
    power_loading_hex: str | None = None
    lcd_temperature_c: float | None = None
    trac_status_hex: str | None = None
    heating_active: bool | None = None
    trac_output_power_percent: int | None = None
    product_code: str | None = None
    ui_version: str | None = None
    rf_firmware_revision: int | None = None
    open_window_detection: bool | None = None
    preemptive_heating: bool | None = None
    away_active: bool | None = None
    away_until: datetime | None = None
    away_target_temperature_c: int | None = None
    advance_active: bool | None = None
    boost_minutes_left: int | None = None
    boost_temperature_c: int | None = None


def gatt_uuid_candidates(
    group: int, characteristic: int
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return standard and GDHV UUID pairs, preferring the Bluetooth SIG base."""
    standard = (
        f"00000000-000{group}-1000-8000-00805f9b34fb",
        f"0000{characteristic:04x}-0000-1000-8000-00805f9b34fb",
    )
    vendor_base = "474c-4e44-494d504c4558"
    service_group = f"000{group}"
    gdhv = (
        f"00000000-{service_group}-{vendor_base}",
        f"0000{characteristic:04x}-{service_group}-{vendor_base}",
    )
    return standard, gdhv


def parse_away_mode(
    value: bytes | None,
) -> tuple[bool, datetime | None, int] | None:
    """Parse the verified Away Mode payload."""
    if value is None or len(value) != AWAY_MODE_PAYLOAD_LENGTH:
        return None
    active, year, month, day, minute, hour, temperature = value
    try:
        until = datetime(2000 + year, month, day, hour, minute)
    except ValueError:
        until = None
    return bool(active), until, temperature


def encode_away_mode(
    active: bool,
    until: datetime,
    target_temperature_c: int = AWAY_TEMPERATURE_MIN_C,
) -> bytes:
    """Encode the verified Away Mode payload."""
    if not 2000 <= until.year <= 2255:
        raise DimplexProtocolError("Away Mode year is outside the encodable range")
    if not 0 <= target_temperature_c <= 0xFF:
        raise DimplexProtocolError("Away Mode target is outside the encodable range")
    return bytes(
        (
            int(active),
            until.year - 2000,
            until.month,
            until.day,
            until.minute,
            until.hour,
            target_temperature_c,
        )
    )


def encode_setpoint(temperature_c: float) -> bytes:
    """Encode a whole-degree Celsius setpoint as the observed two-byte value."""
    value = float(temperature_c)
    if not isfinite(value) or not value.is_integer():
        raise DimplexProtocolError("The panel heater accepts whole-degree setpoints")
    if not 0 <= value <= 0xFF:
        raise DimplexProtocolError("Setpoint is outside the encodable range")
    return bytes((int(value), 0))


_HEATING_MODE_NAMES: Final[dict[int, str]] = {
    HEATING_MODE_TIMER: "timer",
    HEATING_MODE_MANUAL: "manual",
    HEATING_MODE_ECO: "eco",
    HEATING_MODE_FROST_PROTECT: "frost_protect",
}
_TIMER_SUBMODE_NAMES: Final[dict[int, str]] = {
    TIMER_SUBMODE_OUT: PRESET_OUT_ALL_DAY,
    TIMER_SUBMODE_HOME: PRESET_HOME_ALL_DAY,
    TIMER_SUBMODE_USER_TIMER: PRESET_USER_TIMER,
    TIMER_SUBMODE_AWAY: PRESET_AWAY,
}
# Timer profiles: Advance only works while one of them runs (PLXE manual p. 17).
TIMER_MODE_KEYS: Final = frozenset(
    {"timer", PRESET_USER_TIMER, PRESET_HOME_ALL_DAY, PRESET_OUT_ALL_DAY}
)
# Every value heating_mode_key() can return: the operating-mode sensor options.
HEATING_MODE_KEYS: Final = (
    PRESET_USER_TIMER,
    PRESET_HOME_ALL_DAY,
    PRESET_OUT_ALL_DAY,
    PRESET_AWAY,
    "timer",
    PRESET_MANUAL,
    "eco",
    PRESET_FROST_PROTECT,
)

# Verified HeatingMode writes (hardware 2026-11-24): [mode, timer submode, 0...].
_PRESET_PAYLOADS: Final[dict[str, bytes]] = {
    PRESET_MANUAL: bytes((HEATING_MODE_MANUAL, 0, 0, 0, 0, 0)),
    "eco": bytes((HEATING_MODE_ECO, 0, 0, 0, 0, 0)),
    PRESET_FROST_PROTECT: bytes((HEATING_MODE_FROST_PROTECT, 0, 0, 0, 0, 0)),
    PRESET_USER_TIMER: bytes(
        (HEATING_MODE_TIMER, TIMER_SUBMODE_USER_TIMER, 0, 0, 0, 0)
    ),
    PRESET_HOME_ALL_DAY: bytes((HEATING_MODE_TIMER, TIMER_SUBMODE_HOME, 0, 0, 0, 0)),
    PRESET_OUT_ALL_DAY: bytes((HEATING_MODE_TIMER, TIMER_SUBMODE_OUT, 0, 0, 0, 0)),
}


# Error codes with a documented meaning: 0 read live as "no error" on the
# PLX050E (docs/capability-matrix.md); the others are the fault codes of the
# PLXE Series C manual, Issue 7, PDF p. 26 (docs/dimplex-manual-research.md).
# Any other code is shown as its number.
DOCUMENTED_ERROR_CODES: Final = frozenset(
    {0, 20, 21, 22, 23, 24, 30, 32, 33, 34, 40, 41, 50, 52, 53, 54, 55, 60, 61}
)


def error_code_state(code: int | None) -> str | None:
    """Return the translatable state of an error code, else ``Code N``."""
    if code is None:
        return None
    if code == 0:
        return "no_error"
    if code in DOCUMENTED_ERROR_CODES:
        return f"code_{code}"
    return f"Code {code}"


def parse_heating_mode_code(value: bytes | None) -> int | None:
    """Return a known operating-mode code."""
    if not value or value[0] not in _HEATING_MODE_NAMES:
        return None
    return value[0]


def heating_mode_key(value: bytes | None) -> str | None:
    """Return the documented heating mode key, or None for an unknown payload."""
    mode_code = parse_heating_mode_code(value)
    if mode_code is None:
        return None
    if mode_code == HEATING_MODE_TIMER and value is not None and len(value) > 1:
        submode = _TIMER_SUBMODE_NAMES.get(value[1])
        if submode is not None:
            return submode
    return _HEATING_MODE_NAMES[mode_code]


def heating_mode_of(operating_mode_hex: str | None) -> str | None:
    """Return :func:`heating_mode_key` of a snapshot's operating-mode hex."""
    if operating_mode_hex is None:
        return None
    try:
        return heating_mode_key(bytes.fromhex(operating_mode_hex))
    except ValueError:
        return None


def parse_model(value: str | None) -> str | None:
    """Return a readable model from the HeaterType string, e.g. ``PLX050E;C;``.

    INFERENCE: the single letter after the model is the product series
    ("PLX050E;C;" is sold as "PLX050E Series C"; docs/dimplex-manual-research.md
    matches this identity to the PLXE Series C manual). Dimplex does not
    document the field format.
    """
    if value is None:
        return None
    parts = [part.strip() for part in value.split(";") if part.strip()]
    if not parts:
        return None
    if len(parts) == 2 and len(parts[1]) == 1 and parts[1].isalpha():
        return f"{parts[0]} Series {parts[1].upper()}"
    return " ".join(parts)


def encode_preset_mode(preset: str) -> bytes:
    """Encode a verified panel-heater preset command."""
    try:
        return _PRESET_PAYLOADS[preset]
    except KeyError as err:
        raise DimplexProtocolError("Unsupported preset mode") from err


def parse_gdid(value: bytes | None) -> str | None:
    """Decode the 6-byte appliance id as on the heater's label (12 hex digits)."""
    if value is None or len(value) != 6 or not any(value):
        return None
    return value[::-1].hex().upper()


def parse_sound_enabled(value: bytes | None) -> bool | None:
    """Decode the verified three-byte Sound setting."""
    if value is None or len(value) != SOUND_PAYLOAD_LENGTH:
        return None
    if value[0] not in (0, 1) or value[1:] != b"\x00\x00":
        return None
    return bool(value[0])


def encode_sound(enabled: bool) -> bytes:
    """Encode the verified three-byte Sound setting."""
    return bytes((int(enabled), 0, 0))


def parse_flag(value: bytes | None) -> bool | None:
    """Decode a one-byte ``Off=0``/``On=1`` setting; anything else is unknown.

    AdvanceHeating also has boost variants (2-6, 0x81-0x8D): unknown here.
    """
    if value is None or len(value) != 1 or value[0] not in (0, 1):
        return None
    return bool(value[0])


def parse_setback(value: bytes | None) -> bool | None:
    """Decode whether Setback is on from ``[IsActive, Temperature]``."""
    if value is None or len(value) != SETBACK_PAYLOAD_LENGTH or value[0] not in (0, 1):
        return None
    return bool(value[0])


def encode_setback(active: bool, current: bytes | None) -> bytes:
    """Switch Setback, keeping the heater's own Setback temperature byte.

    The temperature byte's scale is unverified, so it is never generated.
    """
    if parse_setback(current) is None:
        raise DimplexProtocolError("The current Setback value is unknown")
    assert current is not None  # checked by parse_setback()
    return bytes((int(active), current[1]))


def parse_temperature_offset(value: bytes | None) -> float | None:
    """Decode the SpConfig room-temperature offset (signed tenths of degC).

    ConfigR: a byte up to 100 is ``+b/10``, any larger byte is ``(b - 256)/10``.
    """
    if value is None or len(value) != 1:
        return None
    raw = value[0]
    return (raw if raw <= 100 else raw - 256) / 10


def encode_temperature_offset(offset_c: float) -> bytes:
    """Encode a room-temperature offset in 0.1 degC steps within the safe limit."""
    value = float(offset_c)
    if not isfinite(value) or abs(value) > TEMPERATURE_OFFSET_LIMIT_C:
        raise DimplexProtocolError("Offset is outside the supported range")
    tenths = round(value * 10)
    if abs(value * 10 - tenths) > 1e-6:
        raise DimplexProtocolError("Offset must use 0.1 degC steps")
    return bytes((tenths % 256,))


def _parse_selected_temperature(value: bytes | None, selector: int) -> float | None:
    """Decode a selected ConfigR temperature-sensor response."""
    if (
        value is None
        or len(value) < 3
        or value[0] != selector
        or not 0 <= value[2] <= 9
    ):
        return None
    return value[1] + value[2] / 10


def _parse_selected_version(value: bytes | None, selector: int) -> str | None:
    """Decode a selected ConfigR software-version response."""
    if value is None or len(value) < 3 or value[0] != selector:
        return None
    return f"{value[1]}.{value[2]}"


# Plain reads of every poll, decoded by parse_snapshot (the selector reads
# ROOM_TEMPERATURE, TRAC and UI_VERSION are passed separately).
SNAPSHOT_FIELDS: Final[tuple[GattAddress, ...]] = (
    HEATER_TYPE,
    PRODUCT_CODE,
    RF_VERSION,
    POWER_LOADING,
    SETPOINT,
    SETPOINT_RANGE,
    ERROR_STATUS,
    HEATING_MODE,
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    AWAY_MODE,
    DISPLAY_UNIT,
    # Advance ends by itself at the next timer period, so it is polled.
    ADVANCE_HEATING,
    # Boost also ends by itself; its temperature is the heater's own setting.
    BOOST_REMAINING,
    BOOST_TEMPERATURE,
)


def parse_snapshot(
    values: dict[GattAddress, bytes],
    *,
    lcd_temperature: bytes | None = None,
    ui_version: bytes | None = None,
    trac: bytes | None = None,
) -> HeaterSnapshot:
    """Decode the safe, known fields from a collection of raw GATT values."""
    identity = values.get(HEATER_TYPE)
    model = (
        identity.decode("ascii", errors="replace").strip("\x00 ") or None
        if identity is not None
        else None
    )
    product = values.get(PRODUCT_CODE)
    product_code = (
        product.decode("ascii", errors="replace").strip("\x00 ") or None
        if product is not None
        else None
    )
    rf_version = values.get(RF_VERSION)
    rf_firmware_revision = (
        rf_version[0] if rf_version is not None and len(rf_version) >= 1 else None
    )

    setpoint = values.get(SETPOINT)
    setpoint_c = (
        int.from_bytes(setpoint[:2], "little")
        if setpoint and len(setpoint) >= 2
        else None
    )
    if setpoint_c == 0:
        setpoint_c = None

    limits = values.get(SETPOINT_RANGE)
    minimum_temperature_c: int | None = None
    maximum_temperature_c: int | None = None
    if limits is not None and len(limits) >= 4:
        minimum_temperature_c = limits[0]
        maximum_temperature_c = limits[2]
        if minimum_temperature_c > maximum_temperature_c:
            minimum_temperature_c = None
            maximum_temperature_c = None

    room = values.get(ROOM_TEMPERATURE)
    current_temperature_c = _parse_selected_temperature(room, 1)
    lcd_temperature_c = _parse_selected_temperature(lcd_temperature, 2)

    error = values.get(ERROR_STATUS)
    error_code = int.from_bytes(error, "little") if error else None
    error_status_hex = error.hex() if error is not None else None

    mode = values.get(HEATING_MODE)
    display_unit = values.get(DISPLAY_UNIT)
    open_window = values.get(OPEN_WINDOW_DETECTION)
    open_window_detection = (
        bool(open_window[0])
        if open_window is not None
        and len(open_window) == 9
        and open_window[0] in (0, 1)
        else None
    )
    preemptive = values.get(PREEMPTIVE_HEATING)
    preemptive_heating = parse_flag(preemptive)
    away_mode = parse_away_mode(values.get(AWAY_MODE))
    away_active = away_mode[0] if away_mode is not None else None
    away_until = away_mode[1] if away_mode is not None else None
    away_target_temperature_c = away_mode[2] if away_mode is not None else None
    advance_active = parse_flag(values.get(ADVANCE_HEATING))

    power_loading = values.get(POWER_LOADING)
    rated_power_w = (
        int.from_bytes(power_loading[:2], "little")
        if power_loading is not None and len(power_loading) >= 2
        else None
    )

    trac_status_hex = trac.hex() if trac is not None else None
    heating_active: bool | None = None
    trac_output_power_percent: int | None = None
    if trac is not None and len(trac) >= 3 and trac[0] == 1:
        if trac[1] in (0, 1):
            heating_active = bool(trac[1])
        if trac[2] <= 100:
            trac_output_power_percent = trac[2]

    return HeaterSnapshot(
        model=model,
        product_code=product_code,
        setpoint_c=setpoint_c,
        minimum_temperature_c=minimum_temperature_c,
        maximum_temperature_c=maximum_temperature_c,
        current_temperature_c=current_temperature_c,
        error_code=error_code,
        error_status_hex=error_status_hex,
        operating_mode_hex=mode.hex() if mode is not None else None,
        display_unit=(display_unit[0] if display_unit else None),
        rated_power_w=rated_power_w,
        power_loading_hex=(power_loading.hex() if power_loading is not None else None),
        lcd_temperature_c=lcd_temperature_c,
        trac_status_hex=trac_status_hex,
        heating_active=heating_active,
        trac_output_power_percent=trac_output_power_percent,
        ui_version=_parse_selected_version(ui_version, 1),
        rf_firmware_revision=rf_firmware_revision,
        open_window_detection=open_window_detection,
        preemptive_heating=preemptive_heating,
        away_active=away_active,
        away_until=away_until,
        away_target_temperature_c=away_target_temperature_c,
        advance_active=advance_active,
        boost_minutes_left=_parse_le16(values.get(BOOST_REMAINING)),
        boost_temperature_c=_parse_whole_degrees(values.get(BOOST_TEMPERATURE)),
    )


def _parse_le16(value: bytes | None) -> int | None:
    """Decode a little-endian 16-bit count, or ``None`` when absent."""
    if value is None or len(value) != 2:
        return None
    return int.from_bytes(value, "little")


def _parse_whole_degrees(value: bytes | None) -> int | None:
    """Decode a ``[degC, 0]`` temperature (the setpoint encoding)."""
    if value is None or len(value) != 2 or value[1] != 0 or value[0] == 0:
        return None
    return value[0]


def encode_boost_minutes(minutes: int) -> bytes:
    """Encode a Boost length (0 ends Boost); the heater ignores more than 240."""
    if isinstance(minutes, bool) or not 0 <= minutes <= BOOST_MAX_MINUTES:
        raise DimplexProtocolError("Boost lasts 1 to 240 minutes")
    return minutes.to_bytes(2, "little")


@dataclass(frozen=True, slots=True)
class SchedulePeriod:
    """One heating period of a timer programme day (local heater time)."""

    start: time
    end: time
    temperature_c: int


def encode_clock(now: datetime) -> bytes:
    """Encode a local time like ConfigR: whole minutes plus the DST flag.

    The flag is the phone's daylight-saving state (``dst() != 0``). Irish time
    in the tz database has a negative winter DST, which matches the flag 1 a
    ConfigR-set heater reported in November (hardware 2026-11-25).
    """
    if now.tzinfo is None:
        raise DimplexProtocolError("The clock needs a time zone")
    if now.year // 100 != CLOCK_CENTURY:
        raise DimplexProtocolError("The heater clock covers 2000-2099 only")
    dst = now.dst()
    return bytes(
        (
            CLOCK_CENTURY,
            now.year % 100,
            now.month,
            now.day,
            now.hour,
            now.minute,
            int(bool(dst)),
        )
    )


def parse_clock(value: bytes | None) -> datetime | None:
    """Decode the heater clock as a naive local time, or ``None`` when invalid."""
    if value is None or len(value) != CLOCK_PAYLOAD_LENGTH:
        return None
    if value[0] != CLOCK_CENTURY:
        return None
    try:
        return datetime(2000 + value[1], value[2], value[3], value[4], value[5])
    except ValueError:
        return None


def encode_schedule_request(programme: int, day: int) -> bytes:
    """Select one programme day for reading (never the save form)."""
    if programme not in (
        TIMER_SUBMODE_OUT,
        TIMER_SUBMODE_HOME,
        TIMER_SUBMODE_USER_TIMER,
    ):
        raise DimplexProtocolError("Unsupported timer programme")
    if not 0 <= day < SCHEDULE_DAYS:
        raise DimplexProtocolError("Day must be 0 (Monday) to 6 (Sunday)")
    return bytes((0, programme, day))


def encode_schedule_day(
    programme: int, day: int, periods: tuple[SchedulePeriod, ...]
) -> bytes:
    """Encode the save form of one day, laid out as ConfigR writes it.

    Periods are sorted; unused slots repeat the last end time (start == end,
    which the heater and ConfigR skip), or are 00:00-00:00 on an empty day.
    """
    header = encode_schedule_request(programme, day)
    ordered = sorted(periods, key=lambda period: period.start)
    if len(ordered) > SCHEDULE_PERIODS:
        raise DimplexProtocolError("A day has at most 4 periods")
    for index, period in enumerate(ordered):
        if not period.start < period.end:
            raise DimplexProtocolError("A period must end after it starts")
        if period.start.second or period.end.second:
            raise DimplexProtocolError("Periods use whole minutes")
        if index and period.start < ordered[index - 1].end:
            raise DimplexProtocolError("Periods must not overlap")
        if not 0 < period.temperature_c <= 0xFF:
            raise DimplexProtocolError("Invalid period temperature")
    filler = ordered[-1].end if ordered else time(0)
    body = b"".join(
        bytes(
            (
                period.start.hour,
                period.start.minute,
                period.end.hour,
                period.end.minute,
                period.temperature_c,
                0,
            )
        )
        for period in ordered
    ) + bytes(
        (filler.hour, filler.minute, filler.hour, filler.minute, SCHEDULE_FILLER_C, 0)
    ) * (SCHEDULE_PERIODS - len(ordered))
    return bytes((1,)) + header[1:] + body + bytes(4)


def parse_schedule_day(
    value: bytes | None, programme: int, day: int
) -> tuple[SchedulePeriod, ...] | None:
    """Decode one day record, or ``None`` when it is not the requested one.

    Layout (ConfigR DayData): [flag, programme, day] then four 6-byte periods
    [start h, start min, end h, end min, temperature degC, 0] and 4 pad bytes.
    ConfigR drops a slot whose start equals its end (its unused-slot filler).
    """
    if (
        value is None
        or len(value) != SCHEDULE_RECORD_LENGTH
        or value[1] != programme
        or value[2] != day
    ):
        return None
    periods: list[SchedulePeriod] = []
    for slot in range(SCHEDULE_PERIODS):
        start_h, start_m, end_h, end_m, temperature = value[3 + 6 * slot : 8 + 6 * slot]
        if (start_h, start_m) == (end_h, end_m):
            continue
        try:
            start = time(start_h, start_m)
            end = time(end_h, end_m)
        except ValueError:
            return None
        if end < start:
            return None
        periods.append(SchedulePeriod(start, end, temperature))
    return tuple(sorted(periods, key=lambda period: period.start))
