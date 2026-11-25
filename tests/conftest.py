"""Shared fixtures and small fakes of the ESPHome proxy seams."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from aioesphomeapi.core import APIConnectionError
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.const import PASSKEY_REQUEST_EVENT
from custom_components.dimplex_ble.protocol import gatt_uuid_candidates

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "14:C1:9F:21:0E:6A"
PIN = "012345"


@pytest.fixture(autouse=True)
def enable_local_custom_integrations(enable_custom_integrations: None) -> None:
    """Make the local integration available to Home Assistant's loader."""


@pytest.fixture(autouse=True)
def no_heater_settle(monkeypatch) -> None:
    """Skip the 10 s heater settle wait; test_link_settle enables it."""
    monkeypatch.setattr("custom_components.dimplex_ble.link.HEATER_SETTLE_S", 0.0)


@pytest.fixture(autouse=True)
def esphome_connection_logger_at_info():
    """Keep aioesphomeapi's PIN-revealing debug logging off, as users should."""
    logger = logging.getLogger("aioesphomeapi.connection")
    level = logger.level
    logger.setLevel(logging.INFO)
    yield logger
    logger.setLevel(level)


@pytest.fixture
def skip_dependencies(hass) -> None:
    """The fakes replace the bluetooth and esphome integrations."""
    hass.config.components.update({"bluetooth", "bluetooth_adapters", "esphome"})


class FakeChar:
    """A GATT characteristic with bleak's surface."""

    def __init__(self, uuid: str, properties: list[str]) -> None:
        self.uuid = uuid
        self.properties = properties


class FakeService:
    """A GATT service with bleak's surface."""

    def __init__(self, uuid: str) -> None:
        self.uuid = uuid
        self.characteristics: list[FakeChar] = []

    def get_characteristic(self, uuid: str) -> FakeChar | None:
        return next((c for c in self.characteristics if c.uuid == uuid), None)


class FakeServices:
    """A service collection with bleak's surface."""

    def __init__(self) -> None:
        self.services: dict[str, FakeService] = {}

    def add(self, address, properties=("read", "write"), *, vendor=False):
        service_id, char_id = gatt_uuid_candidates(*address)[int(vendor)]
        service = self.services.setdefault(service_id, FakeService(service_id))
        char = FakeChar(char_id, list(properties))
        service.characteristics.append(char)
        return char

    def get_service(self, uuid: str) -> FakeService | None:
        return self.services.get(uuid)

    def __iter__(self):
        return iter(self.services.values())


def heater_services() -> FakeServices:
    services = FakeServices()
    for address in (
        (3, 0x2005),
        (0, 0x1023),
        (0, 0x1006),
        (0, 0x1001),
        (0, 0x1005),
        (0, 0x1008),
        (1, 0x0008),
        (2, 0x0005),
    ):
        services.add(address)
    return services


HEATER_VALUES = {
    (3, 0x2005): b"QRAD100E",
    (0, 0x1023): b"\x15\x00",
    (0, 0x1006): bytes((5, 0, 30, 0)),
    (0, 0x1001): bytes((2, 0, 0, 0, 0, 0)),
    (0, 0x1005): b"\x00",
    (0, 0x1008): b"\x01",
    (1, 0x0008): b"\x01\xc3\x00",
    (2, 0x0005): b"\x01\x00\x00",
}


