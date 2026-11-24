"""One authenticated BLE link to a panel heater through its pinned proxy.

By default the link stays open between operations; with ``keep_connected``
off every operation connects, pairs, runs its GATT sequence and disconnects.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any, TypedDict

from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from habluetooth import BaseHaScanner
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from . import responder
from .const import DOMAIN
from .errors import (
    CharacteristicError,
    HeaterUnavailableError,
    att_error_code,
    classify_pair_error,
    pair_error_code,
)
from .protocol import (
    ADVANCE_HEATING,
    AWAY_MODE,
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    SCHEDULE,
    SCHEDULE_DAYS,
    SETPOINT_RANGE,
    GattAddress,
    HeaterSnapshot,
    SchedulePeriod,
    encode_schedule_request,
    gatt_uuid_candidates,
    parse_schedule_day,
    parse_snapshot,
    validate_raw_access,
)

CONNECT_TIMEOUT = 15.0
READ_SNAPSHOT_FIELDS: tuple[GattAddress, ...] = (
    (3, 0x2005),
    (3, 0x200A),
    (3, 0x2009),
    (3, 0x000A),
    (0, 0x1023),
    SETPOINT_RANGE,
    (0, 0x1011),
    (0, 0x1001),
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    AWAY_MODE,
    (0, 0x1005),
    # Advance ends by itself at the next timer period, so it is polled.
    ADVANCE_HEATING,
)
ROOM_TEMPERATURE: GattAddress = (1, 0x0008)
TRAC: GattAddress = (1, 0x0002)
UI_VERSION: GattAddress = (3, 0x2001)
_GATT_ERRORS = (BleakError, TimeoutError, OSError)
# Quiet time a heater needs after a link ends before it accepts a new one.
# Evidence: reconnecting 20-23 ms after a disconnect made the heater accept and
# then drop the link within ~7 ms (HCI 0x13) or answer "Invalid handle"
# (hardware 2026-11-24 17:58:41 and 18:24:23); Dimplex's ConfigR help text tells
# users to wait about 10 s after a link ends so the product can go to sleep
# (docs/configr-firmware-analysis.md).
HEATER_SETTLE_S = 10.0

_LOGGER = logging.getLogger(__name__)


class GattCharacteristicInfo(TypedDict):
    """Characteristic metadata returned by the get_gatt_services action."""

    uuid: str
    properties: list[str]


class GattServiceInfo(TypedDict):
    """Service metadata returned by the get_gatt_services action."""

    uuid: str
    characteristics: list[GattCharacteristicInfo]


def _security_lock(hass: HomeAssistant, source: str) -> asyncio.Lock:
    """Serialize SMP per proxy: the ESP serializes connects, not pairing."""
    locks: dict[str, asyncio.Lock] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "security_locks", {}
    )
    return locks.setdefault(source, asyncio.Lock())


async def _sleep(seconds: float) -> None:
    """Sleep seam for the settle wait."""
    await asyncio.sleep(seconds)


def _link_down_times(hass: HomeAssistant) -> dict[str, float]:
    """Monotonic time of each heater's last link-down, shared by all HeaterLinks."""
    times: dict[str, float] = hass.data.setdefault(DOMAIN, {}).setdefault(
        "link_down", {}
    )
    return times


async def _await_to_end(task: asyncio.Task[Any]) -> None:
    """Await ``task`` to its native end, then re-raise a caller cancellation.

    Disconnecting while the proxy runs SMP fails AUTH_CMPL, and IDF then deletes
    the bond (RC-2); cancelling a forming link orphans it (RC-3). A pending
    ``connect()`` or ``pair()`` must therefore never be abandoned.
    """
    cancelled = False
    while not task.done():
        try:
            await asyncio.wait((task,))
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        if not task.cancelled():
            task.exception()  # retrieved; the caller's cancellation wins
        raise asyncio.CancelledError
    task.result()


