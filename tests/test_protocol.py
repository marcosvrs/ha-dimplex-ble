from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.dimplex_ble.protocol import (
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    DimplexProtocolError,
    HeaterSnapshot,
    _parse_selected_version,
    encode_away_mode,
    encode_preset_mode,
    encode_setback,
    encode_setpoint,
    encode_sound,
    encode_temperature_offset,
    error_code_state,
    gatt_uuid_candidates,
    heating_mode_key,
    heating_mode_of,
    parse_away_mode,
    parse_flag,
    parse_model,
    parse_setback,
    parse_setback_temperature,
    parse_snapshot,
    parse_sound_enabled,
    parse_temperature_offset,
)


def test_parse_selected_version_requires_selector_and_complete_payload() -> None:
    assert _parse_selected_version(b"\x01\x07\x00", 1) == "7.0"
    assert _parse_selected_version(b"\x00\x07\x00", 1) is None
    assert _parse_selected_version(b"\x01\x07", 1) is None


@pytest.mark.parametrize("group", range(4))
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


def test_away_mode_round_trips_verified_payload() -> None:
    until = datetime(2026, 11, 22, 18, 53)
    payload = encode_away_mode(True, until)

    assert payload == bytes.fromhex("011a0b16351207")
    assert parse_away_mode(payload) == (True, until, 7)


def test_parse_snapshot_preserves_product_text_and_exact_power_length() -> None:
    assert parse_snapshot({(3, 0x200A): b"\xff"}).product_code == "\ufffd"
    assert parse_snapshot({(3, 0x200A): b"  EP4213  "}).product_code == "EP4213"
    assert parse_snapshot({(3, 0x200A): b"XEP4213X"}).product_code == "XEP4213X"
    assert parse_snapshot({(3, 0x200A): b"\xc3\xa9"}).product_code == "\ufffd\ufffd"
    assert parse_snapshot({(3, 0x000A): bytes.fromhex("f401")}).rated_power_w == 500


def test_parse_snapshot_distinguishes_trac_active_flag_and_rejects_overrange_power() -> (
    None
):
    idle = parse_snapshot({}, trac=bytes.fromhex("010064"))
    assert idle.heating_active is False
    assert idle.trac_output_power_percent == 100

    overrange = parse_snapshot({}, trac=bytes.fromhex("010165"))
    assert overrange.trac_output_power_percent is None


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (bytes.fromhex("010100000000"), "out_all_day"),
        (bytes.fromhex("010200000000"), "home_all_day"),
        (bytes.fromhex("010300000000"), "user_timer"),
        (bytes.fromhex("010500000000"), "away"),
        (bytes.fromhex("01"), "timer"),
        (bytes.fromhex("01fe00000000"), "timer"),
        (bytes.fromhex("02"), "manual"),
        (bytes.fromhex("030000000000"), "eco"),
        (bytes.fromhex("040000000000"), "frost_protect"),
    ],
)
def test_heating_mode_key_decodes_documented_modes(
    payload: bytes, expected: str
) -> None:
    assert heating_mode_key(payload) == expected


def test_heating_mode_key_returns_none_for_unknown_payload() -> None:
    assert heating_mode_key(None) is None
    assert heating_mode_key(b"") is None
    assert heating_mode_key(bytes.fromhex("fe0100000000")) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PLX050E;C;", "PLX050E Series C"),
        ("QRAD100E;d", "QRAD100E Series D"),
        ("QRAD100E", "QRAD100E"),
        ("PLX050E;CC;", "PLX050E CC"),
        ("PLX050E;1;", "PLX050E 1"),
        ("A;B;C", "A B C"),
        (";;", None),
        (None, None),
    ],
)
def test_parse_model_names_the_series(raw: str | None, expected: str | None) -> None:
    assert parse_model(raw) == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (None, None),
        (0, "no_error"),
        (41, "code_41"),
        (50, "code_50"),
        (61, "code_61"),
        (31, "Code 31"),
        (99, "Code 99"),
    ],
)
def test_error_code_state_names_only_documented_codes(
    code: int | None, expected: str | None
) -> None:
    assert error_code_state(code) == expected


@pytest.mark.parametrize(
    ("value", "expected"), [(0, b"\x00\x00"), (19.0, b"\x13\x00"), (255, b"\xff\x00")]
)
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
    assert heating_mode_key(payload) == preset


def test_home_all_day_preset_encoding() -> None:
    with pytest.raises(DimplexProtocolError):
        encode_preset_mode("home")
    assert encode_preset_mode("home_all_day") == b"\x01\x02\x00\x00\x00\x00"


