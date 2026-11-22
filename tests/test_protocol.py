from __future__ import annotations

import pytest

from custom_components.dimplex_ble.protocol import (
    BLOCKED_READS,
    BLOCKED_WRITES,
    DIMPLEX_GROUPS,
    MAX_GATT_VALUE_BYTES,
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    DimplexProtocolError,
    HeaterSnapshot,
    ProtectedCharacteristicError,
    _parse_selected_version,
    characteristic_uuid,
    decode_known_raw_value,
    encode_heating_mode,
    encode_preset_mode,
    encode_setpoint,
    encode_sound,
    format_heating_mode,
    format_known_raw_value,
    format_readonly_raw_value,
    gatt_uuid_candidates,
    parse_hex_payload,
    parse_preset_mode,
    parse_snapshot,
    parse_sound_enabled,
    service_uuid,
    validate_raw_access,
)


def test_parse_selected_version_requires_selector_and_complete_payload() -> None:
    assert _parse_selected_version(b"\x01\x07\x00", 1) == "7.0"
    assert _parse_selected_version(b"\x00\x07\x00", 1) is None
    assert _parse_selected_version(b"\x01\x07", 1) is None


@pytest.mark.parametrize("group", sorted(DIMPLEX_GROUPS))
def test_service_uuid_formats_each_supported_group(group: int) -> None:
    assert service_uuid(group) == (
        f"00000000-000{group}-1000-8000-00805f9b34fb"
    )

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
            service_uuid(group),
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



@pytest.mark.parametrize(
    ("address", "value", "expected"),
    [
        (
            (0, 0x0006),
            bytes.fromhex("141a0b11133001"),
            {"clock": "2026-11-17T19:48", "clock_flag": 1},
        ),
        (
            (1, 0x0001),
            bytes.fromhex("029eff96"),
            {"backlight_selector": 2, "backlight_rgb": "#9eff96"},
        ),
        ((1, 0x0007), b"\x06", {"last_key_code": 6}),
        ((0, 0x100D), b"\x01", {"advance_active": True}),
        ((0, 0x1028), bytes.fromhex("1500"), {"runback_setpoint_c": 21}),
        (
            (0, 0x102A),
            bytes.fromhex("001a0b11300607"),
            {
                "away_active": False,
                "away_target_temperature_c": 7,
                "away_until": "2026-11-17T06:48",
            },
        ),
    ],
)
def test_decode_known_raw_value_preserves_only_established_meanings(
    address: tuple[int, int],
    value: bytes,
    expected: dict[str, str | int | bool],
) -> None:
    assert decode_known_raw_value(address, value) == expected
    assert decode_known_raw_value((3, 0x200B), b"\x7f\x9d") == {}


def test_decode_known_raw_value_keeps_invalid_dates_raw() -> None:
    assert decode_known_raw_value((0, 0x0006), bytes.fromhex("141a0d140d3701")) == {
        "clock_flag": 1
    }
    assert decode_known_raw_value((0, 0x102A), bytes.fromhex("001a0d11300607")) == {
        "away_active": False,
        "away_target_temperature_c": 7,
    }


@pytest.mark.parametrize(
    ("address", "value"),
    [
        ((3, 0x9999), b"\x00\x00\x00\x00"),
        ((3, 0x9998), b"\x00"),
        ((3, 0x9997), b"\x00\x00\x00\x00\x00\x00\x00"),
    ],
)
def test_decode_known_raw_value_requires_matching_address(
    address: tuple[int, int], value: bytes
) -> None:
    assert decode_known_raw_value(address, value) == {}


@pytest.mark.parametrize(
    ("address", "value", "expected"),
    [
        ((0, 0x0006), bytes.fromhex("141a0b140d3701"), "2026-11-20T13:55 (flag 1)"),
        ((1, 0x0001), bytes.fromhex("029eff96"), "#9eff96 (selector 2)"),
        ((1, 0x0007), b"\x06", "key code 6"),
        ((0, 0x100D), b"\x00", "off"),
        ((0, 0x1028), bytes.fromhex("1500"), "21 °C"),
        (
            (0, 0x102A),
            bytes.fromhex("001a0b11300607"),
            "off, target 7 °C, until 2026-11-17T06:48",
        ),
    ],
)
def test_format_known_raw_value(
    address: tuple[int, int], value: bytes, expected: str
) -> None:
    assert format_known_raw_value(address, value) == expected


def test_format_known_raw_value_keeps_unknown_values_raw() -> None:
    assert format_known_raw_value((3, 0x200B), b"\x7f\x9d") is None


def test_format_known_raw_value_defensively_handles_unexpected_decoder_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.dimplex_ble import protocol

    monkeypatch.setattr(
        protocol, "decode_known_raw_value", lambda *_: {"unexpected": 1}
    )
    assert protocol.format_known_raw_value((3, 0x200F), b"\x00") is None


