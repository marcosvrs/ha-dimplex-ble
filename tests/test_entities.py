from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.components.climate import ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.components.climate.const import PRESET_AWAY
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.climate import DimplexClimate
from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.protocol import (
    PRESET_HOME_ALL_DAY,
    PRESET_MANUAL,
    PRESET_USER_TIMER,
    HeaterSnapshot,
    encode_preset_mode,
    encode_setpoint,
)
from custom_components.dimplex_ble.select import DimplexDisplayUnitSelect
from custom_components.dimplex_ble.sensor import (
    DimplexCalculatedPowerSensor,
    DimplexErrorCodeSensor,
    DimplexLcdTemperatureSensor,
    DimplexOperatingModeSensor,
    DimplexOutputPowerSensor,
    DimplexRatedPowerSensor,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def snapshot(
    *,
    model="QRAD100E",
    setpoint=21,
    minimum=5,
    maximum=30,
    current=19.5,
    error=48879,
    error_hex="efbe",
    mode_hex="0201",
    unit=0,
    rated_power=None,
    power_loading_hex=None,
    lcd_temperature=None,
    trac_status_hex=None,
    heating_active=None,
    trac_output_power_percent=None,
    product_code=None,
    ui_version=None,
    rf_firmware_revision=None,
    away_active=None,
    away_until=None,
    away_target_temperature_c=None,
    boost_minutes_left=None,
    boost_temperature_c=None,
):
    return HeaterSnapshot(
        model=model,
        setpoint_c=setpoint,
        minimum_temperature_c=minimum,
        maximum_temperature_c=maximum,
        current_temperature_c=current,
        error_code=error,
        error_status_hex=error_hex,
        operating_mode_hex=mode_hex,
        display_unit=unit,
        rated_power_w=rated_power,
        power_loading_hex=power_loading_hex,
        lcd_temperature_c=lcd_temperature,
        trac_status_hex=trac_status_hex,
        heating_active=heating_active,
        trac_output_power_percent=trac_output_power_percent,
        product_code=product_code,
        ui_version=ui_version,
        rf_firmware_revision=rf_firmware_revision,
        away_active=away_active,
        away_until=away_until,
        away_target_temperature_c=away_target_temperature_c,
        boost_minutes_left=boost_minutes_left,
        boost_temperature_c=boost_temperature_c,
    )


def make_coordinator(hass, *, data=None, client=None, entry=None):
    """Build a coordinator whose link forwards writes to a recording fake."""
    config_entry = entry or MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    client = client or SimpleNamespace()

    async def async_write(address, payload):
        await client.async_write_raw(*address, payload)

    async def async_write_many(writes):
        for address, payload in writes:
            await client.async_write_raw(*address, payload)

    async def async_read_then_write(address, build, *, read_back=False):
        read = getattr(client, "async_read_raw", None)
        payload = build(await read(*address) if read is not None else None)
        await client.async_write_raw(*address, payload)
        return payload

    link = SimpleNamespace(
        address=ADDRESS,
        source=PROXY,
        async_write=async_write,
        async_write_many=async_write_many,
        async_read_then_write=async_read_then_write,
        sync_clock=True,
        clock_offset_s=None,
    )
    coordinator = DimplexCoordinator(hass, config_entry, link)
    coordinator.data = data
    return config_entry, coordinator


def test_rule_entity_device_identity_is_stable_and_does_not_expose_proxy(hass):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            model="PLX050E;C;",
            product_code="EP4213",
            ui_version="1.7",
            rf_firmware_revision=5,
        ),
    )
    entity = DimplexClimate(coordinator)
    assert entity.unique_id == "aa:bb:cc:dd:ee:20_heater"
    assert entity.device_info["identifiers"] == {(DOMAIN, "aa:bb:cc:dd:ee:20")}
    assert entity.device_info["connections"] == {("bluetooth", "aa:bb:cc:dd:ee:20")}
    assert entity.device_info["name"] == "Dimplex Heater 1"
    assert entity.device_info["model"] == "PLX050E Series C"
    assert entity.device_info["model_id"] == "PLX050E"
    assert entity.device_info["sw_version"] == "UI 1.7 / RF 5"
    assert PROXY not in str(entity.device_info)


def test_rule_device_model_is_unknown_before_the_first_poll(hass):
    _, coordinator = make_coordinator(hass)
    entity = DimplexClimate(coordinator)
    assert entity.device_info["name"] == "Dimplex Heater 1"
    assert entity.device_info["model"] is None


