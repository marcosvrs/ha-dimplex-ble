"""Coordinator for a single Dimplex panel heater."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
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
    BOOST_REMAINING,
    PRESET_HOME_ALL_DAY,
    PRESET_OUT_ALL_DAY,
    PRESET_USER_TIMER,
    ROOM_TEMPERATURE_OFFSET,
    SETBACK,
    SETPOINT,
    SOUND,
    TEMPERATURE_OFFSET_LIMIT_C,
    TIMER_SUBMODE_HOME,
    TIMER_SUBMODE_OUT,
    TIMER_SUBMODE_USER_TIMER,
    DimplexProtocolError,
    GattAddress,
    HeaterSnapshot,
    SchedulePeriod,
    encode_away_mode,
    encode_boost_minutes,
    encode_setback,
    encode_setpoint,
    encode_sound,
    encode_temperature_offset,
    parse_away_mode,
    parse_setback,
    parse_sound_enabled,
    parse_temperature_offset,
)
from .stall import StallMonitor

_LOGGER = logging.getLogger(__name__)
# Time the heater takes to restore its mode after Boost is ended.
BOOST_END_SETTLE_S = 1.5

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
                    # Stay due until every setting was read (a failed read
                    # must not confirm a value just written).
                    self._settings_due = not set(SLOW_SETTINGS) <= set(settings)
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
        await self._async_command(self.link.async_write(address, payload))
        if refresh:
            await self.async_request_refresh()

    async def _async_command[T](self, command: Awaitable[T]) -> T:
        """Run a link command, mapping failures to Home Assistant errors."""
        try:
            return await command
        except HeaterAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise to_homeassistant_error(err) from err
        except (DimplexError, DimplexProtocolError) as err:
            raise to_homeassistant_error(err) from err

    async def async_start_boost(self, minutes: int, temperature_c: int) -> None:
        """Start Boost: the minutes first, then its setpoint (as ConfigR does).

        Under the Away lock, so an Away started meanwhile (for example from the
        Away return time entity) is seen: Boost during Away was never tested.
        """
        async with self._away_lock:
            if self.data is None or self.data.away_active:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="boost_not_in_away"
                )
            await self._async_command(
                self.link.async_write_many(
                    (
                        (BOOST_REMAINING, encode_boost_minutes(minutes)),
                        (SETPOINT, encode_setpoint(temperature_c)),
                    )
                )
            )
            # Before the lock is released, so an Away write right after sees
            # the running Boost and ends it first.
            self._apply_written(boost_minutes_left=minutes)
        await self.async_refresh()

    async def async_cancel_boost(self) -> None:
        """End Boost; the heater returns to its previous mode by itself."""
        await self._async_end_boost()
        await self.async_refresh()

    async def _async_end_boost(self) -> None:
        """Write 0 minutes and wait for the heater to restore its mode."""
        await self._async_command(
            self.link.async_write(BOOST_REMAINING, encode_boost_minutes(0))
        )
        # ConfigR re-reads 1 s after ending Boost; the heater had restored its
        # mode within 1.5 s in every test (hardware 2026-11-25).
        await asyncio.sleep(BOOST_END_SETTLE_S)
        self._apply_written(boost_minutes_left=0)

    @callback
    def _apply_written(self, **changes: Any) -> None:
        """Show what was just written until the next poll reads it back.

        Listeners are told now: with ``always_update=False`` a poll that reads
        the same values notifies no one.
        """
        if isinstance(self.data, HeaterSnapshot):
            self.data = dataclasses.replace(self.data, **changes)
            self.async_update_listeners()

    async def async_save_schedule(
        self, preset: str, days: dict[int, tuple[SchedulePeriod, ...]]
    ) -> None:
        """Save whole days of a programme and verify each by reading it back.

        Days are saved one by one. If the link fails part-way, the days already
        read back are kept, every programme is read again in the background,
        and the error says how many days were saved. Running the action again
        is safe: it replaces the same days. (No rollback: undoing would need
        more writes over the link that just failed.)
        """
        readback: dict[int, tuple[SchedulePeriod, ...] | None] = {}
        try:
            await self._async_command(
                self.link.async_save_schedule(
                    SCHEDULE_PROGRAMMES[preset], days, readback
                )
            )
        except HomeAssistantError as err:
            self._store_schedule_days(preset, readback)
            self._schedules_due = True
            self.config_entry.async_create_background_task(
                self.hass,
                self.async_refresh_schedules(),
                "dimplex_ble schedule reread after a failed save",
            )
            if not readback:
                raise
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="schedule_partly_saved",
                translation_placeholders={
                    "saved": str(len(readback)),
                    "total": str(len(days)),
                },
            ) from err
        self._store_schedule_days(preset, readback)
        expected = {
            day: tuple(sorted(periods, key=lambda period: period.start))
            for day, periods in days.items()
        }
        if any(readback[day] != expected[day] for day in days):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="schedule_not_saved"
            )

    def _store_schedule_days(
        self, preset: str, readback: dict[int, tuple[SchedulePeriod, ...] | None]
    ) -> None:
        """Show the read-back days in the calendars."""
        if not readback:
            return
        week: WeekSchedule = list(self.schedules.get(preset, [None] * 7))
        for day, periods in readback.items():
            week[day] = periods
        self.schedules = {**self.schedules, preset: week}
        self.async_update_listeners()

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
        """Switch Setback, keeping the heater's Setback temperature.

        The record is read and written in one link operation, so a temperature
        changed on the heater's menu since the last poll is kept.
        """

        def build(current: bytes | None) -> bytes:
            try:
                return encode_setback(enabled, current)
            except DimplexProtocolError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="setting_unavailable"
                ) from err

        payload = await self._async_command(
            self.link.async_read_then_write(SETBACK, build)
        )
        self.settings[SETBACK] = payload
        self.async_update_listeners()

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

            def build(current: bytes | None) -> bytes:
                # The live record, read in the same operation: Away may have
                # ended on the heater since the last poll, and writing the
                # record would start it again.
                live = parse_away_mode(current)
                if live is None or not live[0] or live[1] is None:
                    raise ServiceValidationError(
                        translation_domain=DOMAIN,
                        translation_key="away_temperature_needs_away",
                    )
                return encode_away_mode(True, live[1], int(value))

            await self._async_command(self.link.async_read_then_write(AWAY_MODE, build))
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
                # One link operation: in per-poll mode separate writes would be
                # 10 s apart, and the Away write alone can leave the heater in
                # Away (any Away-record write enters it; the mode write ends it).
                await self._async_write_away_mode(False, None, then=(address, payload))
            else:
                await self.async_write(address, payload, refresh=False)
        # Not the debounced request: the next action of a script (a preset of
        # the new mode) is validated against the state, so it must be current.
        await self.async_refresh()

    async def _async_write_away_mode(
        self,
        active: bool,
        until: datetime | None,
        *,
        then: tuple[GattAddress, bytes] | None = None,
    ) -> None:
        """Write the Away record; the caller holds the Away lock."""
        if (
            active
            and isinstance(self.data, HeaterSnapshot)
            and self.data.boost_minutes_left
        ):
            # Boost during Away was never tested: end Boost first, as the
            # climate's Away preset does (also when Away comes from the entity).
            await self._async_end_boost()
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
        writes = ((AWAY_MODE, encode_away_mode(active, until, target_temperature)),)
        await self._async_command(
            self.link.async_write_many(writes + ((then,) if then else ()))
        )
        # Later Away checks must see what was just written, not the snapshot
        # from before it.
        self._apply_written(away_active=active, away_until=until)
