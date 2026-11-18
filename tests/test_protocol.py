from __future__ import annotations

import pytest

from custom_components.dimplex_ble.protocol import (
    BLOCKED_READS,
    BLOCKED_WRITES,
    DIMPLEX_GROUPS,
    MAX_GATT_VALUE_BYTES,
    DimplexProtocolError,
    HeaterSnapshot,
    ProtectedCharacteristicError,
    characteristic_uuid,
    encode_setpoint,
    gatt_uuid_candidates,
    parse_hex_payload,
    parse_snapshot,
    service_uuid,
    validate_raw_access,
)


@pytest.mark.parametrize("group", sorted(DIMPLEX_GROUPS))
def test_service_uuid_formats_each_supported_group(group: int) -> None:
    assert service_uuid(group) == f"00000000-000{group}-1000-8000-00805f9b34fb"


@pytest.mark.parametrize("group", [-1, 4, 255])
def test_service_uuid_rejects_groups_outside_dimplex_services(group: int) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        service_uuid(group)
    assert str(err.value) == "Service group must be between 0 and 3"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, "00000000-0000-1000-8000-00805f9b34fb"), (0x1023, "00001023-0000-1000-8000-00805f9b34fb"), (65535, "0000ffff-0000-1000-8000-00805f9b34fb")],
)
def test_characteristic_uuid_formats_16_bit_ids(value: int, expected: str) -> None:
    assert characteristic_uuid(value) == expected


@pytest.mark.parametrize("value", [-1, 65536])
def test_characteristic_uuid_rejects_out_of_range_ids(value: int) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        characteristic_uuid(value)
    assert str(err.value) == "Characteristic ID must be between 0 and 65535"


@pytest.mark.parametrize("group", sorted(DIMPLEX_GROUPS))
def test_gatt_uuid_candidates_include_sig_and_gdhv_bases(group: int) -> None:
    assert gatt_uuid_candidates(group, 0x1023) == (
        (
            f"00000000-000{group}-1000-8000-00805f9b34fb",
            "00001023-0000-1000-8000-00805f9b34fb",
        ),
        (
            f"00000000-000{group}-474c-4e44-494d504c4558",
            f"00001023-000{group}-474c-4e44-494d504c4558",
        ),
    )


def test_validate_raw_access_allows_safe_reads_and_writes() -> None:
    for group in range(4):
        validate_raw_access(group, 0x1234, write=False)
        validate_raw_access(group, 0x1234, write=True)


@pytest.mark.parametrize("address", sorted(BLOCKED_READS))
def test_validate_raw_access_blocks_every_protected_read(address: tuple[int, int]) -> None:
    with pytest.raises(ProtectedCharacteristicError) as err:
        validate_raw_access(*address, write=False)
    assert str(err.value) == "This characteristic is blocked for reads"


@pytest.mark.parametrize("address", sorted(BLOCKED_WRITES))
def test_validate_raw_access_blocks_every_protected_write(address: tuple[int, int]) -> None:
    with pytest.raises(ProtectedCharacteristicError) as err:
        validate_raw_access(*address, write=True)
    assert str(err.value) == "This characteristic is blocked for writes"


@pytest.mark.parametrize("address", sorted(BLOCKED_READS))
def test_read_protection_does_not_implicitly_block_writes(address: tuple[int, int]) -> None:
    if address not in BLOCKED_WRITES:
        validate_raw_access(*address, write=True)


@pytest.mark.parametrize("address", sorted(BLOCKED_WRITES - BLOCKED_READS))
def test_write_protection_does_not_implicitly_block_reads(address: tuple[int, int]) -> None:
    validate_raw_access(*address, write=False)


@pytest.mark.parametrize(("value", "expected"), [("01", b"\x01"), ("00 ff", b"\x00\xff"), ("aB" * MAX_GATT_VALUE_BYTES, bytes([0xAB]) * MAX_GATT_VALUE_BYTES)])
def test_parse_hex_payload_decodes_valid_values(value: str, expected: bytes) -> None:
    assert parse_hex_payload(value) == expected


