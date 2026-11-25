"""Read-only timer programme schedules (0/0x1002, ConfigR DayData layout)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
import voluptuous as vol
from bleak.exc import BleakError
from conftest import FakeProxy
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from test_connection_mode import advance, kinds, make_entry, setup

from custom_components.dimplex_ble.const import (
    CONF_KEEP_CONNECTED,
    DOMAIN,
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
    encode_schedule_day,
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
    await advance(hass, SCHEDULE_UPDATE_INTERVAL.total_seconds() + 12)  # next poll
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
    proxy.events.clear()

    coordinator.async_schedules_due()  # marks only: no traffic of its own
    assert proxy.events == []
    proxy.read_error = HeaterUnavailableError("gone")
    await coordinator.async_refresh()

    assert coordinator.schedules is before
    assert coordinator._schedules_due  # read again by a later poll


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
    coordinator.async_schedules_due()
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


# --- Saving ----------------------------------------------------------------


def test_save_form_matches_what_the_heater_accepted() -> None:
    """Payloads written to AM2086 on 2026-11-25 and read back unchanged."""
    monday = (
        SchedulePeriod(time(17), time(22), 21),
        SchedulePeriod(time(6), time(8), 20),  # sorted on save
    )
    assert encode_schedule_day(3, 0, monday).hex() == (
        "010300" + "060008001400" + "110016001500" + "160016001500" * 2 + "00000000"
    )
    assert encode_schedule_day(3, 1, ()).hex() == (
        "010301" + "000000001500" * 4 + "00000000"
    )


@pytest.mark.parametrize(
    "periods",
    [
        (SchedulePeriod(time(6), time(7), 21),) * 5,
        (SchedulePeriod(time(8), time(8), 21),),
        (SchedulePeriod(time(9), time(8), 21),),
        (SchedulePeriod(time(8, 0, 30), time(9), 21),),
        (SchedulePeriod(time(6), time(9), 21), SchedulePeriod(time(8), time(10), 21)),
        (SchedulePeriod(time(6), time(9), 0),),
    ],
)
def test_save_form_rejects_invalid_days(periods) -> None:
    with pytest.raises(DimplexProtocolError):
        encode_schedule_day(3, 0, periods)


def device_id(hass, entry) -> str:
    [device] = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device.id


async def set_schedule(hass, entry, **data) -> None:
    await hass.services.async_call(
        DOMAIN,
        "set_schedule",
        {"device_id": device_id(hass, entry), **data},
        blocking=True,
    )


async def test_set_schedule_saves_each_day_and_reads_it_back(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    proxy.events.clear()

    await set_schedule(
        hass,
        entry,
        programme="user_timer",
        days=["mon", "wed"],
        periods=[
            {"start": "17:00", "end": "22:00", "temperature": 21},
            {"start": "06:30", "end": "08:00", "temperature": 20},
        ],
    )

    writes = schedule_writes(proxy)
    # Each day is read back before the next one is written.
    assert [(w[0], w[2]) for w in writes] == [(1, 0), (0, 0), (1, 2), (0, 2)]
    expected = (
        SchedulePeriod(time(6, 30), time(8), 20),
        SchedulePeriod(time(17), time(22), 21),
    )
    week = coordinator.schedules["user_timer"]
    assert week[0] == week[2] == expected
    assert week[1] == parse_schedule_day(record(3, 1, USER), 3, 1)  # untouched


async def test_set_schedule_without_periods_empties_the_days(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)

    await set_schedule(hass, entry, programme="home_all_day", days=["sun"])

    assert coordinator.schedules["home_all_day"][6] == ()
    assert proxy.schedule[(2, 6)][3:] == bytes.fromhex("000000001500" * 4 + "00000000")


async def test_set_schedule_reports_a_day_the_heater_did_not_keep(
    hass, fake_bt
) -> None:
    proxy = with_schedules(fake_bt.add())
    proxy.saves_schedules = False
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)

    with pytest.raises(HomeAssistantError) as err:
        await set_schedule(
            hass,
            entry,
            programme="out_all_day",
            days=["fri"],
            periods=[{"start": "07:00", "end": "09:00", "temperature": 21}],
        )
    assert err.value.translation_key == "schedule_not_saved"
    # The calendar shows what the heater actually holds.
    assert coordinator.schedules["out_all_day"][4] == parse_schedule_day(
        record(1, 4, OUT), 1, 4
    )


async def test_set_schedule_on_a_heater_without_schedules(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass, **{CONF_KEEP_CONNECTED: False})
    coordinator = await setup(hass, entry)
    coordinator.schedules = {}

    await set_schedule(hass, entry, programme="user_timer", days=["tue"])

    assert coordinator.schedules["user_timer"][1] == ()
    assert coordinator.schedules["user_timer"][0] is None
    assert proxy.schedule[(3, 1)][0] == 0


@pytest.mark.parametrize(
    ("periods", "key"),
    [
        ([{"start": "06:00", "end": "07:00", "temperature": 31}], "invalid_setpoint"),
        (
            [
                {"start": "06:00", "end": "09:00", "temperature": 21},
                {"start": "08:00", "end": "10:00", "temperature": 21},
            ],
            "invalid_schedule",
        ),
    ],
)
async def test_set_schedule_rejects_before_writing(hass, fake_bt, periods, key) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    await setup(hass, entry)
    proxy.events.clear()

    with pytest.raises(ServiceValidationError) as err:
        await set_schedule(
            hass, entry, programme="user_timer", days=["mon"], periods=periods
        )
    assert err.value.translation_key == key
    assert schedule_writes(proxy) == []


async def test_set_schedule_needs_the_setpoint_range(hass, fake_bt) -> None:
    with_schedules(fake_bt.add())
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    coordinator.data = replace(coordinator.data, minimum_temperature_c=None)

    period = [{"start": "07:00", "end": "09:00", "temperature": 21}]
    with pytest.raises(ServiceValidationError) as err:
        await set_schedule(
            hass, entry, programme="user_timer", days=["mon"], periods=period
        )
    assert err.value.translation_key == "setpoint_unavailable"

    coordinator.data = None
    with pytest.raises(ServiceValidationError):
        await set_schedule(
            hass, entry, programme="user_timer", days=["mon"], periods=period
        )


async def test_set_schedule_validates_its_fields(hass, fake_bt) -> None:
    with_schedules(fake_bt.add())
    entry = make_entry(hass)
    await setup(hass, entry)
    for bad in (
        {"programme": "away", "days": ["mon"]},
        {"programme": "user_timer", "days": []},
        {"programme": "user_timer", "days": ["monday"]},
    ):
        with pytest.raises(vol.Invalid):
            await set_schedule(hass, entry, **bad)


# --- Failures part-way and input checks (adversarial review 2026-11-25) -------


def fail_saving(proxy: FakeProxy, day: int) -> None:
    """The link drops while saving ``day`` (after the heater took the write)."""

    def on_write(address, payload) -> None:
        if address == (0, 0x1002) and payload[0] == 1 and payload[2] == day:
            raise BleakError("link lost")

    proxy.on_write = on_write


async def test_a_failure_part_way_keeps_the_saved_days(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)
    fail_saving(proxy, day=2)
    period = {"start": "07:00", "end": "09:00", "temperature": 21}

    with pytest.raises(HomeAssistantError) as err:
        await set_schedule(
            hass, entry, programme="user_timer", days=["mon", "wed"], periods=[period]
        )
    assert err.value.translation_key == "schedule_partly_saved"
    assert err.value.translation_placeholders == {"saved": "1", "total": "2"}
    saved = (SchedulePeriod(time(7), time(9), 21),)
    assert coordinator.schedules["user_timer"][0] == saved

    # Every programme is read again, so the calendars show what the heater holds.
    proxy.on_write = None
    await coordinator.async_refresh()
    assert coordinator.schedules["user_timer"][2] == saved


async def test_a_failure_on_the_first_day_reports_the_link_error(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    await setup(hass, entry)
    fail_saving(proxy, day=0)

    with pytest.raises(HomeAssistantError) as err:
        await set_schedule(hass, entry, programme="user_timer", days=["mon"])
    assert err.value.translation_key == "cannot_connect"


@pytest.mark.parametrize("temperature", [21.9, "21.5", True, "warm"])
async def test_set_schedule_refuses_fractional_temperatures(
    hass, fake_bt, temperature
) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    await setup(hass, entry)
    proxy.events.clear()

    # Text fails the schema; numbers get the climate's translated error.
    with pytest.raises((vol.Invalid, ServiceValidationError)) as err:
        await set_schedule(
            hass,
            entry,
            programme="user_timer",
            days=["mon"],
            periods=[{"start": "07:00", "end": "09:00", "temperature": temperature}],
        )
    if isinstance(err.value, ServiceValidationError):
        assert err.value.translation_key == "invalid_setpoint"
    assert schedule_writes(proxy) == []


async def test_set_schedule_accepts_whole_degrees_as_floats(hass, fake_bt) -> None:
    proxy = with_schedules(fake_bt.add())
    entry = make_entry(hass)
    coordinator = await setup(hass, entry)

    await set_schedule(
        hass,
        entry,
        programme="user_timer",
        days=["mon"],
        periods=[{"start": "07:00", "end": "09:00", "temperature": 21.0}],
    )
    assert coordinator.schedules["user_timer"][0][0].temperature_c == 21
    assert proxy.schedule[(3, 0)][7] == 21


async def test_a_failed_setting_read_does_not_starve_the_schedule_read(
    hass, fake_bt, monkeypatch
) -> None:
    """Codex review: a slow-setting GATT error must not hide the calendars."""
    from custom_components.dimplex_ble import link as link_module
    from custom_components.dimplex_ble.protocol import SOUND

    real = link_module._read_optional

    async def read(client, address):
        if address == SOUND:
            raise BleakError("sound characteristic errored")
        return await real(client, address)

    monkeypatch.setattr(link_module, "_read_optional", read)
    proxy = with_schedules(fake_bt.add())

    coordinator = await setup(hass, make_entry(hass))

    # The failing setting never confirms, but the schedules still loaded.
    assert len(schedule_writes(proxy)) == 21
    assert coordinator.schedules["user_timer"][0] is not None
    assert coordinator._settings_due  # Sound retried next poll
    assert not coordinator._schedules_due  # schedules are in hand


async def test_a_schedule_read_failure_keeps_the_poll_and_retries(
    hass, fake_bt, monkeypatch
) -> None:
    """The snapshot and settings succeed; only the schedule read errors."""
    from custom_components.dimplex_ble import link as link_module
    from custom_components.dimplex_ble.protocol import SCHEDULE

    real = link_module._read_optional

    async def read(client, address):
        if address == SCHEDULE:
            raise BleakError("schedule read errored")
        return await real(client, address)

    monkeypatch.setattr(link_module, "_read_optional", read)
    with_schedules(fake_bt.add())

    coordinator = await setup(hass, make_entry(hass))

    assert coordinator.last_update_success  # the poll still succeeded
    assert coordinator.schedules == {}  # nothing decoded, so nothing stored
    assert coordinator._schedules_due  # retried next poll
    assert coordinator.link.connected  # the held link was not dropped
