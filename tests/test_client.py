from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, call, patch

import pytest
from bleak.exc import BleakError

from custom_components.dimplex_ble.client import (
    READ_SNAPSHOT_FIELDS,
    ROOM_TEMPERATURE,
    UI_VERSION,
    DimplexAuthenticationError,
    DimplexBluetoothClient,
    DimplexCharacteristicError,
    DimplexConnectionError,
    _proxy_connection_semaphore,
)
from custom_components.dimplex_ble.pairing import DEBUG_LOG_PREFIX
from custom_components.dimplex_ble.protocol import (
    MAX_GATT_VALUE_BYTES,
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    DimplexProtocolError,
    ProtectedCharacteristicError,
    characteristic_uuid,
    gatt_uuid_candidates,
    service_uuid,
)


def make_client(
    *, props: tuple[str, ...] = ("read", "write")
) -> tuple[MagicMock, MagicMock]:
    char = SimpleNamespace(uuid=characteristic_uuid(0x1234), properties=list(props))
    service = MagicMock()
    service.get_characteristic.side_effect = lambda uuid: (
        char if uuid == char.uuid else None
    )
    services = MagicMock()
    services.get_service.side_effect = lambda uuid: (
        service if uuid == service_uuid(0) else None
    )
    client = MagicMock()
    client.services = services
    client.disconnect = AsyncMock()
    client.unpair = AsyncMock()
    client.read_gatt_char = AsyncMock(return_value=b"\x07")
    client.write_gatt_char = AsyncMock()
    client.pair = AsyncMock(return_value=None)
    return client, char


@pytest.fixture
def hass() -> MagicMock:
    mock_hass = MagicMock()
    mock_hass.data = {"custom_components": object()}
    return mock_hass


@pytest.fixture
def ble_client(hass: MagicMock) -> DimplexBluetoothClient:
    return DimplexBluetoothClient(hass, "heater-address", "proxy-source")


def test_client_initializes_connection_semaphore_as_none(hass: MagicMock) -> None:
    client = DimplexBluetoothClient(hass, "heater-address", "proxy-source")

    assert client._connection_semaphore is None


def test_client_retains_home_assistant_and_proxy_route(hass: MagicMock) -> None:
    client = DimplexBluetoothClient(hass, "heater-address", "proxy-source")

    assert client.hass is hass
    assert client.address == "heater-address"
    assert client.proxy_source == "proxy-source"


async def test_pair_aware_client_forwards_explicit_pair_request() -> None:
    from custom_components.dimplex_ble import client as module

    backend = MagicMock()
    backend.pair = AsyncMock()
    wrapper = module._PairAwareBleakClient(backend, pair=True)

    await wrapper.pair()

    backend.pair.assert_awaited_once_with()


def test_proxy_connection_semaphore_reuses_casefolded_source(hass: MagicMock) -> None:
    first = _proxy_connection_semaphore(hass, "Proxy")
    assert _proxy_connection_semaphore(hass, "proxy") is first
    assert _proxy_connection_semaphore(hass, "other") is not first


def mock_scanner(monkeypatch: pytest.MonkeyPatch, hass: MagicMock) -> object:
    from homeassistant.components import bluetooth

    route_client = object()
    scanner = SimpleNamespace(
        name="Test proxy",
        connector=SimpleNamespace(client=route_client),
    )
    monkeypatch.setattr(
        bluetooth,
        "async_scanner_by_source",
        lambda actual_hass, source: scanner,
    )
    return route_client


async def establish(
    monkeypatch: pytest.MonkeyPatch, hass: MagicMock, client: MagicMock
) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    mock_scanner(monkeypatch, hass)
    monkeypatch.setattr(module, "establish_connection", AsyncMock(return_value=client))


