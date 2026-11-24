"""Dimplex BLE identifiers, byte decoding, and conservative raw-GATT policy."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Final

type GattAddress = tuple[int, int]
OPEN_WINDOW_DETECTION: Final[GattAddress] = (0, 0x1007)
PREEMPTIVE_HEATING: Final[GattAddress] = (0, 0x1008)
HEATING_MODE: Final[GattAddress] = (0, 0x1001)
AWAY_MODE: Final[GattAddress] = (0, 0x102A)
SOUND: Final[GattAddress] = (2, 0x0005)
# ConfigR AdvanceHeatingParameter, SetpointRange, SetbackParameter and
# SpConfigParameter (docs/configr-entity-gap.md, rows 2, 3, 6 and 7).
ADVANCE_HEATING: Final[GattAddress] = (0, 0x100D)
SETPOINT_RANGE: Final[GattAddress] = (0, 0x1006)
SETBACK: Final[GattAddress] = (0, 0x1016)
ROOM_TEMPERATURE_OFFSET: Final[GattAddress] = (2, 0x1019)
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

DIMPLEX_GROUPS: Final = frozenset({0, 1, 2, 3})
MAX_GATT_VALUE_BYTES: Final = 512

# These reads have disconnected observed panel heaters or expose device-specific credentials.
BLOCKED_READS: Final = frozenset(
    {(0, value) for value in range(0x1012, 0x1016)}
    | {(3, 0x2002), (3, 0x2006), (3, 0x2007), (3, 0x2008)}
)

# These characteristics may be read for diagnostics but must never be written
# until their device-specific semantics are established.
READ_ONLY_RAW_FIELDS: Final = frozenset(
    {
        (1, 0x0007),
        (3, 0x000B),
        (3, 0x000C),
        (3, 0x1022),
        (3, 0x1029),
        (1, 0x1010),
        (1, 0x1020),
        (1, 0x300E),
        (2, 0x100C),
        (2, 0x700F),
        (3, 0x2003),
        (3, 0x200B),
        (3, 0x200C),
        (3, 0x200F),
    }
)

# Keep the established protection list for fields whose format or purpose is unsafe.
BLOCKED_WRITES: Final = frozenset(
    {(0, value) for value in range(0x1012, 0x1016)}
    | READ_ONLY_RAW_FIELDS
    | {
        (2, 0x1030),
        (2, 0x1033),
        (2, 0x1034),
        (3, 0x000A),
        (3, 0x1010),
        (3, 0x1037),
        (3, 0x2002),
        (3, 0x2006),
        (3, 0x2007),
        (3, 0x2008),
    }
)


class DimplexProtocolError(ValueError):
    """A requested GATT value or payload is invalid."""


class ProtectedCharacteristicError(DimplexProtocolError):
    """A requested characteristic is unsafe to access through raw GATT."""


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


def service_uuid(group: int) -> str:
    """Return the 128-bit UUID for a Dimplex service group."""
    if group not in DIMPLEX_GROUPS:
        raise DimplexProtocolError("Service group must be between 0 and 3")
    return f"00000000-000{group}-1000-8000-00805f9b34fb"


def characteristic_uuid(characteristic: int) -> str:
    """Return the Bluetooth-base UUID for a 16-bit characteristic identifier."""
    if not 0 <= characteristic <= 0xFFFF:
        raise DimplexProtocolError("Characteristic ID must be between 0 and 65535")
    return f"0000{characteristic:04x}-0000-1000-8000-00805f9b34fb"


def gatt_uuid_candidates(
    group: int, characteristic: int
) -> tuple[tuple[str, str], tuple[str, str]]:
    """Return standard and GDHV UUID pairs, preferring the Bluetooth SIG base."""
    standard = (service_uuid(group), characteristic_uuid(characteristic))
    vendor_base = "474c-4e44-494d504c4558"
    service_group = f"000{group}"
    gdhv = (
        f"00000000-{service_group}-{vendor_base}",
        f"0000{characteristic:04x}-{service_group}-{vendor_base}",
    )
    return standard, gdhv


def validate_raw_access(group: int, characteristic: int, *, write: bool) -> None:
    """Reject non-Dimplex groups and characteristics with known hazards."""
    service_uuid(group)
    characteristic_uuid(characteristic)
    key = (group, characteristic)
    if write and key in BLOCKED_WRITES:
        raise ProtectedCharacteristicError("This characteristic is blocked for writes")
    if not write and key in BLOCKED_READS:
        raise ProtectedCharacteristicError("This characteristic is blocked for reads")


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
    target_temperature_c: int = 7,
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

_PRESET_MODE_CODES: Final[dict[str, int]] = {
    PRESET_MANUAL: HEATING_MODE_MANUAL,
    PRESET_USER_TIMER: HEATING_MODE_TIMER,
    "eco": HEATING_MODE_ECO,
    PRESET_FROST_PROTECT: HEATING_MODE_FROST_PROTECT,
}
_PRESET_MODES_BY_CODE: Final[dict[int, str]] = {
    code: preset for preset, code in _PRESET_MODE_CODES.items()
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


def encode_timer_mode(submode: int) -> bytes:
    """Encode a verified timer-mode selection."""
    if submode not in _TIMER_SUBMODE_NAMES:
        raise DimplexProtocolError("Unsupported timer submode")
    return bytes((HEATING_MODE_TIMER, submode, 0, 0, 0, 0))


def encode_heating_mode(mode_code: int) -> bytes:
    """Encode a verified operating-mode command."""
    if mode_code not in _HEATING_MODE_NAMES:
        raise DimplexProtocolError("Unsupported heating mode")
    if mode_code == HEATING_MODE_TIMER:
        return encode_timer_mode(TIMER_SUBMODE_USER_TIMER)
    return bytes((mode_code, 0, 0, 0, 0, 0))


def encode_preset_mode(preset: str) -> bytes:
    """Encode a verified panel-heater preset command."""
    if preset == PRESET_HOME_ALL_DAY:
        return encode_timer_mode(TIMER_SUBMODE_HOME)
    if preset == PRESET_OUT_ALL_DAY:
        return encode_timer_mode(TIMER_SUBMODE_OUT)
    try:
        code = _PRESET_MODE_CODES[preset]
    except KeyError as err:
        raise DimplexProtocolError("Unsupported preset mode") from err
    return encode_heating_mode(code)


def parse_preset_mode(value: bytes | None) -> str | None:
    """Return a known non-scheduled preset from the operating-mode payload."""
    mode_code = parse_heating_mode_code(value)
    if mode_code is None or mode_code == HEATING_MODE_TIMER:
        return None
    return _PRESET_MODES_BY_CODE.get(mode_code)


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


def parse_advance(value: bytes | None) -> bool | None:
    """Decode AdvanceHeating: ``Off=0``/``On=1``; boost variants are unknown."""
    if value is None or len(value) != 1 or value[0] not in (0, 1):
        return None
    return bool(value[0])


def encode_advance(active: bool) -> bytes:
    """Encode AdvanceHeating ``On=1``/``Off=0``; the boost variants are not used."""
    return bytes((int(active),))


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


def parse_snapshot(
    values: dict[GattAddress, bytes],
    *,
    lcd_temperature: bytes | None = None,
    ui_version: bytes | None = None,
    trac: bytes | None = None,
) -> HeaterSnapshot:
    """Decode the safe, known fields from a collection of raw GATT values."""
    identity = values.get((3, 0x2005))
    model = (
        identity.decode("ascii", errors="replace").strip("\x00 ") or None
        if identity is not None
        else None
    )
    product = values.get((3, 0x200A))
    product_code = (
        product.decode("ascii", errors="replace").strip("\x00 ") or None
        if product is not None
        else None
    )
    rf_version = values.get((3, 0x2009))
    rf_firmware_revision = (
        rf_version[0] if rf_version is not None and len(rf_version) >= 1 else None
    )

    setpoint = values.get((0, 0x1023))
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

    room = values.get((1, 0x0008))
    current_temperature_c = _parse_selected_temperature(room, 1)
    lcd_temperature_c = _parse_selected_temperature(lcd_temperature, 2)

    error = values.get((0, 0x1011))
    error_code = int.from_bytes(error, "little") if error else None
    error_status_hex = error.hex() if error is not None else None

    mode = values.get((0, 0x1001))
    display_unit = values.get((0, 0x1005))
    open_window = values.get(OPEN_WINDOW_DETECTION)
    open_window_detection = (
        bool(open_window[0])
        if open_window is not None
        and len(open_window) == 9
        and open_window[0] in (0, 1)
        else None
    )
    preemptive = values.get(PREEMPTIVE_HEATING)
    preemptive_heating = (
        bool(preemptive[0])
        if preemptive is not None and len(preemptive) == 1 and preemptive[0] in (0, 1)
        else None
    )
    away_mode = parse_away_mode(values.get(AWAY_MODE))
    away_active = away_mode[0] if away_mode is not None else None
    away_until = away_mode[1] if away_mode is not None else None
    away_target_temperature_c = away_mode[2] if away_mode is not None else None
    advance_active = parse_advance(values.get(ADVANCE_HEATING))

    power_loading = values.get((3, 0x000A))
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
    )