def test_rule_climate_uses_ha_temperature_limits_without_reported_snapshot(hass):
    _, coordinator = make_coordinator(hass)
    entity = DimplexClimate(coordinator)
    assert entity.native_current_temperature is None
    assert entity.native_target_temperature is None
    assert entity.min_temp == 7.0
    assert entity.max_temp == 35.0


async def test_rule_climate_translates_protocol_rejections(hass):
    from custom_components.dimplex_ble.protocol import DimplexProtocolError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=DimplexProtocolError("bad value"))
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_temperature(temperature=22)
    assert err.value.translation_key == "invalid_gatt_request"


@pytest.mark.parametrize(
    ("error", "expected"),
    [(0, "no_error"), (50, "code_50"), (48879, "Code 48879"), (None, None)],
)
def test_rule_error_sensor_names_documented_codes_only(hass, error, expected):
    _, coordinator = make_coordinator(hass, data=snapshot(error=error))
    sensor = DimplexErrorCodeSensor(coordinator)
    assert sensor.native_value == expected
    assert sensor.extra_state_attributes is None


def test_rule_climate_exposes_reported_temperature_and_supported_capabilities(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    entity = DimplexClimate(coordinator)
    assert entity.available is True
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.hvac_modes == [HVACMode.HEAT, HVACMode.AUTO]
    assert entity._attr_native_temperature_unit == UnitOfTemperature.CELSIUS
    assert entity.target_temperature_step == 1
    assert entity.precision == 0.1
    assert entity.name is None  # the main feature: named after the device
    assert entity.supported_features == (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    assert entity.preset_modes == CONTINUOUS
    assert entity.preset_mode == "manual"
    assert entity.native_current_temperature == 19.5
    assert entity.native_target_temperature == 21.0
    assert entity.min_temp == 5.0
    assert entity.max_temp == 30.0


@pytest.mark.parametrize(
    ("heating_active", "expected"),
    [(True, HVACAction.HEATING), (False, HVACAction.IDLE), (None, None)],
)
def test_rule_climate_reports_only_directly_reported_hvac_action(
    hass, heating_active, expected
):
    _, coordinator = make_coordinator(
        hass, data=snapshot(heating_active=heating_active)
    )
    assert DimplexClimate(coordinator).hvac_action == expected


def test_rule_diagnostic_sensors_expose_lcd_temperature_and_output_power(
    hass,
):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            rated_power=500,
            lcd_temperature=29.0,
            trac_status_hex="010164",
            trac_output_power_percent=100,
        ),
    )
    lcd_sensor = DimplexLcdTemperatureSensor(coordinator)
    output_sensor = DimplexOutputPowerSensor(coordinator)
    power_sensor = DimplexCalculatedPowerSensor(coordinator)
    assert lcd_sensor.native_value == 29.0
    assert lcd_sensor.device_class == "temperature"
    assert lcd_sensor.native_unit_of_measurement == UnitOfTemperature.CELSIUS
    assert lcd_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert lcd_sensor.entity_registry_enabled_default is False
    assert output_sensor.native_value == 100
    assert output_sensor.extra_state_attributes is None
    assert output_sensor.native_unit_of_measurement == PERCENTAGE
    assert output_sensor.state_class == "measurement"
    assert output_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert output_sensor.entity_registry_enabled_default is False
    assert power_sensor.native_value == 500
    assert power_sensor.device_class == "power"
    assert power_sensor.native_unit_of_measurement == "W"
    assert power_sensor.state_class == "measurement"
    assert power_sensor.entity_category == EntityCategory.DIAGNOSTIC  # off dashboards


SCHEDULES = ["user_timer", "home_all_day", "out_all_day", "boost", "away"]
CONTINUOUS = ["manual", "eco", "frost_protect", "boost", "away"]


