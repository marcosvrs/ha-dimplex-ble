"""Home Assistant integration for Dimplex Bluetooth heaters."""

import asyncio

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry, format_mac
from homeassistant.helpers.typing import ConfigType

from .client import DimplexBluetoothClient
from .const import (
    CLIENTS,
    CONF_ADDRESS,
    CONF_ADDRESS_TYPE,
    CONF_PROXY_SOURCE,
    DOMAIN,
    PENDING_DEVICES,
    PLATFORMS,
)
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .discovery import is_dimplex_advertisement, is_manifest_discovery_name
from .pairing import ble_device_from_source
from .services import async_setup_services

_PENDING_DISCOVERY = "pending_discovery"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _discovery_flow_in_progress(hass: HomeAssistant, address: str) -> bool:
    """Return whether a flow already owns this Bluetooth address."""
    return any(
        isinstance(context := result.get("context"), dict)
        and isinstance(unique_id := context.get("unique_id"), str)
        and unique_id.casefold() == address.casefold()
        for result in hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, include_uninitialized=True
        )
    )


def _any_discovery_flow_in_progress(hass: HomeAssistant) -> bool:
    """Return whether any Dimplex setup flow is already active."""
    return bool(
        hass.config_entries.flow.async_progress_by_handler(
            DOMAIN, include_uninitialized=True
        )
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions and observe matching Dimplex advertisements."""
    del config
    async_setup_services(hass)
    domain_data = hass.data.setdefault(DOMAIN, {})
    pending: set[str] = domain_data.setdefault(_PENDING_DISCOVERY, set())
    clients: list[DimplexBluetoothClient] = domain_data.setdefault(CLIENTS, [])

    async def _async_shutdown_clients(event: object) -> None:
        """Close persistent heater connections before Home Assistant stops."""
        del event
        await asyncio.gather(
            *(client.async_shutdown() for client in tuple(clients)),
            return_exceptions=True,
        )

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_shutdown_clients)

    async def _async_start_discovery_flow(
        service_info: BluetoothServiceInfoBleak, address: str
    ) -> None:
        """Start one discovery flow and release the callback de-duplication key."""
        try:
            await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_BLUETOOTH},
                data=service_info,
            )
        finally:
            pending.discard(address)

    @callback
    def _async_discovered_device(
        service_info: BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Start a setup flow for a matching Dimplex advertisement."""
        del change
        name = service_info.name or service_info.device.name or ""
        if not is_dimplex_advertisement(name):
            return
        if is_manifest_discovery_name(name):
            return
        address = format_mac(service_info.device.address)
        if any(
            existing.unique_id and existing.unique_id.casefold() == address.casefold()
            for existing in hass.config_entries.async_entries(DOMAIN)
        ):
            return
        if (
            address in pending
            or _discovery_flow_in_progress(hass, address)
            or _any_discovery_flow_in_progress(hass)
        ):
            return

        pending.add(address)
        hass.async_create_task(_async_start_discovery_flow(service_info, address))

    cancel_discovery = bluetooth.async_register_callback(
        hass,
        _async_discovered_device,
        {"connectable": True},
        BluetoothScanningMode.PASSIVE,
    )
    domain_data["cancel_discovery"] = cancel_discovery
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DimplexConfigEntry) -> None:
    """Set up one heater and its native entities."""
    address = entry.data[CONF_ADDRESS]
    proxy_source = entry.data[CONF_PROXY_SOURCE]
    domain_data = hass.data.setdefault(DOMAIN, {})
    pending_devices = domain_data.setdefault(PENDING_DEVICES, {})
    device = pending_devices.pop(address, None)
    if device is None:
        device = ble_device_from_source(hass, address, proxy_source)
    if device is None:
        await bluetooth.async_request_active_scan(hass, duration=5.0)
        device = ble_device_from_source(hass, address, proxy_source)
    if device is None:
        address_type = entry.data.get(CONF_ADDRESS_TYPE, 0)
        if type(address_type) is int:
            device = BLEDevice(
                address,
                None,
                {"source": proxy_source, "address_type": address_type},
            )
    client = DimplexBluetoothClient(hass, address, proxy_source, device)

    clients: list[DimplexBluetoothClient] = domain_data.setdefault(CLIENTS, [])
    clients.append(client)
    coordinator = DimplexCoordinator(hass, entry, client)
    entry.runtime_data = coordinator

    # Config entries are created only after pairing succeeds. Require one live
    # snapshot before forwarding entities so startup retries remain meaningful.
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        clients.remove(client)
        shutdown = getattr(client, "async_shutdown", None)
        if shutdown is not None:
            await shutdown()
        raise


async def async_unload_entry(hass: HomeAssistant, entry: DimplexConfigEntry) -> bool:
    """Unload all platforms and release the heater connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    client = getattr(entry.runtime_data, "client", None)
    clients: list[DimplexBluetoothClient] = hass.data.setdefault(DOMAIN, {}).setdefault(
        CLIENTS, []
    )
    if client is not None and client in clients:
        clients.remove(client)
    await entry.runtime_data.async_shutdown()
    return unloaded


async def async_unload(hass: HomeAssistant) -> bool:
    """Stop the integration-wide Bluetooth discovery callback."""
    domain_data = hass.data.get(DOMAIN)
    if domain_data is not None:
        cancel_discovery = domain_data.pop("cancel_discovery", None)
        if cancel_discovery is not None:
            cancel_discovery()
        domain_data.pop(_PENDING_DISCOVERY, None)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    device_entry: DeviceEntry,
) -> bool:
    """Allow removing a heater device that no longer advertises."""
    del device_entry
    return not bluetooth.async_address_present(
        hass, entry.data[CONF_ADDRESS], connectable=True
    )
