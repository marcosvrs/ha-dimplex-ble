"""Diagnostics for one Dimplex panel heater; addresses and the PIN are redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_ADDRESS, CONF_PASSKEY, CONF_PROXY_SOURCE
from .coordinator import DimplexConfigEntry
from .responder import has_responder

# The model is not personal data and is the first thing support needs.
TO_REDACT = {CONF_ADDRESS, CONF_PASSKEY, CONF_PROXY_SOURCE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DimplexConfigEntry
) -> dict[str, Any]:
    """Return link and coordinator state without secrets."""
    coordinator = entry.runtime_data
    link = coordinator.link
    snapshot = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "link": {
            "route_seen": link.route() is not None,
            "connected": link.connected,
            "mode": "persistent" if link.keep_connected else "per_poll",
            "responder": has_responder(hass, link.source),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": type(coordinator.last_exception).__name__
            if coordinator.last_exception
            else None,
            "data": async_redact_data(asdict(snapshot), TO_REDACT)
            if snapshot is not None
            else None,
        },
    }
