"""Actions: save a timer programme schedule, and list GATT metadata."""

from __future__ import annotations

from typing import Any, cast

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
from .coordinator import SCHEDULE_PROGRAMMES, DimplexConfigEntry
from .protocol import (
    SCHEDULE_PERIODS,
    SchedulePeriod,
)

_DEVICE_ID = "device_id"
_DEVICE_SCHEMA = probatio.Schema({probatio.Required(_DEVICE_ID): cv.string})
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _whole_degrees(value: Any) -> int:
    """Accept 21 or 21.0 but reject 21.9: the heater stores whole degrees."""
    if isinstance(value, bool):
        raise probatio.Invalid("expected a whole number of degrees")
    try:
        number = float(value)
    except (TypeError, ValueError) as err:
        raise probatio.Invalid("expected a whole number of degrees") from err
    if not number.is_integer():
        raise probatio.Invalid("expected a whole number of degrees")
    return int(number)


_PERIOD_SCHEMA = probatio.Schema(
    {
        probatio.Required("start"): cv.time,
        probatio.Required("end"): cv.time,
        probatio.Required("temperature"): _whole_degrees,
    }
)
_SET_SCHEDULE_SCHEMA = probatio.Schema(
    {
        probatio.Required(_DEVICE_ID): cv.string,
        probatio.Required("programme"): probatio.In(list(SCHEDULE_PROGRAMMES)),
        probatio.Required("days"): probatio.All(
            cv.ensure_list, [probatio.In(DAYS)], probatio.Length(min=1)
        ),
        probatio.Optional("periods", default=[]): probatio.All(
            cv.ensure_list,
            [_PERIOD_SCHEMA],
            probatio.Length(max=SCHEDULE_PERIODS),
        ),
    }
)


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
        coordinator = _get_entry(call).runtime_data
        services = await coordinator.async_command(
            coordinator.link.async_gatt_services()
        )
        return cast(ServiceResponse, {"services": services})

    async def async_set_schedule(call: ServiceCall) -> None:
        """Save the same periods to the chosen days of one programme."""
        coordinator = _get_entry(call).runtime_data
        periods = tuple(
            SchedulePeriod(p["start"], p["end"], p["temperature"])
            for p in call.data["periods"]
        )
        for period in periods:
            coordinator.check_setpoint(period.temperature_c)
        await coordinator.async_save_schedule(
            call.data["programme"],
            {DAYS.index(day): periods for day in call.data["days"]},
        )

    hass.services.async_register(
        DOMAIN, "set_schedule", async_set_schedule, schema=_SET_SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "get_gatt_services",
        async_get_gatt_services,
        schema=_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
