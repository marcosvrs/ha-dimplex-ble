"""ESPHome-native adapter used by Dimplex pairing and runtime routes."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

ESPHOME_DOMAIN = "esphome"
CONF_BLUETOOTH_MAC_ADDRESS = "bluetooth_mac_address"
CONF_DEVICE_NAME = "device_name"
CONF_NOISE_PSK = "noise_psk"

PAIRING_ARM_ACTION = "arm_dimplex_pairing"
PAIRING_ACTION = "set_dimplex_pairing_passkey"
PAIRING_CANCEL_ACTION = "cancel_dimplex_pairing"
PAIRING_TIMEOUT = 180

_LOGGER = logging.getLogger(__name__)
DEBUG_LOG_PREFIX = "[DEBUG-DIMPLEX-PIN-20261117]"


class DimplexPairingError(Exception):
    """The selected proxy cannot complete first-time passkey pairing."""


class DimplexPairingInProgressError(DimplexPairingError):
    """The selected proxy is already pairing with another panel heater."""


def normalize_address(address: str) -> str:
    """Normalize a Bluetooth address without changing its identity."""
    return address.replace(":", "").replace("-", "").casefold()


def find_proxy_entry(hass: HomeAssistant, source: str) -> ConfigEntry[Any] | None:
    """Find the loaded ESPHome entry that owns one Bluetooth scanner."""
    normalized_source = normalize_address(source)
    for entry in hass.config_entries.async_loaded_entries(ESPHOME_DOMAIN):
        addresses = (
            entry.data.get(CONF_BLUETOOTH_MAC_ADDRESS),
            getattr(entry, "unique_id", None),
        )
        if any(
            isinstance(address, str) and normalize_address(address) == normalized_source
            for address in addresses
        ):
            return entry
    return None


def proxy_action_name(entry: ConfigEntry[Any], action: str = PAIRING_ACTION) -> str:
    """Build the Home Assistant action name used by ESPHome's native API."""
    device_name = entry.data.get(CONF_DEVICE_NAME)
    if not isinstance(device_name, str) or not device_name:
        raise DimplexPairingError("The ESPHome proxy has no registered device name")
    return f"{device_name.replace('-', '_')}_{action}"


def proxy_supports_passkey(hass: HomeAssistant, source: str) -> bool:
    """Return whether this encrypted proxy exposes the complete pairing relay."""
    entry = find_proxy_entry(hass, source)
    if entry is None or not entry.data.get(CONF_NOISE_PSK):
        return False
    try:
        actions = (
            proxy_action_name(entry, PAIRING_ARM_ACTION),
            proxy_action_name(entry, PAIRING_ACTION),
            proxy_action_name(entry, PAIRING_CANCEL_ACTION),
        )
    except DimplexPairingError:
        return False
    return all(hass.services.has_service(ESPHOME_DOMAIN, action) for action in actions)


async def _async_call_proxy_action(
    hass: HomeAssistant,
    source: str,
    action: str,
    data: dict[str, str],
) -> None:
    """Call one encrypted action on the ESPHome proxy for this panel heater."""
    entry = find_proxy_entry(hass, source)
    if entry is None:
        raise DimplexPairingError("The selected ESPHome Bluetooth proxy is unavailable")
    if not entry.data.get(CONF_NOISE_PSK):
        raise DimplexPairingError("The ESPHome proxy native API must be encrypted")
    service = proxy_action_name(entry, action)
    if not hass.services.has_service(ESPHOME_DOMAIN, service):
        raise DimplexPairingError(
            "The ESPHome proxy needs the Dimplex passkey component installed"
        )
    try:
        await hass.services.async_call(ESPHOME_DOMAIN, service, data, blocking=True)
    except HomeAssistantError as err:
        _LOGGER.warning(
            "%s ESPHome pairing action %s failed (%s)",
            DEBUG_LOG_PREFIX,
            action,
            type(err).__name__,
        )
        raise


async def async_arm_pairing(hass: HomeAssistant, source: str, address: str) -> None:
    """Arm one address-scoped, short-lived pairing attempt on its proxy."""
    await _async_call_proxy_action(
        hass,
        source,
        PAIRING_ARM_ACTION,
        {"address": address.replace(":", "")},
    )


async def async_send_passkey(
    hass: HomeAssistant, source: str, address: str, passkey: str
) -> None:
    """Send a short-lived passkey to the ESPHome proxy."""
    await _async_call_proxy_action(
        hass,
        source,
        PAIRING_ACTION,
        {"address": address.replace(":", ""), "passkey": passkey},
    )


async def async_cancel_pairing(hass: HomeAssistant, source: str) -> None:
    """Clear pending passkey state after the pairing attempt ends."""
    await _async_call_proxy_action(hass, source, PAIRING_CANCEL_ACTION, {})
