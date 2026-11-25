"""Actions: read, save and restore timer programme schedules; GATT metadata."""

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
from .schedule_keeper import day_to_json

_DEVICE_ID = "device_id"
_DEVICE_SCHEMA = probatio.Schema({probatio.Required(_DEVICE_ID): cv.string})
DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


_PERIOD_SCHEMA = probatio.Schema(
    {
        probatio.Required("start"): cv.time,
        probatio.Required("end"): cv.time,
        probatio.Required("temperature"): probatio.Coerce(float),
    }
)
_SET_SCHEDULE_SCHEMA = probatio.Schema(
    {
        probatio.Required(_DEVICE_ID): cv.string,
        probatio.Required("programme"): probatio.In(list(SCHEDULE_PROGRAMMES)),
        probatio.Required("days"): probatio.All(
            cv.ensure_list, [probatio.In(DAYS)], probatio.Length(min=1)
        ),
        # Required: leaving it out must not wipe the days; [] clears them.
        probatio.Required("periods"): probatio.All(
            cv.ensure_list,
            [_PERIOD_SCHEMA],
            probatio.Length(max=SCHEDULE_PERIODS),
        ),
    }
)


_PROGRAMME_SCHEMA = probatio.Schema(
    {
        probatio.Required(_DEVICE_ID): cv.string,
        probatio.Optional("programme"): probatio.In(list(SCHEDULE_PROGRAMMES)),
    }
)


def _week_json(week: list[tuple[SchedulePeriod, ...] | None]) -> dict[str, Any]:
    """Name the days; an unknown day is None."""
    return {
        name: None if periods is None else day_to_json(periods)
        for name, periods in zip(DAYS, week, strict=True)
    }


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
        # Whole degrees within the heater's range, as for the climate target.
        periods = tuple(
            SchedulePeriod(
                p["start"], p["end"], coordinator.check_setpoint(p["temperature"])
            )
            for p in call.data["periods"]
        )
        await coordinator.async_save_schedule(
            call.data["programme"],
            {DAYS.index(day): periods for day in call.data["days"]},
        )

    async def async_get_schedule(call: ServiceCall) -> ServiceResponse:
        """Return each programme as the heater holds it, waiting and backed up."""
        coordinator = _get_entry(call).runtime_data
        keeper = coordinator.schedule_keeper
        wanted = call.data.get("programme")
        return cast(
            ServiceResponse,
            {
                preset: {
                    "heater": _week_json(coordinator.schedules[preset])
                    if preset in coordinator.schedules
                    else None,
                    "pending": {
                        DAYS[day]: day_to_json(periods)
                        for day, periods in sorted(
                            keeper.pending.get(preset, {}).items()
                        )
                    },
                    "backup": _week_json(keeper.backup[preset])
                    if preset in keeper.backup
                    else None,
                }
                for preset in SCHEDULE_PROGRAMMES
                if wanted in (None, preset)
            },
        )

    async def async_restore_schedule(call: ServiceCall) -> None:
        """Write the backup copy back to the heater (queued if unreachable)."""
        await _get_entry(call).runtime_data.async_restore_schedule(
            call.data.get("programme")
        )

    hass.services.async_register(
        DOMAIN, "set_schedule", async_set_schedule, schema=_SET_SCHEDULE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        "get_schedule",
        async_get_schedule,
        schema=_PROGRAMME_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        "restore_schedule",
        async_restore_schedule,
        schema=_PROGRAMME_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        "get_gatt_services",
        async_get_gatt_services,
        schema=_DEVICE_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