def _find_characteristic(
    client: BaseBleakClient, address: GattAddress
) -> BleakGATTCharacteristic | None:
    """Find one mapped characteristic across SIG and Dimplex UUID forms."""
    services = client.services
    if services is None:
        return None
    candidates = gatt_uuid_candidates(*address)
    for service_id, characteristic_id in candidates:
        service = services.get_service(service_id)
        if service is not None:
            characteristic = service.get_characteristic(characteristic_id)
            if characteristic is not None:
                return characteristic
    if address[0] == 0:
        for service in services:
            characteristic = service.get_characteristic(candidates[0][1])
            if characteristic is not None:
                return characteristic
    return None


async def _read_optional(client: BaseBleakClient, address: GattAddress) -> bytes | None:
    """Read a present, readable characteristic; omit unsupported fields."""
    characteristic = _find_characteristic(client, address)
    if characteristic is None or "read" not in characteristic.properties:
        return None
    return bytes(await client.read_gatt_char(characteristic))


async def _read_selector(
    client: BaseBleakClient, address: GattAddress, selector: int | bytes
) -> bytes | None:
    """Write a read-only selector, then read the selected value."""
    characteristic = _find_characteristic(client, address)
    if characteristic is None or "read" not in characteristic.properties:
        return None
    properties = characteristic.properties
    if "write" not in properties and "write-without-response" not in properties:
        return None
    payload = bytes((selector, 0, 0)) if isinstance(selector, int) else selector
    await client.write_gatt_char(
        characteristic, payload, response="write" in properties
    )
    return await _read_optional(client, address)


type WeekSchedule = list[tuple[SchedulePeriod, ...] | None]


async def _read_schedules(
    client: BaseBleakClient, programmes: tuple[int, ...]
) -> dict[int, WeekSchedule]:
    """Read 7 days (Monday first) of each timer programme: select, then read."""
    schedules: dict[int, WeekSchedule] = {}
    for programme in programmes:
        schedules[programme] = [
            parse_schedule_day(
                await _read_selector(
                    client, SCHEDULE, encode_schedule_request(programme, day)
                ),
                programme,
                day,
            )
            for day in range(SCHEDULE_DAYS)
        ]
    return schedules


async def _read_snapshot(client: BaseBleakClient) -> HeaterSnapshot:
    """Read every mapped field on an authenticated link."""
    values: dict[GattAddress, bytes] = {}
    for address in READ_SNAPSHOT_FIELDS:
        if (raw := await _read_optional(client, address)) is not None:
            values[address] = raw
    if (room := await _read_selector(client, ROOM_TEMPERATURE, 1)) is not None:
        values[ROOM_TEMPERATURE] = room
    lcd_temperature = await _read_selector(client, ROOM_TEMPERATURE, 2)
    trac = await _read_selector(client, TRAC, 1)
    ui_version = await _read_selector(client, UI_VERSION, 1)
    return parse_snapshot(
        values, lcd_temperature=lcd_temperature, ui_version=ui_version, trac=trac
    )


