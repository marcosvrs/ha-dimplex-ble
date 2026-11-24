"""Home Assistant diagnostics service for Dimplex GATT metadata."""

from __future__ import annotations

from typing import cast

import probatio
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .const import DOMAIN
from .coordinator import DimplexConfigEntry
from .errors import DimplexError, to_homeassistant_error

_DEVICE_ID = "device_id"
_DEVICE_SCHEMA = probatio.Schema({probatio.Required(_DEVICE_ID): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register actions once, independently of config-entry lifetimes."""

    @callback
    def _get_entry(call: ServiceCall) -> DimplexConfigEntry:
        """Resolve a selected panel-heater device to its loaded config entry."""
        _, entry = service.async_get_device_and_config_entry(
            hass, DOMAIN, call.data[_DEVICE_ID]
        )
        return cast(DimplexConfigEntry, entry)

    async def async_get_gatt_services(call: ServiceCall) -> ServiceResponse:
        """Return service/characteristic UUIDs and properties."""
        entry = _get_entry(call)
        try:
            return cast(
                ServiceResponse,
                {"services": await entry.runtime_data.link.async_gatt_services()},
            )
        except DimplexError as err:
            raise to_homeassistant_error(err) from err

    hass.services.async_register(
        DOMAIN,
        "get_gatt_services",
        async_get_gatt_services,
        schema=_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
