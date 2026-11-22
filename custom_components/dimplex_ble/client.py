"""Async BLE access for Dimplex panel heaters through Home Assistant Bluetooth."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager, nullcontext
from functools import partial
from typing import Any, TypedDict, cast

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import DEVICE_NAME, DOMAIN
from .pairing import ble_device_from_source
from .protocol import (
    MAX_GATT_VALUE_BYTES,
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    DimplexProtocolError,
    GattAddress,
    HeaterSnapshot,
    gatt_uuid_candidates,
    parse_snapshot,
    validate_raw_access,
)

_PROXY_CONNECTION_LIMIT = 3
_PAIRING_RECONNECT_DELAY = 10.0
_PROXY_CONNECTION_SEMAPHORES = "proxy_connection_semaphores"
_PROXY_CONNECTION_LOCKS = "proxy_connection_locks"


def _proxy_connection_semaphore(hass: HomeAssistant, source: str) -> asyncio.Semaphore:
    """Share ESPHome's default three GATT slots across this integration's clients."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    semaphores: dict[str, asyncio.Semaphore] = domain_data.setdefault(
        _PROXY_CONNECTION_SEMAPHORES, {}
    )
    key = source.casefold()
    semaphore = semaphores.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_PROXY_CONNECTION_LIMIT)
        semaphores[key] = semaphore
    return semaphore