async def test_connected_uses_bond_owner_and_disconnects(
    ble_client: DimplexBluetoothClient, hass: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    device = object()
    route_client = mock_scanner(monkeypatch, hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda h, a, s: device)
    client, _ = make_client()
    connect = AsyncMock(return_value=client)
    monkeypatch.setattr(module, "establish_connection", connect)
    async with ble_client._connected() as connected:
        assert connected is client
    client_factory = connect.call_args.args[0]
    assert client_factory.func is module._create_pair_aware_client
    assert client_factory.args == (route_client,)
    assert connect.call_args.args[1] is device
    assert connect.call_args.kwargs == {
        "max_attempts": module.CONNECT_ATTEMPTS,
        "timeout": module.CONNECT_TIMEOUT,
        "pair": False,
        "use_services_cache": True,
    }
    await ble_client.async_shutdown()
    client.disconnect.assert_awaited_once_with()


async def test_connected_reuses_connection_until_shutdown(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.dimplex_ble import client as module

    connected_client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, connected_client)

    async with ble_client._connected():
        pass
    async with ble_client._connected():
        pass

    module.establish_connection.assert_awaited_once()
    connected_client.disconnect.assert_not_awaited()

    await ble_client.async_shutdown()

    connected_client.disconnect.assert_awaited_once_with()


async def test_connected_discards_a_stale_connection(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    stale, _ = make_client()
    stale.is_connected = False
    ble_client._connection = stale
    ble_client._connection_semaphore = asyncio.Semaphore(0)
    replacement, _ = make_client()
    await establish(monkeypatch, ble_client.hass, replacement)

    async with ble_client._connected() as connected:
        assert connected is replacement

    stale.disconnect.assert_awaited_once_with()
    assert module.establish_connection.call_args.kwargs["use_services_cache"] is True
    await ble_client.async_shutdown()


async def test_connected_disconnects_when_cancelled(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)

    with pytest.raises(asyncio.CancelledError):
        async with ble_client._connected():
            raise asyncio.CancelledError

    client.disconnect.assert_awaited_once_with()


async def test_connect_route_releases_slot_when_cancelled(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling a connection attempt must not leak an ESPHome GATT slot."""
    from custom_components.dimplex_ble import client as module

    started = asyncio.Event()
    blocked = asyncio.Event()

    async def connect(*_args: object, **_kwargs: object) -> object:
        started.set()
        await blocked.wait()
        return object()

    scanner = SimpleNamespace(
        connector=SimpleNamespace(client=object()),
        name="Test proxy",
    )
    monkeypatch.setattr(module, "establish_connection", connect)
    task = asyncio.create_task(
        ble_client._async_connect_route("proxy-source", object(), scanner, pair=False)
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    semaphore = module._proxy_connection_semaphore(ble_client.hass, "proxy-source")
    acquired = []
    for _ in range(module._PROXY_CONNECTION_LIMIT):
        await asyncio.wait_for(semaphore.acquire(), timeout=0.1)
        acquired.append(None)
    for _ in acquired:
        semaphore.release()


async def test_connect_routes_serialize_establishment_per_proxy(
    hass: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Concurrent heaters must not start ESPHome BLE connects together."""
    from custom_components.dimplex_ble import client as module

    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def connect(*_args: object, **_kwargs: object) -> MagicMock:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.set()
        await release.wait()
        active -= 1
        client, _ = make_client()
        return client

    scanner = SimpleNamespace(
        connector=SimpleNamespace(client=object()),
        name="Test proxy",
    )
    monkeypatch.setattr(module, "establish_connection", connect)
    first_client = DimplexBluetoothClient(hass, "first", "proxy-source")
    second_client = DimplexBluetoothClient(hass, "second", "proxy-source")
    first_task = asyncio.create_task(
        first_client._async_connect_route("proxy-source", object(), scanner, pair=False)
    )
    await started.wait()
    second_task = asyncio.create_task(
        second_client._async_connect_route(
            "proxy-source", object(), scanner, pair=False
        )
    )
    await asyncio.sleep(0)
    assert max_active == 1

    release.set()
    first_result, second_result = await asyncio.gather(first_task, second_task)
    first_result[1].release()
    second_result[1].release()


async def test_shutdown_cancels_inflight_ble_operation(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entry unload must not wait for a stalled proxy operation."""
    connected_client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, connected_client)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def hold_connection() -> None:
        async with ble_client._connected():
            entered.set()
            await release.wait()

    operation = asyncio.create_task(hold_connection())
    await entered.wait()

    await ble_client.async_shutdown()

    assert operation.cancelled()
    connected_client.disconnect.assert_awaited_once_with()


async def test_connected_fails_fast_when_proxy_route_expires(
    hass: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client = DimplexBluetoothClient(hass, "heater-address", "proxy-source")
    mock_scanner(monkeypatch, hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: None)
    connect = AsyncMock()
    monkeypatch.setattr(module, "establish_connection", connect)

    with pytest.raises(DimplexConnectionError, match="configured ESPHome proxy"):
        async with client._connected(pair=False):
            pytest.fail("unreachable")

    connect.assert_not_awaited()


async def test_pairing_timeout_is_connection_failure(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(
        module,
        "establish_connection",
        AsyncMock(
            side_effect=BleakError("Timeout waiting for BluetoothDevicePairingResponse")
        ),
    )

    with pytest.raises(DimplexConnectionError, match="pairing timed out"):
        async with ble_client._connected(pair=True):
            pytest.fail("pairing should fail before yielding a client")


async def test_pairing_response_without_timeout_is_authentication_failure(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(
        module,
        "establish_connection",
        AsyncMock(side_effect=BleakError("BluetoothDevicePairingResponse required")),
    )

    with pytest.raises(DimplexAuthenticationError, match="pairing is required"):
        async with ble_client._connected(pair=True):
            pytest.fail("pairing should fail before yielding a client")


async def test_connection_error_releases_proxy_slot(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(
        module,
        "establish_connection",
        AsyncMock(side_effect=DimplexConnectionError("connection failed")),
    )

    with pytest.raises(DimplexConnectionError):
        async with ble_client._connected(pair=False):
            pytest.fail("connection should fail before yielding a client")


async def test_pairing_uses_captured_device_when_route_cache_expires(
    hass: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    captured_device = object()
    client = DimplexBluetoothClient(
        hass, "heater-address", "proxy-source", captured_device
    )
    mock_scanner(monkeypatch, hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: None)
    connected_client, _ = make_client()
    connect = AsyncMock(return_value=connected_client)
    monkeypatch.setattr(module, "establish_connection", connect)

    async with client._connected(pair=True):
        pass

    assert connect.call_args.args[1] is captured_device
    await client.async_shutdown()


@pytest.mark.parametrize("pair", [False, True])
async def test_connected_adapts_pair_aware_proxy_backend(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    pair: bool,
) -> None:
    from custom_components.dimplex_ble import client as module

    device = SimpleNamespace(address="heater-address")
    connect_calls: list[tuple[bool, float, bool]] = []

    async def connect_backend(
        *, pair: bool, dangerous_use_bleak_cache: bool, **kwargs: float
    ) -> None:
        connect_calls.append((pair, kwargs["timeout"], dangerous_use_bleak_cache))

    backend_client = MagicMock()
    backend_client.connect = connect_backend
    backend_client.pair = AsyncMock()
    backend_client.disconnect = AsyncMock()
    backend_client.services = object()
    backend_factory = MagicMock(return_value=backend_client)
    scanner = SimpleNamespace(
        name="Test proxy",
        connector=SimpleNamespace(client=backend_factory),
    )
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: device)
    monkeypatch.setattr(module.bluetooth, "async_scanner_by_source", lambda *_: scanner)
    helper_globals = module.establish_connection.__globals__
    monkeypatch.setitem(helper_globals, "IS_LINUX", False)
    monkeypatch.setitem(
        helper_globals, "_has_valid_services_in_cache", AsyncMock(return_value=False)
    )

    async with ble_client._connected(pair=pair) as connected:
        assert connected.services is backend_client.services

    backend_factory.assert_called_once_with(
        device, disconnected_callback=None, pair=pair, timeout=module.CONNECT_TIMEOUT
    )
    assert connect_calls == [(pair, helper_globals["BLEAK_TIMEOUT"], False)]
    backend_client.pair.assert_not_awaited()
    await ble_client.async_shutdown()
    backend_client.disconnect.assert_awaited_once_with()


@pytest.mark.parametrize("pair", [False, True])
async def test_connected_without_selected_proxy_device_fails(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    pair: bool,
) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: None)
    with pytest.raises(DimplexConnectionError, match="configured ESPHome proxy"):
        async with ble_client._connected(pair=pair):
            pytest.fail("unreachable")


async def test_connected_without_registered_proxy_fails(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(module.bluetooth, "async_scanner_by_source", lambda *_: None)
    with pytest.raises(DimplexConnectionError, match="proxy is unavailable"):
        async with ble_client._connected():
            pytest.fail("unreachable")


@pytest.mark.parametrize("pair", [False, True])
@pytest.mark.parametrize(
    "cause", [BleakError("failed"), OSError("failed"), TimeoutError("failed")]
)
async def test_connected_translates_connection_failures(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    cause: Exception,
    pair: bool,
) -> None:
    from custom_components.dimplex_ble import client as module

    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    connect = AsyncMock(side_effect=cause)
    monkeypatch.setattr(module, "establish_connection", connect)
    with pytest.raises(DimplexConnectionError, match="Could not connect") as err:
        async with ble_client._connected(pair=pair):
            pass
    assert err.value.__cause__ is cause


async def test_security_request_peer_disconnect_is_retryable(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    cause = BleakError("Error ESP_GATT_CONN_TERMINATE_PEER_USER while securing")
    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(module, "establish_connection", AsyncMock(side_effect=cause))

    with pytest.raises(DimplexConnectionError) as err:
        async with ble_client._connected():
            pass

    assert err.value.__cause__ is cause


@pytest.mark.parametrize(
    "cause", [BleakError("failed"), OSError("failed"), TimeoutError("failed")]
)
async def test_connected_translates_operation_failures_and_disconnects(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    cause: Exception,
) -> None:
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    with pytest.raises(DimplexConnectionError, match="BLE operation failed"):
        async with ble_client._connected():
            raise cause
    client.disconnect.assert_awaited_once_with()


async def test_connected_translates_missing_authentication(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)

    with pytest.raises(DimplexAuthenticationError, match="authentication"):
        async with ble_client._connected():
            raise BleakError("Bluetooth GATT Error: Insufficient authentication")

    assert ble_client._force_service_refresh is True
    client.disconnect.assert_awaited_once_with()


async def test_connected_translates_missing_encryption(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)

    with pytest.raises(DimplexAuthenticationError, match="authentication"):
        async with ble_client._connected():
            raise BleakError("Bluetooth GATT Error: Insufficient encryption")

    assert ble_client._force_service_refresh is True
    client.disconnect.assert_awaited_once_with()


async def test_connected_translates_pairing_failure_to_authentication(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)

    with pytest.raises(DimplexAuthenticationError, match="authentication"):
        async with ble_client._connected(pair=True):
            raise BleakError("Pairing failed due to error: 99")

    client.disconnect.assert_awaited_once_with()


async def test_connected_reconnects_after_operation_failure(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    first, _ = make_client()
    second, _ = make_client()
    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(module, "establish_connection", connect)

    with pytest.raises(DimplexConnectionError, match="BLE operation failed"):
        async with ble_client._connected():
            raise BleakError("connection dropped")

    assert ble_client._force_service_refresh is True
    async with ble_client._connected() as connected:
        assert connected is second

    assert connect.await_count == 2
    first.disconnect.assert_awaited_once_with()
    await ble_client.async_shutdown()
    second.disconnect.assert_awaited_once_with()


async def test_snapshot_uses_secure_bond_connection(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    ble_client._async_read_snapshot = AsyncMock(return_value=SimpleNamespace())

    assert isinstance(await ble_client.async_read_snapshot(), SimpleNamespace)
    assert module.establish_connection.await_args.kwargs["pair"] is False

    client.disconnect.assert_not_awaited()


async def test_retry_authenticated_connection_restarts_and_reads(
    ble_client: DimplexBluetoothClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="custom_components.dimplex_ble.client")
    ble_client.async_shutdown = AsyncMock()
    ble_client.async_read_snapshot = AsyncMock(return_value="snapshot")

    assert await ble_client.async_retry_authenticated_connection() == "snapshot"
    ble_client.async_shutdown.assert_awaited_once_with()
    ble_client.async_read_snapshot.assert_awaited_once_with()
    assert caplog.messages == [
        f"{DEBUG_LOG_PREFIX} Retrying the existing authenticated connection for heater-address"
    ]


async def test_connected_suppresses_disconnect_ble_errors(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, _ = make_client()
    client.disconnect.side_effect = BleakError("disconnect")
    await establish(monkeypatch, ble_client.hass, client)
    async with ble_client._connected():
        pass
    with caplog.at_level("DEBUG", logger="custom_components.dimplex_ble.client"):
        await ble_client.async_shutdown()
    client.disconnect.assert_awaited_once_with()
    assert caplog.records[-1].message == "BLE disconnect failed"
    assert caplog.records[-1].exc_info is not None
    assert caplog.records[-1].exc_info[0] is BleakError


async def test_pair_requests_security_during_connection_setup(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="custom_components.dimplex_ble.client")
    from custom_components.dimplex_ble import client as module

    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    existing, _ = make_client()
    ble_client._connection = existing
    ble_client._connection_semaphore = asyncio.Semaphore(1)
    first, _ = make_client()
    second, _ = make_client()
    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    connect = AsyncMock(side_effect=[first, second])
    monkeypatch.setattr(module, "establish_connection", connect)

    async def read_snapshot(actual_client):
        assert actual_client in (first, second)
        return SimpleNamespace(model="QRAD100E")

    ble_client._async_read_snapshot = read_snapshot

    await ble_client.async_pair()

    assert connect.await_count == 2
    assert sleep.await_count == 1
    assert sleep.await_args_list[0].args == (10.0,)
    assert connect.await_args_list[0].kwargs["pair"] is True
    assert connect.await_args_list[0].kwargs["use_services_cache"] is False
    assert connect.await_args_list[1].kwargs["pair"] is False
    assert connect.await_args_list[1].kwargs["use_services_cache"] is False
    first.disconnect.assert_awaited_once_with()
    second.disconnect.assert_awaited_once_with()
    existing.disconnect.assert_awaited_once_with()
    assert ble_client._force_service_refresh is True
    assert caplog.messages == [
        f"{DEBUG_LOG_PREFIX} Starting PIN pairing for heater-address"
    ]


async def test_pair_rejects_bond_that_cannot_be_reused(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "_PAIRING_RECONNECT_DELAY", 0)

    first, _ = make_client()
    second, _ = make_client()
    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(
        module, "establish_connection", AsyncMock(side_effect=[first, second])
    )
    ble_client._async_read_snapshot = AsyncMock(
        side_effect=[
            SimpleNamespace(model="QRAD100E"),
            BleakError("Bluetooth GATT Error: Insufficient encryption"),
        ]
    )

    with pytest.raises(DimplexAuthenticationError, match="authentication"):
        await ble_client.async_pair()

    first.disconnect.assert_awaited_once_with()
    second.disconnect.assert_awaited_once_with()


async def test_pair_rejects_missing_identity_on_initial_connection(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "_PAIRING_RECONNECT_DELAY", 0)

    client, _ = make_client()
    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(module, "establish_connection", AsyncMock(return_value=client))
    ble_client._async_read_snapshot = AsyncMock(
        return_value=SimpleNamespace(model=None)
    )

    with pytest.raises(
        DimplexConnectionError,
        match=r"\APairing did not return the panel-heater identity\Z",
    ):
        await ble_client.async_pair()

    client.disconnect.assert_awaited_once_with()


async def test_pair_rejects_unverified_identity(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "_PAIRING_RECONNECT_DELAY", 0)
    first, _ = make_client()
    second, _ = make_client()
    mock_scanner(monkeypatch, ble_client.hass)
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    monkeypatch.setattr(
        module, "establish_connection", AsyncMock(side_effect=[first, second])
    )
    ble_client._async_read_snapshot = AsyncMock(
        side_effect=[
            SimpleNamespace(model="QRAD100E"),
            SimpleNamespace(model=None),
        ]
    )

    with pytest.raises(
        DimplexConnectionError,
        match=r"\AThe paired connection did not return the panel-heater identity\Z",
    ):
        await ble_client.async_pair()

    first.disconnect.assert_awaited_once_with()
    second.disconnect.assert_awaited_once_with()


def test_find_characteristic_handles_missing_service_and_characteristic(
    ble_client: DimplexBluetoothClient,
) -> None:
    client = MagicMock()
    client.services.get_service.return_value = None
    assert ble_client._find_characteristic(client, (0, 0x1234)) is None
    service = MagicMock()
    service.get_characteristic.return_value = None
    client.services.get_service.side_effect = lambda uuid: (
        service if uuid == service_uuid(0) else None
    )
    assert ble_client._find_characteristic(client, (0, 0x1234)) is None
    service.get_characteristic.assert_called_once_with(characteristic_uuid(0x1234))


def test_find_characteristic_supports_gdhv_uuid_base(
    ble_client: DimplexBluetoothClient,
) -> None:
    vendor_service_uuid, vendor_characteristic_uuid = gatt_uuid_candidates(0, 0x1234)[1]
    characteristic = SimpleNamespace(
        uuid=vendor_characteristic_uuid, properties=["read"]
    )
    service = MagicMock()
    service.get_characteristic.side_effect = lambda candidate: (
        characteristic if candidate == vendor_characteristic_uuid else None
    )
    client = MagicMock()
    client.services.get_service.side_effect = lambda candidate: (
        service if candidate == vendor_service_uuid else None
    )

    assert ble_client._find_characteristic(client, (0, 0x1234)) is characteristic


def test_find_characteristic_falls_back_for_esphome_group_zero_uuid(
    ble_client: DimplexBluetoothClient,
) -> None:
    characteristic = SimpleNamespace(
        uuid=characteristic_uuid(0x1006), properties=["read"]
    )
    service = MagicMock()
    service.get_characteristic.side_effect = lambda candidate: (
        characteristic if candidate == characteristic.uuid else None
    )
    services = MagicMock()
    services.get_service.return_value = None
    services.__iter__.return_value = iter([service])
    client = MagicMock(services=services)

    assert ble_client._find_characteristic(client, (0, 0x1006)) is characteristic


async def test_optional_read_omits_absent_or_nonreadable_characteristic(
    ble_client: DimplexBluetoothClient,
) -> None:
    client = MagicMock()
    client.read_gatt_char = AsyncMock()
    ble_client._find_characteristic = MagicMock(return_value=None)
    assert await ble_client._async_read_optional(client, (0, 1)) is None
    characteristic = SimpleNamespace(properties=["write"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    assert await ble_client._async_read_optional(client, (0, 1)) is None
    client.read_gatt_char.assert_not_awaited()


async def test_optional_read_converts_result_to_bytes(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client = MagicMock()
    client.read_gatt_char = AsyncMock(return_value=bytearray((1, 2)))
    characteristic = SimpleNamespace(properties=["read"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    elapsed = iter((0.0, 2.0))
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: next(elapsed, 2.0)),
    )
    assert await ble_client._async_read_optional(client, (0, 1)) == b"\x01\x02"


async def test_optional_read_times_out_stalled_characteristic(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client = MagicMock()
    client.read_gatt_char = AsyncMock()
    characteristic = SimpleNamespace(properties=["read"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    monkeypatch.setattr(module, "GATT_READ_TIMEOUT", 0.001)

    async def slow_read(_characteristic):
        await asyncio.sleep(0.01)
        return b"\x01"

    client.read_gatt_char.side_effect = slow_read

    with pytest.raises(TimeoutError):
        await ble_client._async_read_optional(client, (0, 1))


async def test_snapshot_reads_all_fields_and_selects_diagnostic_values(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, char = make_client(props=("read", "write"))
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = char
    client.read_gatt_char = AsyncMock(
        side_effect=[
            b"QRAD",
            b"EP4213",
            b"\x05",
            b"\xf4\x01\x98\x08\x00\x00\x00\x00",
            b"\x15\x00",
            b"\x05\x00\x1e\x00",
            b"\x00",
            b"\x02",
            b"\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            b"\x00",
            b"\x00",
            b"\x01\x15\x05",
            b"\x02\x1d\x00",
            b"\x01\x01\x64",
            b"\x01\x01\x07",
        ]
    )
    assert OPEN_WINDOW_DETECTION in READ_SNAPSHOT_FIELDS
    assert PREEMPTIVE_HEATING in READ_SNAPSHOT_FIELDS

    await establish(monkeypatch, ble_client.hass, client)
    snapshot = await ble_client.async_read_snapshot()
    from custom_components.dimplex_ble import client as module

    assert module.establish_connection.await_args.kwargs["pair"] is False
    assert [
        call.args[0]
        for call in client.read_gatt_char.await_args_list[: len(READ_SNAPSHOT_FIELDS)]
    ] == [char] * len(READ_SNAPSHOT_FIELDS)
    assert [call.args for call in client.write_gatt_char.await_args_list] == [
        (char, bytes((1, 0, 0))),
        (char, bytes((2, 0, 0))),
        (char, bytes((1, 0, 0))),
        (char, bytes((1, 0, 0))),
    ]
    assert snapshot.product_code == "EP4213"
    assert snapshot.rf_firmware_revision == 5
    assert snapshot.ui_version == "1.7"
    assert snapshot.current_temperature_c == 21.5
    assert snapshot.lcd_temperature_c == 29.0
    assert snapshot.trac_status_hex == "010164"
    assert snapshot.open_window_detection is False
    assert snapshot.preemptive_heating is False


async def test_snapshot_skips_room_selector_without_write_capability(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, char = make_client(props=("read",))
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = char
    await establish(monkeypatch, ble_client.hass, client)
    await ble_client.async_read_snapshot()
    client.write_gatt_char.assert_not_awaited()


async def test_snapshot_room_selector_uses_write_without_response(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, char = make_client(props=("read", "write-without-response"))
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = char
    await establish(monkeypatch, ble_client.hass, client)
    await ble_client.async_read_snapshot()
    assert [call.args for call in client.write_gatt_char.await_args_list] == [
        (char, bytes((1, 0, 0))),
        (char, bytes((2, 0, 0))),
        (char, bytes((1, 0, 0))),
        (char, bytes((1, 0, 0))),
    ]
    assert [call.kwargs for call in client.write_gatt_char.await_args_list] == [
        {"response": False},
        {"response": False},
        {"response": False},
        {"response": False},
    ]


async def test_selector_read_uses_trac_address_and_write_response(
    ble_client: DimplexBluetoothClient,
) -> None:
    client = MagicMock()
    client.write_gatt_char = AsyncMock()
    characteristic = SimpleNamespace(properties=["read", "write"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    ble_client._async_read_optional = AsyncMock(return_value=b"\x01\x00\x64")

    assert await ble_client._async_read_selector(client, (1, 0x0002), 1) == (
        b"\x01\x00\x64"
    )
    ble_client._find_characteristic.assert_called_once_with(client, (1, 0x0002))
    client.write_gatt_char.assert_awaited_once_with(
        characteristic, b"\x01\x00\x00", response=True
    )


async def test_selector_write_is_time_bounded(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client = MagicMock()
    characteristic = SimpleNamespace(properties=["read", "write"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)

    async def write_slow(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(0.05)

    client.write_gatt_char = AsyncMock(side_effect=write_slow)
    monkeypatch.setattr(module, "GATT_READ_TIMEOUT", 0.01)
    elapsed = iter((0.0, 2.0))
    monkeypatch.setattr(
        module,
        "time",
        SimpleNamespace(monotonic=lambda: next(elapsed, 2.0)),
    )

    with pytest.raises(asyncio.TimeoutError):
        await ble_client._async_read_selector(client, (1, 0x0002), 1)


async def test_selector_read_returns_none_without_read_write_characteristic(
    ble_client: DimplexBluetoothClient,
) -> None:
    client = MagicMock()
    ble_client._find_characteristic = MagicMock(return_value=None)

    assert await ble_client._async_read_selector(client, (1, 0x0002), 1) is None
    client.write_gatt_char.assert_not_called()


async def test_snapshot_selects_each_selector_with_exact_protocol_address(
    ble_client: DimplexBluetoothClient,
) -> None:
    client = MagicMock()
    client.write_gatt_char = AsyncMock()
    characteristic = SimpleNamespace(properties=["read", "write"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    ble_client._async_read_optional = AsyncMock(return_value=None)

    await ble_client._async_read_snapshot(client)

    selector_calls = ble_client._find_characteristic.call_args_list[-4:]
    assert selector_calls == [
        call(client, ROOM_TEMPERATURE),
        call(client, ROOM_TEMPERATURE),
        call(client, (1, 0x0002)),
        call(client, UI_VERSION),
    ]


async def test_service_metadata_never_reads_values(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    char = SimpleNamespace(uuid="char-uuid", properties=("read", "write"))
    service = SimpleNamespace(uuid="service-uuid", characteristics=[char])
    client, _ = make_client()
    client.services = [service]
    await establish(monkeypatch, ble_client.hass, client)
    module.establish_connection.reset_mock()
    assert await ble_client.async_get_gatt_services() == [
        {
            "uuid": "service-uuid",
            "characteristics": [{"uuid": "char-uuid", "properties": ["read", "write"]}],
        }
    ]
    assert module.establish_connection.call_args.kwargs["pair"] is False
    client.read_gatt_char.assert_not_awaited()


async def test_raw_read_protects_before_connect_and_checks_characteristic(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    validate = Mock(side_effect=module.validate_raw_access)
    monkeypatch.setattr(module, "validate_raw_access", validate)

    with pytest.raises(ProtectedCharacteristicError) as err:
        await ble_client.async_read_raw(0, 0x1012)
    assert str(err.value) == "This characteristic is blocked for reads"
    client, _ = make_client()
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = None
    await establish(monkeypatch, ble_client.hass, client)
    module.establish_connection.reset_mock()
    with pytest.raises(DimplexCharacteristicError) as err:
        await ble_client.async_read_raw(0, 0x1234)
    assert str(err.value) == "Characteristic was not found"
    char = SimpleNamespace(properties=["write"])
    client.services.get_service.return_value.get_characteristic.return_value = char
    with pytest.raises(DimplexCharacteristicError) as err:
        await ble_client.async_read_raw(0, 0x1234)
    assert str(err.value) == "Characteristic is not readable"
    char.properties = ["read"]
    assert await ble_client.async_read_raw(0, 0x1234) == b"\x07"
    client.read_gatt_char.assert_awaited_once_with(char)
    assert module.establish_connection.call_args.kwargs["pair"] is False
    assert all(item.kwargs["write"] is False for item in validate.call_args_list)


async def test_raw_field_batch_reads_available_fields(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    validate = Mock(side_effect=module.validate_raw_access)
    monkeypatch.setattr(module, "validate_raw_access", validate)

    client, char = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    second_char = SimpleNamespace(properties=["read"])
    find_results = iter((char, None, second_char))

    def find_characteristic(actual_client, _address):
        assert actual_client is client
        return next(find_results)

    ble_client._find_characteristic = Mock(side_effect=find_characteristic)
    client.read_gatt_char.side_effect = [b"\x07", b"\x08"]

    fields = ((0, 0x1234), (0, 0x1235), (0, 0x1236))
    assert await ble_client.async_read_raw_fields(fields) == {
        fields[0]: b"\x07",
        fields[2]: b"\x08",
    }
    assert ble_client._find_characteristic.call_args_list == [
        call(client, fields[0]),
        call(client, fields[1]),
        call(client, fields[2]),
    ]
    assert client.read_gatt_char.await_args_list == [
        call(char),
        call(second_char),
    ]
    assert module.establish_connection.call_args.kwargs["pair"] is False
    assert all(item.kwargs["write"] is False for item in validate.call_args_list)


async def test_raw_field_batch_times_out_slow_reads(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client, char = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    monkeypatch.setattr(module, "RAW_READ_TIMEOUT", 0.001)
    ble_client._find_characteristic = Mock(return_value=char)

    async def slow_read(_characteristic):
        await asyncio.sleep(0.01)
        return b"\x07"

    client.read_gatt_char.side_effect = slow_read

    assert await ble_client.async_read_raw_fields(((0, 0x1234),)) == {}


async def test_raw_write_validates_payload_and_capabilities(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ProtectedCharacteristicError) as err:
        await ble_client.async_write_raw(3, 0x200F, b"\x01")
    assert str(err.value) == "This characteristic is blocked for writes"
    with pytest.raises(DimplexProtocolError) as err:
        await ble_client.async_write_raw(0, 0x1234, b"")
    assert str(err.value) == "Payload must not be empty"
    with pytest.raises(DimplexProtocolError) as err:
        await ble_client.async_write_raw(0, 0x1234, b"0" * (MAX_GATT_VALUE_BYTES + 1))
    assert str(err.value) == "Payload exceeds the 512-byte GATT value limit"
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = None
    with pytest.raises(DimplexCharacteristicError) as err:
        await ble_client.async_write_raw(0, 0x1234, b"x")
    assert str(err.value) == "Characteristic was not found"
    char = SimpleNamespace(properties=["read"])
    client.services.get_service.return_value.get_characteristic.return_value = char
    with pytest.raises(DimplexCharacteristicError) as err:
        await ble_client.async_write_raw(0, 0x1234, b"x")
    assert str(err.value) == "Characteristic is not writable"


async def test_raw_write_supports_response_write_and_readback(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client, char = make_client(props=("write", "read"))
    await establish(monkeypatch, ble_client.hass, client)
    module.establish_connection.reset_mock()
    with patch(
        "custom_components.dimplex_ble.client.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        assert await ble_client.async_write_raw(0, 0x1234, b"x") == b"\x07"
    client.write_gatt_char.assert_awaited_once_with(char, b"x", response=True)
    assert module.establish_connection.call_args.kwargs["pair"] is False
    client.read_gatt_char.assert_awaited_once_with(char)
    sleep.assert_awaited_once_with(1)
    await ble_client.async_shutdown()
    no_response, char = make_client(props=("write-without-response",))
    await establish(monkeypatch, ble_client.hass, no_response)
    assert await ble_client.async_write_raw(0, 0x1234, b"x") is None
    no_response.write_gatt_char.assert_awaited_once_with(char, b"x", response=False)


async def test_raw_write_accepts_maximum_gatt_value(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, char = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    payload = bytes((0xA5,)) * MAX_GATT_VALUE_BYTES

    with patch(
        "custom_components.dimplex_ble.client.asyncio.sleep", new_callable=AsyncMock
    ):
        assert await ble_client.async_write_raw(0, 0x1234, payload) == b"\x07"

    client.write_gatt_char.assert_awaited_once_with(char, payload, response=True)


async def test_raw_write_translates_bleak_write_errors(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = make_client()
    client.write_gatt_char.side_effect = BleakError("write")
    await establish(monkeypatch, ble_client.hass, client)
    with pytest.raises(DimplexConnectionError) as err:
        await ble_client.async_write_raw(0, 0x1234, b"x")
    assert str(err.value) == "GATT write failed"


@pytest.mark.parametrize("pair", [False, True])
def test_pair_aware_client_factory_preserves_pair_flag_and_drops_retry_marker(
    pair: bool,
) -> None:
    from custom_components.dimplex_ble.client import _create_pair_aware_client

    backend = MagicMock()
    factory = MagicMock(return_value=backend)

    wrapped = _create_pair_aware_client(
        factory,
        "device",
        pair=pair,
        _is_retry_client=True,
    )

    factory.assert_called_once_with("device", pair=pair)
    assert wrapped._pair is pair
    assert wrapped._client is backend


def test_pair_aware_client_factory_defaults_to_nonpairing() -> None:
    from custom_components.dimplex_ble.client import _create_pair_aware_client

    backend = MagicMock()
    factory = MagicMock(return_value=backend)

    wrapped = _create_pair_aware_client(factory, "device")

    factory.assert_called_once_with("device")
    assert wrapped._pair is False
    assert wrapped._client is backend


async def test_runtime_pair_encryption_failure_releases_route(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    cause = BleakError("Bluetooth GATT Error: Insufficient encryption")
    scanner = SimpleNamespace(
        connector=SimpleNamespace(client=object()),
        name="Test proxy",
    )
    monkeypatch.setattr(
        module,
        "establish_connection",
        AsyncMock(side_effect=cause),
    )

    with pytest.raises(DimplexAuthenticationError, match="authentication") as err:
        await ble_client._async_connect_route(
            "proxy-source", object(), scanner, pair=False
        )

    assert err.value.__cause__ is cause


def test_pair_routes_fail_when_proxy_scanner_is_unavailable(
    hass: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client = DimplexBluetoothClient(hass, "heater-address", "proxy-source", object())
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: None)
    monkeypatch.setattr(module.bluetooth, "async_scanner_by_source", lambda *_: None)

    with pytest.raises(DimplexConnectionError, match="proxy is unavailable"):
        client._connection_routes(pair=True)

    unavailable = SimpleNamespace(connector=SimpleNamespace(can_connect=lambda: False))
    monkeypatch.setattr(
        module.bluetooth, "async_scanner_by_source", lambda *_: unavailable
    )
    with pytest.raises(DimplexConnectionError, match="proxy is unavailable"):
        client._connection_routes(pair=False)


def test_runtime_routes_are_pinned_to_configured_proxy(
    hass: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    primary = SimpleNamespace(connector=SimpleNamespace(can_connect=lambda: True))
    alternate = SimpleNamespace(connector=SimpleNamespace(can_connect=lambda: True))
    scanners = {"proxy-source": primary, "alternate-proxy": alternate}
    device = object()
    calls: list[str] = []
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: device)

    def scanner_for_source(_hass: MagicMock, source: str) -> object:
        calls.append(source)
        return scanners[source]

    monkeypatch.setattr(module.bluetooth, "async_scanner_by_source", scanner_for_source)
    client = DimplexBluetoothClient(hass, "heater-address", "proxy-source")

    routes = client._connection_routes(pair=False)

    assert routes == [("proxy-source", device, primary)]
    assert calls == ["proxy-source"]


async def test_runtime_auth_failure_does_not_try_another_proxy(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    cause = BleakError("Bluetooth GATT Error: Insufficient encryption")
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    mock_scanner(monkeypatch, ble_client.hass)
    connect = AsyncMock(side_effect=cause)
    monkeypatch.setattr(module, "establish_connection", connect)

    with pytest.raises(DimplexAuthenticationError, match="authentication") as err:
        async with ble_client._connected():
            pytest.fail("unreachable")

    assert err.value.__cause__ is cause
    assert connect.await_count == 1
