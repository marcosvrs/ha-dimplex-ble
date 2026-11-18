from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bleak.exc import BleakError

from custom_components.dimplex_ble.client import (
    READ_SNAPSHOT_FIELDS,
    DimplexBluetoothClient,
    DimplexCharacteristicError,
    DimplexConnectionError,
)
from custom_components.dimplex_ble.protocol import (
    MAX_GATT_VALUE_BYTES,
    DimplexProtocolError,
    ProtectedCharacteristicError,
    characteristic_uuid,
    gatt_uuid_candidates,
    service_uuid,
)


def make_client(*, props: tuple[str, ...] = ("read", "write")) -> tuple[MagicMock, MagicMock]:
    char = SimpleNamespace(uuid=characteristic_uuid(0x1234), properties=list(props))
    service = MagicMock()
    service.get_characteristic.side_effect = lambda uuid: char if uuid == char.uuid else None
    services = MagicMock()
    services.get_service.side_effect = lambda uuid: service if uuid == service_uuid(0) else None
    client = MagicMock()
    client.services = services
    client.disconnect = AsyncMock()
    client.read_gatt_char = AsyncMock(return_value=b"\x07")
    client.write_gatt_char = AsyncMock()
    client.pair = AsyncMock(return_value=None)
    return client, char


@pytest.fixture
def hass() -> MagicMock:
    return MagicMock()


@pytest.fixture
def ble_client(hass: MagicMock) -> DimplexBluetoothClient:
    return DimplexBluetoothClient(hass, "heater-address", "proxy-source")



def test_client_retains_home_assistant_and_proxy_route(hass: MagicMock) -> None:
    client = DimplexBluetoothClient(hass, "heater-address", "proxy-source")

    assert client.hass is hass
    assert client.address == "heater-address"
    assert client.proxy_source == "proxy-source"

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


async def establish(monkeypatch: pytest.MonkeyPatch, hass: MagicMock, client: MagicMock) -> None:
    from custom_components.dimplex_ble import client as module

    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: object())
    mock_scanner(monkeypatch, hass)
    monkeypatch.setattr(module, "establish_connection", AsyncMock(return_value=client))


async def test_connected_uses_bond_owner_and_disconnects(ble_client: DimplexBluetoothClient, hass: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
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
    }
    client.disconnect.assert_awaited_once_with()

@pytest.mark.parametrize("pair", [False, True])
async def test_connected_adapts_pair_aware_proxy_backend(
    ble_client: DimplexBluetoothClient,
    monkeypatch: pytest.MonkeyPatch,
    pair: bool,
) -> None:
    from custom_components.dimplex_ble import client as module

    device = object()
    connect_calls: list[tuple[bool, float, bool]] = []

    async def connect_backend(
        *, pair: bool, dangerous_use_bleak_cache: bool, **kwargs: float
    ) -> None:
        connect_calls.append(
            (pair, kwargs["timeout"], dangerous_use_bleak_cache)
        )

    backend_client = MagicMock()
    backend_client.connect = connect_backend
    backend_client.disconnect = AsyncMock()
    backend_client.services = object()
    backend_factory = MagicMock(return_value=backend_client)
    scanner = SimpleNamespace(
        name="Test proxy",
        connector=SimpleNamespace(client=backend_factory),
    )
    monkeypatch.setattr(module, "ble_device_from_source", lambda *_: device)
    monkeypatch.setattr(
        module.bluetooth, "async_scanner_by_source", lambda *_: scanner
    )
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
    monkeypatch.setattr(
        module.bluetooth, "async_scanner_by_source", lambda *_: None
    )
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



@pytest.mark.parametrize("cause", [BleakError("failed"), OSError("failed"), TimeoutError("failed")])
async def test_connected_translates_operation_failures_and_disconnects(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch, cause: Exception) -> None:
    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)
    with pytest.raises(DimplexConnectionError, match="BLE operation failed"):
        async with ble_client._connected():
            raise cause
    client.disconnect.assert_awaited_once_with()