def test_format_known_raw_value_covers_away_on_and_missing_date() -> None:
    value = bytes.fromhex("011a0d11300607")
    assert decode_known_raw_value((0, 0x102A), value)["away_active"] is True
    assert format_known_raw_value((0, 0x102A), value) == "on, target 7 °C"
    invalid_date = bytes.fromhex("141aff140d3701")
    assert format_known_raw_value((0, 0x0006), invalid_date) == "flag 1"


@pytest.mark.parametrize(
    ("address", "value", "expected"),
    [
        ((3, 0x1029), bytes.fromhex("9eff96"), "#9eff96"),
        ((1, 0x1010), b"\x47", "EOL test code 71"),
        ((1, 0x1020), b"\x00", "off"),
        ((1, 0x300E), b"\x01", "on"),
        ((2, 0x100C), b"\x01", "on"),
        (
            (3, 0x200C),
            bytes.fromhex("1416050d0e00"),
            "2022-05-13T14:00 (possible build timestamp)",
        ),
    ],
)
def test_format_readonly_raw_value_uses_conservative_guesses(
    address: tuple[int, int], value: bytes, expected: str
) -> None:
    assert format_readonly_raw_value(address, value) == expected


def test_format_readonly_raw_value_falls_back_for_unresolved_payload() -> None:
    assert format_readonly_raw_value((3, 0x200F), bytes.fromhex("04e90f0000")) is None
    assert format_readonly_raw_value((3, 0x200C), bytes.fromhex("14160d0d0e00")) is None


def test_format_readonly_raw_value_requires_matching_address_and_length() -> None:
    assert format_readonly_raw_value((3, 0x9999), b"\x00\x01\x02") is None
    assert format_readonly_raw_value((3, 0x9998), b"\x00") is None
    assert (
        format_readonly_raw_value((3, 0x9997), bytes.fromhex("1401050d0e2a"))
        is None
    )


def test_format_readonly_raw_value_preserves_timestamp_minute() -> None:
    assert (
        format_readonly_raw_value((3, 0x200C), bytes.fromhex("1416050d0e2a"))
        == "2022-05-13T14:42 (possible build timestamp)"
    )


def test_parse_snapshot_preserves_product_text_and_exact_power_length() -> None:
    assert parse_snapshot({(3, 0x200A): b"\xff"}).product_code == "\ufffd"
    assert (
        parse_snapshot({(3, 0x200A): b"  EP4213  "}).product_code
        == "EP4213"
    )
    assert parse_snapshot({(3, 0x200A): b"XEP4213X"}).product_code == "XEP4213X"
    assert parse_snapshot({(3, 0x200A): b"\xc3\xa9"}).product_code == "\ufffd\ufffd"
    assert (
        parse_snapshot({(3, 0x000A): bytes.fromhex("f401")}).rated_power_w
        == 500
    )


def test_parse_snapshot_distinguishes_trac_active_flag_and_rejects_overrange_power(
) -> None:
    idle = parse_snapshot({}, trac=bytes.fromhex("010064"))
    assert idle.heating_active is False
    assert idle.trac_output_power_percent == 100

    overrange = parse_snapshot({}, trac=bytes.fromhex("010165"))
    assert overrange.trac_output_power_percent is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (bytes.fromhex("010100000000"), "Timer Mode (Out All Day)"),
        (bytes.fromhex("010300000000"), "Timer Mode (User Timer)"),
        (bytes.fromhex("02"), "Manual"),
        (bytes.fromhex("030000000000"), "Eco Mode"),
        (bytes.fromhex("040000000000"), "Frost Protect"),
    ],
)
def test_format_heating_mode_decodes_documented_modes(
    payload: bytes, expected: str
) -> None:
    assert format_heating_mode(payload) == expected


def test_format_heating_mode_returns_none_for_unknown_payload() -> None:
    assert format_heating_mode(b"") is None
    assert format_heating_mode(bytes.fromhex("fe0100000000")) is None


@pytest.mark.parametrize(("value", "expected"), [(0, b"\x00\x00"), (19.0, b"\x13\x00"), (255, b"\xff\x00")])
def test_encode_setpoint_encodes_whole_degrees(value: float, expected: bytes) -> None:
    assert encode_setpoint(value) == expected


@pytest.mark.parametrize("value", [19.5, float("nan"), float("inf"), float("-inf")])
def test_encode_setpoint_rejects_fractional_or_nonfinite_values(value: float) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_setpoint(value)
    assert str(err.value) == "The panel heater accepts whole-degree setpoints"


@pytest.mark.parametrize("value", [-1, 256])
def test_encode_setpoint_rejects_values_outside_byte_range(value: int) -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_setpoint(value)
    assert str(err.value) == "Setpoint is outside the encodable range"


