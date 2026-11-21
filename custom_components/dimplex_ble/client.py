"""Async BLE access for Dimplex heaters through Home Assistant Bluetooth."""

from __future__ import annotations

import asyncio
import logging
import time
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
from .esphome_proxy import find_proxy_entry
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
from .route_policy import RouteCandidate, rank_routes, source_key

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
ROUTE_REBALANCE_COOLDOWN = 300.0
ROUTE_FAILURE_COOLDOWN = 300.0
ROUTE_RSSI_MARGIN = 10
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
        self._active_proxy_source: str | None = None
        self._avoid_proxy_source: str | None = None
        self._last_route_switch = 0.0
        self._route_rssi: dict[str, int] = {}
        self._route_backoff_until: dict[str, float] = {}
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

    def _connection_routes(
        self, *, pair: bool, prefer_active: bool = True
    ) -> list[tuple[str, BLEDevice, Any]]:
        """Return valid ESPHome routes, ranked by the shared route policy."""
        if pair:
            device = ble_device_from_source(self.hass, self.address, self.proxy_source)
            if device is None:
                device = self._device
            try:
                scanner = bluetooth.async_scanner_by_source(
                    self.hass, self.proxy_source
                )
            except RuntimeError:
                scanner = None
            if device is None:
                return []
            if scanner is None or scanner.connector is None:
                raise DimplexConnectionError(
                    "The selected ESPHome Bluetooth proxy is unavailable"
                )
            can_connect = getattr(scanner.connector, "can_connect", None)
            if callable(can_connect) and not can_connect():
                raise DimplexConnectionError(
                    "The selected ESPHome Bluetooth proxy is unavailable"
                )
            return [(self.proxy_source, device, scanner)]

        routes: dict[str, tuple[str, BLEDevice, Any, RouteCandidate]] = {}
        self._route_rssi.clear()

        def add_route(source: str, device: BLEDevice, scanner: Any, rssi: int) -> None:
            get_allocations = getattr(scanner, "get_allocations", None)
            allocations = get_allocations() if callable(get_allocations) else None
            free_slots = getattr(allocations, "free", None)
            candidate = RouteCandidate(
                source=source,
                rssi=rssi,
                free_slots=free_slots if type(free_slots) is int else None,
            )
            routes.setdefault(source_key(source), (source, device, scanner, candidate))

        try:
            scanner_devices = bluetooth.async_scanner_devices_by_address(
                self.hass, self.address.upper(), connectable=True
            )
        except RuntimeError:
            scanner_devices = []
        for scanner_device in scanner_devices:
            source = scanner_device.scanner.source
            if (
                not isinstance(source, str)
                or find_proxy_entry(self.hass, source) is None
            ):
                continue
            scanner = bluetooth.async_scanner_by_source(self.hass, source)
            if scanner is None or scanner.connector is None:
                continue
            can_connect = getattr(scanner.connector, "can_connect", None)
            if callable(can_connect) and not can_connect():
                continue
            advertisement = getattr(scanner_device, "advertisement", None)
            rssi = getattr(advertisement, "rssi", getattr(scanner_device, "rssi", -127))
            add_route(
                source,
                scanner_device.ble_device,
                scanner,
                rssi if isinstance(rssi, int) else -127,
            )

        if source_key(self.proxy_source) not in routes:
            device = ble_device_from_source(self.hass, self.address, self.proxy_source)
            try:
                scanner = bluetooth.async_scanner_by_source(
                    self.hass, self.proxy_source
                )
            except RuntimeError:
                scanner = None
            if scanner is not None and scanner.connector is not None:
                can_connect = getattr(scanner.connector, "can_connect", None)
                if callable(can_connect) and not can_connect():
                    scanner = None
            if (
                device is not None
                and scanner is not None
                and scanner.connector is not None
            ):
                add_route(self.proxy_source, device, scanner, -127)
            elif not routes and device is not None:
                raise DimplexConnectionError(
                    "The selected ESPHome Bluetooth proxy is unavailable"
                )

        self._route_rssi.update(
            {
                source_key(source): candidate.rssi
                for source, _, _, candidate in routes.values()
            }
        )
        ranked_candidates = rank_routes(
            [candidate for _, _, _, candidate in routes.values()],
            active_source=self._active_proxy_source,
            prefer_active=prefer_active,
        )
        ranked = [
            routes[source_key(candidate.source)][:3] for candidate in ranked_candidates
        ]
        if not pair and len(ranked) > 1:
            now = time.monotonic()
            healthy = [
                route
                for route in ranked
                if self._route_backoff_until.get(source_key(route[0]), 0) <= now
            ]
            if healthy:
                ranked = healthy
        if self._avoid_proxy_source is not None and len(ranked) > 1:
            avoided_source = source_key(self._avoid_proxy_source)
            ranked.sort(key=lambda route: source_key(route[0]) == avoided_source)
        return ranked

    def _should_rebalance(self) -> bool:
        """Return whether a materially better route should replace the current one."""
        active_source = self._active_proxy_source
        if active_source is None:
            return False
        if time.monotonic() - self._last_route_switch < ROUTE_REBALANCE_COOLDOWN:
            return False
        try:
            routes = self._connection_routes(pair=False, prefer_active=False)
        except DimplexConnectionError:
            return False
        active_key = active_source.casefold()
        if active_key in {source.casefold() for source, _, _ in routes}:
            now = time.monotonic()
            routes = [
                route
                for route in routes
                if route[0].casefold() == active_key
                or self._route_backoff_until.get(route[0].casefold(), 0) <= now
            ]
        if not routes or routes[0][0].casefold() == active_key:
            return False
        if active_source.casefold() not in {
            source.casefold() for source, _, _ in routes
        }:
            return True
        active_rssi = self._route_rssi.get(active_source.casefold())
        best_rssi = self._route_rssi.get(routes[0][0].casefold())
        return (
            active_rssi is not None
            and best_rssi is not None
            and best_rssi >= active_rssi + ROUTE_RSSI_MARGIN
        )

    async def _async_connect_route(
        self,
        source: str,
        device: BLEDevice,
        scanner: Any,
        *,
        pair: bool,
    ) -> tuple[BleakClient, asyncio.Semaphore]:
        """Connect and request link encryption on one ESPHome route."""
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
            if not pair:
                # ESPHome's pair=False connection opens the GATT link but does
                # not request encryption from an existing bond. The native
                # proxy pair operation maps to esp_ble_set_encryption(); it
                # does not submit a new PIN.
                try:
                    await cast(Any, client).pair()
                except (BleakError, OSError, TimeoutError) as err:
                    raise DimplexAuthenticationError(
                        "BLE authentication is required"
                    ) from err
            return client, semaphore
        except DimplexAuthenticationError:
            if client is not None:
                try:
                    await client.disconnect()
                except (BleakError, OSError, TimeoutError):
                    _LOGGER.debug(
                        "%s BLE encryption cleanup failed",
                        DEBUG_LOG_PREFIX,
                        exc_info=True,
                    )
            semaphore.release()
            raise
        except (BleakError, OSError, TimeoutError) as err:
            semaphore.release()
            if pair:
                _LOGGER.warning(
                    "%s BLE connection failed for %s (%s)",
                    DEBUG_LOG_PREFIX,
                    self.address,
                    type(err).__name__,
                )
                if "BluetoothDevicePairingResponse" in str(err):
                    raise DimplexAuthenticationError("BLE pairing is required") from err
            raise DimplexConnectionError("Could not connect to the heater") from err

    @asynccontextmanager
    async def _connected(self, *, pair: bool = False) -> AsyncIterator[BleakClient]:
        """Reuse one connection; select a route and restore encryption when needed."""

        async with self._lock:
            if (
                self._connection is not None
                and self._connection.is_connected
                and not pair
                and self._should_rebalance()
            ):
                self._avoid_proxy_source = self._active_proxy_source
                await self._disconnect_locked()

            if self._connection is not None and not self._connection.is_connected:
                await self._disconnect_locked()

            if self._connection is None:
                routes = self._connection_routes(pair=pair, prefer_active=False)
                if not routes:
                    if pair:
                        _LOGGER.warning(
                            "%s Pairing could not start: the selected ESPHome proxy has no current route to the heater",
                            DEBUG_LOG_PREFIX,
                        )
                    raise DimplexConnectionError(
                        "The configured ESPHome proxy cannot currently reach the heater"
                    )

                last_error = DimplexConnectionError("Could not connect to the heater")
                authentication_error: DimplexAuthenticationError | None = None
                for source, device, scanner in routes:
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
                    except DimplexAuthenticationError as err:
                        if not pair:
                            self._route_backoff_until[source.casefold()] = (
                                time.monotonic() + ROUTE_FAILURE_COOLDOWN
                            )
                        authentication_error = err
                        last_error = err
                        _LOGGER.debug(
                            "%s Bond is not usable through proxy %s",
                            DEBUG_LOG_PREFIX,
                            source,
                        )
                        if pair:
                            raise
                        continue
                    except DimplexConnectionError as err:
                        if not pair:
                            self._route_backoff_until[source.casefold()] = (
                                time.monotonic() + ROUTE_FAILURE_COOLDOWN
                            )
                        last_error = err
                        _LOGGER.debug(
                            "%s Connection route %s failed; trying the next route",
                            DEBUG_LOG_PREFIX,
                            source,
                        )
                        if pair:
                            raise
                        continue

                    self._route_backoff_until.pop(source.casefold(), None)
                    self._active_proxy_source = source
                    self._last_route_switch = time.monotonic()
                    self._force_service_refresh = False
                    self._avoid_proxy_source = None
                    self._connection = client
                    self._connection_semaphore = semaphore
                    break
                else:
                    if authentication_error is not None:
                        raise authentication_error
                    raise last_error

            try:
                connection = self._connection
                assert connection is not None
                yield connection
            except asyncio.CancelledError:
                await self._disconnect_locked()
                raise
            except (BleakError, OSError, TimeoutError) as err:
                self._force_service_refresh = True
                self._avoid_proxy_source = self._active_proxy_source
                if not pair and self._active_proxy_source is not None:
                    self._route_backoff_until[self._active_proxy_source.casefold()] = (
                        time.monotonic() + ROUTE_FAILURE_COOLDOWN
                    )
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
            async with self._connected(pair=False) as client:
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
