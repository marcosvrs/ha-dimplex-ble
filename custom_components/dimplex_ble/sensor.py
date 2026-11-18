"""Diagnostic sensors that preserve heater-provided values."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up error and raw operating-mode diagnostics."""
    async_add_entities(
        [
            DimplexErrorCodeSensor(entry.runtime_data),
            DimplexOperatingModeSensor(entry.runtime_data),
        ]
    )


class DimplexErrorCodeSensor(DimplexEntity, SensorEntity):
    """Expose every error status as its raw numeric code, including unknown codes."""

    _attr_translation_key = "error_code"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> int | None:
        """Return the exact numeric error status from the heater."""
        data = self.coordinator.data
        return data.error_code if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Preserve the error status bytes alongside its numeric value."""
        data = self.coordinator.data
        if data is None or data.error_status_hex is None:
            return None
        return {"raw_bytes": data.error_status_hex}


class DimplexOperatingModeSensor(DimplexEntity, SensorEntity):
    """Expose operating-mode bytes without assigning unverified meanings."""

    _attr_translation_key = "operating_mode_raw"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        """Return the exact mode payload as hexadecimal bytes."""
        data = self.coordinator.data
        return data.operating_mode_hex if data is not None else None