@pytest.mark.parametrize("value", ["gg", "0", "zz 00"])
def test_parse_hex_payload_rejects_invalid_hex(value: str) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        parse_hex_payload(value)
    assert str(err.value) == "Payload must contain hexadecimal bytes"


def test_parse_hex_payload_rejects_empty_and_oversized_payloads() -> None:
    with pytest.raises(DimplexProtocolError) as err:
        parse_hex_payload("")
    assert str(err.value) == "Payload must not be empty"

    with pytest.raises(DimplexProtocolError) as err:
        parse_hex_payload("00" * (MAX_GATT_VALUE_BYTES + 1))
    assert str(err.value) == "Payload exceeds the 512-byte GATT value limit"


@pytest.mark.parametrize(("value", "expected"), [(0, b"\x00\x00"), (19.0, b"\x13\x00"), (255, b"\xff\x00")])
def test_encode_setpoint_encodes_whole_degrees(value: float, expected: bytes) -> None:
    assert encode_setpoint(value) == expected


@pytest.mark.parametrize("value", [19.5, float("nan"), float("inf"), float("-inf")])
def test_encode_setpoint_rejects_fractional_or_nonfinite_values(value: float) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_setpoint(value)
    assert str(err.value) == "The heater accepts whole-degree setpoints"


@pytest.mark.parametrize("value", [-1, 256])
def test_encode_setpoint_rejects_values_outside_byte_range(value: int) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_setpoint(value)
    assert str(err.value) == "Setpoint is outside the encodable range"


def test_parse_snapshot_decodes_all_available_fields() -> None:
    snapshot = parse_snapshot(
        {
            (3, 0x2005): b"QRAD100E\x00 ",
            (0, 0x1023): b"\x15\x00",
            (0, 0x1006): bytes((5, 0, 30, 0)),
            (1, 0x0008): bytes((1, 21, 7)),
            (0, 0x1011): b"\x34\x12",
            (0, 0x1001): b"\x02\x03",
            (0, 0x1005): b"\x01",
        }
    )
    assert snapshot == HeaterSnapshot("QRAD100E", 21, 5, 30, 21.7, 0x1234, "3412", "0203", 1)


@pytest.mark.parametrize(
    ("values", "expected"),
    [({}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(3, 0x2005): b"\x00 \xff"}, HeaterSnapshot("\ufffd", None, None, None, None, None, None, None, None)),
     ({(3, 0x2005): b"\xc3\xa9"}, HeaterSnapshot("\ufffd\ufffd", None, None, None, None, None, None, None, None)),
     ({(0, 0x1023): b"\x00\x00"}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(0, 0x1023): b"\x14"}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(0, 0x1006): bytes((31, 0, 10, 0))}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(0, 0x1006): b"\x01\x00\x02"}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(1, 0x0008): bytes((0, 20, 3))}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(1, 0x0008): bytes((1, 20, 10))}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
     ({(0, 0x1011): b"\x00\x00"}, HeaterSnapshot(None, None, None, None, None, 0, "0000", None, None)),
     ({(3, 0x2005): b"XXheaterXX"}, HeaterSnapshot("XXheaterXX", None, None, None, None, None, None, None, None)),
     ({(0, 0x1023): b"\x15\x00\x01"}, HeaterSnapshot(None, 21, None, None, None, None, None, None, None)),
     ({(0, 0x1006): bytes((5, 0, 5, 0))}, HeaterSnapshot(None, None, 5, 5, None, None, None, None, None)),
     ({(1, 0x0008): bytes((1, 20, 0))}, HeaterSnapshot(None, None, None, None, 20.0, None, None, None, None)),
     ({(1, 0x0008): bytes((1, 20, 9))}, HeaterSnapshot(None, None, None, None, 20.9, None, None, None, None)),
     ({(0, 0x1005): b"\x00"}, HeaterSnapshot(None, None, None, None, None, None, None, None, 0))],
)
def test_parse_snapshot_handles_missing_malformed_and_zero_fields(values: dict[tuple[int, int], bytes], expected: HeaterSnapshot) -> None:
    assert parse_snapshot(values) == expected
