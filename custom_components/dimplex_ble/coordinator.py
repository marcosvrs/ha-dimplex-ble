"""Coordinator for a single Dimplex panel heater."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from datetime import datetime, timedelta

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_KEEP_CONNECTED,
    DEFAULT_KEEP_CONNECTED,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    POLL_INTERVAL_PER_POLL,
)
from .errors import DimplexError, HeaterAuthError, to_homeassistant_error
from .link import HeaterLink, WeekSchedule
from .protocol import (
    AWAY_MODE,
    AWAY_TEMPERATURE_MAX_C,
    AWAY_TEMPERATURE_MIN_C,
    PRESET_HOME_ALL_DAY,
    PRESET_OUT_ALL_DAY,
    PRESET_USER_TIMER,
    ROOM_TEMPERATURE_OFFSET,
    SETBACK,
    SOUND,
    TEMPERATURE_OFFSET_LIMIT_C,
    TIMER_SUBMODE_HOME,
    TIMER_SUBMODE_OUT,
    TIMER_SUBMODE_USER_TIMER,
    DimplexProtocolError,
    GattAddress,
    HeaterSnapshot,
    encode_away_mode,
    encode_setback,
    encode_sound,
    encode_temperature_offset,
    parse_setback,
    parse_sound_enabled,
    parse_temperature_offset,
)
from .stall import StallMonitor

_LOGGER = logging.getLogger(__name__)

type DimplexConfigEntry = ConfigEntry[DimplexCoordinator]

# Timer programmes and their schedule record id (ConfigR TimerModeDetails).
SCHEDULE_PROGRAMMES: dict[str, int] = {
    PRESET_USER_TIMER: TIMER_SUBMODE_USER_TIMER,
    PRESET_HOME_ALL_DAY: TIMER_SUBMODE_HOME,
    PRESET_OUT_ALL_DAY: TIMER_SUBMODE_OUT,
}

# Settings that only change when someone changes them: read on the slow
# schedule, each with the decoder that accepts its value.
SLOW_SETTINGS: dict[GattAddress, Callable[[bytes | None], object | None]] = {
    SOUND: parse_sound_enabled,
    SETBACK: parse_setback,
    ROOM_TEMPERATURE_OFFSET: parse_temperature_offset,
}


def keep_connected(entry: ConfigEntry) -> bool:
    """Return the entry's connection mode option (persistent by default)."""
    return bool(entry.options.get(CONF_KEEP_CONNECTED, DEFAULT_KEEP_CONNECTED))


