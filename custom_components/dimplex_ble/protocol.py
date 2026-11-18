"""Dimplex BLE identifiers, byte decoding, and conservative raw-GATT policy."""

from __future__ import annotations

from dataclasses import dataclass
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

# Keep the established protection list for fields whose format or purpose is unsafe.
BLOCKED_WRITES: Final = frozenset(
    {(0, value) for value in range(0x1012, 0x1016)}
    | {
        (1, 0x1010),
        (1, 0x1020),
        (1, 0x300E),
        (2, 0x100C),
        (2, 0x700F),
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
        (3, 0x200F),
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


def encode_setpoint(temperature_c: float) -> bytes:
    """Encode a whole-degree Celsius setpoint as the observed two-byte value."""
    value = float(temperature_c)
    if not isfinite(value) or not value.is_integer():
        raise DimplexProtocolError("The heater accepts whole-degree setpoints")
    if not 0 <= value <= 0xFF:
        raise DimplexProtocolError("Setpoint is outside the encodable range")
    return bytes((int(value), 0))


def parse_snapshot(values: dict[GattAddress, bytes]) -> HeaterSnapshot:
    """Decode the safe, known fields from a collection of raw GATT values."""
    identity = values.get((3, 0x2005))
    model = (
        identity.decode("ascii", errors="replace").strip("\x00 ") or None
        if identity is not None
        else None
    )

    setpoint = values.get((0, 0x1023))
    setpoint_c = int.from_bytes(setpoint[:2], "little") if setpoint and len(setpoint) >= 2 else None
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
    current_temperature_c = (
        room[1] + room[2] / 10
        if (
            room is not None
            and len(room) >= 3
            and room[0] == 1
            and 0 <= room[2] <= 9
        )
        else None
    )

    error = values.get((0, 0x1011))
    error_code = int.from_bytes(error, "little") if error else None
    error_status_hex = error.hex() if error is not None else None

    mode = values.get((0, 0x1001))
    display_unit = values.get((0, 0x1005))

    return HeaterSnapshot(
        model=model,
        setpoint_c=setpoint_c,
        minimum_temperature_c=minimum_temperature_c,
        maximum_temperature_c=maximum_temperature_c,
        current_temperature_c=current_temperature_c,
        error_code=error_code,
        error_status_hex=error_status_hex,
        operating_mode_hex=mode.hex() if mode is not None else None,
        display_unit=(display_unit[0] if display_unit else None),
    )