class HeaterLink:
    """Own the heater's BLE client: route, connect, pair, GATT, close."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        source: str,
        passkey: str | None,
        *,
        keep_connected: bool = True,
    ) -> None:
        """Pin the link to the proxy that holds the heater's bond."""
        self._hass = hass
        self.address = address.upper()
        self.source = source
        self._passkey = int(passkey) if passkey else None
        self.keep_connected = keep_connected
        # Called with the link's scanner when a held link drops without our
        # disconnect (the heater or the proxy ended it).
        self.on_link_lost: Callable[[BaseHaScanner | None], None] | None = None
        self._lock = asyncio.Lock()
        self._client: BaseBleakClient | None = None
        self._scanner: BaseHaScanner | None = None
        self._closed = False
        # Serializes this link's responder arm/deliver/clear; ``_owns_responder``
        # is set only while this link's pairing holds the proxy's security lock.
        self._responder_lock = asyncio.Lock()
        self._owns_responder = False

    @property
    def connected(self) -> bool:
        """Return whether a link is currently held."""
        return self._client is not None and self._client.is_connected

    def route(self) -> tuple[BaseHaScanner, BLEDevice] | None:
        """Return the pinned scanner and device if it currently sees the heater."""
        scanner = bluetooth.async_scanner_by_source(self._hass, self.source)
        if scanner is None or scanner.connector is None:
            return None
        for seen in bluetooth.async_scanner_devices_by_address(
            self._hass, self.address, connectable=True
        ):
            if seen.scanner is scanner:
                return scanner, seen.ble_device
        return None

    async def async_snapshot(self) -> HeaterSnapshot:
        """Read the complete mapped snapshot."""
        started = time.monotonic()
        async with self._operation("snapshot") as client:
            snapshot = await _read_snapshot(client)
        _LOGGER.debug(
            "GATT %s snapshot ok %.2fs", self.address, time.monotonic() - started
        )
        return snapshot

    async def async_snapshot_with_settings(
        self, settings: tuple[GattAddress, ...], programmes: tuple[int, ...] = ()
    ) -> tuple[
        HeaterSnapshot, dict[GattAddress, bytes | None], dict[int, WeekSchedule]
    ]:
        """Read the snapshot and the slow settings inside one operation.

        Used by the per-poll mode so the slow settings share the poll's
        connection instead of opening a second one. A failed settings read does
        not fail the snapshot; that setting and the rest are then omitted, and
        so are the schedules.
        """
        started = time.monotonic()
        values: dict[GattAddress, bytes | None] = {}
        schedules: dict[int, WeekSchedule] = {}
        async with self._operation("snapshot") as client:
            snapshot = await _read_snapshot(client)
            try:
                for address in settings:
                    values[address] = await _read_optional(client, address)
                schedules = await _read_schedules(client, programmes)
            except _GATT_ERRORS as err:
                _LOGGER.debug(
                    "GATT %s settings read failed att=%s %s: %s",
                    self.address,
                    att_error_code(err),
                    type(err).__name__,
                    err,
                )
        _LOGGER.debug(
            "GATT %s snapshot+settings ok %.2fs",
            self.address,
            time.monotonic() - started,
        )
        return snapshot, values, schedules

    async def async_read_schedules(
        self, programmes: tuple[int, ...]
    ) -> dict[int, WeekSchedule]:
        """Read the timer programmes (read-only) in one operation."""
        async with self._operation("schedules") as client:
            return await _read_schedules(client, programmes)

    async def async_read(self, address: GattAddress) -> bytes | None:
        """Read one characteristic, or ``None`` when the heater lacks it."""
        async with self._operation(f"read {address[0]}/0x{address[1]:04x}") as client:
            return await _read_optional(client, address)

    async def async_write(self, address: GattAddress, payload: bytes) -> None:
        """Write one characteristic; the next refresh is the readback."""
        validate_raw_access(*address, write=True)
        op = f"write {address[0]}/0x{address[1]:04x}"
        async with self._operation(op) as client:
            characteristic = _find_characteristic(client, address)
            if characteristic is None:
                raise CharacteristicError("Characteristic was not found")
            properties = characteristic.properties
            if "write" not in properties and "write-without-response" not in properties:
                raise CharacteristicError("Characteristic is not writable")
            await client.write_gatt_char(
                characteristic, payload, response="write" in properties
            )
        _LOGGER.debug("GATT %s %s ok", self.address, op)

    async def async_gatt_services(self) -> list[GattServiceInfo]:
        """Return service metadata without reading characteristic values."""
        async with self._operation("services") as client:
            return [
                {
                    "uuid": service.uuid,
                    "characteristics": [
                        {"uuid": char.uuid, "properties": list(char.properties)}
                        for char in service.characteristics
                    ],
                }
                for service in client.services or ()
            ]

    async def async_pair(self, passkey: int | None) -> None:
        """Commission: fresh connect and pair, answering with ``passkey``.

        ``None`` only targets the responder; the PIN is armed separately while
        this pairing waits. The link stays open for the verification read.
        """
        async with self._lock:
            await self._ensure_link(passkey, fresh=True)

    async def async_close(self) -> None:
        """Close the link once; waits for any in-flight operation (incl. pair)."""
        async with self._lock:
            self._closed = True
            await self._disconnect("close")

    @asynccontextmanager
    async def _operation(self, op: str) -> AsyncIterator[BaseBleakClient]:
        """Run one GATT sequence on the link; drop the link on any failure."""
        async with self._lock:
            client = await self._ensure_link(self._passkey, fresh=False)
            try:
                yield client
            except _GATT_ERRORS as err:
                _LOGGER.debug(
                    "GATT %s %s failed att=%s %s: %s",
                    self.address,
                    op,
                    att_error_code(err),
                    type(err).__name__,
                    err,
                )
                await self._disconnect("gatt_error")
                raise HeaterUnavailableError(f"GATT {op} failed") from err
            except asyncio.CancelledError:
                await self._disconnect("cancelled")
                raise
            if not self.keep_connected:
                # Connect and pair already ran to their native end (RC-2/RC-3);
                # only the finished link is released here.
                await self._disconnect("per_poll")

    async def _ensure_link(
        self, passkey: int | None, *, fresh: bool
    ) -> BaseBleakClient:
        """Reuse a live link or connect, then pair before any GATT operation."""
        if self._closed:
            raise HeaterUnavailableError("The link is closed")
        client = self._client
        scanner = bluetooth.async_scanner_by_source(self._hass, self.source)
        if client is not None:
            # A reconnected proxy API registers a new scanner object; a client
            # built on the old one talks to a dead API connection.
            if client.is_connected and scanner is self._scanner:
                return client
            await self._disconnect("stale")
        await self._settle()
        route = self.route()
        if route is None:
            _LOGGER.debug(
                "ROUTE_NOT_SEEN %s via %s scanner=%s",
                self.address,
                self.source,
                scanner is not None,
            )
            raise HeaterUnavailableError("The pinned proxy does not see the heater")
        scanner, device = route
        assert scanner.connector is not None  # checked by route()
        # The heater starts SMP the moment the link is up (Security Request), so
        # the responder must target it before connect, not after (hardware
        # 2026-11-24 16:13: armed 2 s after the link, passkey request missed).
        # The proxy holds ONE responder state for all its heaters, so only the
        # pairing holding the proxy's security lock may arm or clear it.
        async with _security_lock(self._hass, self.source):
            try:
                # Inside the try: a cancellation after the proxy applied the arm
                # but before its reply arrived must still clear it.
                async with self._responder_lock:
                    self._owns_responder = True
                    await responder.async_arm(
                        self._hass, self.source, self.address, passkey
                    )
                return await self._connect_and_pair(scanner, device, fresh=fresh)
            finally:
                await _await_to_end(
                    self._hass.async_create_task(
                        self._release_responder(),
                        f"dimplex_ble responder clear {self.address}",
                    )
                )

    async def async_deliver_pin(self, passkey: int) -> None:
        """Arm ``passkey`` for this link's pairing that waits for its PIN.

        Only a pairing of this link that holds the proxy's security lock owns
        the shared responder; otherwise another heater's pairing may be using
        it, and arming would retarget it away from that heater (RC-2).
        """
        async with self._responder_lock:
            if not self._owns_responder:
                raise HeaterUnavailableError("No pairing of this heater is waiting")
            await responder.async_arm(self._hass, self.source, self.address, passkey)

    async def _release_responder(self) -> None:
        """Clear the responder after any PIN delivery to this pairing ended."""
        async with self._responder_lock:
            self._owns_responder = False
            await responder.async_clear(self._hass, self.source, self.address)

    async def _settle(self) -> None:
        """Wait out the heater's quiet time after its last link-down.

        Runs before the responder is armed and the proxy lock is taken, so a
        cancellation here is safe (nothing is in flight) and other heaters on
        the proxy are not blocked. A command right after a per-poll disconnect
        therefore waits up to HEATER_SETTLE_S.
        """
        last = _link_down_times(self._hass).get(self.address)
        if last is None:
            return
        remaining = HEATER_SETTLE_S - (time.monotonic() - last)
        if remaining > 0:
            _LOGGER.debug("SETTLE %s waiting %.1fs", self.address, remaining)
            await _sleep(remaining)

    def _note_link_down(self) -> None:
        """Record that this heater's link just ended (ours or the heater's)."""
        _link_down_times(self._hass)[self.address] = time.monotonic()

    async def _connect_and_pair(
        self, scanner: BaseHaScanner, device: BLEDevice, *, fresh: bool
    ) -> BaseBleakClient:
        """Connect, then run native pair() to its end; caller holds the lock."""
        assert scanner.connector is not None  # checked by route()
        client = scanner.connector.client(
            device,
            # bleak-esphome calls back on every link-down, ours included.
            disconnected_callback=lambda: self._on_disconnected(client),
            timeout=CONNECT_TIMEOUT,
        )
        _LOGGER.debug(
            "CONNECT_REQUEST %s via %s cache=%s", self.address, self.source, not fresh
        )
        started = time.monotonic()
        # Cancelling a connect while the link is forming leaves IDF 5.5.5 with a
        # "closing" GATT record and an orphan link that rejects every later
        # connect with status 133 until the proxy reboots (RC-3), so the connect
        # also runs to its native end (bounded by CONNECT_TIMEOUT).
        connect = self._hass.async_create_task(
            client.connect(pair=False, dangerous_use_bleak_cache=not fresh),
            f"dimplex_ble connect {self.address}",
        )
        try:
            await _await_to_end(connect)
        except asyncio.CancelledError:
            if connect.done() and not connect.cancelled() and not connect.exception():
                self._client, self._scanner = client, scanner
                await self._disconnect("cancelled_after_connect")
            raise
        except _GATT_ERRORS as err:
            _LOGGER.debug(
                "CONNECT_RESULT %s failed %.2fs %s: %s",
                self.address,
                time.monotonic() - started,
                type(err).__name__,
                err,
            )
            raise HeaterUnavailableError("Connect failed") from err
        _LOGGER.debug(
            "CONNECT_RESULT %s ok %.2fs", self.address, time.monotonic() - started
        )
        self._client, self._scanner = client, scanner
        try:
            await self._pair(client)
        except BaseException:
            await self._disconnect("pair_failed")
            raise
        return client

    async def _pair(self, client: BaseBleakClient) -> None:
        """Run native pair() to its native end (RC-2: never abandon SMP)."""
        _LOGGER.debug("PAIR_REQUEST %s via %s", self.address, self.source)
        started = time.monotonic()
        task = self._hass.async_create_task(
            client.pair(), f"dimplex_ble pair {self.address}"
        )
        try:
            await _await_to_end(task)
        except (BleakError, TimeoutError, NotImplementedError) as err:
            _LOGGER.debug(
                "PAIR_RESULT %s failed code=%s %.2fs %s",
                self.address,
                pair_error_code(err),
                time.monotonic() - started,
                type(err).__name__,
            )
            raise classify_pair_error(err) from err
        _LOGGER.debug(
            "PAIR_RESULT %s ok %.2fs", self.address, time.monotonic() - started
        )

    async def _disconnect(self, reason: str) -> None:
        """Release the client; the caller holds the operation lock."""
        client, self._client, self._scanner = self._client, None, None
        if client is None:
            return
        _LOGGER.debug("DISCONNECT %s reason=%s", self.address, reason)
        # A client already down had its link-down recorded by the callback.
        was_up = client.is_connected
        try:
            await client.disconnect()
        except _GATT_ERRORS as err:
            _LOGGER.debug(
                "DISCONNECT %s failed %s: %s", self.address, type(err).__name__, err
            )
        finally:
            if was_up:
                self._note_link_down()

    def _on_disconnected(self, client: BaseBleakClient) -> None:
        """Report a link-down we did not initiate; the next operation reconnects.

        ``_disconnect`` releases the client before it disconnects, so a
        link-down of the held client was ended by the heater or the proxy.
        """
        self._note_link_down()
        if client is not self._client:
            _LOGGER.debug("DISCONNECTED %s link down (released)", self.address)
            return
        _LOGGER.debug("DISCONNECTED %s link lost", self.address)
        if self.on_link_lost is not None:
            self.on_link_lost(self._scanner)
