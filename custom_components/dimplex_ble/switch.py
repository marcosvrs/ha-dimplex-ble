"""Independent Dimplex panel-heater controls."""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import DimplexError
from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .exceptions import to_homeassistant_error
from .protocol import (
    OPEN_WINDOW_DETECTION,
    PREEMPTIVE_HEATING,
    DimplexProtocolError,
    GattAddress,
)

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up verified independent panel-heater controls."""
    del hass
    async_add_entities(
        [
            DimplexOpenWindowDetectionSwitch(entry.runtime_data),
            DimplexPreemptiveHeatingSwitch(entry.runtime_data),
        ]
    )


class _DimplexBooleanSettingSwitch(DimplexEntity, SwitchEntity):
    """Control a verified independent boolean panel-heater setting."""

    _setting_address: ClassVar[GattAddress]
    _snapshot_attribute: ClassVar[str]
    _explanation_attributes: ClassVar[Mapping[str, str]]

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

    @property
    def extra_state_attributes(self) -> Mapping[str, str]:
        """Expose translated, source-backed user guidance in the details view."""
        return self._explanation_attributes

    async def _async_set_setting(self, enabled: bool) -> None:
        """Write the setting and refresh its device readback."""
        try:
            await self._client.async_write_raw(
                *self._setting_address,
                bytes((int(enabled),)),
            )
            await self.coordinator.async_request_refresh()
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err

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
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:window-open-variant"
    _setting_address = OPEN_WINDOW_DETECTION
    _snapshot_attribute = "open_window_detection"
    _explanation_attributes = {
        "what_it_does": "switches_heater_to_heating_off_when_open_window_detected",
        "how_it_works": "detects_sudden_decrease_in_room_temperature",
        "when_it_applies": "can_interrupt_any_active_mode",
        "important_note": "may_trigger_in_drafty_poorly_insulated_or_undersized_rooms",
    }


class DimplexPreemptiveHeatingSwitch(_DimplexBooleanSettingSwitch):
    """Control the panel heater's independently configurable pre-emptive heating."""

    _attr_translation_key = "preemptive_heating"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:heat-wave"
    _setting_address = PREEMPTIVE_HEATING
    _snapshot_attribute = "preemptive_heating"
    _explanation_attributes = {
        "official_name": "dimplex_calls_this_adaptive_start",
        "what_it_does": "preheats_room_to_target_before_timer_period",
        "how_it_works": "models_room_heating_characteristics_and_starting_temperature",
        "when_it_applies": "timer_mode_only",
        "important_note": "continuously_learning_two_hour_limit_all_same_room_heaters_should_enable",
    }
