"""Experimental raw-GATT text controls for Dimplex panel heaters."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import DimplexError
from .coordinator import DimplexConfigEntry, DimplexCoordinator
from .entity import DimplexEntity
from .exceptions import to_homeassistant_error
from .protocol import (
    EXPERIMENTAL_RAW_FIELD_SPECS,
    DimplexProtocolError,
    GattAddress,
    decode_known_raw_value,
    format_known_raw_value,
    parse_hex_payload,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, slots=True)
class ExperimentalField:
    """Describe one writable raw characteristic exposed for experiments."""

    address: GattAddress
    translation_key: str
    payload_length: int


EXPERIMENTAL_FIELDS: tuple[ExperimentalField, ...] = tuple(
    ExperimentalField(
        (group, characteristic),
        f"raw_{group}_{characteristic:04x}",
        payload_length,
    )
    for (group, characteristic), payload_length in EXPERIMENTAL_RAW_FIELD_SPECS
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up experimental raw payload controls from coordinator data."""
    del hass
    coordinator = entry.runtime_data
    values = coordinator.raw_fields
    async_add_entities(
        DimplexExperimentalRawText(coordinator, field, values.get(field.address))
        for field in EXPERIMENTAL_FIELDS
    )


class DimplexExperimentalRawText(DimplexEntity, TextEntity):
    """Expose one experimental raw characteristic as editable hexadecimal text."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = True
    _attr_icon = "mdi:flask-outline"
    _attr_mode = TextMode.TEXT

    def __init__(
        self,
        coordinator: DimplexCoordinator,
        field: ExperimentalField,
        initial_value: bytes | None,
    ) -> None:
        """Initialize one fixed-width raw payload control."""
        self._field = field
        self._value = initial_value.hex() if initial_value is not None else None
        self._attr_translation_key = field.translation_key
        self._attr_native_min = field.payload_length * 2
        self._attr_native_max = field.payload_length * 2
        self._attr_pattern = rf"[0-9a-fA-F]{{{field.payload_length * 2}}}"
        super().__init__(coordinator)

    @property
    def native_value(self) -> str | None:
        """Return the latest polled or written raw hexadecimal value."""
        raw_value = self.coordinator.raw_fields.get(self._field.address)
        return raw_value.hex() if raw_value is not None else self._value

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool]:
        """Expose raw bytes and any evidence-backed human-readable decoding."""
        group, characteristic = self._field.address
        attributes: dict[str, str | int | bool] = {
            "service_group": group,
            "characteristic": characteristic,
            "payload_bytes": self._field.payload_length,
        }
        value = self.native_value
        if value is not None:
            payload = bytes.fromhex(value)
            attributes["raw_value"] = value
            attributes.update(decode_known_raw_value(self._field.address, payload))
            decoded_value = format_known_raw_value(self._field.address, payload)
            if decoded_value is not None:
                attributes["decoded_value"] = decoded_value
        return attributes

    async def async_set_value(self, value: str) -> None:
        """Write a fixed-width raw payload and retain the delayed readback."""
        try:
            payload = parse_hex_payload(value)
        except DimplexProtocolError as err:
            raise ServiceValidationError(
                translation_domain="dimplex_ble",
                translation_key="invalid_experimental_payload",
            ) from err
        if len(payload) != self._field.payload_length:
            raise ServiceValidationError(
                translation_domain="dimplex_ble",
                translation_key="invalid_experimental_payload_length",
            )

        group, characteristic = self._field.address
        try:
            readback = await self._client.async_write_raw(
                group, characteristic, payload
            )
            await self.coordinator.async_request_refresh()
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err

        self._value = (readback if readback is not None else payload).hex()
        self.async_write_ha_state()