@pytest.mark.parametrize(
    ("mode_hex", "expected", "expected_hvac_mode", "presets"),
    [
        ("010300000000", PRESET_USER_TIMER, HVACMode.AUTO, SCHEDULES),
        ("010200000000", PRESET_HOME_ALL_DAY, HVACMode.AUTO, SCHEDULES),
        ("010100000000", "out_all_day", HVACMode.AUTO, SCHEDULES),
        ("020000000000", "manual", HVACMode.HEAT, CONTINUOUS),
        ("030000000000", "eco", HVACMode.HEAT, CONTINUOUS),
        ("040000000000", "frost_protect", HVACMode.HEAT, CONTINUOUS),
    ],
)
def test_rule_climate_reports_verified_modes(
    hass, mode_hex, expected, expected_hvac_mode, presets
):
    _, coordinator = make_coordinator(hass, data=snapshot(mode_hex=mode_hex))
    entity = DimplexClimate(coordinator)
    assert entity.preset_modes == presets
    assert entity.hvac_mode == expected_hvac_mode
    assert entity.preset_mode == expected
    # HA climate docs: no temperature can be set in Auto (the schedule sets it).
    # Every mode offers a target; Auto's needs the programme or Setback read.
    has_target = expected_hvac_mode == HVACMode.HEAT
    assert ClimateEntityFeature.TARGET_TEMPERATURE in entity.supported_features
    assert ClimateEntityFeature.PRESET_MODE in entity.supported_features
    assert (entity.native_target_temperature is not None) is has_target


def test_rule_climate_offers_every_preset_while_the_mode_is_unknown(hass):
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex=None, away_active=False)
    )
    entity = DimplexClimate(coordinator)
    assert entity.hvac_mode is None
    assert entity.preset_modes == [
        "manual",
        "eco",
        "frost_protect",
        "user_timer",
        "home_all_day",
        "out_all_day",
        "boost",
        "away",
    ]


def test_rule_climate_returns_no_preset_without_mode_payload(hass):
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex=None, away_active=False)
    )

    entity = DimplexClimate(coordinator)
    assert entity.preset_mode is None
    assert entity.extra_state_attributes is None
    _, empty_coordinator = make_coordinator(hass, data=None)
    assert DimplexClimate(empty_coordinator).extra_state_attributes is None


def test_rule_climate_reports_timer_away_submode(hass):
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010500000000", away_active=False)
    )

    assert DimplexClimate(coordinator).preset_mode == PRESET_AWAY


def test_rule_climate_reports_unknown_timer_submode_as_timer_preset(hass):
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010900000000", away_active=False)
    )

    assert DimplexClimate(coordinator).preset_mode == PRESET_USER_TIMER


def test_rule_climate_reports_away_preset(hass):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            mode_hex="010500000000",
            away_active=True,
            away_until=datetime(2026, 11, 22, 18, 53),
            away_target_temperature_c=7,
        ),
    )

    entity = DimplexClimate(coordinator)

    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.preset_mode == PRESET_AWAY
    assert entity.extra_state_attributes == {
        "away_until": datetime(
            2026,
            11,
            22,
            18,
            53,
            tzinfo=dt_util.get_default_time_zone(),
        ).isoformat(),
        "away_target_temperature": 7,
    }


async def test_given_no_away_deadline_when_away_is_selected_then_it_is_rejected(
    hass,
) -> None:
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)

    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode(PRESET_AWAY)

    assert err.value.translation_key == "away_until_required"
    client.async_write_raw.assert_not_awaited()


def test_rule_climate_reports_home_all_day_preset(hass):
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(mode_hex="010200000000"),
    )

    entity = DimplexClimate(coordinator)

    assert entity.hvac_mode == HVACMode.AUTO
    assert entity.preset_mode == PRESET_HOME_ALL_DAY


async def test_rule_climate_writes_timer_preset(hass) -> None:
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x01\x03"))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    await DimplexClimate(coordinator).async_set_preset_mode(PRESET_USER_TIMER)

    assert client.async_write_raw.await_args.args == (
        0,
        0x1001,
        b"\x01\x03\x00\x00\x00\x00",
    )
    coordinator.async_refresh.assert_awaited_once()


async def test_rule_climate_rejects_unsupported_hvac_mode(hass) -> None:
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)

    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_hvac_mode(HVACMode.COOL)

    assert err.value.translation_key == "invalid_hvac_mode"
    client.async_write_raw.assert_not_awaited()


async def test_rule_climate_does_not_rewrite_current_hvac_mode(hass) -> None:
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            mode_hex="010500000000",
            away_active=True,
            away_until=datetime(2063, 11, 22, 18, 53),
            away_target_temperature_c=7,
        ),
        client=client,
    )

    await DimplexClimate(coordinator).async_set_hvac_mode(HVACMode.HEAT)

    client.async_write_raw.assert_not_awaited()


async def test_rule_climate_translates_hvac_write_failures(hass) -> None:
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    failure = HeaterUnavailableError("offline")
    client = SimpleNamespace(async_write_raw=AsyncMock(side_effect=failure))
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex=None, away_active=False), client=client
    )

    with pytest.raises(HomeAssistantError) as err:
        await DimplexClimate(coordinator).async_set_hvac_mode(HVACMode.HEAT)

    assert err.value.translation_key == "cannot_connect"
    assert err.value.__cause__ is failure


