"""Estimated energy: the heater's estimated power integrated over time."""

from __future__ import annotations

import pytest
from conftest import ADDRESS, PIN, PROXY
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache_with_extra_data,
)

from custom_components.dimplex_ble import sensor
from custom_components.dimplex_ble.const import (
    CONF_ADDRESS,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    DOMAIN,
)
from custom_components.dimplex_ble.protocol import POWER_LOADING, TRAC

pytestmark = pytest.mark.usefixtures("skip_dependencies")
FULL = b"\x01\x01\x64"  # heating, 100 % output


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    tick = Clock()
    monkeypatch.setattr(sensor, "monotonic", tick)
    return tick


def heater(fake_bt, output: bytes = FULL):
    proxy = fake_bt.add()
    proxy.services.add(POWER_LOADING)
    proxy.values[POWER_LOADING] = bytes.fromhex("f401") + bytes(6)  # 500 W
    proxy.services.add(TRAC)
    proxy.values[TRAC] = output
    return proxy


async def setup(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater AM2086",
        unique_id=ADDRESS.lower(),
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY, CONF_PASSKEY: PIN},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data


def energy(hass) -> State:
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{ADDRESS.lower()}_estimated_energy"
    )
    return hass.states.get(entity_id)


async def tick(hass) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + sensor.ENERGY_STEP * 2)
    await hass.async_block_till_done()


async def test_500_w_for_an_hour_is_half_a_kwh(hass, fake_bt, clock) -> None:
    heater(fake_bt)
    await setup(hass)
    state = energy(hass)
    assert state.attributes["unit_of_measurement"] == "kWh"
    assert state.attributes["device_class"] == "energy"
    assert state.attributes["state_class"] == "total_increasing"

    clock.now += 3600
    await tick(hass)

    assert float(energy(hass).state) == pytest.approx(0.5)


async def test_an_unreachable_heater_is_not_counted(hass, fake_bt, clock) -> None:
    proxy = heater(fake_bt)
    coordinator = await setup(hass)
    clock.now += 1800
    proxy.sees_heater = False
    await coordinator.async_refresh()  # fails: counts up to now, then stops
    await hass.async_block_till_done()

    clock.now += 7200  # two hours unreachable
    await tick(hass)
    assert energy(hass).state == "unavailable"

    proxy.sees_heater = True  # back: the two hours are not counted
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(energy(hass).state) == pytest.approx(0.25)


async def test_the_total_survives_a_restart(hass, fake_bt, clock) -> None:
    entity_id = "sensor.dimplex_heater_am2086_estimated_energy"
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{ADDRESS.lower()}_estimated_energy",
        suggested_object_id="dimplex_heater_am2086_estimated_energy",
    )
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State(entity_id, "1.5"),
                {"native_value": 1.5, "native_unit_of_measurement": "kWh"},
            )
        ],
    )
    heater(fake_bt, output=b"\x01\x00\x00")  # idle: nothing added
    await setup(hass)

    clock.now += 3600
    await tick(hass)

    assert float(energy(hass).state) == pytest.approx(1.5)
