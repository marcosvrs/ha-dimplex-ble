"""Read-only timer programme schedules (0/0x1002, ConfigR DayData layout)."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from conftest import FakeProxy
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from test_connection_mode import advance, kinds, make_entry, setup

from custom_components.dimplex_ble.const import (
    CONF_KEEP_CONNECTED,
    POLL_INTERVAL_PER_POLL,
    SCHEDULE_UPDATE_INTERVAL,
)
from custom_components.dimplex_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.dimplex_ble.errors import HeaterUnavailableError
from custom_components.dimplex_ble.protocol import (
    DimplexProtocolError,
    SchedulePeriod,
    encode_schedule_request,
    parse_schedule_day,
)

pytestmark = pytest.mark.usefixtures("skip_dependencies")

SCHEDULE_UUID_SUFFIX = "1002"


def record(programme: int, day: int, slots: str) -> bytes:
    """ConfigR factory default records (Constants.cctor), day byte replaced."""
    body = bytes.fromhex(slots)
    return bytes((0, programme, day)) + body + bytes(31 - 3 - len(body))


HOME = "080015001500" + "150015001500" * 3  # 08:00-21:00 21 C, fillers dropped
OUT = "0700081e1500" + "111e16001500" + "160016001500" * 2
USER = "061e091e1500" + "0b000d001500" + "0f0011001500" + "120016001500"


def defaults() -> dict[tuple[int, int], bytes]:
    out = {}
    for day in range(7):
        out[(1, day)] = record(1, day, OUT)
        out[(2, day)] = record(2, day, HOME)
        out[(3, day)] = record(3, day, USER)
    return out


def test_parse_decodes_the_configr_defaults() -> None:
    assert parse_schedule_day(record(2, 0, HOME), 2, 0) == (
        SchedulePeriod(time(8), time(21), 21),
    )
    assert parse_schedule_day(record(1, 4, OUT), 1, 4) == (
        SchedulePeriod(time(7), time(8, 30), 21),
        SchedulePeriod(time(17, 30), time(22), 21),
    )
    assert [p.start for p in parse_schedule_day(record(3, 6, USER), 3, 6)] == [
        time(6, 30),
        time(11),
        time(15),
        time(18),
    ]


def test_parse_sorts_periods_and_keeps_an_empty_day() -> None:
    unsorted = record(3, 1, "120016001400" + "061e091e1300")
    assert [p.temperature_c for p in parse_schedule_day(unsorted, 3, 1)] == [19, 20]
    assert parse_schedule_day(bytes(31)[:1] + bytes((3, 1)) + bytes(28), 3, 1) == ()


@pytest.mark.parametrize(
    "value",
    [
        None,
        bytes(31),  # nothing selected yet: programme 0
        record(2, 1, HOME),  # another day
        record(1, 0, HOME),  # another programme
        record(2, 0, HOME)[:30],
        record(2, 0, "190000001500"),  # 25:00 is not a time
        record(2, 0, "150008001500"),  # ends before it starts
    ],
)
def test_parse_rejects_records_that_are_not_the_requested_day(value) -> None:
    assert parse_schedule_day(value, 2, 0) is None


def test_request_is_never_the_save_form() -> None:
    assert encode_schedule_request(3, 6) == b"\x00\x03\x06"
    with pytest.raises(DimplexProtocolError):
        encode_schedule_request(5, 0)  # Away is not a stored programme
    with pytest.raises(DimplexProtocolError):
        encode_schedule_request(2, 7)


def schedule_writes(proxy: FakeProxy) -> list[bytes]:
    return [
        e[2]
        for e in proxy.events
        if e[0] == "write" and e[1][4:8] == SCHEDULE_UUID_SUFFIX
    ]


def with_schedules(proxy: FakeProxy) -> FakeProxy:
    proxy.services.add((0, 0x1002))
    proxy.values[(0, 0x1002)] = bytes(31)
    proxy.schedule = defaults()
    return proxy


async def test_persistent_link_reads_all_programmes_after_setup(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)

    coordinator = await setup(hass, entry)

    writes = schedule_writes(proxy)
    assert len(writes) == 21
    assert {w[0] for w in writes} == {0}  # select only, never save
    assert {w[1] for w in writes} == {1, 2, 3}
    assert list(coordinator.schedules) == ["user_timer", "home_all_day", "out_all_day"]
    assert coordinator.schedules["home_all_day"][6] == (
        SchedulePeriod(time(8), time(21), 21),
    )
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["schedules"]["home_all_day"][0] == ["08:00-21:00 21C"]
    assert diagnostics["schedules"]["out_all_day"][0] == [
        "07:00-08:30 21C",
        "17:30-22:00 21C",
    ]

    proxy.events.clear()
    await advance(hass, SCHEDULE_UPDATE_INTERVAL.total_seconds() + 1)
    assert len(schedule_writes(proxy)) == 21
    assert "connect" not in kinds(proxy.events)  # over the held link


async def test_a_heater_without_schedules_reads_as_unknown_days(hass, fake_bt) -> None:
    fake_bt.add()
    coordinator = await setup(hass, make_entry(hass))
    assert coordinator.schedules["user_timer"] == [None] * 7


async def test_a_failed_schedule_read_keeps_the_last_schedules(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    coordinator = await setup(hass, make_entry(hass))
    before = coordinator.schedules
    proxy.read_error = HeaterUnavailableError("gone")

    await coordinator.async_refresh_schedules()

    assert coordinator.schedules is before


async def test_no_schedule_read_while_the_heater_is_unreachable(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    coordinator = await setup(hass, make_entry(hass))
    coordinator.last_update_success = False
    proxy.events.clear()

    await coordinator.async_refresh_schedules()

    assert proxy.events == []


async def test_per_poll_reads_schedules_inside_the_poll_connection(
    hass, fake_bt
) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: False})

    coordinator = await setup(hass, entry)

    assert kinds(proxy.events).count("connect") == 1
    assert len(schedule_writes(proxy)) == 21
    assert coordinator.schedules["user_timer"][0] is not None

    # Not due: the next poll reads only the snapshot.
    proxy.events.clear()
    await advance(hass, POLL_INTERVAL_PER_POLL + 1)
    assert schedule_writes(proxy) == []

    # Due again: read inside the following poll, never in an extra connection.
    proxy.events.clear()
    await coordinator.async_refresh_schedules()
    assert proxy.events == []
    await advance(hass, 2 * POLL_INTERVAL_PER_POLL + 2)
    assert len(schedule_writes(proxy)) == 21
    assert kinds(proxy.events).count("connect") == 1


# --- calendars --------------------------------------------------------------


def calendar(coordinator, preset="user_timer"):
    from custom_components.dimplex_ble import calendar as platform

    classes = {
        "user_timer": platform.UserTimerCalendar,
        "home_all_day": platform.HomeAllDayCalendar,
        "out_all_day": platform.OutAllDayCalendar,
    }
    return classes[preset](coordinator)


async def test_setup_adds_one_calendar_per_programme(hass, fake_bt) -> None:
    with_schedules(fake_bt.add())
    await setup(hass, make_entry(hass))

    states = {s.entity_id: s.state for s in hass.states.async_all("calendar")}
    assert list(states) == ["calendar.dimplex_panel_heater_am6532_user_timer_schedule"]
    assert set(states.values()) <= {"on", "off"}
    registry = er.async_get(hass)
    for name in ("home_all_day", "out_all_day"):
        entity_id = f"calendar.dimplex_panel_heater_am6532_{name}_schedule"
        assert registry.async_get(entity_id).disabled_by is not None


async def test_calendar_lists_periods_in_the_window(hass, fake_bt) -> None:
    with_schedules(fake_bt.add())
    coordinator = await setup(hass, make_entry(hass))
    zone = dt_util.get_default_time_zone()
    monday = datetime(2026, 11, 23, tzinfo=zone)  # a Monday
    coordinator.schedules["user_timer"][0] = (
        SchedulePeriod(time(5, 30), time(7, 30), 20),
        SchedulePeriod(time(16), time(17), 21),
    )
    entity = calendar(coordinator)

    events = await entity.async_get_events(
        hass, monday + timedelta(hours=7), monday + timedelta(days=1, hours=7)
    )

    assert [(e.start.isoformat(), e.end.isoformat(), e.summary) for e in events] == [
        (
            (monday + timedelta(hours=5, minutes=30)).isoformat(),
            (monday + timedelta(hours=7, minutes=30)).isoformat(),
            "20 °C",
        ),
        (
            (monday + timedelta(hours=16)).isoformat(),
            (monday + timedelta(hours=17)).isoformat(),
            "21 °C",
        ),
        (  # Tuesday's first default period starts at 06:30
            (monday + timedelta(days=1, hours=6, minutes=30)).isoformat(),
            (monday + timedelta(days=1, hours=9, minutes=30)).isoformat(),
            "21 °C",
        ),
    ]
    # Noon on Monday: the next period is 16:00-17:00.
    with patch(
        "custom_components.dimplex_ble.calendar.dt_util.now",
        return_value=monday + timedelta(hours=12),
    ):
        assert entity.event.start == monday + timedelta(hours=16)


async def test_calendar_is_unavailable_until_a_day_was_read(hass, fake_bt) -> None:
    fake_bt.add()  # no schedule characteristic
    coordinator = await setup(hass, make_entry(hass))
    entity = calendar(coordinator, "home_all_day")

    assert entity.available is False
    assert entity.event is None
    coordinator.schedules = {}
    assert entity.available is False
    assert (
        await entity.async_get_events(
            hass, dt_util.now(), dt_util.now() + timedelta(days=7)
        )
        == []
    )
