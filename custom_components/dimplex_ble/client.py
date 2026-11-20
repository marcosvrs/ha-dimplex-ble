"""Async BLE access for Dimplex heaters through Home Assistant Bluetooth."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
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


_LOGGER = logging.getLogger(__name__)
DEBUG_LOG_PREFIX = "[DEBUG-DIMPLEX-PIN-20261117]"
CONNECT_TIMEOUT = 15.0
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
        await self._client.connect(pair=self._pair, **kwargs)

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
    """Home Assistant could not connect to the heater."""


class DimplexAuthenticationError(DimplexConnectionError):
    """The heater rejected an operation because BLE authentication is missing."""


class DimplexCharacteristicError(DimplexError):
    """A GATT characteristic is absent or lacks the requested property."""


class DimplexBluetoothClient:
    """Maintain one encrypted BLE connection for the heater lifecycle."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        proxy_source: str,
        device: BLEDevice | None = None,
    ) -> None:
        """Initialize a client bound to the proxy that owns this heater's bond."""
        self.hass = hass
        self.address = address
        self.proxy_source = proxy_source
        self._device = device
        self._force_service_refresh = True
        self._lock = asyncio.Lock()
        self._connection: BleakClient | None = None
        self._connection_semaphore: asyncio.Semaphore | None = None

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
        """Close the persistent BLE connection during entry unload."""
        async with self._lock:
            await self._disconnect_locked()

    @asynccontextmanager
    async def _connected(self, *, pair: bool = False) -> AsyncIterator[BleakClient]:
        """Reuse one connection; request ESPHome to establish encryption when needed."""

        async with self._lock:
            if self._connection is not None and not self._connection.is_connected:
                await self._disconnect_locked()

            if self._connection is None:
                device = ble_device_from_source(
                    self.hass, self.address, self.proxy_source
                )
                if device is None and pair:
                    # Pairing may use the discovery device held by the active flow.
                    device = self._device
                if device is None:
                    if pair:
                        _LOGGER.warning(
                            "%s Pairing could not start: the selected ESPHome proxy has no current route to the heater",
                            DEBUG_LOG_PREFIX,
                        )
                    raise DimplexConnectionError(
                        "The configured ESPHome proxy cannot currently reach the heater"
                    )

                scanner = bluetooth.async_scanner_by_source(
                    self.hass, self.proxy_source
                )
                if scanner is None or scanner.connector is None:
                    raise DimplexConnectionError(
                        "The selected ESPHome Bluetooth proxy is unavailable"
                    )
                if pair:
                    _LOGGER.debug(
                        "%s Securing BLE connection through ESPHome proxy %s",
                        DEBUG_LOG_PREFIX,
                        scanner.name,
                    )

                semaphore = _proxy_connection_semaphore(self.hass, self.proxy_source)
                await semaphore.acquire()
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
                except (BleakError, OSError, TimeoutError) as err:
                    semaphore.release()
                    if pair:
                        _LOGGER.warning(
                            "%s Paired BLE connection failed (%s)",
                            DEBUG_LOG_PREFIX,
                            type(err).__name__,
                        )
                        if "BluetoothDevicePairingResponse" in str(err):
                            raise DimplexAuthenticationError(
                                "BLE pairing is required"
                            ) from err
                    raise DimplexConnectionError(
                        "Could not connect to the heater"
                    ) from err
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
                error_text = str(err)
                if (
                    "Insufficient authentication" in error_text
                    or "Insufficient encryption" in error_text
                ):
                    raise DimplexAuthenticationError(
                        "BLE authentication is required"
                    ) from err
                raise DimplexConnectionError("BLE operation failed") from err

    async def async_retry_authenticated_connection(self) -> HeaterSnapshot:
        """Retry the existing bond without initiating a new PIN exchange."""
        _LOGGER.info(
            "%s Retrying the existing authenticated connection for %s",
            DEBUG_LOG_PREFIX,
            self.address,
        )
        await self.async_shutdown()
        return await self.async_read_snapshot()

    async def async_pair(self) -> None:
        """Establish a bond, verify it survives reconnect, and close it."""
        self._force_service_refresh = True
        _LOGGER.info(
            "%s Starting PIN pairing for %s",
            DEBUG_LOG_PREFIX,
            self.address,
        )
        try:
            async with self._connected(pair=True) as client:
                snapshot = await self._async_read_snapshot(client)
                if snapshot.model is None:
                    raise DimplexConnectionError(
                        "Pairing did not return the heater identity"
                    )

            # A successful PIN exchange is not enough: the bond must also be
            # reusable by the normal authenticated runtime path.
            await self.async_shutdown()
            # ConfigR waits for the heater to leave its pairing transition
            # before testing the persisted bond on a fresh connection.
            await asyncio.sleep(_PAIRING_RECONNECT_DELAY)
            self._force_service_refresh = True
            async with self._connected(pair=True) as client:
                snapshot = await self._async_read_snapshot(client)
                if snapshot.model is None:
                    raise DimplexConnectionError(
                        "The paired connection did not return the heater identity"
                    )
        finally:
            self._force_service_refresh = True
            await self.async_shutdown()

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
        await client.write_gatt_char(
            characteristic,
            bytes((selector_value, 0, 0)),
            response="write" in characteristic.properties,
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
        """Read mapped fields on an authenticated GATT connection."""
        async with self._connected(pair=True) as client:
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
        return bytes(await client.read_gatt_char(characteristic))

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
        async with self._connected(pair=True) as client:
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
        async with self._connected(pair=True) as client:
            for address in fields:
                characteristic = self._find_characteristic(client, address)
                if characteristic is None or "read" not in characteristic.properties:
                    continue
                values[address] = bytes(await client.read_gatt_char(characteristic))
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

        async with self._connected(pair=True) as client:
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
