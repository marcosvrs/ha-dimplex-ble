"""Diagnostic sensors for panel-heater-provided values; raw payloads are in diagnostics."""

from datetime import datetime, timedelta
from decimal import Decimal
from time import monotonic

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval

from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .entity import DimplexEntity
from .protocol import (
    HEATING_MODE_KEYS,
    PRESET_AWAY,
    HeaterSnapshot,
    error_code_state,
    heating_mode_of,
)

PARALLEL_UPDATES = 1
# The energy total grows in steps of this while the heater's output is unchanged.
ENERGY_STEP = timedelta(minutes=1)


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
            DimplexEstimatedEnergySensor(entry.runtime_data),
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
        return heating_mode_of(data.operating_mode_hex)


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

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "calculated_power_consumption"
    _unique_suffix = "calculated_power_consumption"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    @property
    def native_value(self) -> float | None:
        """Calculate power from output percentage and rated power."""
        return estimated_power_w(self.coordinator.data)


def estimated_power_w(data: HeaterSnapshot | None) -> float | None:
    """Rated power x output percentage; the heater has no power meter."""
    if (
        data is None
        or data.rated_power_w is None
        or data.trac_output_power_percent is None
    ):
        return None
    return data.rated_power_w * data.trac_output_power_percent / 100


class DimplexEstimatedEnergySensor(DimplexEntity, RestoreSensor):
    """Energy from the estimated power over time, for the Energy dashboard.

    The heater has no energy counter. The power reported at one poll holds
    until the next (the output only changes in steps), so each interval counts
    with the power at its start. Time without a successful poll (heater
    unreachable, Home Assistant stopped) is not counted.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "estimated_energy"
    _unique_suffix = "estimated_energy"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 3

    def __init__(self, coordinator: DimplexCoordinator) -> None:
        """Start from zero until the stored total is restored."""
        super().__init__(coordinator)
        self._total_kwh = 0.0
        self._since: tuple[float, float] | None = None  # (monotonic, watts)

    async def async_added_to_hass(self) -> None:
        """Restore the total, then count on every poll and every minute."""
        await super().async_added_to_hass()
        last = await self.async_get_last_sensor_data()
        if last is not None and isinstance(last.native_value, (int, float, Decimal)):
            self._total_kwh = float(last.native_value)
        self._count()
        self.async_on_remove(
            async_track_time_interval(self.hass, self._async_tick, ENERGY_STEP)
        )

    @property
    def native_value(self) -> float:
        """Return the total in kWh."""
        return round(self._total_kwh, 6)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._count()
        super()._handle_coordinator_update()

    @callback
    def _async_tick(self, now: datetime) -> None:
        del now
        if self._since is not None:
            self._count()
            self.async_write_ha_state()

    def _count(self) -> None:
        """Add the energy since the last sample, then start a new one."""
        now = monotonic()
        if self._since is not None:
            started, watts = self._since
            self._total_kwh += watts * (now - started) / 3_600_000
        watts_now = (
            estimated_power_w(self.coordinator.data)
            if self.coordinator.last_update_success
            else None
        )
        self._since = None if watts_now is None else (now, watts_now)
