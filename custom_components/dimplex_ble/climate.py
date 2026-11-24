"""Native Home Assistant climate entity for a Dimplex panel heater."""

from __future__ import annotations

from math import isfinite
from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    PRESET_AWAY,
    PRESET_ECO,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .protocol import (
    HEATING_MODE,
    HEATING_MODE_ECO,
    HEATING_MODE_FROST_PROTECT,
    HEATING_MODE_MANUAL,
    HEATING_MODE_TIMER,
    PRESET_FROST_PROTECT,
    PRESET_HOME_ALL_DAY,
    PRESET_MANUAL,
    PRESET_OUT_ALL_DAY,
    PRESET_USER_TIMER,
    TIMER_SUBMODE_AWAY,
    TIMER_SUBMODE_HOME,
    TIMER_SUBMODE_OUT,
    TIMER_SUBMODE_USER_TIMER,
    GattAddress,
    encode_heating_mode,
    encode_preset_mode,
    encode_setpoint,
    parse_heating_mode_code,
    parse_preset_mode,
)

PARALLEL_UPDATES = 1
SETPOINT: GattAddress = (0, 0x1023)
# Each needs a text and an icon under state_attributes.preset_mode.
PRESET_MODES = [
    PRESET_AWAY,
    PRESET_HOME_ALL_DAY,
    PRESET_OUT_ALL_DAY,
    PRESET_USER_TIMER,
    PRESET_MANUAL,
    PRESET_ECO,
    PRESET_FROST_PROTECT,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the native panel heater entity."""
    async_add_entities([DimplexClimate(entry.runtime_data)])


class DimplexClimate(DimplexEntity, ClimateEntity):
    """Expose the panel heater's target and room temperature using HA's climate domain."""

    _attr_translation_key = "heater"
    _unique_suffix = "heater"
    # The main feature of the device: the entity is named after the heater.
    _attr_name = None
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_precision = PRECISION_TENTHS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = PRESET_MODES

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the Home Assistant mode represented by the device mode."""
        data = self.coordinator.data
        if data is None or data.operating_mode_hex is None:
            return None
        try:
            mode_code = parse_heating_mode_code(bytes.fromhex(data.operating_mode_hex))
        except ValueError:
            return None
        if mode_code in {
            HEATING_MODE_TIMER,
            HEATING_MODE_MANUAL,
            HEATING_MODE_ECO,
            HEATING_MODE_FROST_PROTECT,
        }:
            return HVACMode.HEAT
        return None

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
        """Return the target read back from the panel heater."""
        data = self.coordinator.data
        return (
            float(data.setpoint_c)
            if data is not None and data.setpoint_c is not None
            else None
        )

    @property
    def preset_mode(self) -> str | None:
        """Return the verified preset represented by the operating-mode payload."""
        data = self.coordinator.data
        if data is None:
            return None
        if data.away_active:
            return PRESET_AWAY
        if data.operating_mode_hex is None:
            return None
        try:
            payload = bytes.fromhex(data.operating_mode_hex)
            if payload and payload[0] == HEATING_MODE_TIMER:
                if len(payload) > 1:
                    if payload[1] == TIMER_SUBMODE_AWAY:
                        return PRESET_AWAY
                    if payload[1] == TIMER_SUBMODE_HOME:
                        return PRESET_HOME_ALL_DAY
                    if payload[1] == TIMER_SUBMODE_OUT:
                        return PRESET_OUT_ALL_DAY
                    if payload[1] == TIMER_SUBMODE_USER_TIMER:
                        return PRESET_USER_TIMER
                return PRESET_USER_TIMER
            preset = parse_preset_mode(payload)
            presets = self.preset_modes
            return preset if presets is not None and preset in presets else None
        except ValueError:
            return None

    @property
    def extra_state_attributes(self) -> dict[str, str | int] | None:
        """Expose the Away deadline and target while Away is active."""
        data = self.coordinator.data
        if data is None:
            return None
        if not data.away_active:
            # The heater keeps its last Away record after Away ends; a stale
            # deadline or target is not shown (as the datetime entity does).
            return None
        attributes: dict[str, str | int] = {}
        if data.away_until is not None:
            attributes["away_until"] = data.away_until.replace(
                tzinfo=dt_util.get_default_time_zone()
            ).isoformat()
        if data.away_target_temperature_c is not None:
            attributes["away_target_temperature"] = data.away_target_temperature_c
        return attributes or None

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return only the verified Trac active/idle action."""
        data = self.coordinator.data
        if data is None or data.heating_active is None:
            return None
        return HVACAction.HEATING if data.heating_active else HVACAction.IDLE

    @property
    def native_temperature_unit(self) -> str:
        """Return the panel heater protocol's Celsius unit."""
        return UnitOfTemperature.CELSIUS

    @property
    def min_temp(self) -> float:
        """Return the minimum reported setpoint or Home Assistant's default."""
        data = self.coordinator.data
        return (
            float(data.minimum_temperature_c)
            if data and data.minimum_temperature_c is not None
            else float(DEFAULT_MIN_TEMP)
        )

    @property
    def max_temp(self) -> float:
        """Return the maximum reported setpoint or Home Assistant's default."""
        data = self.coordinator.data
        return (
            float(data.maximum_temperature_c)
            if data and data.maximum_temperature_c is not None
            else float(DEFAULT_MAX_TEMP)
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Write the requested verified operating mode."""
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_hvac_mode",
            )
        same_mode = self.hvac_mode == hvac_mode
        if same_mode:
            return
        await self.coordinator.async_write_mode(
            HEATING_MODE,
            encode_heating_mode(HEATING_MODE_MANUAL),
            leave_away=self._in_away,
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write a whole-degree Celsius target and wait for panel heater readback."""
        requested_mode = kwargs.get(ATTR_HVAC_MODE)
        if requested_mode is not None and requested_mode != HVACMode.HEAT:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_hvac_mode",
            )

        requested = kwargs.get(ATTR_TEMPERATURE)
        if isinstance(requested, bool) or not isinstance(requested, (int, float)):
            raise self._invalid_setpoint()
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
            raise self._invalid_setpoint()

        await self.coordinator.async_write(SETPOINT, encode_setpoint(temperature_c))

    def _invalid_setpoint(self) -> ServiceValidationError:
        """Return the error naming the heater's whole-degree setpoint range."""
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_setpoint",
            translation_placeholders={
                "min": f"{self.min_temp:g}",
                "max": f"{self.max_temp:g}",
            },
        )

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Write a verified operating preset and wait for panel heater readback."""
        if self.preset_modes is None or preset_mode not in self.preset_modes:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_preset",
            )

        if preset_mode == PRESET_AWAY:
            await self.coordinator.async_set_away_mode(True)
            return
        await self.coordinator.async_write_mode(
            HEATING_MODE, encode_preset_mode(preset_mode), leave_away=self._in_away
        )

    def _in_away(self) -> bool:
        """Return whether the heater is in Away now (checked under the Away lock)."""
        return self.preset_mode == PRESET_AWAY
