"""PIN responder actions on the pinned ESPHome proxy (firmware/dimplex_pin_responder.yaml).

The actions are executed through the ESPHome config entry's own native API client,
not through ``hass.services``: a service call would put the PIN on the event bus
and into the recorder's ``call_service`` events.
"""

from __future__ import annotations

import logging
from typing import Protocol

from aioesphomeapi import DeviceInfo, UserService
from aioesphomeapi.client import APIClient, ExecuteServiceDataType
from aioesphomeapi.core import APIConnectionError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .errors import HeaterUnavailableError, ResponderMissingError

ARM_ACTION = "dimplex_pin_arm"
CLEAR_ACTION = "dimplex_pin_clear"
# passkey_reply ignores values above 999999; the firmware treats this as
# "target this heater, the PIN follows".
TARGET_ONLY = 1_000_000

_LOGGER = logging.getLogger(__name__)


class _ProxyRuntime(Protocol):
    """The parts of the ESPHome entry's RuntimeEntryData used here."""

    client: APIClient
    services: dict[int, UserService]
    available: bool
    device_info: DeviceInfo | None


def _proxy_entry(hass: HomeAssistant, source: str) -> ConfigEntry | None:
    """Return the loaded ESPHome entry whose Bluetooth scanner is ``source``."""
    for entry in hass.config_entries.async_loaded_entries("esphome"):
        runtime: _ProxyRuntime = entry.runtime_data
        info = runtime.device_info
        # Same derivation as bleak_esphome.connect_scanner's scanner source.
        if info and (info.bluetooth_mac_address or info.mac_address) == source:
            return entry
    return None


def _proxy(hass: HomeAssistant, source: str) -> _ProxyRuntime | None:
    if (entry := _proxy_entry(hass, source)) is None:
        return None
    runtime: _ProxyRuntime = entry.runtime_data
    return runtime


def is_proxy_device(hass: HomeAssistant, source: str, device_id: object) -> bool:
    """Return whether ``device_id`` is the ESPHome device of proxy ``source``.

    ESPHome adds its device registry id to every ``esphome.*`` event it fires.
    """
    entry = _proxy_entry(hass, source)
    if entry is None or not isinstance(device_id, str):
        return False
    device = dr.async_get(hass).async_get(device_id)
    return device is not None and entry.entry_id in device.config_entries


def _action(runtime: _ProxyRuntime | None, name: str) -> UserService | None:
    if runtime is None:
        return None
    return next((s for s in runtime.services.values() if s.name == name), None)


def responder_sources(hass: HomeAssistant) -> set[str]:
    """Return the scanner sources of every loaded proxy with the responder."""
    sources: set[str] = set()
    for entry in hass.config_entries.async_loaded_entries("esphome"):
        runtime: _ProxyRuntime = entry.runtime_data
        info = runtime.device_info
        source = info and (info.bluetooth_mac_address or info.mac_address)
        if source and has_responder(hass, source):
            sources.add(source)
    return sources


def proxy_name(hass: HomeAssistant, source: str) -> str | None:
    """Return the proxy's device name as the user sees it in Home Assistant."""
    if (entry := _proxy_entry(hass, source)) is None:
        return None
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    names = (device.name_by_user or device.name for device in devices)
    return next((name for name in names if name), None)


def has_responder(hass: HomeAssistant, source: str) -> bool:
    """Return whether the proxy exposes both responder actions."""
    runtime = _proxy(hass, source)
    return all(_action(runtime, name) for name in (ARM_ACTION, CLEAR_ACTION))


def is_available(hass: HomeAssistant, source: str) -> bool:
    """Return whether the proxy's ESPHome API is connected right now."""
    runtime = _proxy(hass, source)
    return runtime is not None and runtime.available


async def _execute(
    hass: HomeAssistant, source: str, name: str, data: ExecuteServiceDataType
) -> None:
    runtime = _proxy(hass, source)
    action = _action(runtime, name)
    if runtime is None or action is None:
        raise ResponderMissingError(f"Proxy {source} has no {name} action")
    try:
        await runtime.client.execute_service(action, data)
    except APIConnectionError as err:
        raise HeaterUnavailableError(f"Proxy {source} API unavailable") from err


async def async_arm(
    hass: HomeAssistant, source: str, address: str, passkey: int | None
) -> None:
    """Target the responder at ``address``; with a PIN it answers immediately."""
    _LOGGER.debug(
        "RESPONDER_ARM %s via %s pin=%s",
        address,
        source,
        "target-only" if passkey is None else "set",
    )
    await _execute(
        hass,
        source,
        ARM_ACTION,
        {"address": address, "passkey": TARGET_ONLY if passkey is None else passkey},
    )


async def async_clear(hass: HomeAssistant, source: str, address: str) -> None:
    """Disarm the responder; failures only log (the firmware expires the PIN)."""
    _LOGGER.debug("RESPONDER_CLEAR %s via %s", address, source)
    try:
        await _execute(hass, source, CLEAR_ACTION, {})
    except HeaterUnavailableError as err:
        _LOGGER.debug("RESPONDER_CLEAR %s via %s failed: %s", address, source, err)