async def test_rule_climate_translates_timer_preset_write_failures(hass) -> None:
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    failure = HeaterUnavailableError("offline")
    client = SimpleNamespace(async_write_raw=AsyncMock(side_effect=failure))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)

    with pytest.raises(HomeAssistantError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode(PRESET_USER_TIMER)

    assert err.value.translation_key == "cannot_connect"
    assert err.value.__cause__ is failure


@pytest.mark.parametrize(
    ("mode_hex", "hvac_mode"),
    [
        ("020000000000", HVACMode.COOL),
        ("020000000000", HVACMode.AUTO),
    ],
)
async def test_rule_climate_sets_temperature_only_in_heat(
    hass, mode_hex, hvac_mode
) -> None:
    """A temperature together with a mode other than Heat is refused."""
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex=mode_hex), client=client
    )
    kwargs = {"temperature": 22}
    if hvac_mode is not None:
        kwargs["hvac_mode"] = hvac_mode

    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_temperature(**kwargs)

    assert err.value.translation_key == "temperature_needs_heat"
    client.async_write_raw.assert_not_awaited()


@pytest.mark.parametrize("hvac_mode", [None, HVACMode.HEAT])
async def test_rule_climate_temperature_in_auto_switches_to_manual(
    hass, hvac_mode
) -> None:
    """AM2086 2026-11-24: a setpoint written in User Timer was dropped when the
    programme was re-selected, so Auto leaves for Manual before the setpoint."""
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000"), client=client
    )
    coordinator.async_request_refresh = AsyncMock()
    kwargs = {"temperature": 22}
    if hvac_mode is not None:
        kwargs["hvac_mode"] = hvac_mode

    await DimplexClimate(coordinator).async_set_temperature(**kwargs)

    writes = [call.args for call in client.async_write_raw.await_args_list]
    assert writes[-2][:2] == (0, 0x1001)  # the heating mode: Manual
    assert writes[-2][2] == encode_preset_mode(PRESET_MANUAL)
    assert writes[-1] == (0, 0x1023, encode_setpoint(22))


async def test_rule_climate_auto_to_manual_is_one_link_operation(hass) -> None:
    """Codex review: a link dropped between two operations left Manual behind."""
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    _, coordinator = make_coordinator(hass, data=snapshot(mode_hex="010300000000"))
    coordinator.async_request_refresh = AsyncMock()
    coordinator.link.async_write_many = AsyncMock(
        side_effect=HeaterUnavailableError("dropped")
    )

    with pytest.raises(HomeAssistantError):
        await DimplexClimate(coordinator).async_set_temperature(temperature=22)

    coordinator.link.async_write_many.assert_awaited_once_with(
        (
            ((0, 0x1001), encode_preset_mode(PRESET_MANUAL)),
            ((0, 0x1023), encode_setpoint(22)),
        )
    )
    # Manual may have been written before the drop: show the heater's state.
    coordinator.async_request_refresh.assert_awaited_once()


async def test_rule_climate_a_failed_setpoint_after_manual_is_shown(hass) -> None:
    """The link drops after the Manual write: the error and the heater's state."""
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=[None, HeaterUnavailableError("drop")])
    )
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000"), client=client
    )
    coordinator.async_request_refresh = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await DimplexClimate(coordinator).async_set_temperature(temperature=22)

    assert client.async_write_raw.await_args_list[0].args[:2] == (0, 0x1001)
    coordinator.async_request_refresh.assert_awaited_once()


async def test_rule_climate_auto_to_manual_waits_for_away_changes(hass) -> None:
    """Codex review: Away started meanwhile must not be overwritten by Manual."""
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000"), client=client
    )
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)

    async with coordinator._away_lock:
        task = hass.async_create_task(entity.async_set_temperature(temperature=22))
        await asyncio.sleep(0)
        coordinator.data = snapshot(mode_hex="010300000000", away_active=True)
    with pytest.raises(HomeAssistantError) as err:
        await task

    assert err.value.translation_key == "mode_changed"
    client.async_write_raw.assert_not_awaited()


async def test_rule_climate_auto_shows_the_timer_temperature(hass) -> None:
    """AM6532 2026-11-26: Auto with Setback on, outside periods, holds 8 degC."""
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000", setpoint=8)
    )
    coordinator.settings[(0, 0x1016)] = b"\x01\x08"
    assert DimplexClimate(coordinator).native_target_temperature == 8.0


