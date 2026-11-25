"""Native Home Assistant datetime controls for a Dimplex panel heater."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Away return-time control."""
    del hass
    async_add_entities([DimplexAwayUntil(entry.runtime_data)])


class DimplexAwayUntil(DimplexEntity, DateTimeEntity):
    """Expose the Dimplex Away Mode return deadline."""

    _attr_translation_key = "away_until"
    _unique_suffix = "away_until"
    # Needed to select Away; existing installs keep their registry setting.
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Require a current panel-heater snapshot before accepting a deadline."""
        return super().available and self.coordinator.data is not None

    @property
    def native_value(self) -> datetime | None:
        """Return the device's local Away deadline as an aware datetime."""
        data = self.coordinator.data
        # The heater keeps the last deadline after Away ends; a past or idle
        # deadline would mislead, so only an active Away shows its return time.
        if data is None or not data.away_active or data.away_until is None:
            return None
        return data.away_until.replace(tzinfo=dt_util.get_default_time_zone())

    async def async_set_value(self, value: datetime) -> None:
        """Set the deadline and activate Away Mode explicitly."""
        await self.coordinator.async_set_away_mode(until=value)
