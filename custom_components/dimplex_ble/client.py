"""Async BLE access for Dimplex heaters through Home Assistant Bluetooth."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from functools import partial
from typing import Any, TypedDict, cast

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.exc import BleakError
from bleak_retry_connector import establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import DEVICE_NAME
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

_LOGGER = logging.getLogger(__name__)
DEBUG_LOG_PREFIX = "[DEBUG-DIMPLEX-PIN-20261117]"
CONNECT_TIMEOUT = 15.0
CONNECT_ATTEMPTS = 3
READ_SNAPSHOT_FIELDS: tuple[GattAddress, ...] = (
    (3, 0x2005),
    (0, 0x1023),
    (0, 0x1006),
    (0, 0x1011),
    (0, 0x1001),
    (0, 0x1005),
)
ROOM_TEMPERATURE: GattAddress = (1, 0x0008)

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


class DimplexCharacteristicError(DimplexError):
    """A GATT characteristic is absent or lacks the requested property."""


class DimplexBluetoothClient:
    """Open short-lived BLE connections through Home Assistant's Bluetooth stack."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        proxy_source: str,
    ) -> None:
        """Initialize a client bound to the proxy that owns this heater's bond."""
        self.hass = hass
        self.address = address
        self.proxy_source = proxy_source
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _connected(
        self, *, pair: bool = False
    ) -> AsyncIterator[BleakClient]:
        """Connect through the bonded proxy, pairing before service discovery
        if needed."""
        async with self._lock:
            device = ble_device_from_source(
                self.hass, self.address, self.proxy_source
            )
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
                    "%s Pairing through ESPHome proxy %s",
                    DEBUG_LOG_PREFIX,
                    scanner.name,
                )

            try:
                client_factory = partial(
                    _create_pair_aware_client, scanner.connector.client
                )
                client = await establish_connection(
                    cast(type[BleakClient], client_factory),
                    device,
                    DEVICE_NAME,
                    max_attempts=CONNECT_ATTEMPTS,
                    timeout=CONNECT_TIMEOUT,
                    pair=pair,
                )
            except (BleakError, OSError, TimeoutError) as err:
                if pair:
                    _LOGGER.warning(
                        "%s Paired BLE connection failed (%s)",
                        DEBUG_LOG_PREFIX,
                        type(err).__name__,
                    )
                raise DimplexConnectionError(
                    "Could not connect to the heater"
                ) from err

            try:
                try:
                    yield client
                except (BleakError, OSError, TimeoutError) as err:
                    raise DimplexConnectionError("BLE operation failed") from err
            finally:
                try:
                    await client.disconnect()
                except (BleakError, OSError, TimeoutError):
                    _LOGGER.debug("BLE disconnect failed", exc_info=True)

    async def async_pair(self) -> None:
        """Establish a bond through the selected ESPHome proxy."""
        async with self._connected(pair=True):
            pass

    @staticmethod
    def _find_characteristic(
        client: BleakClient, address: GattAddress
    ) -> BleakGATTCharacteristic | None:
        """Find one characteristic within its Dimplex service group."""
        for service_id, characteristic_id in gatt_uuid_candidates(*address):
            service = client.services.get_service(service_id)
            if service is None:
                continue
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

    async def async_read_snapshot(self) -> HeaterSnapshot:
        """Read mapped fields without changing operating settings."""
        async with self._connected() as client:
            values: dict[GattAddress, bytes] = {}
            for address in READ_SNAPSHOT_FIELDS:
                raw = await self._async_read_optional(client, address)
                if raw is not None:
                    values[address] = raw

            selector = self._find_characteristic(client, ROOM_TEMPERATURE)
            if (
                selector is not None
                and "read" in selector.properties
                and ("write" in selector.properties or "write-without-response" in selector.properties)
            ):
                await client.write_gatt_char(
                    selector,
                    bytes((1, 0, 0)),
                    response="write" in selector.properties,
                )
                room_temperature = await self._async_read_optional(
                    client, ROOM_TEMPERATURE
                )
                if room_temperature is not None:
                    values[ROOM_TEMPERATURE] = room_temperature

            return parse_snapshot(values)

    async def async_get_gatt_services(self) -> list[_GattServiceInfo]:
        """Return GATT service metadata without reading characteristic values."""
        async with self._connected() as client:
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
        async with self._connected() as client:
            characteristic = self._find_characteristic(
                client, (group, characteristic_id)
            )
            if characteristic is None:
                raise DimplexCharacteristicError("Characteristic was not found")
            if "read" not in characteristic.properties:
                raise DimplexCharacteristicError("Characteristic is not readable")
            return bytes(await client.read_gatt_char(characteristic))

    async def async_write_raw(
        self, group: int, characteristic_id: int, payload: bytes
    ) -> bytes | None:
        """Write a selected Dimplex characteristic and return its readback."""
        validate_raw_access(group, characteristic_id, write=True)
        if not payload:
            raise DimplexProtocolError("Payload must not be empty")
        if len(payload) > MAX_GATT_VALUE_BYTES:
            raise DimplexProtocolError("Payload exceeds the 512-byte GATT value limit")

        async with self._connected() as client:
            characteristic = self._find_characteristic(
                client, (group, characteristic_id)
            )
            if characteristic is None:
                raise DimplexCharacteristicError("Characteristic was not found")
            supports_response = "write" in characteristic.properties
            if not supports_response and "write-without-response" not in characteristic.properties:
                raise DimplexCharacteristicError("Characteristic is not writable")

            try:
                await client.write_gatt_char(
                    characteristic, payload, response=supports_response
                )
                if "read" not in characteristic.properties:
                    return None
                await asyncio.sleep(1)
                return bytes(await client.read_gatt_char(characteristic))
            except BleakError as err:
                raise DimplexConnectionError("GATT write failed") from err
