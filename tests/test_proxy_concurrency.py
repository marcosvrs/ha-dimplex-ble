from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from custom_components.dimplex_ble import client as client_module
from custom_components.dimplex_ble.client import DimplexBluetoothClient


async def test_multiple_heaters_share_proxy_connection_slots(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Queue the fourth heater while all default ESPHome slots are occupied."""
    scanner = SimpleNamespace(
        name="Test proxy",
        connector=SimpleNamespace(client=object()),
    )
    monkeypatch.setattr(bluetooth, "async_scanner_by_source", lambda *_args: scanner)
    monkeypatch.setattr(
        client_module, "ble_device_from_source", lambda *_args: object()
    )

    clients = [
        DimplexBluetoothClient(hass, f"heater-{index}", "proxy-source")
        for index in range(4)
    ]
    three_connected = asyncio.Event()
    release = asyncio.Event()
    active = 0
    peak_active = 0
    connection_count = 0

    async def connect(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal active, peak_active, connection_count
        active += 1
        connection_count += 1
        peak_active = max(peak_active, active)
        if connection_count == 3:
            three_connected.set()

        backend = MagicMock()

        async def disconnect() -> None:
            nonlocal active
            active -= 1

        backend.disconnect = AsyncMock(side_effect=disconnect)
        return backend

    monkeypatch.setattr(client_module, "establish_connection", connect)

    async def hold_connection(client: DimplexBluetoothClient) -> None:
        async with client._connected():
            await release.wait()
        await client.async_shutdown()

    tasks = [asyncio.create_task(hold_connection(client)) for client in clients]
    await asyncio.wait_for(three_connected.wait(), timeout=1)
    await asyncio.sleep(0)
    assert connection_count == 3

    release.set()
    await asyncio.gather(*tasks)

    assert peak_active == 3
    assert active == 0
    assert connection_count == 4
    assert len(hass.data[client_module.DOMAIN]["proxy_connection_semaphores"]) == 1
