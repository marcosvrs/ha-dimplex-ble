"""Select ESPHome proxy routes for Dimplex pairing."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from bleak.backends.device import BLEDevice
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.core import HomeAssistant

from .const import CONF_PROXY_SOURCE, DOMAIN
from .esphome_proxy import (
    find_proxy_entry,
    proxy_supports_passkey,
)
from .esphome_proxy import (
    normalize_address as _normalize_address,
)
from .route_policy import RouteCandidate, rank_route, rank_routes

_LOGGER = logging.getLogger(__name__)
DEBUG_LOG_PREFIX = "[DEBUG-DIMPLEX-PIN-20261117]"


def _assigned_proxy_count(hass: HomeAssistant, source: str) -> int:
    """Count configured panel heaters already assigned to an ESPHome proxy."""
    normalized_source = source.casefold()
    return sum(
        entry.data.get(CONF_PROXY_SOURCE, "").casefold() == normalized_source
        for entry in hass.config_entries.async_entries(DOMAIN)
        if isinstance(entry.data.get(CONF_PROXY_SOURCE), str)
    )


def _route_candidate(hass: HomeAssistant, source: str, rssi: int) -> RouteCandidate:
    """Build normalized route facts from one Home Assistant scanner."""
    scanner = (
        bluetooth.async_scanner_by_source(hass, source)
        if isinstance(hass, HomeAssistant)
        else None
    )
    allocations = scanner.get_allocations() if scanner is not None else None
    free_slots = getattr(allocations, "free", None)
    return RouteCandidate(
        source=source,
        rssi=rssi,
        free_slots=free_slots if type(free_slots) is int else None,
        assigned_heaters=_assigned_proxy_count(hass, source),
    )


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
    ranked = rank_routes(
        [
            _route_candidate(hass, candidate.source, candidate.rssi)
            for candidate in eligible
        ]
    )
    if ranked:
        selected_source = ranked[0].source.casefold()
        selected = max(
            (
                candidate
                for candidate in eligible
                if candidate.source.casefold() == selected_source
            ),
            key=lambda candidate: candidate.rssi,
        )
        _LOGGER.debug(
            "%s Selected pairing route %s (rank=%s)",
            DEBUG_LOG_PREFIX,
            selected.source,
            rank_route(ranked[0]),
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
    fallback_routes = [
        _route_candidate(hass, source, -127)
        for source in known_sources
        if proxy_supports_passkey(hass, source)
    ]
    ranked_fallbacks = rank_routes(fallback_routes)
    if ranked_fallbacks:
        _LOGGER.debug(
            "%s Using a cached passkey-capable Bluetooth route not present in "
            "the current discovery snapshot",
            DEBUG_LOG_PREFIX,
        )
        return ranked_fallbacks[0].source

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
    """Return the device from the scanner that owns this panel heater's bond."""
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
