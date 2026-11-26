"""The temperature a heater heats towards, Auto included (the climate target)."""

from __future__ import annotations

import dataclasses
from datetime import datetime, time
from types import SimpleNamespace

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_entities import snapshot

from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.protocol import SETBACK, SchedulePeriod

USER_TIMER = "0103"
UNKNOWN_TIMER = "0100"
MANUAL = "0201"
THURSDAY = 3  # 26 Nov 2026
MORNING = (SchedulePeriod(time(6, 15), time(7, 30), 20),)


def coordinator(hass, data) -> DimplexCoordinator:
    link = SimpleNamespace(
        address="AA:BB:CC:DD:EE:30",
        source="proxy",
        sync_clock=True,
        clock_offset_s=None,
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: link.address, CONF_PROXY_SOURCE: link.source},
    )
    result = DimplexCoordinator(hass, entry, link)
    result.data = data
    return result


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 11, 26, hour, minute, tzinfo=dt_util.get_default_time_zone())


def timer(hass, *, setback: bytes | None, mode: str = USER_TIMER, setpoint: int = 8):
    result = coordinator(hass, snapshot(mode_hex=mode, setpoint=setpoint))
    if setback is not None:
        result.settings[SETBACK] = setback
    week = [None] * 7
    week[THURSDAY] = MORNING
    result.schedules = {"user_timer": week}
    return result


async def test_continuous_modes_show_the_setpoint(hass) -> None:
    assert coordinator(hass, snapshot(mode_hex=MANUAL)).target_temperature_c() == 21
    assert coordinator(hass, None).target_temperature_c() is None


async def test_auto_with_setback_shows_what_the_heater_holds(hass) -> None:
    """AM6532 2026-11-26 10:05: Setback on, outside periods, setpoint 8."""
    assert timer(hass, setback=b"\x01\x08").target_temperature_c(at(10, 5)) == 8


async def test_a_period_boundary_changes_the_target_without_a_new_snapshot(
    hass,
) -> None:
    """Codex review: the programme decides, not a setpoint polled before 06:15."""
    heater = timer(hass, setback=b"\x01\x08", setpoint=8)
    assert heater.target_temperature_c(at(6, 14)) == 8
    assert heater.target_temperature_c(at(6, 15)) == 20
    assert heater.target_temperature_c(at(7, 30)) == 8
    heater.schedules = {}  # programme not read yet: the heater's setpoint
    assert heater.target_temperature_c(at(6, 30)) == 8


async def test_auto_without_setback_shows_a_target_only_in_a_period(hass) -> None:
    heater = timer(hass, setback=b"\x00\x08", setpoint=20)
    assert heater.target_temperature_c(at(6, 15)) == 20
    assert heater.target_temperature_c(at(7, 30)) is None  # the period ended
    assert heater.target_temperature_c(at(10, 5)) is None


async def test_advance_swaps_the_current_period(hass) -> None:
    """Advance toggles Heating On/Off until the next period (PLXE manual p. 17)."""
    heater = timer(hass, setback=b"\x00\x08", setpoint=20)
    heater.data = dataclasses.replace(heater.data, advance_active=True)
    assert heater.target_temperature_c(at(10, 5)) == 20  # heats outside a period
    assert heater.target_temperature_c(at(6, 30)) is None  # skips this period


async def test_the_heater_clock_decides(hass) -> None:
    heater = timer(hass, setback=b"\x00\x08", setpoint=20)
    heater.link.clock_offset_s = 30 * 60  # unsynced, or its sync write failed
    assert heater.target_temperature_c(at(6, 0)) == 20  # 06:30 on the heater
    assert heater.target_temperature_c(at(7, 15)) is None  # 07:45 on the heater
    heater.link.clock_offset_s = 0.0  # a successful sync resets it
    assert heater.target_temperature_c(at(6, 0)) is None


async def test_auto_without_a_programme_read_shows_nothing(hass) -> None:
    heater = timer(hass, setback=None)
    heater.schedules = {}
    assert heater.target_temperature_c(at(6, 30)) is None
    unknown = timer(hass, setback=None, mode=UNKNOWN_TIMER, setpoint=20)
    assert unknown.target_temperature_c(at(6, 30)) == 20  # read as User Timer
    assert unknown.target_temperature_c() in (20, None)  # now: any time of day