async def test_rule_climate_auto_target_needs_no_setpoint(hass) -> None:
    """Codex review: a programme target is shown without the setpoint record."""
    from datetime import time

    from custom_components.dimplex_ble.protocol import SchedulePeriod

    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000", setpoint=None)
    )
    coordinator.schedules = {
        "user_timer": [(SchedulePeriod(time(0), time(23, 59), 20),)] * 7
    }
    entity = DimplexClimate(coordinator)
    if dt_util.now().time() < time(23, 59):
        assert entity.native_target_temperature == 20.0


async def test_rule_climate_rechecks_the_auto_target_every_minute(hass) -> None:
    """Codex review: a period ends with an unchanged snapshot, which notifies nobody."""
    from unittest.mock import Mock

    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000", setpoint=8)
    )
    coordinator.settings[(0, 0x1016)] = b"\x01\x08"
    entity = DimplexClimate(coordinator)
    entity.hass = hass
    entity.entity_id = "climate.dimplex_test"
    entity.async_write_ha_state = Mock()
    entity._shown_target = 20.0  # written while a 20 degC period ran

    entity._async_check_target(dt_util.now())  # the period ended: 8 now
    entity.async_write_ha_state.assert_called_once()

    entity._async_check_target(dt_util.now())  # unchanged: no write
    entity.async_write_ha_state.assert_called_once()


async def test_rule_climate_starts_the_minute_check_when_added(hass) -> None:
    from unittest.mock import patch

    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010300000000", setpoint=8)
    )
    coordinator.settings[(0, 0x1016)] = b"\x01\x08"
    entity = DimplexClimate(coordinator)
    entity.hass = hass
    with patch(
        "custom_components.dimplex_ble.climate.async_track_time_interval"
    ) as track:
        await entity.async_added_to_hass()

    assert entity._shown_target == 8.0
    assert track.call_args.args[1] == entity._async_check_target
    for remove in entity._on_remove or []:
        remove()


async def test_rule_climate_auto_resumes_the_last_schedule(hass) -> None:
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010200000000"), client=client
    )
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)
    entity.async_write_ha_state = lambda: None
    entity._handle_coordinator_update()  # sees Home All Day
    coordinator.data = snapshot(mode_hex="020000000000")
    entity._handle_coordinator_update()  # now Manual

    await entity.async_set_hvac_mode(HVACMode.AUTO)

    assert client.async_write_raw.await_args.args == (
        0,
        0x1001,
        b"\x01\x02\x00\x00\x00\x00",
    )


async def test_rule_climate_resumes_the_programme_seen_at_creation(hass) -> None:
    """No update yet since setup: Auto still resumes the startup programme."""
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="010100000000"), client=client
    )
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)
    coordinator.data = snapshot(mode_hex="020000000000")  # switched to Heat

    await entity.async_set_hvac_mode(HVACMode.AUTO)

    assert client.async_write_raw.await_args.args[2] == b"\x01\x01\x00\x00\x00\x00"


async def test_rule_climate_auto_defaults_to_user_timer(hass) -> None:
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()

    await DimplexClimate(coordinator).async_set_hvac_mode(HVACMode.AUTO)

    assert client.async_write_raw.await_args.args == (
        0,
        0x1001,
        b"\x01\x03\x00\x00\x00\x00",
    )


def test_rule_climate_ignores_malformed_reported_modes(hass):
    _, coordinator = make_coordinator(hass, data=snapshot(mode_hex="not-hex"))
    entity = DimplexClimate(coordinator)
    assert entity.hvac_mode is None
    assert entity.preset_mode is None
    _, unknown_coordinator = make_coordinator(
        hass, data=snapshot(mode_hex="fe0000000000")
    )
    assert DimplexClimate(unknown_coordinator).hvac_mode is None
    _, empty_coordinator = make_coordinator(hass)
    assert DimplexClimate(empty_coordinator).preset_mode is None


async def test_rule_climate_translates_preset_write_failures(hass):
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=HeaterUnavailableError("offline"))
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(HomeAssistantError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode("eco")
    assert err.value.translation_key == "cannot_connect"
    assert err.value.translation_domain == DOMAIN
    assert isinstance(err.value.__cause__, HeaterUnavailableError)


async def test_rule_climate_translates_protocol_preset_write_failures(hass):
    from custom_components.dimplex_ble.protocol import DimplexProtocolError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=DimplexProtocolError("invalid"))
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)

    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode("eco")

    assert err.value.translation_key == "invalid_gatt_request"