def test_heating_mode_of_a_snapshot_hex() -> None:
    assert heating_mode_of(None) is None
    assert heating_mode_of("not hex") is None
    assert heating_mode_of("") is None
    assert heating_mode_of("010300000000") == "user_timer"
    assert heating_mode_of("01") == "timer"
    assert heating_mode_of("fe0000000000") is None


def test_preset_mode_encoding_rejects_unverified_modes() -> None:
    with pytest.raises(DimplexProtocolError) as err:
        encode_preset_mode("boost")
    assert str(err.value) == "Unsupported preset mode"


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
    assert (
        parse_snapshot({OPEN_WINDOW_DETECTION: enabled}).open_window_detection is True
    )
    assert (
        parse_snapshot({OPEN_WINDOW_DETECTION: disabled}).open_window_detection is False
    )
    assert (
        parse_snapshot(
            {OPEN_WINDOW_DETECTION: b"\x02" + b"\x00" * 8}
        ).open_window_detection
        is None
    )
    assert (
        parse_snapshot({OPEN_WINDOW_DETECTION: b"\x01"}).open_window_detection is None
    )


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
    [
        ({}, HeaterSnapshot(None, None, None, None, None, None, None, None, None)),
        (
            {(3, 0x2005): b"\x00 \xff"},
            HeaterSnapshot("\ufffd", None, None, None, None, None, None, None, None),
        ),
        (
            {(3, 0x2005): b"\xc3\xa9"},
            HeaterSnapshot(
                "\ufffd\ufffd", None, None, None, None, None, None, None, None
            ),
        ),
        (
            {(0, 0x1023): b"\x00\x00"},
            HeaterSnapshot(None, None, None, None, None, None, None, None, None),
        ),
        (
            {(0, 0x1023): b"\x14"},
            HeaterSnapshot(None, None, None, None, None, None, None, None, None),
        ),
        (
            {(0, 0x1006): bytes((31, 0, 10, 0))},
            HeaterSnapshot(None, None, None, None, None, None, None, None, None),
        ),
        (
            {(0, 0x1006): b"\x01\x00\x02"},
            HeaterSnapshot(None, None, None, None, None, None, None, None, None),
        ),
        (
            {(1, 0x0008): bytes((0, 20, 3))},
            HeaterSnapshot(None, None, None, None, None, None, None, None, None),
        ),
        (
            {(1, 0x0008): bytes((1, 20, 10))},
            HeaterSnapshot(None, None, None, None, None, None, None, None, None),
        ),
        (
            {(0, 0x1011): b"\x00\x00"},
            HeaterSnapshot(None, None, None, None, None, 0, "0000", None, None),
        ),
        (
            {(3, 0x2005): b"XXheaterXX"},
            HeaterSnapshot(
                "XXheaterXX", None, None, None, None, None, None, None, None
            ),
        ),
        (
            {(0, 0x1023): b"\x15\x00\x01"},
            HeaterSnapshot(None, 21, None, None, None, None, None, None, None),
        ),
        (
            {(0, 0x1006): bytes((5, 0, 5, 0))},
            HeaterSnapshot(None, None, 5, 5, None, None, None, None, None),
        ),
        (
            {(1, 0x0008): bytes((1, 20, 0))},
            HeaterSnapshot(None, None, None, None, 20.0, None, None, None, None),
        ),
        (
            {(1, 0x0008): bytes((1, 20, 9))},
            HeaterSnapshot(None, None, None, None, 20.9, None, None, None, None),
        ),
        (
            {(0, 0x1005): b"\x00"},
            HeaterSnapshot(None, None, None, None, None, None, None, None, 0),
        ),
    ],
)
def test_parse_snapshot_handles_missing_malformed_and_zero_fields(
    values: dict[tuple[int, int], bytes], expected: HeaterSnapshot
) -> None:
    assert parse_snapshot(values) == expected


def test_given_invalid_away_payload_or_encodable_limits_then_protocol_errors_are_clear() -> (
    None
):
    """Given invalid Away values, parsing and encoding reject them safely."""
    assert parse_away_mode(b"\x01") is None
    assert parse_away_mode(bytes.fromhex("01000000000007"))[1] is None

    with pytest.raises(DimplexProtocolError, match="year"):
        encode_away_mode(True, datetime(1999, 1, 1))
    with pytest.raises(DimplexProtocolError, match="target"):
        encode_away_mode(True, datetime(2026, 1, 1), 256)


def test_out_all_day_preset_round_trips_the_timer_submode() -> None:
    payload = encode_preset_mode("out_all_day")
    assert payload == b"\x01\x01\x00\x00\x00\x00"
    assert heating_mode_key(payload) == "out_all_day"


