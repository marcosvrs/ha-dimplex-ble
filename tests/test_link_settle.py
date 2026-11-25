"""The heater needs a quiet time after a link ends before a new one."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from conftest import ADDRESS, PIN, PROXY

from custom_components.dimplex_ble import link as link_module
from custom_components.dimplex_ble.link import HeaterLink


class Clock:
    """Fake monotonic clock whose sleep advances time instead of waiting."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(link_module, "HEATER_SETTLE_S", 10.0)
    monkeypatch.setattr(link_module, "time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr(link_module, "_sleep", fake.sleep)
    return fake


def make_link(hass) -> HeaterLink:
    return HeaterLink(hass, ADDRESS.lower(), PROXY, PIN)


async def test_reconnect_within_settle_waits_the_remainder(
    hass, fake_bt, clock, caplog
) -> None:
    caplog.set_level(logging.DEBUG, logger="custom_components.dimplex_ble")
    fake_bt.add()
    link = make_link(hass)
    await link.async_snapshot()  # first link: no history, no wait
    assert clock.sleeps == []

    clock.now += 2.5
    await link.async_snapshot()

    assert clock.sleeps == [pytest.approx(7.5)]
    assert any(
        m.startswith("SETTLE ") and ADDRESS in m and "waiting 7.5s" in m
        for m in caplog.messages
    )


async def test_no_wait_after_the_settle_time(hass, fake_bt, clock) -> None:
    fake_bt.add()
    link = make_link(hass)
    await link.async_snapshot()
    clock.now += 10.0

    await link.async_snapshot()

    assert clock.sleeps == []


async def test_heater_initiated_link_down_counts(hass, fake_bt, clock) -> None:
    proxy = fake_bt.add()
    link = HeaterLink(hass, ADDRESS.lower(), PROXY, PIN)
    await link.async_snapshot()
    proxy.clients[-1].is_connected = False
    proxy.clients[-1].disconnected_callback()
    clock.now += 4.0

    await link.async_snapshot()

    assert clock.sleeps == [pytest.approx(6.0)]


async def test_sleep_seam_really_sleeps(monkeypatch) -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(link_module.asyncio, "sleep", fake_sleep)
    await link_module._sleep(3.0)
    assert slept == [3.0]


async def test_settle_is_shared_across_instances(hass, fake_bt, clock) -> None:
    fake_bt.add()
    runtime = HeaterLink(hass, ADDRESS.lower(), PROXY, PIN)
    await runtime.async_snapshot()
    await runtime.async_close()
    clock.now += 1.0

    flow = HeaterLink(hass, ADDRESS.lower(), PROXY, None)
    await flow.async_pair(None)

    assert clock.sleeps == [pytest.approx(9.0)]


async def test_cancel_during_settle_arms_and_connects_nothing(
    hass, fake_bt, monkeypatch, clock
) -> None:
    proxy = fake_bt.add()
    link = make_link(hass)
    await link.async_snapshot()
    before = len(proxy.events)
    gate = asyncio.Event()

    async def blocked(seconds: float) -> None:
        await gate.wait()

    monkeypatch.setattr(link_module, "_sleep", blocked)
    task = hass.async_create_task(link.async_snapshot())
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(proxy.events) == before
    assert not link.connected