async def test_rule_climate_writes_verified_preset_mode(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x03"))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    await DimplexClimate(coordinator).async_set_preset_mode("eco")

    assert client.async_write_raw.await_args == (
        (0, 0x1001, b"\x03\x00\x00\x00\x00\x00"),
    )
    coordinator.async_refresh.assert_awaited_once()


async def test_rule_climate_writes_home_all_day_preset(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x01\x02"))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    await DimplexClimate(coordinator).async_set_preset_mode(PRESET_HOME_ALL_DAY)

    assert client.async_write_raw.await_args == (
        (0, 0x1001, b"\x01\x02\x00\x00\x00\x00"),
    )
    coordinator.async_refresh.assert_awaited_once()


async def test_rule_climate_writes_out_all_day_preset(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh = AsyncMock()

    await DimplexClimate(coordinator).async_set_preset_mode("out_all_day")

    assert client.async_write_raw.await_args == (
        (0, 0x1001, b"\x01\x01\x00\x00\x00\x00"),
    )
    coordinator.async_refresh.assert_awaited_once()


async def test_rule_climate_writes_and_clears_away_preset(hass):
    away_payload = bytes.fromhex("013f0b16351207")
    client = SimpleNamespace(
        async_write_raw=AsyncMock(
            side_effect=[away_payload, b"\x00\x3f\x0b\x16\x35\x12\x07", b"\x03"]
        )
    )
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            mode_hex="010500000000",
            away_active=True,
            away_until=datetime(2063, 11, 22, 18, 53),
            away_target_temperature_c=7,
        ),
        client=client,
    )
    coordinator.async_request_refresh = AsyncMock()
    coordinator.async_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)

    await entity.async_set_preset_mode(PRESET_AWAY)
    assert client.async_write_raw.await_args_list[0].args == (
        0,
        0x102A,
        away_payload,
    )

    await entity.async_set_preset_mode("eco")
    assert client.async_write_raw.await_args_list[1].args == (
        0,
        0x102A,
        bytes.fromhex("003f0b16351207"),
    )
    assert client.async_write_raw.await_args_list[2].args == (
        0,
        0x1001,
        b"\x03\x00\x00\x00\x00\x00",
    )
    assert coordinator.async_request_refresh.await_count == 1
    coordinator.async_refresh.assert_awaited_once()


