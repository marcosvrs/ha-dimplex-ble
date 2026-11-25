"""Commissioning, reauth and reconfigure share one path: proxy, pair, PIN."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import probatio
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import responder
from .const import (
    CONF_ADDRESS,
    CONF_BOOST_MINUTES,
    CONF_KEEP_CONNECTED,
    CONF_PASSKEY,
    CONF_PROXY_SOURCE,
    CONF_SYNC_CLOCK,
    DEFAULT_BOOST_MINUTES,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_SYNC_CLOCK,
    DOMAIN,
    PASSKEY_REQUEST_EVENT,
)
from .discovery import advertisement_label, is_dimplex_advertisement
from .errors import DimplexError, HeaterAuthError
from .link import HeaterLink
from .protocol import BOOST_MAX_MINUTES

_LOGGER = logging.getLogger(__name__)
# A released heater re-advertised within seconds on hardware (ledger 15:39).
ADVERTISEMENT_WAIT = 30
# Heaters advertise several times per second; a farther proxy hears one
# within this window after the nearest proxy did.
DISCOVERY_WINDOW = 5


# A proxy already holding this many connections often refuses the uncached
# connect that commissioning always makes, and a third heater on it is refused
# after every Home Assistant restart: the ESP32 controller answers "Rejected:
# Resources" (RC-4, esphome/esphome#22859; hardware 2026-11-24, 4/4 refusals).
BUSY_CONNECTIONS = 2
# ESPHome reports 0 or -127 dBm when it has no real reading.
_NO_RSSI = (0, -127)
# habluetooth names a remote scanner "<name> (<source MAC>)".
_SCANNER_SUFFIX = re.compile(r"\s*\([0-9A-Fa-f:]{17}\)$")


def _friendly_name(hass: HomeAssistant, source: str, scanner_name: str) -> str:
    """The proxy's device name in Home Assistant, never its MAC address."""
    return responder.proxy_name(hass, source) or (
        _SCANNER_SUFFIX.sub("", scanner_name) or scanner_name
    )


@dataclass(frozen=True)
class _Candidate:
    """One responder proxy that hears the heater, as the user should see it.

    Slots come from bleak-esphome's ``scanner.get_allocations()`` (slots,
    free, allocated = addresses connected through the proxy right now). Each
    configured heater keeps ONE persistent link, i.e. one slot, for as long
    as its entry is set up, so heaters pinned to the proxy count even while
    momentarily disconnected.
    """

    source: str
    name: str
    rssi: int | None
    slots: int | None
    free: int | None
    connections: int
    current: bool

    @property
    def full(self) -> bool:
        return self.free is not None and self.free <= 0

    @property
    def busy(self) -> bool:
        return self.full or self.connections >= BUSY_CONNECTIONS

    @property
    def tier(self) -> int:
        """0 free and quiet, 1 free but busy, 2 slots unknown, 3 full."""
        if self.free is None:
            return 2
        if self.full:
            return 3
        return 0 if self.connections < BUSY_CONNECTIONS else 1

    def sort_key(self) -> tuple[int, bool, int, str, str]:
        rssi = -1000 if self.rssi is None else self.rssi
        return (self.tier, not self.current, -rssi, self.name, self.source)

    def label(self, *, recommended: bool) -> str:
        """The proxy's name and at most one short tag; README explains them."""
        if self.full:
            tag = "⚠ full"
        elif self.connections >= BUSY_CONNECTIONS:
            tag = f"⚠ already serves {BUSY_CONNECTIONS} heaters"
        elif self.current:
            tag = "current"
        elif recommended:
            tag = "Recommended"
        else:
            return self.name
        return f"{self.name} · {tag}"


@dataclass
class _ProxySurvey:
    """Selectable proxies (recommended first) and why others are missing."""

    candidates: list[_Candidate] = field(default_factory=list)
    not_selectable: list[str] = field(default_factory=list)

    @property
    def options(self) -> list[SelectOptionDict]:
        return [
            {"value": c.source, "label": c.label(recommended=index == 0)}
            for index, c in enumerate(self.candidates)
        ]

    @property
    def all_busy(self) -> bool:
        """Every proxy is full or already holds two connections."""
        return bool(self.candidates) and all(c.busy for c in self.candidates)

    @property
    def notes(self) -> str:
        """One line naming proxies that hear the heater but cannot be chosen."""
        if not self.not_selectable:
            return ""
        names = ", ".join(sorted(self.not_selectable))
        return f"\n\nNot shown: {names} (missing Dimplex firmware or offline)."


