"""Home Assistant actions for raw Dimplex GATT access."""

from __future__ import annotations

from typing import cast

import probatio
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .client import DimplexError
from .const import DOMAIN
from .coordinator import DimplexConfigEntry
from .exceptions import to_homeassistant_error
from .protocol import DimplexProtocolError, parse_hex_payload

_DEVICE_ID = "device_id"
_SERVICE_GROUP = "service_group"
_CHARACTERISTIC = "characteristic"
_PAYLOAD = "payload"

_READ_SCHEMA = probatio.Schema(
    {
        probatio.Required(_DEVICE_ID): cv.string,
        probatio.Required(_SERVICE_GROUP): vol.All(int, vol.Range(min=0, max=3)),
        probatio.Required(_CHARACTERISTIC): vol.All(int, vol.Range(min=0, max=0xFFFF)),
    }
)
_WRITE_SCHEMA = probatio.Schema(
    {
        probatio.Required(_DEVICE_ID): cv.string,
        probatio.Required(_SERVICE_GROUP): vol.All(int, vol.Range(min=0, max=3)),
        probatio.Required(_CHARACTERISTIC): vol.All(int, vol.Range(min=0, max=0xFFFF)),
        probatio.Required(_PAYLOAD): cv.string,
    }
)
_DEVICE_SCHEMA = probatio.Schema({probatio.Required(_DEVICE_ID): cv.string})


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register actions once, independently of config-entry lifetimes."""

    @callback
    def _get_entry(call: ServiceCall) -> DimplexConfigEntry:
        """Resolve a selected heater device to its loaded config entry."""
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
                {
                    "services": await entry.runtime_data.client.async_get_gatt_services()
                },
            )
        except DimplexError as err:
            raise to_homeassistant_error(err) from err

    async def async_read_gatt(call: ServiceCall) -> ServiceResponse:
        """Read a selected, non-protected Dimplex characteristic."""
        entry = _get_entry(call)
        group = call.data[_SERVICE_GROUP]
        characteristic = call.data[_CHARACTERISTIC]
        try:
            value = await entry.runtime_data.client.async_read_raw(
                group, characteristic
            )
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err
        return {
            _SERVICE_GROUP: group,
            _CHARACTERISTIC: characteristic,
            "value": value.hex(),
        }

    async def async_write_gatt(call: ServiceCall) -> ServiceResponse:
        """Write a selected characteristic and return its delayed readback."""
        entry = _get_entry(call)
        group = call.data[_SERVICE_GROUP]
        characteristic = call.data[_CHARACTERISTIC]
        try:
            payload = parse_hex_payload(call.data[_PAYLOAD])
            readback = await entry.runtime_data.client.async_write_raw(
                group, characteristic, payload
            )
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err
        return {
            _SERVICE_GROUP: group,
            _CHARACTERISTIC: characteristic,
            "written": payload.hex(),
            "readback": readback.hex() if readback is not None else None,
        }

    hass.services.async_register(
        DOMAIN,
        "get_gatt_services",
        async_get_gatt_services,
        schema=_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "read_gatt",
        async_read_gatt,
        schema=_READ_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    service.async_register_admin_service(
        hass,
        DOMAIN,
        "write_gatt",
        async_write_gatt,
        schema=_WRITE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
