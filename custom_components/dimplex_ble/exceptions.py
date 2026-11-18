"""Translate BLE and protocol failures into Home Assistant action errors."""

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .client import DimplexCharacteristicError, DimplexConnectionError, DimplexError
from .const import DOMAIN
from .protocol import DimplexProtocolError, ProtectedCharacteristicError


def to_homeassistant_error(
    error: DimplexError | DimplexProtocolError,
) -> HomeAssistantError:
    """Return a translated Home Assistant exception for a failed BLE operation."""
    if isinstance(error, ProtectedCharacteristicError):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="protected_characteristic",
        )
    if isinstance(error, DimplexProtocolError):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_gatt_request",
        )
    if isinstance(error, DimplexCharacteristicError):
        return ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="characteristic_unavailable",
        )
    if isinstance(error, DimplexConnectionError):
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
        )
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="cannot_connect",
    )
