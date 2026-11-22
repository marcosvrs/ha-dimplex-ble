from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dimplex_ble import commissioning
from custom_components.dimplex_ble.esphome_proxy import DimplexPairingError


@pytest.fixture
def pairing_actions(monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncMock, AsyncMock]:
    arm = AsyncMock()
    cancel = AsyncMock()
    monkeypatch.setattr(commissioning, "async_arm_pairing", arm)
    monkeypatch.setattr(commissioning, "async_cancel_pairing", cancel)
    return arm, cancel


def make_hass() -> MagicMock:
    hass = MagicMock()
    hass.data = {}
    hass.async_create_task = lambda coroutine: asyncio.create_task(coroutine)
    return hass


async def test_session_exposes_one_task_and_waits_for_completion(
    pairing_actions,
) -> None:
    hass = make_hass()
    pair = AsyncMock()
    session = commissioning.DimplexPairingSession(hass, "proxy", "AA:BB", pair)

    assert session.task is None
    with pytest.raises(DimplexPairingError) as error:
        await session.async_wait()
    assert str(error.value) == "The pairing attempt has not been started"

    task = await session.async_start()
    assert session.task is task
    assert await session.async_start() is task
    await session.async_wait()
    session.async_cancel()
    assert pair.await_count == 1
    pairing_actions[0].assert_awaited_once_with(hass, "proxy", "AA:BB")
    pairing_actions[1].assert_awaited_once_with(hass, "proxy")


async def test_session_cancellation_runs_relay_cleanup(pairing_actions) -> None:
    hass = make_hass()
    started = asyncio.Event()
    gate = asyncio.get_running_loop().create_future()

    async def pair() -> None:
        started.set()
        await gate

    session = commissioning.DimplexPairingSession(hass, "proxy", "AA:BB", pair)
    task = await session.async_start()
    await started.wait()
    session.async_cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    pairing_actions[1].assert_awaited_once_with(hass, "proxy")


def test_session_does_not_cancel_completed_task() -> None:
    session = commissioning.DimplexPairingSession(
        make_hass(), "proxy", "AA:BB", AsyncMock()
    )
    task = MagicMock()
    task.done.return_value = True
    session._task = task

    session.async_cancel()

    task.cancel.assert_not_called()


async def test_start_pairing_function_uses_managed_session(pairing_actions) -> None:
    hass = make_hass()
    task = await commissioning.async_start_pairing(hass, "proxy", "AA:BB", AsyncMock())

    await task
    pairing_actions[0].assert_awaited_once_with(hass, "proxy", "AA:BB")
    pairing_actions[1].assert_awaited_once_with(hass, "proxy")


async def test_pair_with_passkey_sends_pin_before_pair(monkeypatch) -> None:
    hass = make_hass()
    send_passkey = AsyncMock()
    pair = AsyncMock()
    monkeypatch.setattr(commissioning.esphome_proxy, "async_send_passkey", send_passkey)

    async def start_pairing(_hass, _source, _address, pair_callback, *, pairing_timeout):
        assert pairing_timeout == 30.0
        await pair_callback()
        return asyncio.create_task(asyncio.sleep(0))

    monkeypatch.setattr(commissioning, "async_start_pairing", start_pairing)

    await commissioning.async_pair_with_passkey(
        hass,
        "proxy",
        "AA:BB",
        "123456",
        pair,
        repair_timeout=30.0,
    )

    send_passkey.assert_awaited_once_with(hass, "proxy", "AA:BB", "123456")
    pair.assert_awaited_once_with()
