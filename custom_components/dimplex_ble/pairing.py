"""ESPHome proxy selection and one-time PIN forwarding."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_PROXY_SOURCE, DOMAIN

# ESPHome config-entry keys; avoid importing its eager package initializer.
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


def _normalize_address(address: str) -> str:
    """Normalize a Bluetooth address without changing its identity."""
    return address.replace(":", "").replace("-", "").casefold()


_PAIRING_LOCKS = "proxy_pairing_locks"


def proxy_pairing_lock(hass: HomeAssistant, source: str) -> asyncio.Lock:
    """Return one lock per proxy so concurrent flows cannot replace a pending PIN."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    locks: dict[str, asyncio.Lock] = domain_data.setdefault(_PAIRING_LOCKS, {})
    key = source.casefold()
    lock = locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        locks[key] = lock
    return lock


class DimplexPairingError(Exception):
    """The selected proxy cannot complete first-time passkey pairing."""


class DimplexPairingInProgressError(DimplexPairingError):
    """The selected proxy is already pairing with another heater."""


def find_proxy_entry(hass: HomeAssistant, source: str) -> ConfigEntry[Any] | None:
    """Find the loaded ESPHome entry that owns one Bluetooth scanner."""
    normalized_source = _normalize_address(source)
    for entry in hass.config_entries.async_loaded_entries(ESPHOME_DOMAIN):
        addresses = (
            entry.data.get(CONF_BLUETOOTH_MAC_ADDRESS),
            getattr(entry, "unique_id", None),
        )
        if any(
            isinstance(address, str)
            and _normalize_address(address) == normalized_source
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


def _assigned_proxy_count(hass: HomeAssistant, source: str) -> int:
    """Count configured heaters already assigned to an ESPHome proxy."""
    normalized_source = source.casefold()
    return sum(
        entry.data.get(CONF_PROXY_SOURCE, "").casefold() == normalized_source
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(entry.data.get(CONF_PROXY_SOURCE), str)
    )


def _route_rank(
    hass: HomeAssistant,
    candidate: BluetoothServiceInfoBleak,
) -> tuple[int, int, int, int]:
    """Rank a passkey-capable route for safe assignment-time balancing."""
    source = candidate.source
    scanner = (
        bluetooth.async_scanner_by_source(hass, source)
        if source and isinstance(hass, HomeAssistant)
        else None
    )
    allocations = scanner.get_allocations() if scanner is not None else None
    if allocations is not None and allocations.free <= 0:
        return (-1, 0, 0, candidate.rssi)

    # Prefer routes with a known free slot. The fallback keeps host/local
    # adapters eligible when their allocation is not exposed.
    allocation_known = int(allocations is not None)
    free_slots = allocations.free if allocations is not None else 0
    assigned_heaters = _assigned_proxy_count(hass, source) if source else 0
    return (allocation_known, free_slots, -assigned_heaters, candidate.rssi)


def _source_has_capacity(hass: HomeAssistant, source: str) -> bool:
    """Return whether a known proxy route can accept a new GATT connection."""
    if not isinstance(hass, HomeAssistant):
        return True
    scanner = bluetooth.async_scanner_by_source(hass, source)
    allocations = scanner.get_allocations() if scanner is not None else None
    return allocations is None or allocations.free > 0

def select_proxy_source(
    hass: HomeAssistant,
    address: str,
    service_infos: Sequence[BluetoothServiceInfoBleak],
) -> str | None:
    """Choose a route for new pairing using capacity, load, then RSSI."""
    normalized_address = _normalize_address(address)
    candidates = [
        info
        for info in service_infos
        if info.connectable
        and info.source
        and _normalize_address(info.device.address) == normalized_address
    ]
    eligible = [
        candidate
        for candidate in candidates
        if proxy_supports_passkey(hass, candidate.source)
    ]
    selected = max(
        (candidate for candidate in eligible if _route_rank(hass, candidate)[0] >= 0),
        key=lambda candidate: _route_rank(hass, candidate),
        default=None,
    )
    if selected is not None:
        _LOGGER.debug(
            "%s Selected pairing route %s (rank=%s)",
            DEBUG_LOG_PREFIX,
            selected.source,
            _route_rank(hass, selected),
        )
        return selected.source

    # A Bluetooth discovery callback can originate from the host adapter while
    # the ESPHome proxy's cached route is not in the current service-info
    # snapshot.  The scanner registry still knows the source-specific route;
    # use it as a pairing fallback rather than rejecting a valid proxy route.
    known_sources = {
        scanner_device.scanner.source
        for scanner_device in bluetooth.async_scanner_devices_by_address(
            hass, address.upper(), connectable=True
        )
        if scanner_device.scanner.source
    }
    fallback_sources = [
        source
        for source in known_sources
        if _source_has_capacity(hass, source)
        and proxy_supports_passkey(hass, source)
    ]
    if fallback_sources:
        _LOGGER.debug(
            "%s Using a cached passkey-capable Bluetooth route not present in "
            "the current discovery snapshot",
            DEBUG_LOG_PREFIX,
        )
        return min(
            fallback_sources,
            key=lambda source: (
                _assigned_proxy_count(hass, source),
                source.casefold(),
            ),
        )

    sources = [candidate.source for candidate in candidates]
    matching_entries = sum(
        find_proxy_entry(hass, source) is not None for source in sources
    )
    _LOGGER.warning(
        "No passkey-capable ESPHome route "
        "(connectable_candidates=%d, "
        "candidates_with_source=%d, matching_entries=%d)",
        len(candidates),
        len(sources),
        matching_entries,
    )
    return None


def ble_device_from_source(
    hass: HomeAssistant, address: str, source: str
) -> BLEDevice | None:
    """Return the device from the scanner that owns this heater's bond."""
    normalized_address = address.upper()
    normalized_source = source.casefold()
    scanner_devices = bluetooth.async_scanner_devices_by_address(
        hass, normalized_address, connectable=True
    )
    for scanner_device in scanner_devices:
        if scanner_device.scanner.source.casefold() == normalized_source:
            _LOGGER.debug(
                "%s Found a source-specific Bluetooth route",
                DEBUG_LOG_PREFIX,
            )
            return scanner_device.ble_device
    _LOGGER.debug(
        "%s No source-specific Bluetooth route is currently available",
        DEBUG_LOG_PREFIX,
    )
    return None


async def _async_call_proxy_action(
    hass: HomeAssistant,
    source: str,
    action: str,
    data: dict[str, str],
) -> None:
    """Call one encrypted action on the ESPHome proxy that owns this heater."""
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
    """Send a short-lived passkey to the ESPHome proxy over its API action."""
    await _async_call_proxy_action(
        hass,
        source,
        PAIRING_ACTION,
        {"address": address.replace(":", ""), "passkey": passkey},
    )


async def async_cancel_pairing(hass: HomeAssistant, source: str) -> None:
    """Clear any pending passkey state after the pairing attempt ends."""
    await _async_call_proxy_action(hass, source, PAIRING_CANCEL_ACTION, {})


async def _async_run_pairing(
    hass: HomeAssistant,
    source: str,
    pair: Callable[[], Awaitable[None]],
    lock: asyncio.Lock,
) -> None:
    """Run pairing with a bounded wait, then clear relay state and release lock."""
    try:
        await asyncio.wait_for(pair(), timeout=PAIRING_TIMEOUT)
    except asyncio.CancelledError:
        _LOGGER.warning(
            "%s BLE pairing task cancelled",
            DEBUG_LOG_PREFIX,
        )
        raise
    except TimeoutError:
        _LOGGER.warning(
            "%s BLE pairing attempt timed out after %d seconds",
            DEBUG_LOG_PREFIX,
            PAIRING_TIMEOUT,
        )
        raise
    except Exception as err:
        _LOGGER.warning(
            "%s BLE pairing attempt failed (%s)",
            DEBUG_LOG_PREFIX,
            type(err).__name__,
            exc_info=True,
        )
        raise
    finally:
        try:
            await async_cancel_pairing(hass, source)
        except (DimplexPairingError, HomeAssistantError):
            _LOGGER.debug(
                "%s Could not clear the ESPHome pairing relay",
                DEBUG_LOG_PREFIX,
                exc_info=True,
            )
        finally:
            lock.release()


async def async_start_pairing(
    hass: HomeAssistant,
    source: str,
    address: str,
    pair: Callable[[], Awaitable[None]],
) -> asyncio.Task[None]:
    """Arm the relay, then pair in the background while holding its proxy lock."""
    lock = proxy_pairing_lock(hass, source)
    if lock.locked():
        raise DimplexPairingInProgressError(
            "The selected ESPHome proxy is already pairing with another heater"
        )
    await lock.acquire()
    try:
        await async_arm_pairing(hass, source, address)
        return hass.async_create_task(_async_run_pairing(hass, source, pair, lock))
    except BaseException:
        lock.release()
        raise
