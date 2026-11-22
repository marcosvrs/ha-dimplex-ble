from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dimplex_ble.const import CONF_ADDRESS, CONF_PROXY_SOURCE, DOMAIN
from custom_components.dimplex_ble.coordinator import DimplexCoordinator
from custom_components.dimplex_ble.text import (
    EXPERIMENTAL_FIELDS,
    DimplexExperimentalRawText,
    async_setup_entry,
)

ADDRESS = "AA:BB:CC:DD:EE:20"
PROXY = "esphome-proxy"


def make_coordinator(hass, client):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dimplex Heater 1",
        data={CONF_ADDRESS: ADDRESS, CONF_PROXY_SOURCE: PROXY},
    )
    return DimplexCoordinator(hass, entry, client)


async def test_setup_exposes_coordinator_polled_experimental_fields(hass) -> None:
    client = SimpleNamespace(address=ADDRESS)
    coordinator = make_coordinator(hass, client)
    coordinator.raw_fields = {(0, 0x100D): b"\x01"}
    entry = SimpleNamespace(runtime_data=coordinator)
    entities = []

    await async_setup_entry(hass, entry, entities.extend)

    field = next(field for field in EXPERIMENTAL_FIELDS if field.address == (0, 0x100D))
    entity = next(entity for entity in entities if entity._field == field)
    assert entity.native_value == "01"
    assert entity.extra_state_attributes == {
        "service_group": 0,
        "characteristic": 0x100D,
        "payload_bytes": 1,
        "raw_value": "01",
        "advance_active": True,
        "decoded_value": "on",
    }
    assert {entity._field.address for entity in entities} >= {
        (0, 0x0006),
        (0, 0x1002),
        (1, 0x0001),
    }
    assert all(field.address != (0, 0x1007) for field in EXPERIMENTAL_FIELDS)


async def test_setup_keeps_controls_when_polled_values_are_missing(hass) -> None:
    client = SimpleNamespace(address=ADDRESS)
    coordinator = make_coordinator(hass, client)
    entry = SimpleNamespace(runtime_data=coordinator)
    entities = []

    await async_setup_entry(hass, entry, entities.extend)

    assert len(entities) == len(EXPERIMENTAL_FIELDS)




async def test_experimental_text_adds_established_decoded_attributes(hass) -> None:
    client = SimpleNamespace(address=ADDRESS)
    coordinator = make_coordinator(hass, client)
    field = next(field for field in EXPERIMENTAL_FIELDS if field.address == (0, 0x100D))
    entity = DimplexExperimentalRawText(coordinator, field, b"\x01")

    assert entity.native_value == "01"
    assert entity.extra_state_attributes["advance_active"] is True
    assert entity.extra_state_attributes["raw_value"] == "01"
    assert entity.extra_state_attributes["decoded_value"] == "on"




async def test_experimental_text_maps_write_failure(hass) -> None:
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=DimplexConnectionError("proxy failed")),
    )
    coordinator = make_coordinator(hass, client)
    entity = DimplexExperimentalRawText(
        coordinator, EXPERIMENTAL_FIELDS[0], b"\x01" * 7
    )

    with pytest.raises(HomeAssistantError):
        await entity.async_set_value("00" * 7)



async def test_experimental_text_maps_protocol_write_failure(hass) -> None:
    from custom_components.dimplex_ble.protocol import DimplexProtocolError

    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(side_effect=DimplexProtocolError("invalid")),
    )
    coordinator = make_coordinator(hass, client)
    entity = DimplexExperimentalRawText(
        coordinator, EXPERIMENTAL_FIELDS[0], b"\x01" * 7
    )

    with pytest.raises(ServiceValidationError) as err:
        await entity.async_set_value("00" * 7)

    assert err.value.translation_key == "invalid_gatt_request"

@pytest.mark.parametrize("value", ["", "00", "zz" * 9, "00" * 8])
async def test_experimental_text_rejects_invalid_payload(hass, value: str) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = make_coordinator(hass, client)
    entity = DimplexExperimentalRawText(coordinator, EXPERIMENTAL_FIELDS[0], None)

    with pytest.raises(ServiceValidationError):
        await entity.async_set_value(value)

    client.async_write_raw.assert_not_awaited()


async def test_experimental_text_validation_errors_are_translated(
    hass,
) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = make_coordinator(hass, client)
    entity = DimplexExperimentalRawText(coordinator, EXPERIMENTAL_FIELDS[0], None)

    with pytest.raises(ServiceValidationError) as invalid:
        await entity.async_set_value("zz")
    assert invalid.value.translation_domain == DOMAIN
    assert invalid.value.translation_key == "invalid_experimental_payload"

    with pytest.raises(ServiceValidationError) as wrong_length:
        await entity.async_set_value("00")
    assert wrong_length.value.translation_domain == DOMAIN
    assert wrong_length.value.translation_key == "invalid_experimental_payload_length"


async def test_experimental_text_prefers_device_readback(hass, monkeypatch) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=b"\x22" * 7),
    )
    coordinator = make_coordinator(hass, client)
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexExperimentalRawText(
        coordinator, EXPERIMENTAL_FIELDS[0], b"\x11" * 7
    )
    monkeypatch.setattr(entity, "async_write_ha_state", Mock())

    await entity.async_set_value("11" * 7)

    assert entity.native_value == "22" * 7


async def test_experimental_text_uses_payload_when_readback_is_none(
    hass, monkeypatch
) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=None),
    )
    coordinator = make_coordinator(hass, client)
    coordinator.async_request_refresh = AsyncMock()
    entity = DimplexExperimentalRawText(
        coordinator, EXPERIMENTAL_FIELDS[0], b"\x11" * 7
    )
    monkeypatch.setattr(entity, "async_write_ha_state", Mock())

    await entity.async_set_value("11" * 7)

    assert entity.native_value == "11" * 7
