"""Temperature settings of a Dimplex panel heater."""

from __future__ import annotations

from homeassistant.components.climate import DEFAULT_MAX_TEMP, DEFAULT_MIN_TEMP
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .protocol import (
    AWAY_TEMPERATURE_MAX_C,
    AWAY_TEMPERATURE_MIN_C,
    ROOM_TEMPERATURE_OFFSET,
    SETBACK,
    SETBACK_TEMPERATURE_MAX_C,
    SETBACK_TEMPERATURE_MIN_C,
    TEMPERATURE_OFFSET_LIMIT_C,
    parse_setback_temperature,
    parse_temperature_offset,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the panel heater's temperature settings."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        [
            DimplexAwayTemperatureNumber(coordinator),
            DimplexBoostTemperatureNumber(coordinator),
            DimplexSetbackTemperatureNumber(coordinator),
            DimplexTemperatureOffsetNumber(coordinator),
        ]
    )


class _DimplexTemperatureNumber(DimplexEntity, NumberEntity):
    """A whole-degree Celsius setting."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        """Require a value reported by the heater."""
        return super().available and self.native_value is not None


class DimplexAwayTemperatureNumber(_DimplexTemperatureNumber):
    """The temperature the heater holds while Away is on."""

    _attr_translation_key = "away_temperature"
    _unique_suffix = "away_temperature"
    _attr_native_min_value = AWAY_TEMPERATURE_MIN_C
    _attr_native_max_value = AWAY_TEMPERATURE_MAX_C

    @property
    def native_value(self) -> float | None:
        """Return the Away target reported by the heater."""
        data = self.coordinator.data
        if data is None or data.away_target_temperature_c is None:
            return None
        return float(data.away_target_temperature_c)

    async def async_set_native_value(self, value: float) -> None:
        """Write the Away target."""
        await self.coordinator.async_set_away_temperature(value)


class DimplexBoostTemperatureNumber(_DimplexTemperatureNumber):
    """The temperature the Boost preset heats to (the heater's own setting)."""

    _attr_translation_key = "boost_temperature"
    _unique_suffix = "boost_temperature"

    @property
    def native_value(self) -> float | None:
        """Return the Boost temperature reported by the heater."""
        data = self.coordinator.data
        if data is None or data.boost_temperature_c is None:
            return None
        return float(data.boost_temperature_c)

    @property
    def native_min_value(self) -> float:
        """The heater's lowest setpoint (ConfigR clamps Boost to the range)."""
        data = self.coordinator.data
        low = data.minimum_temperature_c if data is not None else None
        return float(low) if low is not None else DEFAULT_MIN_TEMP

    @property
    def native_max_value(self) -> float:
        """The heater's highest setpoint."""
        data = self.coordinator.data
        high = data.maximum_temperature_c if data is not None else None
        return float(high) if high is not None else DEFAULT_MAX_TEMP

    async def async_set_native_value(self, value: float) -> None:
        """Write the Boost temperature."""
        await self.coordinator.async_set_boost_temperature(value)


class DimplexSetbackTemperatureNumber(_DimplexTemperatureNumber):
    """What Setback holds during Heating Off periods of a timer programme."""

    _attr_translation_key = "setback_temperature"
    _unique_suffix = "setback_temperature"
    _attr_native_min_value = SETBACK_TEMPERATURE_MIN_C
    _attr_native_max_value = SETBACK_TEMPERATURE_MAX_C

    @property
    def native_value(self) -> float | None:
        """Return the Setback temperature last read from the heater."""
        value = parse_setback_temperature(self.coordinator.settings.get(SETBACK))
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        """Write the Setback temperature, keeping Setback on or off."""
        await self.coordinator.async_set_setback_temperature(value)


class DimplexTemperatureOffsetNumber(DimplexEntity, NumberEntity):
    """Correction added to the room reading (installer setting)."""

    _attr_translation_key = "temperature_offset"
    _unique_suffix = "temperature_offset"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    # A difference, not a temperature: no device class, so Home Assistant never
    # converts it like an absolute temperature.
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = -TEMPERATURE_OFFSET_LIMIT_C
    _attr_native_max_value = TEMPERATURE_OFFSET_LIMIT_C
    _attr_native_step = 0.1
    _attr_mode = NumberMode.BOX

    @property
    def available(self) -> bool:
        """Require a valid slow-refreshed offset."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | None:
        """Return the offset reported by the heater."""
        return parse_temperature_offset(
            self.coordinator.settings.get(ROOM_TEMPERATURE_OFFSET)
        )

    async def async_set_native_value(self, value: float) -> None:
        """Write the offset."""
        await self.coordinator.async_set_temperature_offset(value)