def _proxy_connection_lock(hass: HomeAssistant, source: str) -> asyncio.Lock:
    """Serialize ESPHome BLE connection establishment per proxy."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks: dict[str, asyncio.Lock] = domain_data.setdefault(_PROXY_CONNECTION_LOCKS, {})
    key = source.casefold()
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


_LOGGER = logging.getLogger(__name__)
DEBUG_LOG_PREFIX = "[DEBUG-DIMPLEX-PIN-20261117]"
CONNECT_TIMEOUT = 15.0
GATT_READ_TIMEOUT = 5.0
RAW_READ_TIMEOUT = 3.0
CONNECT_ATTEMPTS = 3
READ_SNAPSHOT_FIELDS: tuple[GattAddress, ...] = (
    (3, 0x2005),
    (3, 0x200A),
    (3, 0x2009),
    (3, 0x000A),
    (0, 0x1023),
    (0, 0x1006),
    (0, 0x1011),
    (0, 0x1001),
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    (0, 0x1005),
)
ROOM_TEMPERATURE: GattAddress = (1, 0x0008)
UI_VERSION: GattAddress = (3, 0x2001)


class _GattCharacteristicInfo(TypedDict):
    uuid: str
    properties: list[str]


class _GattServiceInfo(TypedDict):
    uuid: str
    characteristics: list[_GattCharacteristicInfo]


class _PairAwareBleakClient:
    """Adapt the ESPHome connector's connect(pair=...) API to the retry helper."""

    def __init__(self, client: Any, pair: bool) -> None:
        self._client = client
        self._pair = pair

    async def connect(self, **kwargs: Any) -> None:
        """Pass the ESPHome-only pairing flag when the retry helper connects."""
        _LOGGER.debug(
            "%s Establishing BLE link for %s (pair=%s)",
            DEBUG_LOG_PREFIX,
            self._client,
            self._pair,
        )
        await self._client.connect(pair=self._pair, **kwargs)

    async def pair(self, *args: Any, **kwargs: Any) -> None:
        """Log any explicit pairing request before forwarding it."""
        _LOGGER.debug(
            "%s Explicit BLE pair request for %s",
            DEBUG_LOG_PREFIX,
            self._client,
        )
        await self._client.pair(*args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _create_pair_aware_client(
    client_factory: Callable[..., Any], *args: Any, **kwargs: Any
) -> Any:
    """Wrap a source-specific client for bleak-retry-connector."""
    pair = cast(bool, kwargs.get("pair", False))
    kwargs.pop("_is_retry_client", None)
    return _PairAwareBleakClient(client_factory(*args, **kwargs), pair)


class DimplexError(Exception):
    """Base class for integration BLE operation failures."""


class DimplexConnectionError(DimplexError):
    """Home Assistant could not connect to the panel heater."""


class DimplexAuthenticationError(DimplexConnectionError):
    """The panel heater rejected an operation because BLE authentication is missing."""


def _is_authentication_error(error: BaseException) -> bool:
    """Return whether a BLE error explicitly reports missing link security."""
    message = str(error).casefold()
    return (
        "insufficient authentication" in message
        or "insufficient encryption" in message
        or "pairing failed due to error:" in message
    )


class DimplexCharacteristicError(DimplexError):
    """A GATT characteristic is absent or lacks the requested property."""


class DimplexBluetoothClient:
    """Maintain one encrypted BLE connection for the panel heater lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        proxy_source: str,
        device: BLEDevice | None = None,
    ) -> None:
        """Initialize a client bound to the proxy that owns this panel heater's bond."""
        self.hass = hass
        self.address = address
        self.proxy_source = proxy_source
        self._device = device
        self._force_service_refresh = False
        self._lock = asyncio.Lock()
        self._connection: BleakClient | None = None
        self._connection_semaphore: asyncio.Semaphore | None = None
        self._operation_task: asyncio.Task[Any] | None = None

    async def _disconnect_locked(self) -> None:
        """Close and release the persistent connection; caller owns the lock."""
        client = self._connection
        semaphore = self._connection_semaphore
        self._connection = None
        self._connection_semaphore = None
        try:
            if client is not None:
                try:
                    await client.disconnect()
                except (BleakError, OSError, TimeoutError):
                    _LOGGER.debug("BLE disconnect failed", exc_info=True)
        finally:
            if semaphore is not None:
                semaphore.release()

    async def async_shutdown(self) -> None:
        """Cancel an in-flight BLE operation and close its connection."""
        operation = self._operation_task
        current_task = asyncio.current_task()
        if (
            operation is not None
            and operation is not current_task
            and not operation.done()
        ):
            operation.cancel()
            try:
                await operation
            except asyncio.CancelledError:
                pass
        async with self._lock:
            await self._disconnect_locked()

    def _connection_routes(self, *, pair: bool) -> list[tuple[str, BLEDevice, Any]]:
        """Return only the configured proxy route for this panel heater.

        ESPHome Bluetooth proxy bonds are local to the proxy that completed
        commissioning.  An advertisement seen by another proxy does not make
        that proxy a valid authenticated route, so runtime reconnects must not
        roam between proxies.
        """
        del pair
        device = ble_device_from_source(self.hass, self.address, self.proxy_source)
        if device is None:
            device = self._device
        try:
            scanner = bluetooth.async_scanner_by_source(self.hass, self.proxy_source)
        except RuntimeError:
            scanner = None
        if device is None:
            return []
        if scanner is None or scanner.connector is None:
            raise DimplexConnectionError(
                "The configured ESPHome Bluetooth proxy is unavailable"
            )
        can_connect = getattr(scanner.connector, "can_connect", None)
        if callable(can_connect) and not can_connect():
            raise DimplexConnectionError(
                "The configured ESPHome Bluetooth proxy is unavailable"
            )
        return [(self.proxy_source, device, scanner)]

    async def _async_connect_route(
        self,
        source: str,
        device: BLEDevice,
        scanner: Any,
        *,
        pair: bool,
    ) -> tuple[BleakClient, asyncio.Semaphore]:
        """Serialize connection establishment for one ESPHome proxy."""
        async with _proxy_connection_lock(self.hass, source):
            return await self._async_connect_route_locked(
                source, device, scanner, pair=pair
            )

    async def _async_connect_route_locked(
        self,
        source: str,
        device: BLEDevice,
        scanner: Any,
        *,
        pair: bool,
    ) -> tuple[BleakClient, asyncio.Semaphore]:
        """Connect through one ESPHome route without changing its bond."""
        semaphore = _proxy_connection_semaphore(self.hass, source)
        await semaphore.acquire()
        client: BleakClient | None = None
        try:
            client_factory = partial(
                _create_pair_aware_client, scanner.connector.client
            )
            client = await establish_connection(
                cast(type[BleakClient], client_factory),
                device,
                DEVICE_NAME,
                max_attempts=1 if pair else CONNECT_ATTEMPTS,
                timeout=CONNECT_TIMEOUT,
                pair=pair,
                use_services_cache=not self._force_service_refresh,
            )
            return client, semaphore
        except asyncio.CancelledError:
            semaphore.release()
            raise
        except DimplexConnectionError:
            semaphore.release()
            raise
        except (BleakError, OSError, TimeoutError) as err:
            semaphore.release()
            if _is_authentication_error(err):
                raise DimplexAuthenticationError(
                    "BLE authentication is required"
                ) from err
            if pair:
                _LOGGER.warning(
                    "%s BLE connection failed for %s (%s)",
                    DEBUG_LOG_PREFIX,
                    self.address,
                    type(err).__name__,
                )
                error_message = str(err)
                if "BluetoothDevicePairingResponse" in error_message:
                    if "timeout" in error_message.casefold():
                        raise DimplexConnectionError("BLE pairing timed out") from err
                    raise DimplexAuthenticationError("BLE pairing is required") from err
            raise DimplexConnectionError(
                "Could not connect to the panel heater"
            ) from err

    @asynccontextmanager
    async def _connected(
        self, *, pair: bool = False, lock_held: bool = False
    ) -> AsyncIterator[BleakClient]:
        """Reuse one connection; select a route and restore encryption when needed."""

        operation_task = asyncio.current_task()
        previous_operation = self._operation_task
        self._operation_task = operation_task
        lock_context = nullcontext() if lock_held else self._lock
        try:
            async with lock_context:
                if self._connection is not None and not self._connection.is_connected:
                    await self._disconnect_locked()

                if self._connection is None:
                    routes = self._connection_routes(pair=pair)
                    if not routes:
                        if pair:
                            _LOGGER.warning(
                                "%s Pairing could not start: the configured ESPHome proxy has no current route to the panel heater",
                                DEBUG_LOG_PREFIX,
                            )
                        raise DimplexConnectionError(
                            "The configured ESPHome proxy cannot currently reach the panel heater"
                        )

                    source, device, scanner = routes[0]
                    _LOGGER.debug(
                        "%s Connecting heater %s through ESPHome proxy %s (pair=%s)",
                        DEBUG_LOG_PREFIX,
                        self.address,
                        scanner.name,
                        pair,
                    )
                    try:
                        client, semaphore = await self._async_connect_route(
                            source, device, scanner, pair=pair
                        )
                    except DimplexAuthenticationError:
                        _LOGGER.debug(
                            "%s Bond is not usable through configured proxy %s",
                            DEBUG_LOG_PREFIX,
                            source,
                        )
                        raise
                    except DimplexConnectionError:
                        _LOGGER.debug(
                            "%s Configured connection route %s failed",
                            DEBUG_LOG_PREFIX,
                            source,
                        )
                        raise

                    self._force_service_refresh = False
                    self._connection = client
                    self._connection_semaphore = semaphore

                try:
                    connection = self._connection
                    assert connection is not None
                    yield connection
                except asyncio.CancelledError:
                    await self._disconnect_locked()
                    raise
                except (BleakError, OSError, TimeoutError) as err:
                    self._force_service_refresh = True
                    await self._disconnect_locked()
                    if _is_authentication_error(err):
                        raise DimplexAuthenticationError(
                            "BLE authentication is required"
                        ) from err
                    raise DimplexConnectionError("BLE operation failed") from err
        finally:
            if self._operation_task is operation_task:
                self._operation_task = previous_operation

    async def async_retry_connection(self) -> HeaterSnapshot:
        """Restart one failed GATT session and restore link security."""
        await self.async_shutdown()
        return await self.async_read_snapshot()

    async def async_retry_authenticated_connection(self) -> HeaterSnapshot:
        """Retry the existing bond through the proxy's encryption handshake."""
        _LOGGER.info(
            "%s Retrying the existing authenticated connection for %s",
            DEBUG_LOG_PREFIX,
            self.address,
        )
        return await self.async_retry_connection()

    async def async_pair(self) -> None:
        """Establish a bond, verify it survives reconnect, and close it."""
        async with self._lock:
            await self._disconnect_locked()
            self._force_service_refresh = True
            _LOGGER.info(
                "%s Starting PIN pairing for %s",
                DEBUG_LOG_PREFIX,
                self.address,
            )
            try:
                async with self._connected(pair=True, lock_held=True) as client:
                    snapshot = await self._async_read_snapshot(client)
                    if snapshot.model is None:
                        raise DimplexConnectionError(
                            "Pairing did not return the panel-heater identity"
                        )

                # A successful PIN exchange is not enough: the bond must also be
                # reusable by the normal authenticated runtime path.
                await self._disconnect_locked()
                # ConfigR waits for the panel heater to leave its pairing transition
                # before testing the persisted bond on a fresh connection.
                await asyncio.sleep(_PAIRING_RECONNECT_DELAY)
                self._force_service_refresh = True
                async with self._connected(pair=False, lock_held=True) as client:
                    snapshot = await self._async_read_snapshot(client)
                    if snapshot.model is None:
                        raise DimplexConnectionError(
                            "The paired connection did not return the panel-heater identity"
                        )
            finally:
                self._force_service_refresh = True
                await self._disconnect_locked()

    async def _async_read_selector(
        self,
        client: BleakClient,
        address: GattAddress,
        selector_value: int,
    ) -> bytes | None:
        """Write a read-only selector and return its selected value."""
        characteristic = self._find_characteristic(client, address)
        if (
            characteristic is None
            or "read" not in characteristic.properties
            or not (
                "write" in characteristic.properties
                or "write-without-response" in characteristic.properties
            )
        ):
            return None
        started = time.monotonic()
        try:
            async with asyncio.timeout(GATT_READ_TIMEOUT):
                await client.write_gatt_char(
                    characteristic,
                    bytes((selector_value, 0, 0)),
                    response="write" in characteristic.properties,
                )
        finally:
            elapsed = time.monotonic() - started
            if elapsed > 1:
                _LOGGER.debug(
                    "%s Selector write %s=%s took %.3fs",
                    DEBUG_LOG_PREFIX,
                    address,
                    selector_value,
                    elapsed,
                )
        return await self._async_read_optional(client, address)

    async def _async_read_snapshot(self, client: BleakClient) -> HeaterSnapshot:
        """Read mapped fields on an already established GATT connection."""
        values: dict[GattAddress, bytes] = {}
        for address in READ_SNAPSHOT_FIELDS:
            raw = await self._async_read_optional(client, address)
            if raw is not None:
                values[address] = raw

        room_temperature = await self._async_read_selector(client, ROOM_TEMPERATURE, 1)
        lcd_temperature = await self._async_read_selector(client, ROOM_TEMPERATURE, 2)
        trac = await self._async_read_selector(client, (1, 0x0002), 1)
        ui_version = await self._async_read_selector(client, UI_VERSION, 1)
        if room_temperature is not None:
            values[ROOM_TEMPERATURE] = room_temperature

        return parse_snapshot(
            values,
            lcd_temperature=lcd_temperature,
            ui_version=ui_version,
            trac=trac,
        )

    async def async_read_snapshot(self) -> HeaterSnapshot:
        """Read mapped fields over the existing authenticated bond."""
        async with self._connected(pair=False) as client:
            return await self._async_read_snapshot(client)

    @staticmethod
    def _find_characteristic(
        client: BleakClient, address: GattAddress
    ) -> BleakGATTCharacteristic | None:
        candidates = gatt_uuid_candidates(*address)
        for service_id, characteristic_id in candidates:
            service = client.services.get_service(service_id)
            if service is None:
                continue
            characteristic = service.get_characteristic(characteristic_id)
            if characteristic is not None:
                return characteristic

        # ESPHome can report the main control service with an all-zero UUID.
        # Search discovered services as a fallback; the characteristic UUID
        # remains stable.
        if address[0] == 0:
            characteristic_id = candidates[0][1]
            for service in client.services:
                characteristic = service.get_characteristic(characteristic_id)
                if characteristic is not None:
                    return characteristic
        return None

    async def _async_read_optional(
        self, client: BleakClient, address: GattAddress
    ) -> bytes | None:
        """Read a present, readable characteristic; omit unsupported fields."""
        characteristic = self._find_characteristic(client, address)
        if characteristic is None or "read" not in characteristic.properties:
            return None
        started = time.monotonic()
        try:
            async with asyncio.timeout(GATT_READ_TIMEOUT):
                value = bytes(await client.read_gatt_char(characteristic))
        finally:
            elapsed = time.monotonic() - started
            if elapsed > 1:
                _LOGGER.debug(
                    "%s GATT read %s took %.3fs",
                    DEBUG_LOG_PREFIX,
                    address,
                    elapsed,
                )
        return value

    async def async_get_gatt_services(self) -> list[_GattServiceInfo]:
        """Return GATT service metadata without reading characteristic values."""
        async with self._connected(pair=False) as client:
            return [
                {
                    "uuid": service.uuid,
                    "characteristics": [
                        {
                            "uuid": characteristic.uuid,
                            "properties": list(characteristic.properties),
                        }
                        for characteristic in service.characteristics
                    ],
                }
                for service in client.services
            ]

    async def async_read_raw(self, group: int, characteristic_id: int) -> bytes:
        """Read a selected Dimplex characteristic unless it is protected."""
        validate_raw_access(group, characteristic_id, write=False)
        async with self._connected(pair=False) as client:
            characteristic = self._find_characteristic(
                client, (group, characteristic_id)
            )
            if characteristic is None:
                raise DimplexCharacteristicError("Characteristic was not found")
            if "read" not in characteristic.properties:
                raise DimplexCharacteristicError("Characteristic is not readable")
            return bytes(await client.read_gatt_char(characteristic))

    async def async_read_raw_fields(
        self, fields: Sequence[GattAddress]
    ) -> dict[GattAddress, bytes]:
        """Read available experimental fields over one authenticated connection."""
        for group, characteristic_id in fields:
            validate_raw_access(group, characteristic_id, write=False)

        values: dict[GattAddress, bytes] = {}
        async with self._connected(pair=False) as client:
            for address in fields:
                characteristic = self._find_characteristic(client, address)
                if characteristic is None or "read" not in characteristic.properties:
                    continue
                try:
                    async with asyncio.timeout(RAW_READ_TIMEOUT):
                        values[address] = bytes(
                            await client.read_gatt_char(characteristic)
                        )
                except (BleakError, OSError, TimeoutError):
                    _LOGGER.debug(
                        "%s Optional raw read failed for %s",
                        DEBUG_LOG_PREFIX,
                        address,
                        exc_info=True,
                    )
        return values

    async def async_write_raw(
        self, group: int, characteristic_id: int, payload: bytes
    ) -> bytes | None:
        """Write a selected Dimplex characteristic and return its readback."""
        validate_raw_access(group, characteristic_id, write=True)
        if not payload:
            raise DimplexProtocolError("Payload must not be empty")
        if len(payload) > MAX_GATT_VALUE_BYTES:
            raise DimplexProtocolError("Payload exceeds the 512-byte GATT value limit")

        async with self._connected(pair=False) as client:
            characteristic = self._find_characteristic(
                client, (group, characteristic_id)
            )
            if characteristic is None:
                raise DimplexCharacteristicError("Characteristic was not found")
            supports_response = "write" in characteristic.properties
            if (
                not supports_response
                and "write-without-response" not in characteristic.properties
            ):
                raise DimplexCharacteristicError("Characteristic is not writable")
            try:
                await client.write_gatt_char(
                    characteristic, payload, response=supports_response
                )
                if "read" not in characteristic.properties:
                    return None
                await asyncio.sleep(1)
                return bytes(await client.read_gatt_char(characteristic))
            except (BleakError, OSError, TimeoutError) as err:
                raise DimplexConnectionError("GATT write failed") from err