async def test_connected_suppresses_disconnect_ble_errors(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = make_client()
    client.disconnect.side_effect = BleakError("disconnect")
    await establish(monkeypatch, ble_client.hass, client)
    async with ble_client._connected():
        pass
    client.disconnect.assert_awaited_once_with()


async def test_pair_requests_security_during_connection_setup(
    ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from custom_components.dimplex_ble import client as module

    client, _ = make_client()
    await establish(monkeypatch, ble_client.hass, client)

    await ble_client.async_pair()

    module.establish_connection.assert_awaited_once()
    assert module.establish_connection.await_args.kwargs["pair"] is True
    client.pair.assert_not_awaited()
    client.disconnect.assert_awaited_once_with()


def test_find_characteristic_handles_missing_service_and_characteristic(ble_client: DimplexBluetoothClient) -> None:
    client = MagicMock()
    client.services.get_service.return_value = None
    assert ble_client._find_characteristic(client, (0, 0x1234)) is None
    service = MagicMock()
    service.get_characteristic.return_value = None
    client.services.get_service.side_effect = (
        lambda uuid: service if uuid == service_uuid(0) else None
    )
    assert ble_client._find_characteristic(client, (0, 0x1234)) is None
    service.get_characteristic.assert_called_once_with(characteristic_uuid(0x1234))


def test_find_characteristic_supports_gdhv_uuid_base(
    ble_client: DimplexBluetoothClient,
) -> None:
    vendor_service_uuid, vendor_characteristic_uuid = gatt_uuid_candidates(
        0, 0x1234
    )[1]
    characteristic = SimpleNamespace(
        uuid=vendor_characteristic_uuid, properties=["read"]
    )
    service = MagicMock()
    service.get_characteristic.side_effect = (
        lambda candidate: characteristic
        if candidate == vendor_characteristic_uuid
        else None
    )
    client = MagicMock()
    client.services.get_service.side_effect = (
        lambda candidate: service if candidate == vendor_service_uuid else None
    )

    assert ble_client._find_characteristic(client, (0, 0x1234)) is characteristic


async def test_optional_read_omits_absent_or_nonreadable_characteristic(ble_client: DimplexBluetoothClient) -> None:
    client = MagicMock()
    client.read_gatt_char = AsyncMock()
    ble_client._find_characteristic = MagicMock(return_value=None)
    assert await ble_client._async_read_optional(client, (0, 1)) is None
    characteristic = SimpleNamespace(properties=["write"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    assert await ble_client._async_read_optional(client, (0, 1)) is None
    client.read_gatt_char.assert_not_awaited()


async def test_optional_read_converts_result_to_bytes(ble_client: DimplexBluetoothClient) -> None:
    client = MagicMock()
    client.read_gatt_char = AsyncMock(return_value=bytearray((1, 2)))
    characteristic = SimpleNamespace(properties=["read"])
    ble_client._find_characteristic = MagicMock(return_value=characteristic)
    assert await ble_client._async_read_optional(client, (0, 1)) == b"\x01\x02"


async def test_snapshot_reads_all_fields_and_selects_room_temperature(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client, char = make_client(props=("read", "write"))
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = char
    client.read_gatt_char = AsyncMock(side_effect=[b"QRAD", b"\x14\x00", b"\x05\x00\x1e\x00", b"\x00", b"\x02", b"\x00", b"\x01\x15\x05"])
    await establish(monkeypatch, ble_client.hass, client)
    snapshot = await ble_client.async_read_snapshot()
    assert [call.args[0] for call in client.read_gatt_char.await_args_list[:len(READ_SNAPSHOT_FIELDS)]] == [char] * len(READ_SNAPSHOT_FIELDS)
    client.write_gatt_char.assert_awaited_once_with(char, bytes((1, 0, 0)), response=True)
    assert snapshot.model == "QRAD"
    assert snapshot.current_temperature_c == 21.5


async def test_snapshot_skips_room_selector_without_write_capability(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client, char = make_client(props=("read",))
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = char
    await establish(monkeypatch, ble_client.hass, client)
    await ble_client.async_read_snapshot()
    client.write_gatt_char.assert_not_awaited()


async def test_snapshot_room_selector_uses_write_without_response(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client, char = make_client(props=("read", "write-without-response"))
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = char
    await establish(monkeypatch, ble_client.hass, client)
    await ble_client.async_read_snapshot()
    client.write_gatt_char.assert_awaited_once_with(char, bytes((1, 0, 0)), response=False)


async def test_service_metadata_never_reads_values(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    char = SimpleNamespace(uuid="char-uuid", properties=("read", "write"))
    service = SimpleNamespace(uuid="service-uuid", characteristics=[char])
    client, _ = make_client()
    client.services = [service]
    await establish(monkeypatch, ble_client.hass, client)
    assert await ble_client.async_get_gatt_services() == [{"uuid": "service-uuid", "characteristics": [{"uuid": "char-uuid", "properties": ["read", "write"]}]}]
    client.read_gatt_char.assert_not_awaited()


async def test_raw_read_protects_before_connect_and_checks_characteristic(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ProtectedCharacteristicError) as err:
        await ble_client.async_read_raw(0, 0x1012)
    assert str(err.value) == "This characteristic is blocked for reads"
    client, _ = make_client()
    client.services.get_service.side_effect = None
    client.services.get_service.return_value.get_characteristic.side_effect = None
    client.services.get_service.return_value.get_characteristic.return_value = None
    await establish(monkeypatch, ble_client.hass, client)
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


async def test_raw_write_validates_payload_and_capabilities(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
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


async def test_raw_write_supports_response_write_and_readback(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
    client, char = make_client(props=("write", "read"))
    await establish(monkeypatch, ble_client.hass, client)
    with patch("custom_components.dimplex_ble.client.asyncio.sleep", new_callable=AsyncMock) as sleep:
        assert await ble_client.async_write_raw(0, 0x1234, b"x") == b"\x07"
    client.write_gatt_char.assert_awaited_once_with(char, b"x", response=True)
    client.read_gatt_char.assert_awaited_once_with(char)
    sleep.assert_awaited_once_with(1)
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

    with patch("custom_components.dimplex_ble.client.asyncio.sleep", new_callable=AsyncMock):
        assert await ble_client.async_write_raw(0, 0x1234, payload) == b"\x07"

    client.write_gatt_char.assert_awaited_once_with(char, payload, response=True)
async def test_raw_write_translates_bleak_write_errors(ble_client: DimplexBluetoothClient, monkeypatch: pytest.MonkeyPatch) -> None:
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