@pytest.mark.parametrize(("payload", "active"), [(b"\x00", False), (b"\x01", True)])
def test_advance_reads_off_and_on(payload: bytes, active: bool) -> None:
    assert parse_flag(payload) is active


@pytest.mark.parametrize("payload", [None, b"", b"\x02", b"\x06", b"\x81", b"\x00\x00"])
def test_advance_leaves_boost_variants_and_bad_lengths_unknown(
    payload: bytes | None,
) -> None:
    assert parse_flag(payload) is None


def test_parse_snapshot_decodes_advance() -> None:
    assert parse_snapshot({(0, 0x100D): b"\x01"}).advance_active is True
    assert parse_snapshot({(0, 0x100D): b"\x05"}).advance_active is None
    assert parse_snapshot({}).advance_active is None


@pytest.mark.parametrize(
    ("payload", "active"), [(b"\x00\x08", False), (b"\x01\x08", True)]
)
def test_setback_round_trips_and_keeps_the_temperature_byte(
    payload: bytes, active: bool
) -> None:
    assert parse_setback(payload) is active
    assert encode_setback(active, payload) == payload
    assert encode_setback(not active, payload)[1:] == b"\x08"


@pytest.mark.parametrize(
    ("payload", "temperature"),
    [
        (b"\x00\x08", 8),
        (b"\x01\x0f", 15),
        (b"\x00\x07", None),  # refused by AM6532 and AM2086 2026-11-25
        (b"\x00\x12", 18),
        (b"\x00\x06", None),
        (b"\x00\x13", 19),  # heater menu maximum; kept by AM2086 2026-11-25
        (b"\x00\x14", None),
        (b"\x02\x08", None),
        (None, None),
    ],
)
def test_setback_temperature_is_whole_degrees(payload, temperature) -> None:
    """AM2086 2026-11-25: display 8 degC read 0x08; a written 0x0F showed 15 degC."""
    assert parse_setback_temperature(payload) == temperature


def test_setback_temperature_keeps_setback_on_or_off() -> None:
    assert encode_setback(None, b"\x01\x08", 15) == b"\x01\x0f"
    assert encode_setback(None, b"\x00\x0f", 8) == b"\x00\x08"
    for bad in (7, 20):
        with pytest.raises(DimplexProtocolError):
            encode_setback(None, b"\x00\x08", bad)


@pytest.mark.parametrize("payload", [None, b"", b"\x01", b"\x02\x08", b"\x01\x08\x00"])
def test_setback_rejects_unknown_payloads(payload: bytes | None) -> None:
    assert parse_setback(payload) is None
    with pytest.raises(DimplexProtocolError):
        encode_setback(True, payload)


@pytest.mark.parametrize(
    ("raw", "offset"),
    [
        (0, 0.0),
        (2, 0.2),
        (30, 3.0),
        (100, 10.0),
        (101, -15.5),
        (226, -3.0),
        (254, -0.2),
        (255, -0.1),
    ],
)
def test_temperature_offset_decodes_configr_signed_tenths(
    raw: int, offset: float
) -> None:
    assert parse_temperature_offset(bytes((raw,))) == pytest.approx(offset)


@pytest.mark.parametrize("offset", [-3.0, -2.9, -0.2, -0.1, 0.0, 0.1, 0.2, 1.5, 3.0])
def test_temperature_offset_round_trips_within_the_limit(offset: float) -> None:
    assert parse_temperature_offset(encode_temperature_offset(offset)) == (
        pytest.approx(offset)
    )


@pytest.mark.parametrize("offset", [-3.1, 3.1, 0.05, 1.25, float("nan"), float("inf")])
def test_temperature_offset_rejects_unsafe_or_unencodable_values(
    offset: float,
) -> None:
    with pytest.raises(DimplexProtocolError):
        encode_temperature_offset(offset)


@pytest.mark.parametrize("payload", [None, b"", b"\x00\x00"])
def test_temperature_offset_rejects_bad_lengths(payload: bytes | None) -> None:
    assert parse_temperature_offset(payload) is None


@pytest.mark.parametrize(
    ("low", "high", "payload"),
    [
        (7, 30, b"\x07\x00\x1e\x00"),
        (7, 8, b"\x07\x00\x08\x00"),
        (16, 22, b"\x10\x00\x16\x00"),
    ],
)
def test_setpoint_range_is_read_from_the_snapshot(
    low: int, high: int, payload: bytes
) -> None:
    snapshot = parse_snapshot({(0, 0x1006): payload})
    assert (snapshot.minimum_temperature_c, snapshot.maximum_temperature_c) == (
        low,
        high,
    )
