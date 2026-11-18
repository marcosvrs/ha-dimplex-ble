"""Home Assistant integration for Dimplex Bluetooth heaters."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.device_registry import DeviceEntry, format_mac
from homeassistant.helpers.typing import ConfigType

from .client import DimplexBluetoothClient
from .const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN, PLATFORMS
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .discovery import is_dimplex_advertisement
from .services import async_setup_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions and observe matching Dimplex advertisements."""
    async_setup_services(hass)

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

        address = format_mac(service_info.device.address)
        if any(
            existing.unique_id
            and existing.unique_id.casefold() == address.casefold()
            for existing in hass.config_entries.async_entries(DOMAIN)
        ):
            return
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_BLUETOOTH},
                data=service_info,
            )
        )

    cancel_discovery = bluetooth.async_register_callback(
        hass,
        _async_discovered_device,
        {"connectable": True},
        BluetoothScanningMode.PASSIVE,
    )
    hass.data.setdefault(DOMAIN, {})["cancel_discovery"] = cancel_discovery
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: DimplexConfigEntry
) -> None:
    """Set up one heater and its native entities."""
    client = DimplexBluetoothClient(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data[CONF_PROXY_SOURCE],
    )
    coordinator = DimplexCoordinator(hass, entry, client)
    entry.runtime_data = coordinator

    # Heaters may be out of range or await bonding during setup. Keep entities
    # registered and let the coordinator mark them unavailable until reachable.
    await coordinator.async_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(
    hass: HomeAssistant, entry: DimplexConfigEntry
) -> bool:
    """Unload all platforms for one heater."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_unload(hass: HomeAssistant) -> bool:
    """Stop the integration-wide Bluetooth discovery callback."""
    domain_data = hass.data.get(DOMAIN)
    if domain_data is not None:
        cancel_discovery = domain_data.pop("cancel_discovery", None)
        if cancel_discovery is not None:
            cancel_discovery()
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