@pytest.mark.parametrize(
    ("preset", "payload"),
    [
        ("manual", b"\x02\x00\x00\x00\x00\x00"),
        ("eco", b"\x03\x00\x00\x00\x00\x00"),
        ("frost_protect", b"\x04\x00\x00\x00\x00\x00"),
    ],
)
def test_preset_mode_encoding_matches_verified_commands(
    preset: str, payload: bytes
) -> None:
    assert encode_preset_mode(preset) == payload
    assert parse_preset_mode(payload) == preset


def test_preset_mode_parser_ignores_unknown_and_scheduled_values() -> None:
    assert parse_preset_mode(None) is None
    assert parse_preset_mode(b"") is None
    assert parse_preset_mode(b"\x01\x03\x00\x00\x00\x00") is None
    assert parse_preset_mode(b"\xfe\x00\x00\x00\x00\x00") is None


def test_preset_mode_encoding_rejects_unverified_modes() -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_preset_mode("boost")
    assert str(err.value) == "Unsupported preset mode"


def test_heating_mode_encoding_rejects_unknown_modes() -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_heating_mode(0xFF)
    assert str(err.value) == "Unsupported heating mode"


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b"\x00\x00\x00", False), (b"\x01\x00\x00", True)],
)
def test_sound_setting_decodes_verified_payload(payload: bytes, expected: bool) -> None:
    assert parse_sound_enabled(payload) is expected
    assert encode_sound(expected) == payload


def test_sound_setting_rejects_unknown_payloads() -> None:
    assert parse_sound_enabled(None) is None
    assert parse_sound_enabled(b"\x01") is None
    assert parse_sound_enabled(b"\x02\x00\x00") is None
    assert parse_sound_enabled(b"\x01\x01\x00") is None


def test_parse_snapshot_decodes_preemptive_heating_only_for_boolean_payloads() -> None:
    assert parse_snapshot({PREEMPTIVE_HEATING: b"\x01"}).preemptive_heating is True
    assert parse_snapshot({PREEMPTIVE_HEATING: b"\x00"}).preemptive_heating is False
    assert parse_snapshot({PREEMPTIVE_HEATING: b"\x02"}).preemptive_heating is None
    assert parse_snapshot({PREEMPTIVE_HEATING: b"\x01\x00"}).preemptive_heating is None


def test_parse_snapshot_decodes_open_window_only_for_boolean_payloads() -> None:
    enabled = b"\x01" + b"\x00" * 8
    disabled = b"\x00" + b"\x00" * 8
    assert parse_snapshot({OPEN_WINDOW_DETECTION: enabled}).open_window_detection is True
    assert parse_snapshot({OPEN_WINDOW_DETECTION: disabled}).open_window_detection is False
    assert (
        parse_snapshot({OPEN_WINDOW_DETECTION: b"\x02" + b"\x00" * 8})
        .open_window_detection
        is None
    )
    assert parse_snapshot({OPEN_WINDOW_DETECTION: b"\x01"}).open_window_detection is None


def test_parse_snapshot_decodes_all_available_fields() -> None:
    snapshot = parse_snapshot(
        {
            (3, 0x2005): b"QRAD100E\x00 ",
            (3, 0x200A): b"EP4213\x00",
            (3, 0x2009): b"\x05",
            (3, 0x000A): bytes.fromhex("f401980800000000"),
            (0, 0x1023): b"\x15\x00",
            (0, 0x1006): bytes((5, 0, 30, 0)),
            (1, 0x0008): bytes((1, 21, 7)),
            (0, 0x1011): b"\x34\x12",
            (0, 0x1001): b"\x02\x03",
            (0, 0x1005): b"\x01",
        },
        lcd_temperature=bytes.fromhex("021d00"),
        ui_version=bytes.fromhex("010107"),
        trac=bytes.fromhex("010164"),
    )
    assert snapshot == HeaterSnapshot(
        "QRAD100E",
        21,
        5,
        30,
        21.7,
        0x1234,
        "3412",
        "0203",
        1,
        rated_power_w=500,
        power_loading_hex="f401980800000000",
        lcd_temperature_c=29.0,
        trac_status_hex="010164",
        heating_active=True,
        trac_output_power_percent=100,
        product_code="EP4213",
        ui_version="1.7",
        rf_firmware_revision=5,
    )


def test_parse_snapshot_decodes_idle_trac_and_rejects_untrusted_values() -> None:
    idle = parse_snapshot({}, trac=bytes.fromhex("010000"))
    assert idle.trac_status_hex == "010000"
    assert idle.heating_active is False
    assert idle.trac_output_power_percent == 0

    invalid = parse_snapshot({}, trac=bytes.fromhex("0301ff"))
    assert invalid.trac_status_hex == "0301ff"
    assert invalid.heating_active is None
    assert invalid.trac_output_power_percent is None


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
