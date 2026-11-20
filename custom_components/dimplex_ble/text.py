"""Experimental raw-GATT text controls for Dimplex heaters."""

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


EXPERIMENTAL_FIELDS: tuple[ExperimentalField, ...] = (
    ExperimentalField((0, 0x0006), "raw_0_0006", 7),
    ExperimentalField((0, 0x1002), "raw_0_1002", 31),
    ExperimentalField((1, 0x0001), "raw_1_0001", 4),
    ExperimentalField((0, 0x1007), "raw_0_1007", 9),
    ExperimentalField((0, 0x1008), "raw_0_1008", 1),
    ExperimentalField((0, 0x100D), "raw_0_100d", 1),
    ExperimentalField((0, 0x1016), "raw_0_1016", 2),
    ExperimentalField((0, 0x1017), "raw_0_1017", 2),
    ExperimentalField((0, 0x1027), "raw_0_1027", 2),
    ExperimentalField((0, 0x1028), "raw_0_1028", 2),
    ExperimentalField((0, 0x102A), "raw_0_102a", 7),
    ExperimentalField((2, 0x0005), "raw_2_0005", 3),
    ExperimentalField((2, 0x100A), "raw_2_100a", 1),
    ExperimentalField((2, 0x100B), "raw_2_100b", 1),
    ExperimentalField((2, 0x100F), "raw_2_100f", 1),
    ExperimentalField((2, 0x1018), "raw_2_1018", 1),
    ExperimentalField((2, 0x1019), "raw_2_1019", 1),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up experimental raw payload controls."""
    del hass
    client = entry.runtime_data.client
    try:
        values = await client.async_read_raw_fields(
            tuple(field.address for field in EXPERIMENTAL_FIELDS)
        )
    except DimplexError:
        values = {}

    async_add_entities(
        DimplexExperimentalRawText(entry.runtime_data, field, values.get(field.address))
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
        """Return the last observed or written raw hexadecimal value."""
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool]:
        """Expose raw bytes and any evidence-backed human-readable decoding."""
        group, characteristic = self._field.address
        attributes: dict[str, str | int | bool] = {
            "service_group": group,
            "characteristic": characteristic,
            "payload_bytes": self._field.payload_length,
        }
        if self._value is not None:
            payload = bytes.fromhex(self._value)
            attributes["raw_value"] = self._value
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