class FakeClient:
    """ESPHomeClient stand-in: records every call into the shared event log."""

    def __init__(self, proxy: FakeProxy, device: BLEDevice, **kwargs: Any) -> None:
        self.proxy = proxy
        self.device = device
        self.disconnected_callback = kwargs["disconnected_callback"]
        self.timeout = kwargs["timeout"]
        self.is_connected = False
        self.services: FakeServices | None = None
        self.cached = True
        self.notify: dict[str, Any] = {}
        proxy.clients.append(self)

    @property
    def mtu_size(self) -> int:
        """bleak-esphome: a connect without the cache exchanges the MTU."""
        return 23 if self.cached else self.proxy.fresh_mtu

    async def start_notify(self, char: FakeChar, callback: Any) -> None:
        self.events.append(("notify", char.uuid))
        self.notify[char.uuid] = callback

    def notify_from_heater(self, uuid: str, data: bytes) -> None:
        self.notify[uuid](bytearray(data))

    @property
    def events(self) -> list[Any]:
        return self.proxy.events

    async def connect(self, pair: bool, dangerous_use_bleak_cache: bool) -> None:
        self.events.append(("connect", pair, dangerous_use_bleak_cache))
        self.cached = dangerous_use_bleak_cache
        if self.proxy.connect_gate is not None:
            await self.proxy.connect_gate
        if self.proxy.connect_error is not None:
            raise self.proxy.connect_error
        self.is_connected = True
        self.services = self.proxy.services

    async def pair(self) -> None:
        self.events.append("pair_start")
        try:
            if self.proxy.pair_gate is not None:
                await self.proxy.pair_gate
            if self.proxy.pair_errors:
                raise self.proxy.pair_errors.pop(0)
        finally:
            self.events.append("pair_end")

    async def disconnect(self) -> None:
        self.events.append("disconnect")
        self.is_connected = False
        if self.proxy.disconnect_error is not None:
            raise self.proxy.disconnect_error

    def _address(self, char: FakeChar) -> tuple[int, int] | None:
        return next(
            (
                address
                for address in self.proxy.values
                if char.uuid in {pair[1] for pair in gatt_uuid_candidates(*address)}
            ),
            None,
        )

    async def read_gatt_char(self, char: FakeChar) -> bytearray:
        self.events.append(("read", char.uuid))
        if self.proxy.read_error is not None:
            raise self.proxy.read_error
        if self.proxy.read_gate is not None:
            await self.proxy.read_gate
        return bytearray(self.proxy.values.get(self._address(char), b""))

    async def write_gatt_char(self, char: FakeChar, data: bytes, response: bool):
        self.events.append(("write", char.uuid, bytes(data), response))
        address = self._address(char)
        schedule = (0, 0x1002)
        if schedule in self.proxy.values and address == schedule:
            if data[0] == 1 and self.proxy.saves_schedules:
                # [1, p, d, ...] saves that day; it reads back with byte 0 = 0.
                self.proxy.schedule[(data[1], data[2])] = b"\x00" + bytes(data[1:])
            # The heater returns the (programme, day) record a [0, p, d] write selects.
            self.proxy.values[schedule] = self.proxy.schedule.get(
                (data[1], data[2]), bytes(31)
            )
        if self.proxy.on_write is not None:
            self.proxy.on_write(address, bytes(data))


class FakeScanner:
    """habluetooth scanner stand-in whose connector builds FakeClients."""

    def __init__(self, proxy: FakeProxy) -> None:
        self.source = proxy.source
        self.name = f"proxy {proxy.source}"
        self.slots = 3
        self.free: int | None = 3
        self.allocated: list[str] = []
        self.discovered_device_timestamps: dict[str, float] = {}
        self.connector = SimpleNamespace(
            client=lambda device, **kwargs: FakeClient(proxy, device, **kwargs)
        )

    def get_allocations(self):
        if self.free is None:
            return None
        return SimpleNamespace(
            slots=self.slots, free=self.free, allocated=list(self.allocated)
        )


class FakeApiClient:
    """aioesphomeapi APIClient stand-in recording executed actions."""

    def __init__(self, proxy: FakeProxy) -> None:
        self.proxy = proxy
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def execute_service(self, service, data):
        if not self.proxy.api_connected:
            raise APIConnectionError("not connected")
        self.calls.append((service.name, dict(data)))
        self.proxy.events.append(("action", service.name, dict(data)))
        if service.name == "dimplex_pin_arm" and self.proxy.on_arm is not None:
            self.proxy.on_arm(data)


class FakeProxy:
    """One ESPHome Bluetooth proxy: config entry runtime data plus scanner."""

    def __init__(self, source: str = PROXY, *, responder: bool = True) -> None:
        self.source = source
        self.events: list[Any] = []
        self.clients: list[FakeClient] = []
        self.services = heater_services()
        self.values = dict(HEATER_VALUES)
        # (programme, day) -> 31-byte schedule record, once 0/0x1002 is added.
        self.schedule: dict[tuple[int, int], bytes] = {}
        self.saves_schedules = True
        # Called with (address, payload) after every write, to emulate a device.
        self.on_write: Any = None
        self.connect_error: BaseException | None = None
        self.connect_gate: asyncio.Future[None] | None = None
        self.pair_errors: list[BaseException] = []
        self.pair_gate: asyncio.Future[None] | None = None
        self.read_error: BaseException | None = None
        self.read_gate: asyncio.Future[None] | None = None
        self.disconnect_error: BaseException | None = None
        self.api_connected = True
        self.on_arm = None
        self.sees_heater = True
        self.fresh_mtu = 517
        self.rssi = -60
        self.entry_id = f"esphome_{source}"
        self.scanner = FakeScanner(self)
        self.api = FakeApiClient(self)
        names = ["dimplex_pin_arm", "dimplex_pin_clear"] if responder else []
        self.runtime = SimpleNamespace(
            client=self.api,
            services={
                key: SimpleNamespace(name=name, key=key)
                for key, name in enumerate(names)
            },
            available=True,
            device_info=SimpleNamespace(bluetooth_mac_address=source, mac_address="x"),
        )

    def reconnect_api(self) -> None:
        """Simulate an API reconnect: ESPHome registers a new scanner object."""
        self.scanner = FakeScanner(self)

    @property
    def actions(self) -> list[tuple[str, dict[str, Any]]]:
        return self.api.calls