async def test_rule_climate_rejects_unverified_preset_mode(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    with pytest.raises(ServiceValidationError) as err:
        await DimplexClimate(coordinator).async_set_preset_mode("comfort")
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_preset"


@pytest.mark.parametrize(
    "data",
    [None, snapshot(minimum=None), snapshot(maximum=None)],
)
def test_rule_climate_is_unavailable_without_snapshot_or_reported_range(hass, data):
    _, coordinator = make_coordinator(hass, data=data)
    assert DimplexClimate(coordinator).available is False


async def test_rule_climate_rejects_invalid_and_unavailable_setpoints(hass):
    _, coordinator = make_coordinator(hass, data=snapshot())
    entity = DimplexClimate(coordinator)
    for value in (True, "21", 20.5, float("nan"), float("inf"), 4, 31):
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_temperature(temperature=value)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "invalid_setpoint"

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_temperature()
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "invalid_setpoint"
    for missing_bound in (snapshot(minimum=None), snapshot(maximum=None)):
        coordinator.data = missing_bound
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_set_temperature(temperature=21)
        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "setpoint_unavailable"
    coordinator.data = None
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_temperature(temperature=21)
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "setpoint_unavailable"


async def test_rule_climate_writes_inclusive_reported_boundaries(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x16\x00"))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)
    for temperature, payload in ((5, b"\x05\x00"), (30, b"\x1e\x00")):
        await entity.async_set_temperature(temperature=temperature)
        assert client.async_write_raw.await_args_list[-1].args == (0, 0x1023, payload)
    assert client.async_write_raw.await_count == 2
    assert coordinator.async_request_refresh.await_count == 2


async def test_rule_climate_translates_ble_write_failures(hass):
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=HeaterUnavailableError("offline"))
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(HomeAssistantError) as err:
        await DimplexClimate(coordinator).async_set_temperature(temperature=22)
    assert err.value.translation_key == "cannot_connect"
    assert err.value.translation_domain == DOMAIN
    assert isinstance(err.value.__cause__, HeaterUnavailableError)


async def test_rule_select_preserves_reported_unit_and_validates_writes(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock(return_value=b"\x01"))
    _, coordinator = make_coordinator(hass, data=snapshot(unit=1), client=client)
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexDisplayUnitSelect(coordinator)
    assert entity.current_option == "fahrenheit"
    assert entity.options == ["celsius", "fahrenheit"]
    assert entity.entity_category == EntityCategory.CONFIG
    await entity.async_select_option("celsius")
    client.async_write_raw.assert_awaited_once_with(0, 0x1005, b"\x00")
    coordinator.async_request_refresh.assert_awaited_once_with()

    for invalid in (None, "kelvin"):
        with pytest.raises(ServiceValidationError) as err:
            await entity.async_select_option(invalid)
        assert err.value.translation_key == "invalid_temperature_unit"
        assert err.value.translation_domain == DOMAIN


@pytest.mark.parametrize(
    "unit, expected",
    [
        (None, None),
        (2, None),
        (0, "celsius"),
        (1, "fahrenheit"),
    ],
)
def test_rule_select_uses_only_known_reported_units(hass, unit, expected):
    data = snapshot(unit=unit) if unit is not None else None
    _, coordinator = make_coordinator(hass, data=data)
    if unit == 2:
        coordinator.data = snapshot(unit=2)
    assert DimplexDisplayUnitSelect(coordinator).current_option == expected


async def test_rule_select_translates_ble_write_failures(hass):
    from custom_components.dimplex_ble.errors import HeaterUnavailableError

    client = SimpleNamespace(
        async_write_raw=AsyncMock(side_effect=HeaterUnavailableError("offline"))
    )
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(HomeAssistantError) as err:
        await DimplexDisplayUnitSelect(coordinator).async_select_option("fahrenheit")
    assert err.value.translation_key == "cannot_connect"
    assert err.value.translation_domain == DOMAIN


async def test_rule_select_preserves_characteristic_error_translation(hass):
    from custom_components.dimplex_ble.errors import CharacteristicError

    failure = CharacteristicError("missing")
    client = SimpleNamespace(async_write_raw=AsyncMock(side_effect=failure))
    _, coordinator = make_coordinator(hass, data=snapshot(), client=client)
    with pytest.raises(ServiceValidationError) as err:
        await DimplexDisplayUnitSelect(coordinator).async_select_option("fahrenheit")
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "characteristic_unavailable"
    assert err.value.__cause__ is failure


def test_rule_diagnostic_sensors_keep_raw_payloads_out_of_attributes(hass):
    """Raw payloads live only in diagnostics; an unknown mode is unknown."""
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(error=64206, error_hex="eefa", mode_hex="fe01"),
    )
    error_sensor = DimplexErrorCodeSensor(coordinator)
    mode_sensor = DimplexOperatingModeSensor(coordinator)
    assert error_sensor.native_value == "Code 64206"
    assert error_sensor.extra_state_attributes is None
    assert error_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert mode_sensor.native_value is None
    assert mode_sensor.extra_state_attributes is None
    assert mode_sensor.entity_category == EntityCategory.DIAGNOSTIC
    rated_sensor = DimplexRatedPowerSensor(
        make_coordinator(
            hass,
            data=snapshot(rated_power=500, power_loading_hex="f401980800000000"),
        )[1]
    )
    assert rated_sensor.native_value == 500
    assert rated_sensor.extra_state_attributes is None
    assert rated_sensor.device_class == "power"
    assert rated_sensor.native_unit_of_measurement == "W"
    assert rated_sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert rated_sensor.entity_registry_enabled_default is False
    assert mode_sensor.entity_registry_enabled_default is False


@pytest.mark.parametrize(
    ("mode_hex", "away_active", "expected"),
    [
        ("040000000000", False, "frost_protect"),
        ("010200000000", None, "home_all_day"),
        ("010100000000", None, "out_all_day"),
        ("020000000000", True, "away"),
        (None, False, None),
    ],
)
def test_rule_operating_mode_sensor_is_a_translated_enum(
    hass, mode_hex, away_active, expected
):
    _, coordinator = make_coordinator(
        hass, data=snapshot(mode_hex=mode_hex, away_active=away_active)
    )
    sensor = DimplexOperatingModeSensor(coordinator)
    assert sensor.native_value == expected
    assert sensor.device_class == "enum"
    assert sensor.translation_key == "operating_mode"
    assert sensor.unique_id == "aa:bb:cc:dd:ee:20_operating_mode_raw"
    assert expected is None or expected in sensor.options


