"""Dimplex BLE identifiers, byte decoding, and conservative raw-GATT policy."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Final

type GattAddress = tuple[int, int]

DIMPLEX_GROUPS: Final = frozenset({0, 1, 2, 3})
MAX_GATT_VALUE_BYTES: Final = 512

# These reads have disconnected observed heaters or expose device-specific credentials.
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
    """Decoded values from one read-only heater poll."""

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


def parse_hex_payload(value: str) -> bytes:
    """Parse a non-empty hex payload within the ATT attribute value limit."""
    try:
        payload = bytes.fromhex(value)
    except ValueError as err:
        raise DimplexProtocolError("Payload must contain hexadecimal bytes") from err
    if not payload:
        raise DimplexProtocolError("Payload must not be empty")
    if len(payload) > MAX_GATT_VALUE_BYTES:
        raise DimplexProtocolError("Payload exceeds the 512-byte GATT value limit")
    return payload


def decode_known_raw_value(
    address: GattAddress, value: bytes
) -> dict[str, str | int | bool]:
    """Decode only payload layouts established by the protocol evidence."""
    decoded: dict[str, str | int | bool] = {}

    if address == (0, 0x0006) and len(value) == 7:
        century, year, month, day, hour, minute, flag = value
        try:
            decoded["clock"] = datetime(
                century * 100 + year,
                month,
                day,
                hour,
                minute,
            ).isoformat(timespec="minutes")
        except ValueError:
            pass
        decoded["clock_flag"] = flag
    elif address == (1, 0x0001) and len(value) == 4:
        decoded["backlight_selector"] = value[0]
        decoded["backlight_rgb"] = f"#{value[1]:02x}{value[2]:02x}{value[3]:02x}"
    elif address == (1, 0x0007) and len(value) == 1:
        decoded["last_key_code"] = value[0]
    elif address == (0, 0x100D) and len(value) == 1 and value[0] in (0, 1):
        decoded["advance_active"] = bool(value[0])
    elif address == (0, 0x1028) and len(value) == 2:
        decoded["runback_setpoint_c"] = int.from_bytes(value, "little")
    elif address == (0, 0x102A) and len(value) == 7:
        active, year, month, day, minute, hour, temperature = value
        decoded["away_active"] = bool(active)
        decoded["away_target_temperature_c"] = temperature
        try:
            decoded["away_until"] = datetime(
                2000 + year,
                month,
                day,
                hour,
                minute,
            ).isoformat(timespec="minutes")
        except ValueError:
            pass

    return decoded


def format_known_raw_value(address: GattAddress, value: bytes) -> str | None:
    """Return a human-readable value only for an established payload layout."""
    decoded = decode_known_raw_value(address, value)
    if not decoded:
        return None
    if address == (0, 0x0006):
        clock = decoded.get("clock")
        flag = decoded.get("clock_flag")
        return f"{clock} (flag {flag})" if clock is not None else f"flag {flag}"
    if address == (1, 0x0001):
        return f"{decoded['backlight_rgb']} (selector {decoded['backlight_selector']})"
    if address == (1, 0x0007):
        return f"key code {decoded['last_key_code']}"
    if address == (0, 0x100D):
        return "on" if decoded["advance_active"] else "off"
    if address == (0, 0x1028):
        return f"{decoded['runback_setpoint_c']} °C"
    if address == (0, 0x102A):
        status = "on" if decoded["away_active"] else "off"
        target = decoded["away_target_temperature_c"]
        until = decoded.get("away_until")
        suffix = f", until {until}" if until is not None else ""
        return f"{status}, target {target} °C{suffix}"
    return None


def format_readonly_raw_value(address: GattAddress, value: bytes) -> str | None:
    """Format only conservative or explicitly marked read-only guesses."""
    known = format_known_raw_value(address, value)
    if known is not None:
        return known
    if address == (3, 0x1029) and len(value) == 3:
        return f"#{value[0]:02x}{value[1]:02x}{value[2]:02x}"
    if address == (1, 0x1010) and len(value) == 1:
        return f"EOL test code {value[0]}"
    if address in {(1, 0x1020), (1, 0x300E), (2, 0x100C)} and len(value) == 1:
        if value[0] in (0, 1):
            return "on" if value[0] else "off"
    if address == (3, 0x200C) and len(value) == 6 and value[0] == 0x14:
        try:
            timestamp = datetime(
                value[0] * 100 + value[1],
                value[2],
                value[3],
                value[4],
                value[5],
            )
        except ValueError:
            return None
        return f"{timestamp.isoformat(timespec='minutes')} (possible build timestamp)"
    return None


def encode_setpoint(temperature_c: float) -> bytes:
    """Encode a whole-degree Celsius setpoint as the observed two-byte value."""
    value = float(temperature_c)
    if not isfinite(value) or not value.is_integer():
        raise DimplexProtocolError("The heater accepts whole-degree setpoints")
    if not 0 <= value <= 0xFF:
        raise DimplexProtocolError("Setpoint is outside the encodable range")
    return bytes((int(value), 0))


_PRESET_MODE_CODES: Final[dict[str, int]] = {
    "eco": 0x03,
    "frost": 0x04,
}


def encode_preset_mode(preset: str) -> bytes:
    """Encode a verified storage-heater preset command."""
    try:
        code = _PRESET_MODE_CODES[preset]
    except KeyError as err:
        raise DimplexProtocolError("Unsupported preset mode") from err
    return bytes((code, 0, 0, 0, 0, 0))


def parse_preset_mode(value: bytes | None) -> str | None:
    """Return a known preset name from the heater operating-mode payload."""
    if not value:
        return None
    for preset, code in _PRESET_MODE_CODES.items():
        if value[0] == code:
            return preset
    return None


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

    limits = values.get((0, 0x1006))
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
    )
