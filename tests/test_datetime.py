from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.datetime import DimplexAwayUntil, async_setup_entry
from custom_components.dimplex_ble.errors import HeaterUnavailableError

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def _link(client):
    """Link fake that records writes/reads on a client-shaped mock."""

    async def async_write(address, payload):
        await client.async_write_raw(*address, payload)

    async def async_read(address):
        return await client.async_read_raw(*address)

    async def async_write_many(writes):
        for address, payload in writes:
            await client.async_write_raw(*address, payload)

    return SimpleNamespace(
        address=ADDRESS,
        source=PROXY,
        async_write=async_write,
        async_read=async_read,
        async_write_many=async_write_many,
    )


def make_coordinator(hass, *, away_until=None, client=None, away_active=True):
    client = client or SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    coordinator = DimplexCoordinator(hass, entry, _link(client))
    coordinator.data = SimpleNamespace(
        away_active=away_active,
        away_until=away_until,
        away_target_temperature_c=7,
    )
    coordinator.last_update_success = True
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def test_given_heater_snapshot_when_datetime_platform_loads_then_it_adds_away_control(
    hass,
) -> None:
    until = datetime(2026, 11, 22, 18, 53)
    coordinator = make_coordinator(hass, away_until=until)
    entities = []

    await async_setup_entry(
        hass,
        SimpleNamespace(runtime_data=coordinator),
        entities.extend,
    )

    assert len(entities) == 1
    assert entities[0].translation_key == "away_until"
    # Away needs it, so it is enabled for new installs, as a setting.
    assert entities[0].entity_registry_enabled_default is True
    assert entities[0].entity_category is EntityCategory.CONFIG
    assert entities[0].native_value == until.replace(
        tzinfo=dt_util.get_default_time_zone()
    )
    await coordinator.async_shutdown()


def test_given_no_heater_snapshot_when_away_datetime_is_read_then_it_is_unavailable(
    hass,
) -> None:
    coordinator = make_coordinator(hass)
    coordinator.data = None
    entity = DimplexAwayUntil(coordinator)

    assert entity.native_value is None
    assert entity.available is False


async def test_given_future_return_time_when_user_sets_it_then_away_is_activated(
    hass,
) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=b"\x01"),
    )
    coordinator = make_coordinator(hass, client=client)
    value = (dt_util.now() + timedelta(days=2)).replace(second=0, microsecond=0)

    await DimplexAwayUntil(coordinator).async_set_value(value)

    assert client.async_write_raw.await_args.args == (
        0,
        0x102A,
        bytes(
            (
                1,
                value.year - 2000,
                value.month,
                value.day,
                value.minute,
                value.hour,
                7,
            )
        ),
    )
    coordinator.async_request_refresh.assert_awaited_once()


async def test_given_past_return_time_when_user_sets_it_then_it_is_rejected(
    hass,
) -> None:
    entity = DimplexAwayUntil(make_coordinator(hass))
    value = dt_util.now() - timedelta(minutes=1)
    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_value(value)

    assert err.value.translation_key == "invalid_away_until"


async def test_given_connection_failure_when_user_sets_return_time_then_error_is_translated(
    hass,
) -> None:
    failure = HeaterUnavailableError("offline")
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=failure),
    )
    entity = DimplexAwayUntil(make_coordinator(hass, client=client))
    value = (dt_util.now() + timedelta(days=2)).replace(second=0, microsecond=0)

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_value(value)

    assert err.value.translation_key == "cannot_connect"
    assert err.value.__cause__ is failure


async def test_inactive_away_hides_the_stale_deadline(hass) -> None:
    """The heater keeps its last deadline after Away ends; it must not show."""
    coordinator = make_coordinator(
        hass, away_until=datetime(2026, 11, 22, 18, 59), away_active=False
    )
    entities = []

    await async_setup_entry(
        hass, SimpleNamespace(runtime_data=coordinator), entities.extend
    )

    assert entities[0].native_value is None
    await coordinator.async_shutdown()
