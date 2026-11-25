"""Heater clock sync (0/0x0006 SetRtc), as ConfigR does once per connection."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, tzinfo
from types import SimpleNamespace

import pytest
from bleak.exc import BleakError
from conftest import FakeProxy
from test_connection_mode import make_entry, setup

from custom_components.dimplex_ble import link as link_module
from custom_components.dimplex_ble.const import (
    CONF_SYNC_CLOCK,
)
from custom_components.dimplex_ble.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.dimplex_ble.protocol import (
    CLOCK,
    DimplexProtocolError,
    encode_clock,
    parse_clock,
)

pytestmark = pytest.mark.usefixtures("skip_dependencies")

CLOCK_UUID_SUFFIX = "0006"


class Zone(tzinfo):
    """A fixed zone with an explicit DST, independent of the host's tzdata."""

    def __init__(self, dst: timedelta) -> None:
        self._dst = dst

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def dst(self, dt: datetime | None) -> timedelta:
        return self._dst

    def tzname(self, dt: datetime | None) -> str:
        return "Z"


# Irish winter time in the tz database: UTC+0 with a negative DST.
IRISH_WINTER = Zone(timedelta(hours=-1))
NOW = datetime(2026, 11, 25, 3, 31, 40, tzinfo=IRISH_WINTER)
NOW_BYTES = bytes((20, 26, 11, 25, 3, 31, 1))
SLOW_HEATER = bytes((20, 26, 11, 25, 1, 14, 0))  # AM2086 on 2026-11-25


def test_encode_matches_configr_and_the_heater_it_set() -> None:
    assert encode_clock(NOW) == NOW_BYTES  # AM6532 read 14 1a 0b 19 03 1d 01
    assert encode_clock(NOW.replace(tzinfo=Zone(timedelta(0))))[6] == 0
    with pytest.raises(DimplexProtocolError):
        encode_clock(NOW.replace(tzinfo=None))
    with pytest.raises(DimplexProtocolError):
        encode_clock(NOW.replace(year=2100))


def test_parse_reads_local_minutes() -> None:
    assert parse_clock(SLOW_HEATER) == datetime(2026, 11, 25, 1, 14)


@pytest.mark.parametrize(
    "value",
    [
        None,
        SLOW_HEATER[:6],
        bytes((19,)) + SLOW_HEATER[1:],
        bytes((20, 26, 13, 25, 1, 14, 0)),
    ],
)
def test_parse_rejects_invalid_clocks(value) -> None:
    assert parse_clock(value) is None


@pytest.fixture
def fixed_now(monkeypatch):
    """Freeze only the link's clock (freezing HA's breaks cached properties)."""
    monkeypatch.setattr(link_module, "dt_util", SimpleNamespace(now=lambda: NOW))


def with_clock(proxy: FakeProxy, value: bytes = SLOW_HEATER) -> FakeProxy:
    proxy.services.add(CLOCK)
    proxy.values[CLOCK] = value
    return proxy


def clock_events(proxy: FakeProxy) -> list[tuple]:
    return [
        e
        for e in proxy.events
        if isinstance(e, tuple)
        and e[0] in ("read", "write")
        and e[1][4:8] == CLOCK_UUID_SUFFIX
    ]


@pytest.mark.usefixtures("fixed_now")
async def test_a_slow_clock_is_set_once_per_connection(hass, fake_bt, caplog) -> None:
    proxy = with_clock(fake_bt.add())
    entry = make_entry(hass)
    caplog.set_level(logging.INFO, "custom_components.dimplex_ble.link")

    coordinator = await setup(hass, entry)

    read, write = clock_events(proxy)
    assert read[0] == "read"
    assert write[0] == "write" and write[2] == NOW_BYTES
    assert "it was -137 min off" in caplog.text
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["link"]["clock_offset_s"] == 0
    assert diagnostics["link"]["sync_clock"] is True

    # The next update is a new connection: checked again, now in sync.
    proxy.values[CLOCK] = NOW_BYTES
    proxy.events.clear()
    await coordinator.async_refresh()
    assert [e[0] for e in clock_events(proxy)] == ["read"]


@pytest.mark.usefixtures("fixed_now")
async def test_a_clock_within_a_minute_is_left_alone(hass, fake_bt) -> None:
    proxy = with_clock(fake_bt.add(), bytes((20, 26, 11, 25, 3, 30, 1)))
    coordinator = await setup(hass, make_entry(hass))

    assert [e[0] for e in clock_events(proxy)] == ["read"]
    assert coordinator.link.clock_offset_s == -60


@pytest.mark.usefixtures("fixed_now")
async def test_sync_off_only_measures(hass, fake_bt) -> None:
    proxy = with_clock(fake_bt.add())
    coordinator = await setup(hass, make_entry(hass, **{CONF_SYNC_CLOCK: False}))

    assert [e[0] for e in clock_events(proxy)] == ["read"]
    assert coordinator.link.clock_offset_s == -137 * 60


@pytest.mark.usefixtures("fixed_now")
async def test_an_unreadable_clock_is_skipped(hass, fake_bt) -> None:
    proxy = with_clock(fake_bt.add(), b"\x00")
    coordinator = await setup(hass, make_entry(hass))

    assert {e[0] for e in clock_events(proxy)} == {"read"}  # never written
    assert coordinator.link.clock_offset_s is None
    assert coordinator.last_update_success

    # Not checked yet, so the next operation tries again (not in an hour).
    proxy.values[CLOCK] = SLOW_HEATER
    proxy.events.clear()
    await coordinator.async_refresh()
    assert [e[0] for e in clock_events(proxy)] == ["read", "write"]
    assert coordinator.link.clock_offset_s == 0


@pytest.mark.usefixtures("fixed_now")
async def test_a_clock_failure_never_fails_the_poll(hass, fake_bt, monkeypatch) -> None:
    with_clock(fake_bt.add())
    read = link_module._read_optional

    async def failing_clock(client, address):
        if address == CLOCK:
            raise BleakError("clock read failed")
        return await read(client, address)

    monkeypatch.setattr(link_module, "_read_optional", failing_clock)
    coordinator = await setup(hass, make_entry(hass))

    assert coordinator.last_update_success
    assert coordinator.link.clock_offset_s is None

    monkeypatch.setattr(link_module, "_read_optional", read)
    await coordinator.async_refresh()  # retried on the next operation
    assert coordinator.link.clock_offset_s == 0
