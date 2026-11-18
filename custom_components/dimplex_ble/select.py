"""Native Home Assistant select entities for Dimplex settings."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import DimplexError
from .const import DOMAIN
from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .exceptions import to_homeassistant_error

PARALLEL_UPDATES = 1
DISPLAY_UNIT_OPTIONS = ["celsius", "fahrenheit"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the display-unit setting."""
    async_add_entities([DimplexDisplayUnitSelect(entry.runtime_data)])


class DimplexDisplayUnitSelect(DimplexEntity, SelectEntity):
    """Read and write the heater's display temperature unit."""

    _attr_translation_key = "temperature_unit"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = DISPLAY_UNIT_OPTIONS

    @property
    def current_option(self) -> str | None:
        """Return the last unit reported by the heater."""
        data = self.coordinator.data
        if data is None or data.display_unit not in (0, 1):
            return None
        return DISPLAY_UNIT_OPTIONS[data.display_unit]

    async def async_select_option(self, option: str) -> None:
        """Write the selected display unit and refresh from readback."""
        if option not in DISPLAY_UNIT_OPTIONS:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_temperature_unit",
            )
        try:
            await self._client.async_write_raw(
                0,
                0x1005,
                bytes((DISPLAY_UNIT_OPTIONS.index(option),)),
            )
            await self.coordinator.async_request_refresh()
        except DimplexError as err:
            raise to_homeassistant_error(err) from err
