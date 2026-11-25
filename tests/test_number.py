from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entities import snapshot

from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.number import (
    DimplexAwayTemperatureNumber,
    DimplexBoostTemperatureNumber,
    DimplexSetbackTemperatureNumber,
    DimplexTemperatureOffsetNumber,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"
OFFSET = (2, 0x1019)


def make_coordinator(hass, data, *, reads=(), keep=lambda payload: payload):
    client = SimpleNamespace(
        async_write_raw=AsyncMock(), async_read_raw=AsyncMock(side_effect=reads)
    )

    async def async_write(address, payload):
        await client.async_write_raw(*address, payload)

    async def async_read(address):
        return await client.async_read_raw(*address)

    async def async_read_then_write(address, build):
        payload = build(await client.async_read_raw(*address))
        await client.async_write_raw(*address, payload)
        return keep(payload)  # the record as the heater reads back

    link = SimpleNamespace(
        address=ADDRESS,
        source=PROXY,
        async_write=async_write,
        async_read=async_read,
        async_read_then_write=async_read_then_write,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    coordinator = DimplexCoordinator(hass, entry, link)
    coordinator.data = data
    coordinator.async_request_refresh = AsyncMock()
    return coordinator, client


async def test_setup_adds_the_temperature_settings(hass) -> None:
    coordinator, _ = make_coordinator(hass, snapshot())
    entities = []

    await async_setup_entry(
        hass, SimpleNamespace(runtime_data=coordinator), entities.extend
    )

    assert [entity.unique_id.split("_", 1)[1] for entity in entities] == [
        "away_temperature",
        "boost_temperature",
        "setback_temperature",
        "temperature_offset",
    ]
    assert all(e.entity_category is EntityCategory.CONFIG for e in entities)
    assert [e.entity_registry_enabled_default for e in entities] == [
        True,
        True,
        True,
        False,
    ]
    assert all(
        e.native_unit_of_measurement == UnitOfTemperature.CELSIUS for e in entities
    )
    # The offset is a difference: never converted like an absolute temperature.
    away, boost, setback, offset = entities
    assert offset.device_class is None
    assert away.device_class == boost.device_class == setback.device_class
    assert away.device_class == "temperature"
    assert (away.native_min_value, away.native_max_value) == (7, 18)
    assert (setback.native_min_value, setback.native_max_value) == (8, 19)
    assert (offset.native_min_value, offset.native_max_value) == (-3.0, 3.0)
    assert offset.native_step == 0.1
    assert all(entity.icon is None for entity in entities)


def test_numbers_are_unavailable_without_device_values(hass) -> None:
    coordinator, _ = make_coordinator(hass, None)
    for cls in (
        DimplexAwayTemperatureNumber,
        DimplexTemperatureOffsetNumber,
    ):
        entity = cls(coordinator)
        assert entity.native_value is None
        assert entity.available is False


def test_numbers_report_device_values(hass) -> None:
    coordinator, _ = make_coordinator(
        hass, snapshot(minimum=7, maximum=30, away_target_temperature_c=9)
    )
    coordinator.settings[OFFSET] = b"\xfe"

    assert DimplexAwayTemperatureNumber(coordinator).native_value == 9
    offset = DimplexTemperatureOffsetNumber(coordinator)
    assert offset.native_value == pytest.approx(-0.2)
    assert offset.available is True


def test_away_is_unavailable_when_unreported(hass) -> None:
    coordinator, _ = make_coordinator(hass, snapshot(away_target_temperature_c=None))
    assert DimplexAwayTemperatureNumber(coordinator).available is False


async def test_away_temperature_keeps_state_and_return_time(hass) -> None:
    until = datetime(2026, 11, 26, 18, 30)
    coordinator, client = make_coordinator(
        hass,
        snapshot(away_active=True, away_until=until, away_target_temperature_c=7),
        reads=[bytes((1, 26, 11, 26, 30, 18, 7))],  # the live record
    )

    await DimplexAwayTemperatureNumber(coordinator).async_set_native_value(12)

    client.async_write_raw.assert_awaited_once_with(
        0, 0x102A, bytes((1, 26, 11, 26, 30, 18, 12))
    )
    coordinator.async_request_refresh.assert_awaited_once()


async def test_away_ended_on_the_heater_since_the_poll_is_not_restarted(
    hass,
) -> None:
    """Review round 9: the snapshot still says Away, the heater says off."""
    until = datetime(2026, 11, 26, 18, 30)
    coordinator, client = make_coordinator(
        hass,
        snapshot(away_active=True, away_until=until, away_target_temperature_c=7),
        reads=[bytes((0, 26, 11, 26, 30, 18, 7))],
    )

    with pytest.raises(ServiceValidationError) as err:
        await DimplexAwayTemperatureNumber(coordinator).async_set_native_value(12)

    assert err.value.translation_key == "away_temperature_needs_away"
    client.async_write_raw.assert_not_awaited()


@pytest.mark.parametrize(
    "until", [None, datetime(2026, 11, 25, 6, 28)], ids=["no_deadline", "stale_future"]
)
async def test_away_temperature_is_refused_while_away_is_off(hass, until) -> None:
    """Hardware 2026-11-24 18:44/18:48: any write of the Away record makes the
    heater enter Away, so the temperature can only change while Away is on."""
    coordinator, client = make_coordinator(
        hass, snapshot(away_active=False, away_until=until, away_target_temperature_c=7)
    )

    with pytest.raises(ServiceValidationError) as err:
        await DimplexAwayTemperatureNumber(coordinator).async_set_native_value(8)

    assert err.value.translation_key == "away_temperature_needs_away"
    client.async_write_raw.assert_not_awaited()


@pytest.mark.parametrize("value", [6, 19, 10.5])
async def test_away_temperature_rejects_values_outside_the_range(hass, value) -> None:
    coordinator, client = make_coordinator(hass, snapshot(away_active=False))

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_set_away_temperature(value)

    assert err.value.translation_key == "invalid_setpoint"
    assert err.value.translation_placeholders == {"min": "7", "max": "18"}
    client.async_write_raw.assert_not_awaited()


@pytest.mark.parametrize(
    "data",
    [None, snapshot(away_active=None)],
)
async def test_away_temperature_needs_a_known_away_state(hass, data) -> None:
    coordinator, client = make_coordinator(hass, data)

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_set_away_temperature(10)

    assert err.value.translation_key == "setting_unavailable"
    client.async_write_raw.assert_not_awaited()


async def test_away_without_a_valid_return_time_is_not_rewritten(hass) -> None:
    """The live record decides: no valid return time, nothing to keep."""
    coordinator, client = make_coordinator(
        hass,
        snapshot(away_active=True, away_until=None),
        reads=[bytes((1, 0, 0, 0, 0, 0, 7))],
    )

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_set_away_temperature(10)

    assert err.value.translation_key == "away_temperature_needs_away"
    client.async_write_raw.assert_not_awaited()


async def test_temperature_offset_writes_signed_tenths(hass) -> None:
    coordinator, client = make_coordinator(hass, snapshot())
    coordinator.settings[OFFSET] = b"\x00"
    coordinator._settings_due = False
    entity = DimplexTemperatureOffsetNumber(coordinator)

    await entity.async_set_native_value(-0.3)

    client.async_write_raw.assert_awaited_once_with(2, 0x1019, b"\xfd")
    assert entity.native_value == pytest.approx(-0.3)
    assert coordinator._settings_due  # the next poll reads it back
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.parametrize("value", [3.1, -3.5, 0.25])
async def test_temperature_offset_rejects_values_outside_the_range(hass, value) -> None:
    coordinator, client = make_coordinator(hass, snapshot())

    with pytest.raises(ServiceValidationError) as err:
        await coordinator.async_set_temperature_offset(value)

    assert err.value.translation_key == "invalid_temperature_offset"
    assert err.value.translation_placeholders == {"min": "-3", "max": "3"}
    client.async_write_raw.assert_not_awaited()


async def test_setback_temperature_reads_and_writes_the_setback_record(hass) -> None:
    coordinator, client = make_coordinator(hass, snapshot(), reads=[b"\x00\x08"])
    entity = DimplexSetbackTemperatureNumber(coordinator)
    assert entity.native_value is None and not entity.available  # not read yet

    coordinator.settings[(0, 0x1016)] = b"\x00\x08"
    assert entity.native_value == 8.0
    assert (entity.native_min_value, entity.native_max_value) == (8, 19)
    assert entity.entity_category is EntityCategory.CONFIG

    await entity.async_set_native_value(12.0)

    client.async_write_raw.assert_awaited_once_with(0, 0x1016, b"\x00\x0c")
    assert entity.native_value == 12.0


async def test_a_refused_setback_temperature_is_reported_not_shown(hass) -> None:
    """AM6532 and AM2086 2026-11-25: a written 7 degC read back as the old 8 degC."""
    coordinator, client = make_coordinator(
        hass, snapshot(), reads=[b"\x01\x08"], keep=lambda payload: b"\x01\x08"
    )
    coordinator.settings[(0, 0x1016)] = b"\x01\x08"
    entity = DimplexSetbackTemperatureNumber(coordinator)

    with pytest.raises(HomeAssistantError) as err:
        await coordinator.async_set_setback_temperature(9)

    assert err.value.translation_key == "setting_not_kept"
    client.async_write_raw.assert_awaited_once_with(0, 0x1016, b"\x01\x09")
    assert entity.native_value == 8.0
    with pytest.raises(ServiceValidationError):  # below the kept range
        await entity.async_set_native_value(7.0)


async def test_an_unreadable_setback_after_a_write_is_reported(hass) -> None:
    coordinator, _ = make_coordinator(
        hass, snapshot(), reads=[b"\x01\x08"], keep=lambda payload: None
    )
    coordinator.settings[(0, 0x1016)] = b"\x01\x08"

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_setback_temperature(9)

    assert coordinator.settings[(0, 0x1016)] == b"\x01\x08"


async def test_boost_temperature_follows_the_heater_and_its_range(hass) -> None:
    """AM2086 2026-11-25: display 21 degC read 15 00; a written 17 00 showed 23."""
    coordinator, client = make_coordinator(
        hass, snapshot(minimum=7, maximum=30, boost_temperature_c=21)
    )
    entity = DimplexBoostTemperatureNumber(coordinator)
    assert entity.native_value == 21.0
    assert (entity.native_min_value, entity.native_max_value) == (7.0, 30.0)

    await entity.async_set_native_value(23.0)

    client.async_write_raw.assert_awaited_once_with(0, 0x1028, b"\x17\x00")
    for bad in (6.0, 31.0, 22.5):
        with pytest.raises(ServiceValidationError):
            await entity.async_set_native_value(bad)


def test_boost_temperature_without_heater_values(hass) -> None:
    coordinator, _ = make_coordinator(hass, None)
    entity = DimplexBoostTemperatureNumber(coordinator)
    assert entity.native_value is None and not entity.available
    assert (entity.native_min_value, entity.native_max_value) == (7.0, 35.0)


def test_boost_temperature_needs_the_setpoint_range(hass) -> None:
    """Codex review: without the range every write would be refused."""
    coordinator, _ = make_coordinator(
        hass, snapshot(minimum=None, maximum=None, boost_temperature_c=21)
    )
    assert not DimplexBoostTemperatureNumber(coordinator).available
