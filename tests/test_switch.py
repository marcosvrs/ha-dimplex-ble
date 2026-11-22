from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.client import DimplexError
from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.switch import (
    DimplexOpenWindowDetectionSwitch,
    DimplexPreemptiveHeatingSwitch,
    DimplexSoundSwitch,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def make_coordinator(
    hass,
    *,
    preemptive: bool | None,
    open_window: bool | None = None,
    sound: bytes | None = None,
    client=None,
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
    coordinator.sound_value = sound
    coordinator.async_request_refresh = AsyncMock()
    return coordinator


async def test_setup_adds_verified_setting_switches(hass) -> None:
    coordinator = make_coordinator(
        hass, preemptive=False, open_window=True, sound=b"\x01\x00\x00"
    )
    entities = []

    await async_setup_entry(
        hass,
        SimpleNamespace(runtime_data=coordinator),
        entities.extend,
    )

    assert len(entities) == 3
    assert {entity.translation_key for entity in entities} == {
        "sound",
        "open_window_detection",
        "preemptive_heating",
    }
    assert entities[0].is_on is True
    assert entities[1].is_on is True
    assert entities[2].is_on is False
    assert entities[1].extra_state_attributes["when_it_applies"] == (
        "can_interrupt_any_active_mode"
    )
    assert entities[2].extra_state_attributes["official_name"] == (
        "dimplex_calls_this_adaptive_start"
    )
    await coordinator.async_shutdown()


def test_sound_switch_requires_verified_sound_value(hass) -> None:
    unavailable = DimplexSoundSwitch(
        make_coordinator(hass, preemptive=False, sound=None)
    )
    available = DimplexSoundSwitch(
        make_coordinator(hass, preemptive=False, sound=b"\x01\x00\x00")
    )

    assert unavailable.available is False
    assert available.available is True
    assert available.is_on is True


async def test_sound_switch_writes_without_main_coordinator_refresh(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=[b"\x01\x00\x00", b"\x00\x00\x00"]),
    )
    coordinator = make_coordinator(
        hass, preemptive=False, sound=b"\x00\x00\x00", client=client
    )
    entity = DimplexSoundSwitch(coordinator)

    await entity.async_turn_on()
    await entity.async_turn_off()

    assert client.async_write_raw.await_args_list[0].args == (
        2,
        0x0005,
        b"\x01\x00\x00",
    )
    assert client.async_write_raw.await_args_list[1].args == (
        2,
        0x0005,
        b"\x00\x00\x00",
    )
    coordinator.async_request_refresh.assert_not_awaited()
    client.async_write_raw.assert_awaited()
    assert coordinator.sound_value == b"\x00\x00\x00"


async def test_sound_poll_is_independent_and_updates_cache(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw_fields=AsyncMock(return_value={(2, 0x0005): b"\x01\x00\x00"}),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    coordinator.last_update_success = True

    await coordinator.async_refresh_sound()

    assert coordinator.sound_value == b"\x01\x00\x00"
    client.async_read_raw_fields.assert_awaited_once_with(((2, 0x0005),))


async def test_sound_polling_has_its_own_slow_lifecycle(hass, monkeypatch) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw_fields=AsyncMock(return_value={(2, 0x0005): b"\x01\x00\x00"}),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    cancel_poll = Mock()
    track_poll = Mock(return_value=cancel_poll)
    monkeypatch.setattr(
        "custom_components.dimplex_ble.coordinator.async_track_time_interval",
        track_poll,
    )

    coordinator.async_start_sound_polling()
    await hass.async_block_till_done()

    track_poll.assert_called_once()
    client.async_read_raw_fields.assert_awaited_once_with(((2, 0x0005),))
    await coordinator.async_shutdown()
    cancel_poll.assert_called_once_with()


async def test_sound_poll_ignores_failed_and_invalid_reads(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw_fields=AsyncMock(side_effect=[DimplexError("offline"), {}]),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)

    await coordinator.async_refresh_sound()
    await coordinator.async_refresh_sound()

    assert coordinator.sound_value is None


async def test_sound_switch_rejects_invalid_readback(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=b"\x02\x00\x00"),
    )
    coordinator = make_coordinator(
        hass, preemptive=False, sound=b"\x00\x00\x00", client=client
    )
    entity = DimplexSoundSwitch(coordinator)

    with pytest.raises(HomeAssistantError):
        await entity.async_turn_on()


async def test_sound_switch_translates_write_failures(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=DimplexError("offline")),
    )
    coordinator = make_coordinator(
        hass, preemptive=False, sound=b"\x00\x00\x00", client=client
    )

    with pytest.raises(HomeAssistantError):
        await DimplexSoundSwitch(coordinator).async_turn_off()


async def test_sound_poll_task_is_cancelled_on_shutdown(hass) -> None:
    coordinator = make_coordinator(hass, preemptive=False)
    cancel_poll = Mock()
    coordinator._cancel_sound_poll = cancel_poll
    coordinator._sound_refresh_task = asyncio.create_task(asyncio.sleep(60))

    await coordinator.async_shutdown()

    cancel_poll.assert_called_once_with()
    assert coordinator._sound_refresh_task.cancelled()


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
