from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.errors import DimplexError
from custom_components.dimplex_ble.protocol import SOUND
from custom_components.dimplex_ble.switch import (
    DimplexAdvanceSwitch,
    DimplexOpenWindowDetectionSwitch,
    DimplexPreemptiveHeatingSwitch,
    DimplexSetbackSwitch,
    DimplexSoundSwitch,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def _link(client):
    """Link fake that records writes/reads on a client-shaped mock."""

    async def async_write(address, payload):
        await client.async_write_raw(*address, payload)

    async def async_read(address):
        return await client.async_read_raw(*address)

    async def async_read_then_write(address, build):
        payload = build(await client.async_read_raw(*address))
        await client.async_write_raw(*address, payload)
        return payload

    return SimpleNamespace(
        address=ADDRESS,
        source=PROXY,
        keep_connected=True,
        async_write=async_write,
        async_read=async_read,
        async_read_then_write=async_read_then_write,
    )


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
    coordinator = DimplexCoordinator(hass, entry, _link(client))
    coordinator.data = SimpleNamespace(
        preemptive_heating=preemptive,
        open_window_detection=open_window,
    )
    if sound is not None:
        coordinator.settings[SOUND] = sound
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

    assert len(entities) == 5
    assert {entity.translation_key for entity in entities} == {
        "sound",
        "open_window_detection",
        "preemptive_heating",
        "setback",
        "advance",
    }
    assert entities[0].is_on is True
    assert entities[1].is_on is True
    assert entities[2].is_on is False
    # Guidance lives in the README, icons in icons.json: no state attributes.
    assert all(entity.extra_state_attributes is None for entity in entities)
    assert all(entity.icon is None for entity in entities)
    assert [entity.unique_id.split("_", 1)[1] for entity in entities] == [
        "sound",
        "open_window_detection",
        "preemptive_heating",
        "setback",
        "advance",
    ]
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


async def test_sound_switch_writes_and_reads_back_without_main_refresh(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=[b"\x01\x00\x00", b"\x00\x00\x00"]),
        async_read_raw=AsyncMock(side_effect=[b"\x01\x00\x00", b"\x00\x00\x00"]),
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
    # Each write is read back on the held link.
    assert [call.args for call in client.async_read_raw.await_args_list] == [
        (2, 0x0005),
        (2, 0x0005),
    ]
    assert coordinator.settings.get(SOUND) == b"\x00\x00\x00"


async def test_sound_poll_is_independent_and_updates_cache(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw=AsyncMock(return_value=b"\x01\x00\x00"),
    )
    coordinator = make_coordinator(
        hass, preemptive=False, sound=b"\x00\x00\x00", client=client
    )
    coordinator.last_update_success = True

    await coordinator.async_refresh_settings()

    assert coordinator.settings.get(SOUND) == b"\x01\x00\x00"
    assert [call.args for call in client.async_read_raw.await_args_list] == [
        (2, 0x0005),
        (0, 0x1016),
        (2, 0x1019),
    ]


async def test_sound_poll_ignores_failed_and_invalid_reads(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw=AsyncMock(
            side_effect=[DimplexError("offline"), b"\x02\x00\x00", None, None]
        ),
    )
    coordinator = make_coordinator(
        hass, preemptive=False, sound=b"\x00\x00\x00", client=client
    )
    coordinator.last_update_success = True

    await coordinator.async_refresh_settings()
    await coordinator.async_refresh_settings()

    assert coordinator.settings.get(SOUND) == b"\x00\x00\x00"


async def test_sound_poll_skips_an_unavailable_coordinator(hass) -> None:
    client = SimpleNamespace(async_read_raw=AsyncMock())
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    coordinator.last_update_success = False

    await coordinator.async_refresh_settings()

    client.async_read_raw.assert_not_awaited()


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


SETBACK = (0, 0x1016)


def test_setback_switch_requires_a_verified_value(hass) -> None:
    coordinator = make_coordinator(hass, preemptive=False)
    entity = DimplexSetbackSwitch(coordinator)
    assert entity.available is False
    assert entity.entity_category is EntityCategory.CONFIG

    coordinator.settings[SETBACK] = b"\x01\x08"
    assert entity.available is True
    assert entity.is_on is True


async def test_setback_switch_keeps_the_temperature_set_on_the_heater(hass) -> None:
    """The heater's menu changed the temperature (8 -> 10) since the last poll."""
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(),
        async_read_raw=AsyncMock(side_effect=[b"\x00\x0a", b"\x01\x0a"]),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    coordinator.settings[SETBACK] = b"\x00\x08"
    entity = DimplexSetbackSwitch(coordinator)

    await entity.async_turn_on()
    assert coordinator.settings[SETBACK] == b"\x01\x0a"
    await entity.async_turn_off()

    assert [call.args for call in client.async_write_raw.await_args_list] == [
        (0, 0x1016, b"\x01\x0a"),
        (0, 0x1016, b"\x00\x0a"),
    ]
    assert entity.is_on is False
    coordinator.async_request_refresh.assert_not_awaited()


async def test_setback_switch_refuses_without_a_readable_record(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(),
        async_read_raw=AsyncMock(return_value=None),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)

    with pytest.raises(ServiceValidationError) as err:
        await DimplexSetbackSwitch(coordinator).async_turn_on()

    assert err.value.translation_key == "setting_unavailable"
    client.async_write_raw.assert_not_awaited()


async def test_setting_readback_failure_keeps_the_written_value(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(),
        async_read_raw=AsyncMock(side_effect=DimplexError("offline")),
    )
    coordinator = make_coordinator(hass, preemptive=False, client=client)

    await DimplexSoundSwitch(coordinator).async_turn_on()

    assert coordinator.settings.get(SOUND) == b"\x01\x00\x00"


async def test_per_poll_setting_write_is_read_back_by_the_next_poll(hass) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    coordinator.link.keep_connected = False
    coordinator._settings_due = False

    await DimplexSoundSwitch(coordinator).async_turn_on()

    assert coordinator._settings_due is True
    assert coordinator.settings.get(SOUND) == b"\x01\x00\x00"


def _advance_coordinator(hass, client, *, mode_hex, away=False, advance=False):
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    coordinator.data = SimpleNamespace(
        operating_mode_hex=mode_hex, away_active=away, advance_active=advance
    )
    return coordinator


@pytest.mark.parametrize("mode_hex", ["010300000000", "010200000000", "010100000000"])
async def test_advance_switch_writes_in_timer_presets_and_refreshes(
    hass, mode_hex
) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = _advance_coordinator(hass, client, mode_hex=mode_hex)
    entity = DimplexAdvanceSwitch(coordinator)
    assert entity.available is True
    assert entity.is_on is False
    assert entity.entity_category is None

    await entity.async_turn_on()
    await entity.async_turn_off()

    assert [call.args for call in client.async_write_raw.await_args_list] == [
        (0, 0x100D, b"\x01"),
        (0, 0x100D, b"\x00"),
    ]
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.parametrize(
    ("mode_hex", "away"),
    [
        ("020000000000", False),
        ("030000000000", False),
        ("040000000000", False),
        ("010500000000", False),
        ("010300000000", True),
        (None, False),
    ],
)
async def test_advance_switch_refuses_outside_timer_presets(
    hass, mode_hex, away
) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = _advance_coordinator(hass, client, mode_hex=mode_hex, away=away)

    with pytest.raises(ServiceValidationError) as err:
        await DimplexAdvanceSwitch(coordinator).async_turn_on()

    assert err.value.translation_key == "advance_requires_timer"
    client.async_write_raw.assert_not_awaited()


async def test_advance_switch_refuses_without_data(hass) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = make_coordinator(hass, preemptive=False, client=client)
    coordinator.data = None

    with pytest.raises(ServiceValidationError):
        await DimplexAdvanceSwitch(coordinator).async_turn_on()


def test_advance_switch_is_unavailable_for_unknown_values(hass) -> None:
    coordinator = _advance_coordinator(
        hass, None, mode_hex="010300000000", advance=None
    )
    assert DimplexAdvanceSwitch(coordinator).available is False