def test_rule_diagnostic_sensors_are_empty_before_first_snapshot(hass):
    _, coordinator = make_coordinator(hass)
    assert DimplexErrorCodeSensor(coordinator).native_value is None
    assert DimplexErrorCodeSensor(coordinator).extra_state_attributes is None
    assert DimplexOperatingModeSensor(coordinator).native_value is None


@pytest.mark.parametrize(
    "entity_type",
    [DimplexRatedPowerSensor, DimplexOutputPowerSensor],
)
def test_rule_diagnostic_sensor_raw_attributes_are_empty_without_payload(
    hass, entity_type
):
    _, coordinator = make_coordinator(hass, data=snapshot())
    assert entity_type(coordinator).extra_state_attributes is None


@pytest.mark.parametrize(
    "data",
    [None, snapshot(rated_power=500), snapshot(trac_output_power_percent=100)],
)
def test_rule_calculated_power_requires_both_source_values(hass, data):
    _, coordinator = make_coordinator(hass, data=data)
    assert DimplexCalculatedPowerSensor(coordinator).native_value is None


async def test_given_away_when_auto_is_requested_then_away_is_cleared_first(
    hass,
) -> None:
    """Away counts as Heat; Auto leaves Away, then resumes the schedule."""
    client = SimpleNamespace(async_write_raw=AsyncMock(side_effect=[b"\x00", b"\x01"]))
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            mode_hex=None,
            away_active=True,
            away_until=datetime(2026, 11, 22, 18, 53),
            away_target_temperature_c=7,
        ),
        client=client,
    )
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexClimate(coordinator)
    assert entity.hvac_mode == HVACMode.HEAT

    await entity.async_set_hvac_mode(HVACMode.AUTO)

    assert client.async_write_raw.await_args_list[0].args[0:2] == (0, 0x102A)
    assert client.async_write_raw.await_args_list[1].args == (
        0,
        0x1001,
        b"\x01\x03\x00\x00\x00\x00",
    )


def test_rule_climate_hides_a_stale_away_record_while_away_is_off(hass):
    """The heater keeps its last Away record after Away ends: not shown."""
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            away_active=False,
            away_until=datetime(2026, 11, 22, 18, 53),
            away_target_temperature_c=7,
        ),
    )

    assert DimplexClimate(coordinator).extra_state_attributes is None


async def test_away_temperature_waits_for_leaving_away_and_then_is_refused(hass):
    """ANY write of the Away record re-enters Away (ledger 18:44/18:48).

    An Away temperature change checked against the snapshot from before
    leaving Away must not write after the leave-away sequence.
    """
    gate = hass.loop.create_future()
    writes: list[tuple[int, int]] = []

    async def write_raw(group, uuid, payload):
        writes.append((group, uuid))
        if len(writes) == 1:
            await gate

    client = SimpleNamespace(async_write_raw=write_raw)
    _, coordinator = make_coordinator(
        hass,
        data=snapshot(
            mode_hex="010500000000",
            away_active=True,
            away_until=datetime(2063, 11, 22, 18, 53),
            away_target_temperature_c=7,
        ),
        client=client,
    )
    coordinator.async_request_refresh = AsyncMock()

    leave = hass.async_create_task(
        DimplexClimate(coordinator).async_set_preset_mode("eco")
    )
    await asyncio.sleep(0)
    assert writes == [(0, 0x102A)]
    change = hass.async_create_task(coordinator.async_set_away_temperature(10))
    await asyncio.sleep(0)
    assert writes == [(0, 0x102A)]  # waits for the whole leave-away sequence

    gate.set_result(None)
    await leave
    with pytest.raises(ServiceValidationError) as err:
        await change

    assert err.value.translation_key == "away_temperature_needs_away"
    assert writes == [(0, 0x102A), (0, 0x1001)]
    assert coordinator.data.away_active is False


async def test_away_mode_write_records_the_written_state_until_the_next_poll(hass):
    client = SimpleNamespace(async_write_raw=AsyncMock())
    _, coordinator = make_coordinator(
        hass, data=snapshot(away_active=False), client=client
    )
    coordinator.async_request_refresh = AsyncMock()
    until = datetime(2063, 11, 22, 18, 53)

    await coordinator.async_set_away_mode(until=until)

    assert coordinator.data.away_active is True
    assert coordinator.data.away_until == until
