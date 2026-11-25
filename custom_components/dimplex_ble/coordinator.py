"""Coordinator for a single Dimplex panel heater."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from math import isfinite
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
    DOMAIN,
    UPDATE_INTERVAL,
)
from .errors import (
    DimplexError,
    HeaterAuthError,
    HeaterUnavailableError,
    to_homeassistant_error,
)
from .link import HeaterLink, WeekSchedule
from .protocol import (
    AWAY_MODE,
    AWAY_TEMPERATURE_MAX_C,
    AWAY_TEMPERATURE_MIN_C,
    BOOST_REMAINING,
    BOOST_TEMPERATURE,
    GDID,
    HEATING_MODE,
    PRESET_AWAY,
    PRESET_HOME_ALL_DAY,
    PRESET_OUT_ALL_DAY,
    PRESET_USER_TIMER,
    ROOM_TEMPERATURE_OFFSET,
    SETBACK,
    SETBACK_TEMPERATURE_MAX_C,
    SETBACK_TEMPERATURE_MIN_C,
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
    encode_boost_temperature,
    encode_schedule_day,
    encode_setback,
    encode_setpoint,
    encode_sound,
    encode_temperature_offset,
    heating_mode_of,
    parse_away_mode,
    parse_gdid,
    parse_setback,
    parse_sound_enabled,
    parse_temperature_offset,
)
from .schedule_keeper import ScheduleKeeper
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
    GDID: parse_gdid,  # the appliance id Dimplex's firmware lookup needs
    SOUND: parse_sound_enabled,
    SETBACK: parse_setback,
    ROOM_TEMPERATURE_OFFSET: parse_temperature_offset,
}


def whole_degrees(value: object, low: int, high: int) -> int:
    """Return ``value`` as whole degrees within ``low``..``high``, or refuse it."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or not float(value).is_integer()
        or not low <= value <= high
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="invalid_setpoint",
            translation_placeholders={"min": str(low), "max": str(high)},
        )
    return int(value)


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
            update_interval=UPDATE_INTERVAL,
            always_update=False,
        )
        self.link = link
        # Last verified raw value of each slow setting.
        self.settings: dict[GattAddress, bytes] = {}
        # Slow reads ride on the next poll's connection (both link modes).
        self._settings_due = True
        # Timer programme -> 7 days (Monday first); a day is None when unread.
        self.schedules: dict[str, WeekSchedule] = {}
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
        self.schedule_keeper = ScheduleKeeper(hass, entry.entry_id, entry.title)
        self._applying_pending = False
        # Saves and the flush of waiting changes never interleave, so a newer
        # save cannot be overwritten by an older waiting one.
        self._schedule_lock = asyncio.Lock()
        self.schedules_read_at: datetime | None = None

    async def _async_update_data(self) -> HeaterSnapshot:
        """Read one snapshot; a rejected PIN starts reauth and stops polling."""
        try:
            (
                snapshot,
                settings,
                schedules,
            ) = await self.link.async_snapshot(
                tuple(SLOW_SETTINGS) if self._settings_due else (),
                tuple(SCHEDULE_PROGRAMMES.values()) if self._schedules_due else (),
            )
            if self._settings_due:
                # Stay due until every setting was read (a failed read must not
                # confirm a value just written).
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
        if self.schedule_keeper.pending and not self._applying_pending:
            # The heater answers again: write the changes that waited for it.
            self._applying_pending = True
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_apply_pending(),
                "dimplex_ble pending schedule",
            )
        return snapshot

    async def _async_apply_pending(self) -> None:
        """Write the schedule changes saved while the heater was unreachable."""
        try:
            async with self._schedule_lock:
                # Read only now: a save that ran first has already cleared it.
                for preset, days in list(self.schedule_keeper.pending.items()):
                    await self._async_save_schedule(preset, dict(days))
        except HomeAssistantError as err:
            # A refused day is dropped (it would never be kept): say so.
            log = (
                _LOGGER.warning
                if err.translation_key == "schedule_not_saved"
                else _LOGGER.debug
            )
            log("Pending schedule for %s not written: %s", self.link.address, err)
        finally:
            self._applying_pending = False

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
        await self.async_command(self.link.async_write(address, payload))
        if refresh:
            await self.async_request_refresh()

    def check_setpoint(self, value: object) -> int:
        """Return a whole-degree target within the heater's reported range."""
        data = self.data
        low = data.minimum_temperature_c if data is not None else None
        high = data.maximum_temperature_c if data is not None else None
        if low is None or high is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="setpoint_unavailable"
            )
        return whole_degrees(value, low, high)

    async def async_command[T](self, command: Awaitable[T]) -> T:
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
            await self.async_command(
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

    async def _async_end_boost(self) -> None:
        """Write 0 minutes and wait for the heater to restore its mode."""
        await self.async_command(
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
        """Save days of a programme (see :meth:`_async_save_schedule`)."""
        async with self._schedule_lock:
            await self._async_save_schedule(preset, days)

    async def _async_save_schedule(
        self, preset: str, days: dict[int, tuple[SchedulePeriod, ...]]
    ) -> None:
        """Save whole days of a programme and verify each by reading it back.

        Days are saved one by one. If the link fails part-way, the days already
        read back are kept, every programme is read again in the background,
        and the error says how many days were saved. Running the action again
        is safe: it replaces the same days. (No rollback: undoing would need
        more writes over the link that just failed.)
        """
        programme = SCHEDULE_PROGRAMMES[preset]
        try:
            payloads = {
                day: encode_schedule_day(programme, day, periods)
                for day, periods in days.items()
            }
        except DimplexProtocolError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_schedule",
                translation_placeholders={"reason": str(err)},
            ) from err
        expected = {
            day: tuple(sorted(periods, key=lambda period: period.start))
            for day, periods in days.items()
        }
        readback: dict[int, tuple[SchedulePeriod, ...] | None] = {}
        try:
            await self.async_command(
                self.link.async_save_schedule(programme, payloads, readback)
            )
        except HomeAssistantError as err:
            self._store_schedule_days(preset, readback)
            self._schedules_due = True  # every programme is read again
            saved = self._note_saved(preset, expected, readback)
            if err.translation_key != HeaterUnavailableError.translation_key:
                raise
            # Unreachable: the days not yet saved wait for the heater (on disk);
            # the heater runs its programme without Bluetooth meanwhile.
            self.schedule_keeper.queue(
                preset, {d: p for d, p in expected.items() if d not in saved}
            )
            return
        self._store_schedule_days(preset, readback)
        if self._note_saved(preset, expected, readback) != set(expected):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="schedule_not_saved"
            )

    def _note_saved(
        self,
        preset: str,
        expected: dict[int, tuple[SchedulePeriod, ...]],
        readback: dict[int, tuple[SchedulePeriod, ...] | None],
    ) -> set[int]:
        """Back up the days the heater kept; a refused day is not retried."""
        saved = set()
        for day, periods in readback.items():
            if periods == expected[day]:
                self.schedule_keeper.note_saved(preset, day, periods)
                saved.add(day)
            else:
                self.schedule_keeper.drop(preset, day)
        return saved

    async def async_restore_schedule(self, preset: str | None = None) -> None:
        """Write the backup copy of one programme (or all) back to the heater."""
        backup = self.schedule_keeper.backup
        presets = [preset] if preset is not None else list(backup)
        # Restoring part of a week would leave the rest as the heater has it,
        # which after a reset is not what the user saved: only whole weeks.
        if not presets or any(p not in backup or None in backup[p] for p in presets):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="no_schedule_backup"
            )
        for name in presets:
            days = {d: p for d, p in enumerate(backup[name]) if p is not None}
            await self.async_save_schedule(name, days)

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

    @callback
    def async_settings_due(self, now: datetime | None = None) -> None:
        """Read the slow settings on the next poll."""
        del now
        self._settings_due = True

    @callback
    def async_schedules_due(self, now: datetime | None = None) -> None:
        """Read the timer programmes on the next poll.

        Schedules change only on the heater, in the Dimplex app or through the
        set_schedule action.
        """
        del now
        self._schedules_due = True

    def _apply_schedules(self, schedules: dict[int, WeekSchedule]) -> None:
        """Store the programmes by preset and notify listeners on change."""
        by_preset = {
            preset: schedules[programme]
            for preset, programme in SCHEDULE_PROGRAMMES.items()
            if programme in schedules
        }
        self.schedules_read_at = dt_util.utcnow()
        for preset, week in by_preset.items():
            self.schedule_keeper.note_read(preset, week)
        if by_preset != self.schedules:
            self.schedules = by_preset
            self.async_update_listeners()

    @property
    def gdid(self) -> str | None:
        """Return the heater's Dimplex appliance id once read."""
        return parse_gdid(self.settings.get(GDID))

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
        """Write a slow setting and show it at once; the next poll reads it back."""
        await self.async_write(address, payload, refresh=False)
        self.settings[address] = payload
        self.async_update_listeners()
        self._settings_due = True

    async def async_set_sound(self, enabled: bool) -> None:
        """Write the sound setting."""
        await self.async_write_setting(SOUND, encode_sound(enabled))

    async def async_set_setback(self, enabled: bool) -> None:
        """Switch Setback, keeping the heater's Setback temperature."""
        await self._async_write_setback(enabled, None)

    async def async_set_setback_temperature(self, value: object) -> None:
        """Set the Setback temperature (whole degC), keeping Setback on/off."""
        temperature = whole_degrees(
            value, SETBACK_TEMPERATURE_MIN_C, SETBACK_TEMPERATURE_MAX_C
        )
        await self._async_write_setback(None, temperature)

    async def _async_write_setback(
        self, enabled: bool | None, temperature: int | None
    ) -> None:
        """Change one part of the Setback record, keeping the other.

        The record is read, written and read back in one link operation, so a
        change made on the heater's menu since the last poll is kept, and a
        value the heater refuses is reported instead of shown.
        """
        written: list[bytes] = []

        def build(current: bytes | None) -> bytes:
            try:
                written.append(encode_setback(enabled, current, temperature))
            except DimplexProtocolError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="setting_unavailable"
                ) from err
            return written[0]

        kept = await self.async_command(
            self.link.async_read_then_write(SETBACK, build, read_back=True)
        )
        if kept is not None:  # an unreadable record keeps the last known value
            self.settings[SETBACK] = kept
            self.async_update_listeners()
        if kept != written[0]:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="setting_not_kept"
            )
        self._settings_due = True  # confirmed by the next poll, as other writes

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

    async def async_set_boost_temperature(self, temperature_c: float) -> None:
        """Set the Boost temperature within the heater's setpoint range.

        A Boost already running keeps its target; the next Boost uses this.
        """
        value = self.check_setpoint(temperature_c)
        await self.async_write(BOOST_TEMPERATURE, encode_boost_temperature(value))

    async def async_set_away_temperature(self, temperature_c: float) -> None:
        """Change the Away target, keeping Away's state and return time."""
        value = whole_degrees(
            temperature_c, AWAY_TEMPERATURE_MIN_C, AWAY_TEMPERATURE_MAX_C
        )
        async with self._away_lock:
            data = self.data
            if data is None or data.away_active is None:
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="setting_unavailable"
                )
            # Writing the Away record always makes the heater enter Away, whatever
            # the active byte or deadline (hardware 2026-11-24 18:44 and 18:48,
            # AM2086). Only an active Away can have its temperature changed.
            if not data.away_active:
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
                return encode_away_mode(True, live[1], value)

            await self.async_command(self.link.async_read_then_write(AWAY_MODE, build))
        await self.async_request_refresh()

    async def async_set_away_mode(self, *, until: datetime | None = None) -> None:
        """Start Away until ``until`` (or the last return time still ahead)."""
        async with self._away_lock:
            now = dt_util.now().replace(tzinfo=None)
            data = self.data
            if until is not None and until.tzinfo is not None:
                until = dt_util.as_local(until).replace(tzinfo=None)
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
            if isinstance(data, HeaterSnapshot) and data.boost_minutes_left:
                # Boost during Away was never tested: end Boost first (only once
                # the request is known to be valid, so a refused one keeps it).
                await self._async_end_boost()
            start = until

            def build(current: bytes | None) -> bytes:
                # Keep the Away temperature the heater holds now (it may have
                # changed on the heater since the last poll).
                live = parse_away_mode(current)
                target = self._away_target(live[2] if live is not None else None)
                return encode_away_mode(True, start, target)

            await self.async_command(self.link.async_read_then_write(AWAY_MODE, build))
            # Later Away checks must see what was just written.
            self._apply_written(away_active=True, away_until=until)
        await self.async_request_refresh()

    async def async_set_heating_mode(self, payload: bytes) -> None:
        """Write a heating mode, ending Boost and leaving Away first.

        Under the Away lock, so an Away temperature change cannot re-enter Away
        between the writes.
        """
        async with self._away_lock:
            data = self.data
            if isinstance(data, HeaterSnapshot) and data.boost_minutes_left:
                await self._async_end_boost()
            data = self.data
            if data is not None and (
                data.away_active
                or heating_mode_of(data.operating_mode_hex) == PRESET_AWAY
            ):
                # One link operation: in per-poll mode separate writes would be
                # 10 s apart, and the Away write alone can leave the heater in
                # Away (any Away-record write enters it; the mode write ends it).
                until = data.away_until or dt_util.now().replace(tzinfo=None)
                leave = encode_away_mode(False, until, self._away_target())
                await self.async_command(
                    self.link.async_write_many(
                        ((AWAY_MODE, leave), (HEATING_MODE, payload))
                    )
                )
                self._apply_written(away_active=False, away_until=until)
            else:
                await self.async_write(HEATING_MODE, payload, refresh=False)
        # Not the debounced request: the next action of a script (a preset of
        # the new mode) is validated against the state, so it must be current.
        await self.async_refresh()

    def _away_target(self, value: int | None = None) -> int:
        """Return the Away temperature to keep when rewriting the Away record.

        ``value`` (the live record's) or the last poll's, if it is within the
        documented 7-18 degC; otherwise 7 degC, so a malformed record never
        writes an unheated Away.
        """
        data = self.data
        if value is None and data is not None:
            value = data.away_target_temperature_c
        if (
            value is not None
            and AWAY_TEMPERATURE_MIN_C <= value <= AWAY_TEMPERATURE_MAX_C
        ):
            return value
        return AWAY_TEMPERATURE_MIN_C
