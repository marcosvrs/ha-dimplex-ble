from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.client import DimplexError
from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.switch import (
    DimplexOpenWindowDetectionSwitch,
    DimplexPreemptiveHeatingSwitch,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def make_coordinator(
    hass, *, preemptive: bool | None, open_window: bool | None = None, client=None
):
    client = client or SimpleNamespace(address=ADDRESS)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    coordinator = DimplexCoordinator(hass, entry, client)
    coordinator.data = SimpleNamespace(
        preemptive_heating=preemptive,
        open_window_detection=open_window,
    )
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def test_setup_adds_verified_setting_switches(hass) -> None:
    coordinator = make_coordinator(hass, preemptive=False, open_window=True)
    entities = []

    await async_setup_entry(
        hass,
        SimpleNamespace(runtime_data=coordinator),
        entities.extend,
    )

    assert len(entities) == 2
    assert {entity.translation_key for entity in entities} == {
        "open_window_detection",
        "preemptive_heating",
    }
    assert entities[0].is_on is True
    assert entities[1].is_on is False
    assert entities[0].extra_state_attributes["when_it_applies"] == (
        "can_interrupt_any_active_mode"
    )
    assert entities[1].extra_state_attributes["official_name"] == (
        "dimplex_calls_this_adaptive_start"
    )


def test_preemptive_heating_switch_availability_requires_verified_value(hass) -> None:
    unavailable = DimplexPreemptiveHeatingSwitch(
        make_coordinator(hass, preemptive=None)
    )
    available = DimplexPreemptiveHeatingSwitch(make_coordinator(hass, preemptive=True))

    assert unavailable.available is False
    assert available.available is True
    assert available.is_on is True


def test_open_window_detection_switch_availability_requires_verified_value(
    hass,
) -> None:
    unavailable = DimplexOpenWindowDetectionSwitch(
        make_coordinator(hass, preemptive=False, open_window=None)
    )
    available = DimplexOpenWindowDetectionSwitch(
        make_coordinator(hass, preemptive=False, open_window=True)
    )

    assert unavailable.available is False
    assert available.available is True
    assert available.is_on is True


async def test_open_window_detection_switch_writes_and_refreshes(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=b"\x01"),
    )
    coordinator = make_coordinator(
        hass,
        preemptive=False,
        open_window=False,
        client=client,
    )
    entity = DimplexOpenWindowDetectionSwitch(coordinator)

    await entity.async_turn_on()
    await entity.async_turn_off()

    assert client.async_write_raw.await_args_list[0].args == (0, 0x1007, b"\x01")
    assert client.async_write_raw.await_args_list[1].args == (0, 0x1007, b"\x00")
    assert coordinator.async_request_refresh.await_count == 2


async def test_preemptive_heating_switch_writes_and_refreshes(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=b"\x01"),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    entity = DimplexPreemptiveHeatingSwitch(coordinator)

    await entity.async_turn_on()
    await entity.async_turn_off()

    assert client.async_write_raw.await_args_list[0].args == (0, 0x1008, b"\x01")
    assert client.async_write_raw.await_args_list[1].args == (0, 0x1008, b"\x00")
    assert coordinator.async_request_refresh.await_count == 2


async def test_switch_translates_write_error(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=DimplexError("write failed")),
    )
    coordinator = make_coordinator(
        hass,
        preemptive=False,
        open_window=False,
        client=client,
    )
    entity = DimplexOpenWindowDetectionSwitch(coordinator)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()

    coordinator.async_request_refresh.assert_not_awaited()
