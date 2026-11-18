from __future__ import annotations

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.dimplex_ble.client import (
    DimplexCharacteristicError,
    DimplexConnectionError,
    DimplexError,
)
from custom_components.dimplex_ble.const import DOMAIN
from custom_components.dimplex_ble.exceptions import to_homeassistant_error
from custom_components.dimplex_ble.protocol import (
    DimplexProtocolError,
    ProtectedCharacteristicError,
)


@pytest.mark.parametrize(
    ("error", "expected_type", "translation_key"),
    [
        (ProtectedCharacteristicError("private characteristic"), ServiceValidationError, "protected_characteristic"),
        (DimplexProtocolError("bad request"), ServiceValidationError, "invalid_gatt_request"),
        (DimplexCharacteristicError("private address"), ServiceValidationError, "characteristic_unavailable"),
        (DimplexConnectionError("PIN 123456"), HomeAssistantError, "cannot_connect"),
        (DimplexError("private address and PIN 123456"), HomeAssistantError, "cannot_connect"),
    ],
)
async def test_errors_translate_to_safe_consumer_facing_exceptions(
    hass,
    error: Exception,
    expected_type: type[Exception],
    translation_key: str,
) -> None:
    translated = to_homeassistant_error(error)
    assert isinstance(translated, expected_type)
    assert translated.translation_domain == DOMAIN
    assert translated.translation_key == translation_key
    assert "123456" not in str(translated)
    assert "private address" not in str(translated)