def _survey_proxies(
    hass: HomeAssistant,
    address: str,
    current: str | None = None,
    entry_id: str | None = None,
) -> _ProxySurvey:
    """Rank the proxies that hear the heater: free and quiet, then signal."""
    own = address.upper()
    # Other heaters pinned to a proxy hold a persistent link there (one slot
    # each) even while momentarily disconnected; this heater's own entry is
    # excluded because a repair has just released its link.
    pinned: dict[str, int] = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        source = entry.data.get(CONF_PROXY_SOURCE)
        if entry.entry_id != entry_id and source and entry.disabled_by is None:
            pinned[source] = pinned.get(source, 0) + 1
    survey = _ProxySurvey()
    for seen in bluetooth.async_scanner_devices_by_address(
        hass, address, connectable=True
    ):
        scanner = seen.scanner
        source = scanner.source
        name = _friendly_name(hass, source, scanner.name)
        if not responder.has_responder(hass, source) or not responder.is_available(
            hass, source
        ):
            survey.not_selectable.append(name)
            continue
        allocations = scanner.get_allocations()
        slots = free = None
        connected = 0
        if allocations is not None:
            slots, free = allocations.slots, allocations.free
            others = [a for a in allocations.allocated if a.upper() != own]
            connected = len(others)
            if len(others) < len(allocations.allocated):
                # The just-released link may still read as used briefly.
                free = min(slots, free + 1)
        rssi = seen.advertisement.rssi
        survey.candidates.append(
            _Candidate(
                source=source,
                name=name,
                rssi=None if rssi is None or rssi in _NO_RSSI else rssi,
                slots=slots,
                free=free,
                connections=max(connected, pinned.get(source, 0)),
                current=source == current,
            )
        )
    survey.candidates.sort(key=_Candidate.sort_key)
    return survey


class DimplexBleConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair a panel heater through a proxy while the user types its PIN."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow-local state."""
        self._address: str | None = None
        self._name: str | None = None
        self._discovered: dict[str, str | None] = {}
        self._link: HeaterLink | None = None
        self._pair_task: asyncio.Task[None] | None = None
        self._passkey_requested: asyncio.Future[None] | None = None
        self._unsub_passkey: CALLBACK_TYPE | None = None
        self._waiter: asyncio.Task[None] | None = None
        self._reload_entry_id: str | None = None
        self._pin_error: str | None = None
        self._sync_clock: bool | None = None
        # Set when Home Assistant removed the flow: start no new pairing.
        self._removed = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DimplexOptionsFlow:
        """Return the connection mode options flow."""
        del config_entry
        return DimplexOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Start setup for a panel heater matched by the manifest."""
        if not is_dimplex_advertisement(discovery_info.name):
            return self.async_abort(reason="not_dimplex")
        await self.async_set_unique_id(format_mac(discovery_info.address))
        self._abort_if_unique_id_configured()
        self._address = discovery_info.address.upper()
        self._name = advertisement_label(discovery_info.name)
        self.context["title_placeholders"] = {"name": self._name or self._address}
        return await self.async_step_confirm()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """List nearby, unconfigured panel heaters."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(format_mac(address))
            self._abort_if_unique_id_configured()
            self._address = address
            self._name = self._discovered.get(address)
            return await self.async_step_confirm()
        configured = self._async_current_ids(include_ignore=False)
        for info in bluetooth.async_discovered_service_info(self.hass):
            if (
                is_dimplex_advertisement(info.name)
                and format_mac(info.address) not in configured
            ):
                self._discovered[info.address.upper()] = advertisement_label(info.name)
        if not self._discovered:
            return self.async_abort(reason="no_heaters_found")
        options: list[SelectOptionDict] = [
            {"value": address, "label": label or address}
            for address, label in self._discovered.items()
        ]
        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_ADDRESS): SelectSelector(
                        SelectSelectorConfig(options=options)
                    )
                }
            ),
        )

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the proxy that will hold the bond."""
        return await self._async_step_proxy("confirm", user_input)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """The heater rejected the stored PIN."""
        self._address = str(entry_data[CONF_ADDRESS]).upper()
        self._name = self._get_reauth_entry().title
        # HA creates this flow on its own; nothing is released until the user
        # confirms the repair.
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({}),
            description_placeholders={"name": self._name},
        )

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The user confirmed the repair: choose the proxy."""
        del user_input
        return await self.async_step_reauth_proxy()

    async def async_step_reauth_proxy(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair on the chosen proxy."""
        entry = self._get_reauth_entry()
        return await self._async_step_proxy("reauth_proxy", user_input, entry)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-pair, optionally on another proxy."""
        entry = self._get_reconfigure_entry()
        self._address = str(entry.data[CONF_ADDRESS]).upper()
        self._name = entry.title
        return await self._async_step_proxy("reconfigure", user_input, entry)

    async def async_step_pairing(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pairing started: a PIN request, a bonded success, or a failure decides."""
        del user_input
        task, requested, waiter = (
            self._pair_task,
            self._passkey_requested,
            self._waiter,
        )
        assert task is not None and requested is not None and waiter is not None
        if not waiter.done():
            return self._show_pairing_progress(waiter)
        self._stop_listening()
        if task.done() and not task.cancelled() and task.exception() is None:
            # The proxy already held a valid bond: no passkey, no PIN on display.
            self._pair_task = None
            return self.async_show_progress_done(next_step_id="paired")
        if requested.done():
            # After a rejected PIN the user is already typing: go straight to
            # the form with the error. Otherwise offer the PIN or a re-show.
            next_step = "pin" if self._pin_error else "pin_ready"
            return self.async_show_progress_done(next_step_id=next_step)
        self._pair_task = None  # failed before the heater asked for a PIN
        return self.async_show_progress_done(next_step_id="pairing_failed")

    async def async_step_paired(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify a pairing that needed no PIN and finish."""
        del user_input
        if (result := await self._async_finish(None)) is None:
            return self._show_retry_form("cannot_connect")
        return result

    async def async_step_pairing_failed(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a new pairing attempt on the same proxy."""
        if user_input is None:
            return self._show_retry_form("pairing_not_started")
        return self._start_pairing()

    async def async_step_pin_ready(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The heater shows its PIN: enter it, or have it shown again."""
        del user_input
        return self._show_pin_menu("pin_ready")

    async def async_step_pin_still_shown(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The pairing still waits: its PIN is still on the display."""
        del user_input
        return self._show_pin_menu("pin_still_shown")

    async def async_step_show_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Make the heater display its PIN again.

        A pairing still waiting keeps the PIN on the display and is never
        cancelled (bond safety); only an ended pairing is started anew.
        """
        del user_input
        task = self._pair_task
        if task is not None and not task.done():
            return await self.async_step_pin_still_shown()
        if task is not None and not task.cancelled():
            task.exception()  # ended (e.g. the 30 s timeout); retrieved here
        return self._start_pairing()

    async def async_step_pin(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the PIN shown on the heater while pairing waits for it."""
        if user_input is None:
            error, self._pin_error = self._pin_error, None
            return self._show_pin_form(error)
        pin = str(user_input.get(CONF_PASSKEY) or "").strip()
        if not pin:
            return await self.async_step_show_pin()
        if len(pin) != 6 or not pin.isascii() or not pin.isdecimal():
            return self._show_pin_form("invalid_passkey")
        if error := await self._async_complete_pairing(int(pin)):
            if error == "pin_rejected" and not self._removed:
                # The rejected pairing ended and the display cleared; ask again
                # only once a new pairing shows the PIN (hardware 2026-11-24).
                self._pin_error = error
                return self._start_pairing()
            return self._show_pin_form(error)
        if (result := await self._async_finish(pin)) is None:
            return self._show_pin_form("cannot_connect")
        return result

    def async_remove(self) -> None:
        """Finish cleanup without ever aborting an in-flight pair()."""
        self._removed = True
        self._stop_listening()
        if self._link is not None or self._reload_entry_id is not None:
            self.hass.async_create_background_task(
                self._async_cleanup(self._link, self._pair_task, self._reload_entry_id),
                "dimplex_ble flow cleanup",
            )
        super().async_remove()

    async def _async_step_proxy(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        entry: ConfigEntry | None = None,
    ) -> ConfigFlowResult:
        """Pick the proxy, then start pairing and show the PIN form."""
        assert self._address is not None
        address = self._address
        if user_input is not None and CONF_PROXY_SOURCE in user_input:
            # The choice is made: no second release wait or survey (an empty
            # submit means "look again" and falls through).
            if CONF_SYNC_CLOCK in user_input:
                self._sync_clock = bool(user_input[CONF_SYNC_CLOCK])
            self._link = HeaterLink(
                self.hass, address, user_input[CONF_PROXY_SOURCE], None
            )
            return self._start_pairing()
        repair = entry is not None
        current = entry.data[CONF_PROXY_SOURCE] if entry is not None else None
        entry_id = entry.entry_id if entry is not None else None
        if entry is not None:
            # A connected heater does not advertise, so no proxy can be offered
            # until the running link is released (one central at a time).
            if entry.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_RETRY):
                await self.hass.config_entries.async_unload(entry.entry_id)
                self._reload_entry_id = entry.entry_id
        # Repairs list only proxies heard after the release: history from
        # before it would offer just the proxy that held the link.
        if repair:
            await self._async_wait_for_proxy(address)
        survey = _survey_proxies(self.hass, address, current, entry_id)
        placeholders = {"name": self._name or address, "proxy_notes": survey.notes}
        if not survey.candidates and not repair:
            return self.async_abort(
                reason="no_proxy", description_placeholders=placeholders
            )
        if not survey.candidates:
            # Keep the repair open: the heater may simply be out of sight now.
            return self.async_show_form(
                step_id=step_id,
                data_schema=probatio.Schema({}),
                description_placeholders=placeholders,
                errors={"base": "no_proxy"},
            )
        options = survey.options
        schema: dict[Any, Any] = {
            # The recommended proxy is always listed first.
            probatio.Required(
                CONF_PROXY_SOURCE, default=options[0]["value"]
            ): SelectSelector(SelectSelectorConfig(options=options))
        }
        if step_id != "reauth_proxy":
            sync = (
                entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK)
                if entry is not None
                else DEFAULT_SYNC_CLOCK
            )
            schema[probatio.Required(CONF_SYNC_CLOCK, default=sync)] = bool
        return self.async_show_form(
            step_id=step_id,
            data_schema=probatio.Schema(schema),
            description_placeholders=placeholders,
            errors={"base": "proxies_busy"} if survey.all_busy else {},
        )

    async def _async_wait_for_proxy(self, address: str) -> None:
        """Collect which responder proxies hear the released heater again.

        A released heater re-sends an identical advertisement, which
        change-driven callbacks (async_process_advertisements) never report,
        so every-advertisement callbacks are used. The nearest proxy usually
        hears it first; collection continues until every responder proxy has
        reported or the discovery window after the first one closes, so a
        farther proxy can still be offered (hardware 2026-11-24 16:25).
        """
        expected = responder.responder_sources(self.hass)
        heard: set[str] = set()
        first = asyncio.Event()
        everyone = asyncio.Event()

        @callback
        def _on_advertisement(info: BluetoothServiceInfoBleak) -> None:
            if info.source in expected:
                heard.add(info.source)
                first.set()
                if heard >= expected:
                    everyone.set()

        unsub = bluetooth.async_register_advertisement_callback(
            self.hass, _on_advertisement, address
        )
        try:
            async with asyncio.timeout(ADVERTISEMENT_WAIT):
                await first.wait()
            with suppress(TimeoutError):
                async with asyncio.timeout(DISCOVERY_WINDOW):
                    await everyone.wait()
        except TimeoutError:
            _LOGGER.debug("COMMISSION %s not advertising to a responder", address)
        finally:
            unsub()
        # The callback runs before the manager records the advertisement.
        await asyncio.sleep(0)
        _LOGGER.debug(
            "COMMISSION %s heard by %s of %s", address, sorted(heard), sorted(expected)
        )

    def _start_pairing(self) -> ConfigFlowResult:
        """Start pairing target-only and wait for its first outcome."""
        link = self._link
        assert link is not None
        self._pair_task = self.hass.async_create_task(
            link.async_pair(None), "dimplex_ble commission pair"
        )
        requested: asyncio.Future[None] = self.hass.loop.create_future()
        self._passkey_requested = requested

        @callback
        def _on_passkey_request(event: Event[dict[str, Any]]) -> None:
            # The firmware fires this when the heater asks the proxy for the
            # passkey, i.e. when the PIN is on the heater display.
            if (
                not requested.done()
                and str(event.data.get("address", "")).upper() == link.address
                and responder.is_proxy_device(
                    self.hass, link.source, event.data.get("device_id")
                )
            ):
                requested.set_result(None)

        self._stop_listening()
        self._unsub_passkey = self.hass.bus.async_listen(
            PASSKEY_REQUEST_EVENT, _on_passkey_request
        )
        self._waiter = self.hass.async_create_task(
            self._async_first_outcome(self._pair_task, requested),
            "dimplex_ble commission wait",
        )
        return self._show_pairing_progress(self._waiter)

    @staticmethod
    async def _async_first_outcome(
        task: asyncio.Task[None], requested: asyncio.Future[None]
    ) -> None:
        """Return once pairing ended or the heater asked for its PIN.

        asyncio.wait never cancels ``task``: a removed flow cancels only this
        waiter, and pair() must run to its native end.
        """
        await asyncio.wait((task, requested), return_when=asyncio.FIRST_COMPLETED)

    def _show_pairing_progress(self, waiter: asyncio.Task[None]) -> ConfigFlowResult:
        return self.async_show_progress(
            step_id="pairing",
            progress_action="pairing",
            progress_task=waiter,
            description_placeholders={"name": self._name or str(self._address)},
        )

    @callback
    def _stop_listening(self) -> None:
        if self._unsub_passkey is not None:
            self._unsub_passkey()
            self._unsub_passkey = None

    async def _async_finish(self, pin: str | None) -> ConfigFlowResult | None:
        """Verify with a snapshot, release the link, and store the pairing.

        Without ``pin`` (bond already on the proxy) a repair keeps the stored
        PIN and a new entry is created without one.
        """
        link = self._link
        assert link is not None
        try:
            await link.async_snapshot()
        except DimplexError:
            return None
        await self._async_release()
        data = {CONF_PROXY_SOURCE: link.source}
        if pin is not None:
            data[CONF_PASSKEY] = pin
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            self._reload_entry_id = None
            entry = self._get_repair_entry()
            if self._sync_clock is None:
                return self.async_update_reload_and_abort(entry, data_updates=data)
            return self.async_update_reload_and_abort(
                entry,
                data_updates=data,
                options={**entry.options, CONF_SYNC_CLOCK: self._sync_clock},
            )
        sync_clock = (
            DEFAULT_SYNC_CLOCK if self._sync_clock is None else self._sync_clock
        )
        return self.async_create_entry(
            title=f"Dimplex Heater {self._name or link.address}",
            data={CONF_ADDRESS: link.address, **data},
            options={CONF_SYNC_CLOCK: sync_clock},
        )

    async def _async_complete_pairing(self, pin: int) -> str | None:
        """Deliver the PIN to the waiting pairing, or retry once with it armed.

        The pairing task stays on the flow until it has ended, so a flow
        removed meanwhile hands it to the cleanup, which waits for it before
        closing the link (RC-2: never abandon SMP).
        """
        link = self._link
        assert link is not None
        task = self._pair_task
        if task is not None:
            try:
                if not task.done():
                    # Armed only while this pairing holds the proxy's lock.
                    await link.async_deliver_pin(pin)
                await asyncio.shield(task)
            except HeaterAuthError:
                return "pin_rejected"
            except DimplexError as err:
                if not task.done():
                    return "cannot_connect"  # the PIN could not be delivered
                _LOGGER.debug("COMMISSION %s first pair failed: %s", link.address, err)
            else:
                return None
            finally:
                if task.done():
                    self._pair_task = None
        if self._removed:
            return "cannot_connect"  # an abandoned flow starts no new pairing
        # A pairing with the PIN armed before connect; tracked like the first.
        retry = self.hass.async_create_task(
            link.async_pair(pin), "dimplex_ble commission pair with pin"
        )
        self._pair_task = retry
        try:
            await asyncio.shield(retry)
        except HeaterAuthError:
            return "pin_rejected"
        except DimplexError:
            return "pairing_failed"
        finally:
            if retry.done():
                self._pair_task = None
        return None

    async def _async_release(self) -> None:
        """Close the temporary link; its pairing already cleared the responder."""
        link, self._link = self._link, None
        assert link is not None
        await link.async_close()

    async def _async_cleanup(
        self,
        link: HeaterLink | None,
        task: asyncio.Task[None] | None,
        reload_entry_id: str | None,
    ) -> None:
        """Wait for pairing to end, close, and restore a paused entry.

        The responder is never cleared here: it is shared by every heater on
        the proxy, and the pairing that armed it clears it under the proxy's
        security lock when it ends (link.py), so a clear from here could
        disarm another heater's pairing.
        """
        if task is not None:
            await asyncio.wait((task,))
            if not task.cancelled():
                task.exception()
        if link is not None:
            await link.async_close()
        if reload_entry_id is not None:
            self.hass.config_entries.async_schedule_reload(reload_entry_id)

    def _get_repair_entry(self) -> ConfigEntry:
        """Return the entry being repaired or reconfigured."""
        if self.source == SOURCE_RECONFIGURE:
            return self._get_reconfigure_entry()
        return self._get_reauth_entry()

    def _show_retry_form(self, error: str) -> ConfigFlowResult:
        """Pairing ended without a PIN request; submit to pair again."""
        return self.async_show_form(
            step_id="pairing_failed",
            data_schema=probatio.Schema({}),
            description_placeholders={"name": self._name or str(self._address)},
            errors={"base": error},
        )

    def _show_pin_menu(self, step_id: str) -> ConfigFlowResult:
        """Offer the PIN form or showing the PIN on the display again."""
        return self.async_show_menu(
            step_id=step_id,
            menu_options=["pin", "show_pin"],
            description_placeholders={"name": self._name or str(self._address)},
        )

    def _show_pin_form(self, error: str | None = None) -> ConfigFlowResult:
        """Ask for the six-digit PIN; empty shows it on the display again."""
        return self.async_show_form(
            step_id="pin",
            data_schema=probatio.Schema(
                {
                    probatio.Optional(CONF_PASSKEY): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD, autocomplete="off"
                        )
                    )
                }
            ),
            description_placeholders={"name": self._name or str(self._address)},
            errors={"base": error} if error else {},
        )


class DimplexOptionsFlow(OptionsFlowWithReload):
    """Connection, clock sync and Boost length; saving reloads the entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the heater settings."""
        if user_input is not None:
            # The number selector returns a float; the heater takes whole minutes.
            minutes = int(user_input[CONF_BOOST_MINUTES])
            return self.async_create_entry(
                data={**user_input, CONF_BOOST_MINUTES: minutes}
            )
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=probatio.Schema(
                {
                    probatio.Required(
                        CONF_KEEP_CONNECTED,
                        default=options.get(
                            CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED
                        ),
                    ): bool,
                    probatio.Required(
                        CONF_SYNC_CLOCK,
                        default=options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK),
                    ): bool,
                    probatio.Required(
                        CONF_BOOST_MINUTES,
                        default=options.get(CONF_BOOST_MINUTES, DEFAULT_BOOST_MINUTES),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=1,
                            max=BOOST_MAX_MINUTES,
                            step=1,
                            unit_of_measurement=UnitOfTime.MINUTES,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                }
            ),
        )
