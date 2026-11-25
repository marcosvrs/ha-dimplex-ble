"""Boost (ConfigR Runback): RunbackTime minutes, then the setpoint; 0 ends it.

The fake heater below behaves as AM2086 did on 2026-11-25: Boost from a
continuous mode reads Manual with the duration code in byte 3, Boost from a timer
programme keeps the programme, and ending Boost restores the previous mode.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from conftest import FakeProxy
from homeassistant.components.climate import ClimateEntityFeature, HVACMode
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_connection_mode import make_entry, setup
from test_entities import ADDRESS, PROXY, make_coordinator, snapshot

from custom_components.dimplex_ble import coordinator as coordinator_module
from custom_components.dimplex_ble.climate import (
    AUTO_PRESETS,
    HEAT_PRESETS,
    DimplexClimate,
)
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_BOOST_MINUTES,
    CONF_PROXY_SOURCE,
    DOMAIN,
)
from custom_components.dimplex_ble.protocol import (
    AWAY_MODE,
    BOOST_REMAINING,
    BOOST_TEMPERATURE,
    HEATING_MODE,
    SETPOINT,
    DimplexProtocolError,
    encode_boost_minutes,
    parse_snapshot,
)

pytestmark = pytest.mark.usefixtures("skip_dependencies")

FROST = bytes((4, 0, 0, 0, 0, 0))
USER_TIMER = bytes((1, 3, 0, 0, 0, 0))


def test_minutes_encode_little_endian_up_to_240() -> None:
    assert encode_boost_minutes(30) == b"\x1e\x00"
    assert encode_boost_minutes(240) == b"\xf0\x00"
    assert encode_boost_minutes(0) == b"\x00\x00"
    for minutes in (241, -1, True):
        with pytest.raises(DimplexProtocolError):
            encode_boost_minutes(minutes)


@pytest.mark.parametrize(
    ("remaining", "temperature", "expected"),
    [
        (b"\x1d\x00", b"\x15\x00", (29, 21)),
        (b"\x1d", b"\x15\x01", (None, None)),
        (None, b"\x00\x00", (None, None)),
    ],
)
def test_snapshot_reads_boost(remaining, temperature, expected) -> None:
    values = {BOOST_TEMPERATURE: temperature}
    if remaining is not None:
        values[BOOST_REMAINING] = remaining
    snapshot = parse_snapshot(values)
    assert (snapshot.boost_minutes_left, snapshot.boost_temperature_c) == expected


def heater(proxy: FakeProxy, mode: bytes, boost_temperature: bytes = b"\x16\x00"):
    """Give the fake heater Boost and the AM2086 Boost behaviour."""
    for address in (BOOST_REMAINING, BOOST_TEMPERATURE):
        proxy.services.add(address)
    proxy.values[BOOST_REMAINING] = b"\x00\x00"
    proxy.values[BOOST_TEMPERATURE] = boost_temperature
    proxy.values[HEATING_MODE] = mode
    before = {"mode": mode}

    def on_write(address, payload) -> None:
        values = proxy.values
        if address == BOOST_REMAINING and payload != b"\x00\x00":
            before["mode"] = values[HEATING_MODE]
            values[BOOST_REMAINING] = payload
            timer = values[HEATING_MODE][0] == 1
            values[HEATING_MODE] = (
                values[HEATING_MODE][:3] if timer else bytes((2, 0, 0))
            ) + bytes((2, 0, 0))
        elif address == BOOST_REMAINING:
            values[BOOST_REMAINING] = payload
            values[HEATING_MODE] = before["mode"]
        elif address in (SETPOINT, HEATING_MODE):
            values[address] = payload

    proxy.on_write = on_write
    return proxy


def writes(proxy: FakeProxy) -> list[tuple[str, bytes]]:
    return [
        (e[1][4:8], e[2])
        for e in proxy.events
        if e[0] == "write" and e[1][4:8] in ("1027", "1023", "1001")
    ]


@pytest.fixture(autouse=True)
def no_settle(monkeypatch) -> None:
    monkeypatch.setattr(coordinator_module, "BOOST_END_SETTLE_S", 0)


# --- The writes, over the real link and coordinator -------------------------


async def test_start_writes_minutes_then_the_setpoint_in_one_link(
    hass, fake_bt
) -> None:
    proxy = heater(fake_bt.add(), FROST)
    coordinator = await setup(hass, make_entry(hass))
    proxy.events.clear()

    await coordinator.async_start_boost(30, 22)

    assert writes(proxy) == [("1027", b"\x1e\x00"), ("1023", b"\x16\x00")]
    assert coordinator.data.boost_minutes_left == 30
    assert coordinator.data.boost_temperature_c == 22
    assert coordinator.data.operating_mode_hex == "020000020000"


async def test_cancel_writes_zero_and_reads_the_restored_mode(hass, fake_bt) -> None:
    proxy = heater(fake_bt.add(), USER_TIMER)
    coordinator = await setup(hass, make_entry(hass))
    await coordinator.async_start_boost(60, 22)
    assert coordinator.data.operating_mode_hex == "010300020000"
    proxy.events.clear()

    await coordinator.async_cancel_boost()

    assert writes(proxy) == [("1027", b"\x00\x00")]
    assert coordinator.data.boost_minutes_left == 0
    assert coordinator.data.operating_mode_hex == "010300000000"


# --- The climate entity -------------------------------------------------------


def climate(hass, *, minutes=0, mode_hex="040000000000", boost_temperature=22, **kw):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
        options=kw.pop("options", {}),
    )
    _, coordinator = make_coordinator(
        hass,
        entry=entry,
        data=snapshot(
            mode_hex=mode_hex,
            boost_minutes_left=minutes,
            boost_temperature_c=boost_temperature,
            **kw,
        ),
    )
    coordinator.async_start_boost = AsyncMock()
    coordinator.async_cancel_boost = AsyncMock()
    coordinator.async_write_mode = AsyncMock()
    coordinator.async_write = AsyncMock()
    coordinator.async_set_away_mode = AsyncMock()
    return coordinator, DimplexClimate(coordinator)


def test_boost_is_a_preset_of_both_modes() -> None:
    assert "boost" in HEAT_PRESETS
    assert "boost" in AUTO_PRESETS


async def test_boost_starts_with_the_option_length_and_heater_temperature(
    hass,
) -> None:
    coordinator, entity = climate(hass, options={CONF_BOOST_MINUTES: 30})

    await entity.async_set_preset_mode("boost")

    coordinator.async_start_boost.assert_awaited_once_with(30, 22)


async def test_boost_defaults_to_an_hour(hass) -> None:
    coordinator, entity = climate(hass)
    await entity.async_set_preset_mode("boost")
    coordinator.async_start_boost.assert_awaited_once_with(60, 22)


async def test_boost_from_a_continuous_mode_is_heat_with_a_target(hass) -> None:
    coordinator, entity = climate(hass, minutes=29, mode_hex="020000010000")

    assert entity.preset_mode == "boost"
    assert entity.hvac_mode == HVACMode.HEAT
    assert entity.preset_modes == HEAT_PRESETS
    assert entity.native_target_temperature == 21

    await entity.async_set_preset_mode("boost")
    coordinator.async_start_boost.assert_not_awaited()  # already running


async def test_boost_from_a_timer_programme_stays_auto_with_a_target(hass) -> None:
    coordinator, entity = climate(hass, minutes=29, mode_hex="010300010000")

    assert entity.hvac_mode == HVACMode.AUTO
    assert entity.preset_modes == AUTO_PRESETS
    assert entity.supported_features & ClimateEntityFeature.TARGET_TEMPERATURE
    assert entity.native_target_temperature == 21

    await entity.async_set_temperature(temperature=23)
    coordinator.async_write.assert_awaited_once()
    with pytest.raises(ServiceValidationError):
        await entity.async_set_temperature(temperature=23, hvac_mode=HVACMode.HEAT)


async def test_another_preset_ends_boost_first(hass) -> None:
    coordinator, entity = climate(hass, minutes=29, mode_hex="020000010000")
    order: list[str] = []
    coordinator.async_cancel_boost.side_effect = lambda: order.append("cancel")
    coordinator.async_write_mode.side_effect = lambda *a, **k: order.append("mode")

    await entity.async_set_preset_mode("eco")

    assert order == ["cancel", "mode"]


async def test_away_ends_boost_first(hass) -> None:
    coordinator, entity = climate(hass, minutes=29, mode_hex="020000010000")
    await entity.async_set_preset_mode("away")
    coordinator.async_cancel_boost.assert_awaited_once()
    coordinator.async_set_away_mode.assert_awaited_once_with(True)


async def test_a_mode_change_ends_boost_first(hass) -> None:
    coordinator, entity = climate(hass, minutes=29, mode_hex="010300010000")

    await entity.async_set_hvac_mode(HVACMode.HEAT)

    coordinator.async_cancel_boost.assert_awaited_once()
    coordinator.async_write_mode.assert_awaited_once()


async def test_the_same_mode_only_ends_boost(hass) -> None:
    coordinator, entity = climate(hass, minutes=29, mode_hex="020000010000")
    coordinator.async_cancel_boost.side_effect = lambda: setattr(
        coordinator,
        "data",
        snapshot(mode_hex="040000000000", boost_minutes_left=0),
    )

    await entity.async_set_hvac_mode(HVACMode.HEAT)

    coordinator.async_cancel_boost.assert_awaited_once()
    coordinator.async_write_mode.assert_not_awaited()


def test_boost_is_not_offered_in_away(hass) -> None:
    _, entity = climate(hass, away_active=True)
    assert entity.preset_mode == "away"
    assert "boost" not in entity.preset_modes
    assert "away" in entity.preset_modes


@pytest.mark.parametrize(
    "kw",
    [
        {"boost_temperature": None},
        {"boost_temperature": 64},
        {"minimum": None},
    ],
)
async def test_boost_needs_a_valid_heater_temperature(hass, kw) -> None:
    coordinator, entity = climate(hass, **kw)

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_preset_mode("boost")
    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == "boost_unavailable"
    coordinator.async_start_boost.assert_not_awaited()


async def test_boost_without_data_is_unavailable(hass) -> None:
    coordinator, entity = climate(hass)
    coordinator.data = None
    with pytest.raises(ServiceValidationError):
        await entity.async_set_preset_mode("boost")


# --- Boost and Away never overlap (adversarial review 2026-11-25) -------------


async def test_away_started_meanwhile_stops_a_pending_boost(hass, fake_bt) -> None:
    """Away from the return-time entity runs outside the climate's call queue."""
    proxy = heater(fake_bt.add(), FROST)
    coordinator = await setup(hass, make_entry(hass))
    proxy.events.clear()

    async with coordinator._away_lock:  # an Away write is in progress
        start = hass.async_create_task(coordinator.async_start_boost(30, 22))
        await asyncio.sleep(0)
        coordinator.data = replace(coordinator.data, away_active=True)

    with pytest.raises(ServiceValidationError) as err:
        await start
    assert err.value.translation_key == "boost_not_in_away"
    assert writes(proxy) == []


