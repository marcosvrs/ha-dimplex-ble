"""Independent Dimplex panel-heater controls."""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .protocol import (
    ADVANCE_HEATING,
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    SETBACK,
    TIMER_MODE_KEYS,
    GattAddress,
    encode_advance,
    heating_mode_key,
    parse_setback,
    parse_sound_enabled,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up verified independent panel-heater controls."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        [
            DimplexSoundSwitch(coordinator),
            DimplexOpenWindowDetectionSwitch(coordinator),
            DimplexPreemptiveHeatingSwitch(coordinator),
            DimplexSetbackSwitch(coordinator),
            DimplexAdvanceSwitch(coordinator),
        ]
    )


class DimplexSoundSwitch(DimplexEntity, SwitchEntity):
    """Control the verified panel-heater Sound setting."""

    _attr_translation_key = "sound"
    _unique_suffix = "sound"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Require a valid independently refreshed Sound value."""
        return (
            super().available
            and parse_sound_enabled(self.coordinator.sound_value) is not None
        )

    @property
    def is_on(self) -> bool:
        """Return whether panel-heater sounds are enabled."""
        return parse_sound_enabled(self.coordinator.sound_value) is True

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable panel-heater sounds."""
        del kwargs
        await self.coordinator.async_set_sound(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable panel-heater sounds."""
        del kwargs
        await self.coordinator.async_set_sound(False)


class DimplexSetbackSwitch(DimplexEntity, SwitchEntity):
    """Control Setback: hold a set temperature in timer Heating Off periods."""

    _attr_translation_key = "setback"
    _unique_suffix = "setback"
    _attr_entity_category = EntityCategory.CONFIG

    @property
    def available(self) -> bool:
        """Require a valid slow-refreshed Setback value."""
        return (
            super().available
            and parse_setback(self.coordinator.settings.get(SETBACK)) is not None
        )

    @property
    def is_on(self) -> bool:
        """Return whether Setback is on."""
        return parse_setback(self.coordinator.settings.get(SETBACK)) is True

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn Setback on."""
        del kwargs
        await self.coordinator.async_set_setback(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn Setback off."""
        del kwargs
        await self.coordinator.async_set_setback(False)


class _DimplexBooleanSettingSwitch(DimplexEntity, SwitchEntity):
    """Control a verified independent boolean panel-heater setting."""

    _setting_address: ClassVar[GattAddress]
    _snapshot_attribute: ClassVar[str]

    @property
    def available(self) -> bool:
        """Require a live poll containing the verified setting."""
        data = self.coordinator.data
        return (
            super().available
            and data is not None
            and getattr(data, self._snapshot_attribute) is not None
        )

    @property
    def is_on(self) -> bool:
        """Return the last device-reported setting."""
        data = self.coordinator.data
        return data is not None and getattr(data, self._snapshot_attribute) is True

    async def _async_set_setting(self, enabled: bool) -> None:
        """Write the setting and refresh its device readback."""
        await self.coordinator.async_write(
            self._setting_address, bytes((int(enabled),))
        )

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable the setting."""
        del kwargs
        await self._async_set_setting(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable the setting."""
        del kwargs
        await self._async_set_setting(False)


class DimplexOpenWindowDetectionSwitch(_DimplexBooleanSettingSwitch):
    """Control the panel heater's open-window detection."""

    _attr_translation_key = "open_window_detection"
    _unique_suffix = "open_window_detection"
    _attr_entity_category = EntityCategory.CONFIG
    _setting_address = OPEN_WINDOW_DETECTION
    _snapshot_attribute = "open_window_detection"


class DimplexPreemptiveHeatingSwitch(_DimplexBooleanSettingSwitch):
    """Control the panel heater's independently configurable pre-emptive heating."""

    _attr_translation_key = "preemptive_heating"
    _unique_suffix = "preemptive_heating"
    _attr_entity_category = EntityCategory.CONFIG
    _setting_address = PREEMPTIVE_HEATING
    _snapshot_attribute = "preemptive_heating"


class DimplexAdvanceSwitch(_DimplexBooleanSettingSwitch):
    """Toggle the current timer period's heating until the next period starts."""

    _attr_translation_key = "advance"
    _unique_suffix = "advance"
    _setting_address = ADVANCE_HEATING
    _snapshot_attribute = "advance_active"

    async def _async_set_setting(self, enabled: bool) -> None:
        """Write Advance On/Off and refresh its device readback."""
        await self.coordinator.async_write(ADVANCE_HEATING, encode_advance(enabled))

    async def async_turn_on(self, **kwargs: object) -> None:
        """Start Advance; the heater supports it only in a timer preset."""
        del kwargs
        data = self.coordinator.data
        mode = None
        if data is not None and data.operating_mode_hex is not None:
            mode = heating_mode_key(bytes.fromhex(data.operating_mode_hex))
        if data is None or data.away_active or mode not in TIMER_MODE_KEYS:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="advance_requires_timer"
            )
        await self._async_set_setting(True)
