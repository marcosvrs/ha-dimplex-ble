"""Native Home Assistant climate entity for a Dimplex panel heater."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_HVAC_MODE,
    DEFAULT_MAX_TEMP,
    DEFAULT_MIN_TEMP,
    PRESET_AWAY,
    PRESET_BOOST,
    PRESET_ECO,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import settings
from .const import DOMAIN
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .entity import DimplexEntity
from .protocol import (
    PRESET_FROST_PROTECT,
    PRESET_HOME_ALL_DAY,
    PRESET_MANUAL,
    PRESET_OUT_ALL_DAY,
    PRESET_USER_TIMER,
    SETPOINT,
    TIMER_MODE_KEYS,
    encode_preset_mode,
    encode_setpoint,
    heating_mode_of,
)

PARALLEL_UPDATES = 1
# HA climate docs: AUTO "follows a schedule ... You cannot set a temperature in
# this mode"; presets change how the active mode behaves. The heater's timer
# programmes are schedules (a setpoint written in User Timer is dropped when the
# programme is re-selected, AM2086 2026-11-24), so they are the presets of AUTO;
# the continuous modes are the presets of HEAT. Away (a fixed temperature until a
# return time) and Boost (a temporary temperature, after which the heater returns
# to the mode it left) are offered in both so they are one action away.
# Each preset needs a text and an icon under state_attributes.preset_mode.
SCHEDULE_PRESETS = frozenset(
    {PRESET_USER_TIMER, PRESET_HOME_ALL_DAY, PRESET_OUT_ALL_DAY}
)
HEAT_PRESETS = [
    PRESET_MANUAL,
    PRESET_ECO,
    PRESET_FROST_PROTECT,
    PRESET_BOOST,
    PRESET_AWAY,
]
AUTO_PRESETS = [
    PRESET_USER_TIMER,
    PRESET_HOME_ALL_DAY,
    PRESET_OUT_ALL_DAY,
    PRESET_BOOST,
    PRESET_AWAY,
]
PRESET_MODES = [*HEAT_PRESETS[:3], *AUTO_PRESETS]


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
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO]
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_precision = PRECISION_TENTHS
    # The schedule Auto resumes; updated whenever the heater reports one.
    _last_schedule = PRESET_USER_TIMER

    def __init__(self, coordinator: DimplexCoordinator) -> None:
        """Start from the programme the first poll found, not a default."""
        super().__init__(coordinator)
        self._remember_schedule()

    @property
    def _boosting(self) -> bool:
        """Return whether Boost runs (ConfigR: while RunbackTime is above 0)."""
        data = self.coordinator.data
        return bool(data and data.boost_minutes_left)

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return Auto for a timer programme and Heat for a continuous mode or Away.

        Boost keeps the mode it was started from: a timer programme stays in the
        mode byte, a continuous mode reads Manual (hardware 2026-11-25).
        """
        if self._boosting:
            data = self.coordinator.data
            assert data is not None
            timer = heating_mode_of(data.operating_mode_hex) in TIMER_MODE_KEYS
            return HVACMode.AUTO if timer else HVACMode.HEAT
        preset = self.preset_mode
        if preset is None:
            return None
        return HVACMode.AUTO if preset in SCHEDULE_PRESETS else HVACMode.HEAT

    @property
    def preset_modes(self) -> list[str]:
        """Return the presets of the active mode (all while the mode is unknown).

        Boost is not offered in Away: starting it there was never tested.
        """
        mode = self.hvac_mode
        if mode == HVACMode.AUTO:
            presets = AUTO_PRESETS
        elif mode == HVACMode.HEAT:
            presets = HEAT_PRESETS
        else:
            return PRESET_MODES
        if self.preset_mode == PRESET_AWAY:
            return [preset for preset in presets if preset != PRESET_BOOST]
        return presets

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Offer a target temperature except in Auto, where the schedule sets it."""
        if self.hvac_mode == HVACMode.AUTO and not self._boosting:
            return ClimateEntityFeature.PRESET_MODE
        return (
            ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.PRESET_MODE
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Remember the last schedule so Auto resumes it."""
        self._remember_schedule()
        super()._handle_coordinator_update()

    def _remember_schedule(self) -> None:
        """Track the timer programme in the mode bytes, also during Boost.

        A Boost started from a programme keeps it in the mode bytes (01 0p 00 0n,
        hardware 2026-11-25), so it is read from there, not from the preset.
        """
        data = self.coordinator.data
        if data is not None:
            mode = heating_mode_of(data.operating_mode_hex)
            if mode in SCHEDULE_PRESETS:
                self._last_schedule = mode

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
        """Return the target read back from the panel heater (none in Auto).

        In Away the heater holds the Away temperature, not the setpoint.
        """
        data = self.coordinator.data
        if (
            data is not None
            and data.away_active
            and data.away_target_temperature_c is not None
        ):
            return float(data.away_target_temperature_c)
        if data is None or data.setpoint_c is None:
            return None
        if self.hvac_mode == HVACMode.AUTO and not self._boosting:
            return None
        return float(data.setpoint_c)

    @property
    def preset_mode(self) -> str | None:
        """Return the verified preset represented by the operating-mode payload."""
        data = self.coordinator.data
        if data is None:
            return None
        if data.boost_minutes_left:
            return PRESET_BOOST
        if data.away_active:
            return PRESET_AWAY
        mode = heating_mode_of(data.operating_mode_hex)
        if mode == "timer":  # a timer submode this integration does not know
            return PRESET_USER_TIMER
        return mode if mode in PRESET_MODES else None

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
        if self.hvac_mode == hvac_mode:
            return  # idempotent: a running Boost keeps running
        await self.coordinator.async_set_heating_mode(
            encode_preset_mode(self._last_schedule)
            if hvac_mode == HVACMode.AUTO
            else encode_preset_mode(PRESET_MANUAL)
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Write a whole-degree Celsius target and wait for panel heater readback."""
        requested_mode = kwargs.get(ATTR_HVAC_MODE)
        # During Boost the target is Boost's temperature, in either mode.
        allowed = self.hvac_mode if self._boosting else HVACMode.HEAT
        if (
            self.hvac_mode == HVACMode.AUTO and not self._boosting
        ) or requested_mode not in (None, allowed):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="temperature_needs_heat",
            )

        requested = kwargs.get(ATTR_TEMPERATURE)
        if self.preset_mode == PRESET_AWAY and isinstance(requested, (int, float)):
            # The target shown in Away is the Away temperature (7-18 degC).
            await self.coordinator.async_set_away_temperature(float(requested))
            return
        temperature_c = self.coordinator.check_setpoint(requested)
        await self.coordinator.async_write(SETPOINT, encode_setpoint(temperature_c))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Write a verified operating preset and wait for panel heater readback."""
        # HA checks the preset against the active mode's list before this call.
        if preset_mode not in PRESET_MODES:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_preset",
            )

        if preset_mode == PRESET_BOOST:
            await self._async_start_boost()
            return
        if preset_mode == PRESET_AWAY:
            await self.coordinator.async_set_away_mode()  # ends Boost first
            return
        await self.coordinator.async_set_heating_mode(encode_preset_mode(preset_mode))

    async def _async_start_boost(self) -> None:
        """Start Boost at the heater's Boost temperature for the chosen length."""
        if self._boosting:
            return
        data = self.coordinator.data
        temperature = data.boost_temperature_c if data is not None else None
        if (
            data is None
            or temperature is None
            or data.minimum_temperature_c is None
            or data.maximum_temperature_c is None
            or not data.minimum_temperature_c
            <= temperature
            <= data.maximum_temperature_c
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="boost_unavailable"
            )
        minutes = settings.boost_minutes(self.coordinator.hass)
        await self.coordinator.async_start_boost(minutes, temperature)