async def test_entering_away_ends_a_running_boost_first(hass, fake_bt) -> None:
    proxy = heater(fake_bt.add(), FROST)
    proxy.services.add(AWAY_MODE)
    proxy.values[AWAY_MODE] = bytes(8)
    coordinator = await setup(hass, make_entry(hass))
    await coordinator.async_start_boost(30, 22)
    proxy.events.clear()

    await coordinator.async_set_away_mode(
        True, until=dt_util.now() + timedelta(hours=13)
    )

    boost_and_away = [
        (e[1][4:8], e[2][:1])
        for e in proxy.events
        if e[0] == "write" and e[1][4:8] in ("1027", "102a")
    ]
    assert boost_and_away == [("1027", b"\x00"), ("102a", b"\x01")]


async def test_entering_away_without_boost_writes_only_away(hass, fake_bt) -> None:
    proxy = heater(fake_bt.add(), FROST)
    proxy.services.add(AWAY_MODE)
    proxy.values[AWAY_MODE] = bytes(8)
    coordinator = await setup(hass, make_entry(hass))
    proxy.events.clear()

    await coordinator.async_set_away_mode(
        True, until=dt_util.now() + timedelta(hours=13)
    )

    assert "1027" not in [e[1][4:8] for e in proxy.events if e[0] == "write"]


async def test_boost_is_recorded_before_the_away_lock_is_released(
    hass, fake_bt
) -> None:
    heater(fake_bt.add(), FROST)
    coordinator = await setup(hass, make_entry(hass))
    coordinator.async_refresh = AsyncMock()  # the poll after Boost is slow

    await coordinator.async_start_boost(30, 22)

    assert coordinator.data.boost_minutes_left == 30


async def test_in_away_the_target_is_the_away_temperature(hass) -> None:
    coordinator, entity = climate(
        hass, mode_hex="010500000000", away_active=True, away_target_temperature_c=10
    )
    coordinator.async_set_away_temperature = AsyncMock()

    assert entity.native_target_temperature == 10
    await entity.async_set_temperature(temperature=12)

    coordinator.async_set_away_temperature.assert_awaited_once_with(12.0)
    coordinator.async_write.assert_not_awaited()
