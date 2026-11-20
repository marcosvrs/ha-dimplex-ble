from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import ServiceValidationError
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


async def test_setup_reads_and_exposes_experimental_fields(hass) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw_fields=AsyncMock(return_value={(0, 0x1007): b"\x01" * 9}),
    )
    coordinator = make_coordinator(hass, client)
    entry = SimpleNamespace(runtime_data=coordinator)
    entities = []

    await async_setup_entry(hass, entry, entities.extend)

    field = next(field for field in EXPERIMENTAL_FIELDS if field.address == (0, 0x1007))
    entity = next(entity for entity in entities if entity._field == field)
    assert entity.native_value == "01" * 9
    assert entity.extra_state_attributes == {
        "service_group": 0,
        "characteristic": 0x1007,
        "payload_bytes": 9,
        "raw_value": "01" * 9,
    }
    assert {entity._field.address for entity in entities} >= {
        (0, 0x0006),
        (0, 0x1002),
        (1, 0x0001),
    }
    client.async_read_raw_fields.assert_awaited_once()


async def test_setup_keeps_controls_when_initial_read_fails(hass) -> None:
    from custom_components.dimplex_ble.client import DimplexConnectionError

    client = SimpleNamespace(
        address=ADDRESS,
        async_read_raw_fields=AsyncMock(side_effect=DimplexConnectionError()),
    )
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

async def test_experimental_text_writes_readback_and_refreshes(
    hass, monkeypatch
) -> None:
    client = SimpleNamespace(
        address=ADDRESS,
        async_write_raw=AsyncMock(return_value=b"\x00" * 9),
    )
    coordinator = make_coordinator(hass, client)
    coordinator.async_request_refresh = AsyncMock()
    field = next(field for field in EXPERIMENTAL_FIELDS if field.address == (0, 0x1007))
    entity = DimplexExperimentalRawText(coordinator, field, b"\x01" * 9)
    write_state = Mock()
    monkeypatch.setattr(entity, "async_write_ha_state", write_state)

    await entity.async_set_value("00" * 9)

    client.async_write_raw.assert_awaited_once_with(0, 0x1007, b"\x00" * 9)
    coordinator.async_request_refresh.assert_awaited_once()
    assert entity.native_value == "00" * 9
    write_state.assert_called_once_with()


@pytest.mark.parametrize("value", ["", "00", "zz" * 9, "00" * 8])
async def test_experimental_text_rejects_invalid_payload(hass, value: str) -> None:
    client = SimpleNamespace(address=ADDRESS, async_write_raw=AsyncMock())
    coordinator = make_coordinator(hass, client)
    entity = DimplexExperimentalRawText(coordinator, EXPERIMENTAL_FIELDS[0], None)

    with pytest.raises(ServiceValidationError):
        await entity.async_set_value(value)

    client.async_write_raw.assert_not_awaited()