class FakeBluetooth:
    """Patches HA's bluetooth API and the ESPHome entry lookup with FakeProxies."""

    def __init__(self, hass, monkeypatch) -> None:
        self.hass = hass
        self.proxies: list[FakeProxy] = []
        monkeypatch.setattr(
            "custom_components.dimplex_ble.config_flow.ADVERTISEMENT_WAIT", 0.05
        )
        monkeypatch.setattr(
            "custom_components.dimplex_ble.config_flow.DISCOVERY_WINDOW", 0.05
        )
        self.callbacks: list[Any] = []
        monkeypatch.setattr(
            "homeassistant.components.bluetooth.async_scanner_by_source",
            self.scanner_by_source,
        )
        monkeypatch.setattr(
            "homeassistant.components.bluetooth.async_scanner_devices_by_address",
            self.devices_by_address,
        )
        monkeypatch.setattr(
            "homeassistant.components.bluetooth.async_register_advertisement_callback",
            self.register_advertisement_callback,
        )
        monkeypatch.setattr(
            "homeassistant.components.bluetooth.async_current_scanners",
            lambda hass: [proxy.scanner for proxy in self.proxies],
        )
        self.hass = hass
        loaded_entries = hass.config_entries.async_loaded_entries
        monkeypatch.setattr(
            hass.config_entries,
            "async_loaded_entries",
            lambda domain: (
                [
                    SimpleNamespace(entry_id=proxy.entry_id, runtime_data=proxy.runtime)
                    for proxy in self.proxies
                ]
                if domain == "esphome"
                else loaded_entries(domain)
            ),
        )

    def add(self, proxy: FakeProxy | None = None) -> FakeProxy:
        proxy = proxy or FakeProxy()
        self.proxies.append(proxy)
        return proxy

    def scanner_by_source(self, hass, source):
        return next((p.scanner for p in self.proxies if p.source == source), None)

    def devices_by_address(self, hass, address, connectable=True):
        return [
            SimpleNamespace(
                scanner=p.scanner,
                ble_device=BLEDevice(
                    address, "AM6532", {"source": p.source, "address_type": 0}
                ),
                advertisement=SimpleNamespace(rssi=p.rssi),
            )
            for p in self.proxies
            if p.sees_heater
        ]

    def register_advertisement_callback(self, hass, callback, address):
        """habluetooth: fire on every advertisement from ``address``."""
        registration = (callback, address)
        self.callbacks.append(registration)
        # A heater no link holds advertises continuously: every proxy that
        # can hear it reports it shortly after anyone starts listening.
        for proxy in self.proxies:
            if proxy.sees_heater:
                self.hass.loop.call_soon(self.advertise, proxy.source, address)
        return lambda: self.callbacks.remove(registration)

    def advertise(self, source: str, address: str = ADDRESS) -> None:
        """One advertisement heard by ``source``, identical data included.

        Like habluetooth, the scanner records the sighting first (what
        async_scanner_devices_by_address reads), then the manager runs the
        per-advertisement callbacks before updating its own history.
        """
        from custom_components.dimplex_ble import stall

        for proxy in self.proxies:
            if proxy.source == source:
                proxy.sees_heater = True
                # habluetooth's per-scanner history of the last sighting
                proxy.scanner.discovered_device_timestamps[address] = stall.monotonic()
        for callback, registered in list(self.callbacks):
            if registered == address:
                callback(SimpleNamespace(source=source, address=address))

    def device_id(self, proxy: FakeProxy) -> str:
        """The device ESPHome registers for the proxy's config entry."""
        if self.hass.config_entries.async_get_entry(proxy.entry_id) is None:
            MockConfigEntry(domain="esphome", entry_id=proxy.entry_id).add_to_hass(
                self.hass
            )
        return (
            dr.async_get(self.hass)
            .async_get_or_create(
                config_entry_id=proxy.entry_id,
                identifiers={("esphome", proxy.source)},
            )
            .id
        )

    def passkey_request(
        self, proxy: FakeProxy, address: str = ADDRESS, device_id: str | None = None
    ) -> None:
        """The firmware's event: the heater asked this proxy for the passkey."""
        self.hass.bus.async_fire(
            PASSKEY_REQUEST_EVENT,
            {
                "address": address,
                "device_id": device_id or self.device_id(proxy),
            },
        )


@pytest.fixture
def fake_bt(hass, monkeypatch) -> FakeBluetooth:
    """Bluetooth with a configurable set of fake ESPHome proxies."""
    return FakeBluetooth(hass, monkeypatch)


def pair_failure(code: int) -> BleakError:
    """The exact error bleak-esphome 4.0.0 raises for a failed pairing."""
    return BleakError(f"Pairing failed due to error: {code}")


def gatt_failure(att: int) -> BleakError:
    """A GATT error carrying the ATT status in ``__cause__`` like bleak-esphome."""
    cause = Exception(f"GATT error {att}")
    cause.error = SimpleNamespace(error=att)  # type: ignore[attr-defined]
    err = BleakError(str(cause))
    err.__cause__ = cause
    return err
