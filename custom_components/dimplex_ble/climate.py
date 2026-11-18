"""Native Home Assistant climate entity for a Dimplex heater."""

from __future__ import annotations

from math import isfinite
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import DimplexError
from .const import DOMAIN
from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .exceptions import to_homeassistant_error
from .protocol import DimplexProtocolError, encode_setpoint

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the native heater entity."""
    async_add_entities([DimplexClimate(entry.runtime_data)])


class DimplexClimate(DimplexEntity, ClimateEntity):
    """Expose the heater's target and room temperature using HA's climate domain."""

    _attr_translation_key = "heater"
    _attr_hvac_mode = HVACMode.HEAT
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    @property
    def available(self) -> bool:
        """Require a live GATT snapshot and a device-reported setpoint range."""
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and data.minimum_temperature_c is not None
            and data.maximum_temperature_c is not None
        )

    @property
    def native_current_temperature(self) -> float | None:
        """Return the selected room-temperature reading in Celsius."""
        data = self.coordinator.data
        return data.current_temperature_c if data is not None else None

    @property
    def native_target_temperature(self) -> float | None:
        """Return the target read back from the heater."""
        data = self.coordinator.data
        return float(data.setpoint_c) if data is not None and data.setpoint_c is not None else None

    @property
    def min_temp(self) -> float:
        """Return the minimum reported setpoint or Home Assistant's default."""
        data = self.coordinator.data
        return float(data.minimum_temperature_c) if data and data.minimum_temperature_c is not None else super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum reported setpoint or Home Assistant's default."""
        data = self.coordinator.data
        return float(data.maximum_temperature_c) if data and data.maximum_temperature_c is not None else super().max_temp

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write a whole-degree Celsius target and wait for heater readback."""
        requested = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(requested, bool) or not isinstance(requested, (int, float)):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_setpoint",
            )
        temperature_c = float(requested)
        data = self.coordinator.data
        if (
            data is None
            or data.minimum_temperature_c is None
            or data.maximum_temperature_c is None
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="setpoint_unavailable",
            )
        if (
            not isfinite(temperature_c)
            or not temperature_c.is_integer()
            or not data.minimum_temperature_c
            <= temperature_c
            <= data.maximum_temperature_c
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_setpoint",
            )

        try:
            payload = encode_setpoint(temperature_c)
            await self._client.async_write_raw(0, 0x1023, payload)
            await self.coordinator.async_request_refresh()
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err
