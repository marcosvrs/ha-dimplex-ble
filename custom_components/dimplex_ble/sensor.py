"""Diagnostic sensors that preserve heater-provided values."""

from dataclasses import dataclass

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

from .client import DimplexError
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .entity import DimplexEntity
from .protocol import (
    GattAddress,
    decode_known_raw_value,
    format_readonly_raw_value,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, slots=True)
class ExperimentalReadOnlyField:
    """Describe a raw characteristic exposed only as a diagnostic sensor."""

    address: GattAddress
    translation_key: str


EXPERIMENTAL_READ_ONLY_FIELDS: tuple[ExperimentalReadOnlyField, ...] = (
    ExperimentalReadOnlyField((1, 0x0007), "raw_readonly_1_0007"),
    ExperimentalReadOnlyField((3, 0x000B), "raw_readonly_3_000b"),
    ExperimentalReadOnlyField((3, 0x000C), "raw_readonly_3_000c"),
    ExperimentalReadOnlyField((3, 0x1022), "raw_readonly_3_1022"),
    ExperimentalReadOnlyField((3, 0x1029), "raw_readonly_3_1029"),
    ExperimentalReadOnlyField((1, 0x1010), "raw_readonly_1_1010"),
    ExperimentalReadOnlyField((1, 0x1020), "raw_readonly_1_1020"),
    ExperimentalReadOnlyField((1, 0x300E), "raw_readonly_1_300e"),
    ExperimentalReadOnlyField((2, 0x100C), "raw_readonly_2_100c"),
    ExperimentalReadOnlyField((2, 0x700F), "raw_readonly_2_700f"),
    ExperimentalReadOnlyField((3, 0x2003), "raw_readonly_3_2003"),
    ExperimentalReadOnlyField((3, 0x200B), "raw_readonly_3_200b"),
    ExperimentalReadOnlyField((3, 0x200C), "raw_readonly_3_200c"),
    ExperimentalReadOnlyField((3, 0x200F), "raw_readonly_3_200f"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up heater diagnostics."""
    del hass
    client = entry.runtime_data.client
    try:
        values = await client.async_read_raw_fields(
            tuple(field.address for field in EXPERIMENTAL_READ_ONLY_FIELDS)
        )
    except DimplexError:
        values = {}

    async_add_entities(
        [
            DimplexErrorCodeSensor(entry.runtime_data),
            DimplexOperatingModeSensor(entry.runtime_data),
            DimplexRatedPowerSensor(entry.runtime_data),
            DimplexLcdTemperatureSensor(entry.runtime_data),
            DimplexOutputPowerSensor(entry.runtime_data),
            DimplexCalculatedPowerSensor(entry.runtime_data),
            *(
                DimplexExperimentalRawSensor(
                    entry.runtime_data, field, values.get(field.address)
                )
                for field in EXPERIMENTAL_READ_ONLY_FIELDS
            ),
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


class DimplexRatedPowerSensor(DimplexEntity, SensorEntity):
    """Expose the first PowerLoading word as the heater's rated power."""

    _attr_translation_key = "rated_power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfPower.WATT

    @property
    def native_value(self) -> int | None:
        """Return the documented rated power in watts."""
        data = self.coordinator.data
        return data.rated_power_w if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Preserve the complete PowerLoading payload for diagnostics."""
        data = self.coordinator.data
        if data is None or data.power_loading_hex is None:
            return None
        return {"power_loading_raw": data.power_loading_hex}


class DimplexLcdTemperatureSensor(DimplexEntity, SensorEntity):
    """Expose the controller LCD thermistor reading for diagnostics."""

    _attr_translation_key = "lcd_temperature"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self) -> float | None:
        """Return the selected LCD thermistor temperature."""
        data = self.coordinator.data
        return data.lcd_temperature_c if data is not None else None


class DimplexOutputPowerSensor(DimplexEntity, SensorEntity):
    """Expose the PLX report-screen output-power percentage."""

    _attr_translation_key = "output_power"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Return the Trac output-power percentage."""
        data = self.coordinator.data
        return data.trac_output_power_percent if data is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Preserve the complete Trac response."""
        data = self.coordinator.data
        if data is None or data.trac_status_hex is None:
            return None
        return {"trac_raw": data.trac_status_hex}


class DimplexCalculatedPowerSensor(DimplexEntity, SensorEntity):
    """Expose the calculated electrical power consumption."""

    _attr_translation_key = "calculated_power_consumption"
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


class DimplexExperimentalRawSensor(DimplexEntity, SensorEntity):
    """Expose one unclassified raw characteristic without write access."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:flask-outline"

    def __init__(
        self,
        coordinator: DimplexCoordinator,
        field: ExperimentalReadOnlyField,
        initial_value: bytes | None,
    ) -> None:
        """Initialize one read-only raw diagnostic sensor."""
        self._field = field
        self._value = initial_value.hex() if initial_value is not None else None
        self._attr_translation_key = field.translation_key
        super().__init__(coordinator)

    @property
    def native_value(self) -> str | None:
        """Return a decoded value or the exact raw hexadecimal fallback."""
        if self._value is None:
            return None
        payload = bytes.fromhex(self._value)
        return format_readonly_raw_value(self._field.address, payload) or self._value

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool]:
        """Expose raw bytes and any evidence-backed human-readable decoding."""
        group, characteristic = self._field.address
        attributes: dict[str, str | int | bool] = {
            "service_group": group,
            "characteristic": characteristic,
            "raw_bytes": len(self._value) // 2 if self._value is not None else 0,
        }
        if self._value is not None:
            payload = bytes.fromhex(self._value)
            attributes["raw_value"] = self._value
            attributes.update(decode_known_raw_value(self._field.address, payload))
            decoded_value = format_readonly_raw_value(self._field.address, payload)
            if decoded_value is not None:
                attributes["decoded_value"] = decoded_value
        return attributes
