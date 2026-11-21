"""Diagnostics for one Dimplex heater, with Bluetooth addresses redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_PROXY_SOURCE
from .coordinator import DimplexConfigEntry
from .esphome_proxy import find_proxy_entry, proxy_supports_passkey
from .pairing import ble_device_from_source


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DimplexConfigEntry
) -> dict[str, Any]:
    """Return runtime and bond-owner diagnostics without disclosing addresses."""
    snapshot = entry.runtime_data.data
    snapshot_data = asdict(snapshot) if snapshot is not None else None
    if snapshot_data is not None:
        snapshot_data["model"] = "REDACTED"
    proxy_source = entry.data.get(CONF_PROXY_SOURCE)
    address = entry.data.get(CONF_ADDRESS)
    if isinstance(proxy_source, str) and proxy_source:
        proxy_entry_loaded = find_proxy_entry(hass, proxy_source) is not None
        passkey_relay_supported = proxy_supports_passkey(hass, proxy_source)
    else:
        proxy_entry_loaded = False
        passkey_relay_supported = False
    route_available = (
        ble_device_from_source(hass, address, proxy_source) is not None
        if isinstance(address, str)
        and address
        and isinstance(proxy_source, str)
        and proxy_source
        else False
    )
    return {
        "entry": {
            "address": "REDACTED",
            "proxy_source": "REDACTED",
        },
        "bond_owner": {
            "configured": isinstance(proxy_source, str) and bool(proxy_source),
            "proxy_entry_loaded": proxy_entry_loaded,
            "route_available": route_available,
            "passkey_relay_supported": passkey_relay_supported,
        },
        "coordinator": {
            "last_update_success": entry.runtime_data.last_update_success,
            "data": snapshot_data,
        },
    }
