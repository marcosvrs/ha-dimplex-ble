"""Diagnostic sensors for panel-heater-provided values; raw payloads are in diagnostics."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .protocol import HEATING_MODE_KEYS, PRESET_AWAY, error_code_state, heating_mode_key

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    del hass

    async_add_entities(
        [
            DimplexErrorCodeSensor(entry.runtime_data),
            DimplexOperatingModeSensor(entry.runtime_data),
            DimplexRatedPowerSensor(entry.runtime_data),
            DimplexLcdTemperatureSensor(entry.runtime_data),
            DimplexOutputPowerSensor(entry.runtime_data),
            DimplexCalculatedPowerSensor(entry.runtime_data),
        ]
    )


class DimplexErrorCodeSensor(DimplexEntity, SensorEntity):
    """Expose the error status: documented codes by name, others as ``Code N``."""

    _attr_translation_key = "error_code"
    _unique_suffix = "error_code"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return the translatable state of the panel heater's error code."""
        data = self.coordinator.data
        return error_code_state(data.error_code if data is not None else None)


class DimplexOperatingModeSensor(DimplexEntity, SensorEntity):
    """Expose the documented operating mode; unknown payloads are unknown."""

    _attr_translation_key = "operating_mode"
    _unique_suffix = "operating_mode_raw"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(HEATING_MODE_KEYS)
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> str | None:
        """Return the mode the panel heater reports (Away when active)."""
        data = self.coordinator.data
        if data is None:
            return None
        if data.away_active:
            return PRESET_AWAY
        if data.operating_mode_hex is None:
            return None
        return heating_mode_key(bytes.fromhex(data.operating_mode_hex))


class DimplexRatedPowerSensor(DimplexEntity, SensorEntity):
    """Expose the first PowerLoading word as the panel heater's rated power."""

    _attr_translation_key = "rated_power"
    _unique_suffix = "rated_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def native_value(self) -> int | None:
        """Return the documented rated power in watts."""
        data = self.coordinator.data
        return data.rated_power_w if data is not None else None


class DimplexLcdTemperatureSensor(DimplexEntity, SensorEntity):
    """Expose the controller LCD thermistor reading for diagnostics."""

    _attr_translation_key = "lcd_temperature"
    _unique_suffix = "lcd_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> float | None:
        """Return the selected LCD thermistor temperature."""
        data = self.coordinator.data
        return data.lcd_temperature_c if data is not None else None


class DimplexOutputPowerSensor(DimplexEntity, SensorEntity):
    """Expose the PLX report-screen output-power percentage."""

    _attr_translation_key = "output_power"
    _unique_suffix = "output_power"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Return the Trac output-power percentage."""
        data = self.coordinator.data
        return data.trac_output_power_percent if data is not None else None


class DimplexCalculatedPowerSensor(DimplexEntity, SensorEntity):
    """Expose the calculated electrical power consumption."""

    _attr_translation_key = "calculated_power_consumption"
    _unique_suffix = "calculated_power_consumption"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Calculate power from output percentage and rated power."""
        data = self.coordinator.data
        if (
            data is None
            or data.rated_power_w is None
            or data.trac_output_power_percent is None
        ):
            return None
        return data.rated_power_w * data.trac_output_power_percent / 100
