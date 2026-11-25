"""Home Assistant integration for Dimplex Bluetooth panel heaters."""

from __future__ import annotations

import logging

from homeassistant.components import bluetooth
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import responder
from .const import (
    CONF_ADDRESS,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    CONF_SYNC_CLOCK,
    DEFAULT_SYNC_CLOCK,
    DOMAIN,
    PASSKEY_REQUEST_EVENT,
    PLATFORMS,
    SCHEDULE_UPDATE_INTERVAL,
    SETTINGS_UPDATE_INTERVAL,
)
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .entity import device_info_for_coordinator
from .link import HeaterLink
from .schedule_keeper import STORAGE_VERSION, pending_issue_id
from .services import async_setup_services
from .stall import silent_since, stall_issue_id

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions."""
    del config
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DimplexConfigEntry) -> bool:
    """Set up one panel heater on its pinned proxy."""
    link = HeaterLink(
        hass,
        entry.data[CONF_ADDRESS],
        entry.data[CONF_PROXY_SOURCE],
        entry.data.get(CONF_PASSKEY),
        sync_clock=entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK),
    )
    coordinator = DimplexCoordinator(hass, entry, link)
    entry.runtime_data = coordinator
    await coordinator.schedule_keeper.async_load()

    async def _async_close_on_stop(event: Event) -> None:
        """Close the link cleanly so the proxy does not keep it (core#187087)."""
        del event
        await link.async_close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_close_on_stop)
    )
    if not entry.data.get(CONF_PASSKEY):

        @callback
        def _on_passkey_request(event: Event) -> None:
            """No PIN is stored, so nothing can answer: ask the user for it.

            The entry was set up on a proxy that already held the bond; once
            that bond is gone the heater asks for its PIN on every connect.
            """
            if str(
                event.data.get("address", "")
            ).upper() == link.address and responder.is_proxy_device(
                hass, link.source, event.data.get("device_id")
            ):
                entry.async_start_reauth(hass)

        entry.async_on_unload(
            hass.bus.async_listen(PASSKEY_REQUEST_EVENT, _on_passkey_request)
        )
    entry.async_on_unload(
        bluetooth.async_register_advertisement_callback(
            # habluetooth keys addresses upper-case, as HeaterLink stores it.
            hass,
            coordinator.async_handle_advertisement,
            link.address,
        )
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, coordinator.async_settings_due, SETTINGS_UPDATE_INTERVAL
        )
    )
    entry.async_on_unload(
        async_track_time_interval(
            hass, coordinator.async_schedules_due, SCHEDULE_UPDATE_INTERVAL
        )
    )

    # Register the device before the first refresh, so a setup retry after a
    # transient BLE outage still offers the device's name and area step.
    device_info = device_info_for_coordinator(coordinator)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=device_info["identifiers"],
        connections=device_info["connections"],
        manufacturer=device_info["manufacturer"],
        name=device_info["name"],
    )
    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DimplexConfigEntry) -> bool:
    """Stop polling, wait for in-flight work, then close the link once."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator = entry.runtime_data
        await coordinator.async_shutdown()
        await coordinator.link.async_close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: DimplexConfigEntry) -> None:
    """Drop the entry's repairs and its schedule backup together with the entry."""
    ir.async_delete_issue(hass, DOMAIN, stall_issue_id(entry.entry_id))
    silent_since(hass).pop(stall_issue_id(entry.entry_id), None)
    ir.async_delete_issue(hass, DOMAIN, pending_issue_id(entry.entry_id))
    await Store(
        hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.schedules"
    ).async_remove()


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: DimplexConfigEntry, device_entry: DeviceEntry
) -> bool:
    """Allow removing a panel heater device that no longer advertises."""
    del device_entry
    return not bluetooth.async_address_present(
        hass, str(entry.data[CONF_ADDRESS]).upper(), connectable=True
    )