class DimplexCoordinator(DataUpdateCoordinator[HeaterSnapshot]):
    """Poll the heater through its link and map failures once."""

    config_entry: DimplexConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: DimplexConfigEntry, link: HeaterLink
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=DEFAULT_UPDATE_INTERVAL
            if keep_connected(entry)
            else timedelta(seconds=POLL_INTERVAL_PER_POLL),
            always_update=False,
        )
        self.link = link
        # Last verified raw value of each slow setting.
        self.settings: dict[GattAddress, bytes] = {}
        # Per-poll mode only: the settings read is due on the next poll's connection.
        self._settings_due = True
        # Timer programme -> 7 days (Monday first); a day is None when unread.
        self.schedules: dict[str, WeekSchedule] = {}
        # Per-poll mode only: the schedule read is due on the next poll's connection.
        self._schedules_due = True
        # Serializes every Away check-and-write with the writes that leave Away:
        # ANY write of the Away record makes the heater enter Away (ledger,
        # 2026-11-24 18:44/18:48), so a stale "active" check must never write
        # after Away was left. Never held across polls.
        self._away_lock = asyncio.Lock()
        self.stall = StallMonitor(
            hass, entry.entry_id, entry.title, link.address, link.source
        )
        link.on_link_lost = self.stall.async_link_lost

    async def _async_update_data(self) -> HeaterSnapshot:
        """Read one snapshot; a rejected PIN starts reauth and stops polling."""
        try:
            if self.link.keep_connected or not (
                self._settings_due or self._schedules_due
            ):
                snapshot = await self.link.async_snapshot()
            else:
                (
                    snapshot,
                    settings,
                    schedules,
                ) = await self.link.async_snapshot_with_settings(
                    tuple(SLOW_SETTINGS) if self._settings_due else (),
                    tuple(SCHEDULE_PROGRAMMES.values()) if self._schedules_due else (),
                )
                if self._settings_due:
                    self._settings_due = False
                    self._apply_settings(settings)
                if self._schedules_due and schedules:
                    self._schedules_due = False
                    self._apply_schedules(schedules)
        except HeaterAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key=err.translation_key
            ) from err
        except DimplexError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN, translation_key=err.translation_key
            ) from err
        self.stall.async_heard()
        return snapshot

    async def async_shutdown(self) -> None:
        """Cancel the stall window, then stop polling."""
        self.stall.async_stop()
        await super().async_shutdown()

    @callback
    def async_handle_advertisement(
        self, service_info: BluetoothServiceInfoBleak
    ) -> None:
        """Refresh at once when the pinned proxy sees an unavailable heater again.

        Registered for every advertisement: a released heater re-sends identical
        data, which change-driven callbacks never report. Any scanner hearing it
        also proves the heater's radio has not stalled.
        """
        self.stall.async_heard()
        if (
            self.last_update_success
            or service_info.source != self.link.source
            or isinstance(self.last_exception, ConfigEntryAuthFailed)
        ):
            return
        # The native debouncer coalesces bursts of advertisements.
        self.hass.async_create_task(self.async_request_refresh())

    async def async_write(
        self, address: GattAddress, payload: bytes, *, refresh: bool = True
    ) -> None:
        """Write a setting, mapping failures to Home Assistant errors."""
        try:
            await self.link.async_write(address, payload)
        except HeaterAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise to_homeassistant_error(err) from err
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err
        if refresh:
            await self.async_request_refresh()

    @property
    def sound_value(self) -> bytes | None:
        """Return the last verified Sound payload."""
        return self.settings.get(SOUND)

    @sound_value.setter
    def sound_value(self, value: bytes | None) -> None:
        """Replace the cached Sound payload."""
        if value is None:
            self.settings.pop(SOUND, None)
        else:
            self.settings[SOUND] = value

    async def async_refresh_settings(self, now: datetime | None = None) -> None:
        """Read the slow settings on their own slow schedule."""
        del now
        if not self.link.keep_connected:
            # Never open a second connection: the next poll reads them.
            self._settings_due = True
            return
        if not self.last_update_success:
            return
        await self._async_read_settings(tuple(SLOW_SETTINGS))

    async def async_refresh_schedules(self, now: datetime | None = None) -> None:
        """Read the three timer programmes (read-only) on their slow schedule.

        Schedules change only on the heater or in the Dimplex app.
        """
        del now
        if not self.link.keep_connected:
            # Never open a second connection: the next poll reads them.
            self._schedules_due = True
            return
        if not self.last_update_success:
            return
        try:
            schedules = await self.link.async_read_schedules(
                tuple(SCHEDULE_PROGRAMMES.values())
            )
        except DimplexError as err:
            _LOGGER.debug("Schedule read failed for %s: %s", self.link.address, err)
            return
        self._apply_schedules(schedules)

    def _apply_schedules(self, schedules: dict[int, WeekSchedule]) -> None:
        """Store the programmes by preset and notify listeners on change."""
        by_preset = {
            preset: schedules[programme]
            for preset, programme in SCHEDULE_PROGRAMMES.items()
            if programme in schedules
        }
        if by_preset != self.schedules:
            self.schedules = by_preset
            self.async_update_listeners()

    async def _async_read_settings(self, addresses: tuple[GattAddress, ...]) -> None:
        """Read settings over the held link; stop at the first failure."""
        values: dict[GattAddress, bytes | None] = {}
        for address in addresses:
            try:
                values[address] = await self.link.async_read(address)
            except DimplexError as err:
                _LOGGER.debug("Setting read failed for %s: %s", self.link.address, err)
                break
        self._apply_settings(values)

    def _apply_settings(self, values: dict[GattAddress, bytes | None]) -> None:
        """Store verified setting values and notify listeners on change."""
        changed = False
        for address, value in values.items():
            if (
                value is not None
                and SLOW_SETTINGS[address](value) is not None
                and value != self.settings.get(address)
            ):
                self.settings[address] = value
                changed = True
        if changed:
            self.async_update_listeners()

    async def async_write_setting(self, address: GattAddress, payload: bytes) -> None:
        """Write a slow setting, show it at once, then read it back.

        The persistent link reads it back immediately; per-poll mode reads it
        inside the next poll's connection instead of opening another one.
        """
        await self.async_write(address, payload, refresh=False)
        self.settings[address] = payload
        self.async_update_listeners()
        if self.link.keep_connected:
            await self._async_read_settings((address,))
        else:
            self._settings_due = True

    async def async_set_sound(self, enabled: bool) -> None:
        """Write the sound setting."""
        await self.async_write_setting(SOUND, encode_sound(enabled))

    async def async_set_setback(self, enabled: bool) -> None:
        """Switch Setback, keeping the heater's Setback temperature."""
        try:
            payload = encode_setback(enabled, self.settings.get(SETBACK))
        except DimplexProtocolError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="setting_unavailable"
            ) from err
        await self.async_write_setting(SETBACK, payload)

    async def async_set_temperature_offset(self, offset_c: float) -> None:
        """Write the room-temperature offset in 0.1 degC steps."""
        try:
            payload = encode_temperature_offset(offset_c)
        except DimplexProtocolError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_temperature_offset",
                translation_placeholders={
                    "min": f"{-TEMPERATURE_OFFSET_LIMIT_C:g}",
                    "max": f"{TEMPERATURE_OFFSET_LIMIT_C:g}",
                },
            ) from err
        await self.async_write_setting(ROOM_TEMPERATURE_OFFSET, payload)

    async def async_set_away_temperature(self, temperature_c: float) -> None:
        """Change the Away target, keeping Away's state and return time."""
        value = float(temperature_c)
        if (
            not value.is_integer()
            or not AWAY_TEMPERATURE_MIN_C <= value <= AWAY_TEMPERATURE_MAX_C
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_setpoint",
                translation_placeholders={
                    "min": str(AWAY_TEMPERATURE_MIN_C),
                    "max": str(AWAY_TEMPERATURE_MAX_C),
                },
            )
        async with self._away_lock:
            data = self.data
            if (
                data is None
                or data.away_active is None
                or (data.away_active and data.away_until is None)
            ):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="setting_unavailable"
                )
            # Writing the Away record always makes the heater enter Away, whatever
            # the active byte or deadline (hardware 2026-11-24 18:44 and 18:48,
            # AM2086). Only an active Away can have its temperature changed.
            until = data.away_until
            if not data.away_active or until is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="away_temperature_needs_away",
                )
            await self.async_write(
                AWAY_MODE,
                encode_away_mode(data.away_active, until, int(value)),
                refresh=False,
            )
        await self.async_request_refresh()

    async def async_set_away_mode(
        self,
        active: bool,
        *,
        until: datetime | None = None,
        refresh: bool = True,
    ) -> None:
        """Write Away Mode with an explicit future deadline when activating."""
        async with self._away_lock:
            await self._async_write_away_mode(active, until)
        if refresh:
            await self.async_request_refresh()

    async def async_write_mode(
        self,
        address: GattAddress,
        payload: bytes,
        *,
        leave_away: Callable[[], bool],
    ) -> None:
        """Leave Away first when ``leave_away()``, then write a mode, as one sequence.

        ``leave_away`` is evaluated under the Away lock, and holding the lock
        across both writes keeps an Away temperature change from re-entering
        Away between them.
        """
        async with self._away_lock:
            if leave_away():
                await self._async_write_away_mode(False, None)
            await self.async_write(address, payload, refresh=False)
        # Not the debounced request: the next action of a script (a preset of
        # the new mode) is validated against the state, so it must be current.
        await self.async_refresh()

    async def _async_write_away_mode(
        self, active: bool, until: datetime | None
    ) -> None:
        """Write the Away record; the caller holds the Away lock."""
        now = dt_util.now().replace(tzinfo=None)
        data = self.data
        if until is not None and until.tzinfo is not None:
            until = dt_util.as_local(until).replace(tzinfo=None)
        if active:
            if until is None and data is not None and data.away_until is not None:
                if data.away_until > now:
                    until = data.away_until
            if until is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="away_until_required"
                )
            if until <= now:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="invalid_away_until"
                )
        elif until is None:
            until = (
                data.away_until
                if data is not None and data.away_until is not None
                else now
            )
        target_temperature = (
            data.away_target_temperature_c
            if data is not None and data.away_target_temperature_c is not None
            else 7
        )
        await self.async_write(
            AWAY_MODE,
            encode_away_mode(active, until, target_temperature),
            refresh=False,
        )
        if isinstance(self.data, HeaterSnapshot):
            # Until the next poll reads it back, later Away checks must see
            # what was just written, not the snapshot from before it.
            self.data = dataclasses.replace(
                self.data, away_active=active, away_until=until
            )
