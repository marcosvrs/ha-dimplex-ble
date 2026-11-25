"""Raise a repair when a heater's radio stalls after its link ended abnormally.

Hardware 2026-11-24: twice a link ended without our disconnect (supervision
timeout) and the heater then stopped advertising to every receiver until it
was power-cycled at the wall; after ~50 clean disconnects it always advertised
again within seconds. So: a lost link plus silence from the heater on every
scanner for ``STALL_WINDOW`` seconds means the user has to power-cycle it.

A lost link whose proxy went offline (ESPHome API down, or reconnected with a
new scanner object) is a proxy problem, not a heater stall, and raises nothing.

The start of the silence survives setup retries: a heater that stalls during
the first update after a restart leaves its entry retrying setup, and every
retry builds a new monitor (AM6532 2026-11-25 12:23, no repair for 10+ min).
"""

from __future__ import annotations

import logging
from datetime import datetime

from bluetooth_data_tools import monotonic_time_coarse as monotonic
from habluetooth import BaseHaScanner
from homeassistant.components import bluetooth
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later

from . import responder
from .const import DOMAIN

STALL_WINDOW = 60

_LOGGER = logging.getLogger(__name__)


def stall_issue_id(entry_id: str) -> str:
    """Return the repair issue id of one config entry's heater."""
    return f"heater_stalled_{entry_id}"


def silent_since(hass: HomeAssistant) -> dict[str, tuple[float, BaseHaScanner | None]]:
    """Issue id -> (when the silence began, on which scanner); outlives retries."""
    since: dict[str, tuple[float, BaseHaScanner | None]] = hass.data.setdefault(
        f"{DOMAIN}_silent_since", {}
    )
    return since


class StallMonitor:
    """Watch one heater for silence after an abnormal link loss."""

    def __init__(
        self, hass: HomeAssistant, entry_id: str, name: str, address: str, source: str
    ) -> None:
        """Bind the monitor to one config entry's heater and pinned proxy."""
        self._hass = hass
        self._address = address
        self._source = source
        self._name = name
        self.issue_id = stall_issue_id(entry_id)
        self._scanner: BaseHaScanner | None = None
        self._cancel_timer: CALLBACK_TYPE | None = None
        # A repair raised before a reload is still in the registry.
        self._raised = (
            ir.async_get(hass).async_get_issue(DOMAIN, self.issue_id) is not None
        )
        self._stopped = False

    def _proxy_online(self, scanner: BaseHaScanner | None) -> bool:
        """Return whether the proxy that held the link is still the same, up one."""
        return (
            scanner is not None
            and bluetooth.async_scanner_by_source(self._hass, self._source) is scanner
            and responder.is_available(self._hass, self._source)
        )

    @callback
    def async_link_lost(self, scanner: BaseHaScanner | None) -> None:
        """Start the silence window: the link dropped, or the proxy lost the heater.

        A window already running is kept, so repeated polls cannot postpone it.
        """
        if self._stopped or self._hass.is_stopping:
            return
        if not self._proxy_online(scanner):
            silent_since(self._hass).pop(self.issue_id, None)  # a proxy problem
            return
        if self._cancel_timer is not None:
            return
        self._scanner = scanner
        remaining = STALL_WINDOW - (monotonic() - self._silence_start(scanner))
        if remaining <= 0:
            self._raise()
            return
        self._cancel_timer = async_call_later(
            self._hass, remaining, self._async_window_expired
        )

    def _silence_start(self, scanner: BaseHaScanner | None) -> float:
        """Return when the silence began; a gap no monitor watched resets it.

        Between setup retries nothing listens: a sighting in HA's Bluetooth
        history after the start, or another scanner (the proxy rebooted), means
        the silence was interrupted (Codex review).
        """
        store = silent_since(self._hass)
        carried = store.get(self.issue_id)
        if carried is not None:
            heard = bluetooth.async_last_service_info(
                self._hass, self._address, connectable=False
            )
            if carried[1] is scanner and (heard is None or heard.time <= carried[0]):
                return carried[0]
        store[self.issue_id] = (monotonic(), scanner)
        return store[self.issue_id][0]

    @callback
    def async_heard(self) -> None:
        """The heater is alive (advertisement or snapshot): cancel and clear."""
        self._cancel()
        silent_since(self._hass).pop(self.issue_id, None)
        if self._raised:
            self._raised = False
            ir.async_delete_issue(self._hass, DOMAIN, self.issue_id)
            _LOGGER.info("Heater %s responds again; repair cleared", self._address)

    @callback
    def async_stop(self) -> None:
        """Cancel the pending window for good (unload)."""
        self._stopped = True
        self._cancel()

    @callback
    def _cancel(self) -> None:
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

    @callback
    def _async_window_expired(self, now: datetime) -> None:
        del now
        self._cancel_timer = None
        self._raise()

    @callback
    def _raise(self) -> None:
        if not self._proxy_online(self._scanner):
            silent_since(self._hass).pop(self.issue_id, None)  # a proxy problem
            return
        if self._raised:
            return
        self._raised = True
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            self.issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="heater_stalled",
            translation_placeholders={"name": self._name},
        )
        _LOGGER.warning(
            "Heater %s silent %d s after its link dropped; power-cycle it at the wall",
            self._address,
            STALL_WINDOW,
        )
