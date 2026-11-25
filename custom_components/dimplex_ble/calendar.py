"""Calendars of the heater's timer programmes (User Timer, ...)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import DimplexConfigEntry
from .entity import DimplexEntity
from .protocol import PRESET_HOME_ALL_DAY, PRESET_OUT_ALL_DAY, PRESET_USER_TIMER

PARALLEL_UPDATES = 0
# How far ahead the "next period" state looks (one week covers every day).
LOOKAHEAD = timedelta(days=8)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DimplexConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one calendar per timer programme."""
    del hass
    async_add_entities(
        cls(entry.runtime_data)
        for cls in (UserTimerCalendar, HomeAllDayCalendar, OutAllDayCalendar)
    )


class DimplexScheduleCalendar(DimplexEntity, CalendarEntity):
    """The heating periods of one timer programme, as stored on the heater.

    Not editable as events: programmes change on the heater, in the Dimplex
    app, or with the set_schedule action.
    Times are the heater's local times, shown in Home Assistant's time zone.
    """

    # The timer programme (a key of SCHEDULE_PROGRAMMES) this calendar shows.
    _preset: str

    @property
    def available(self) -> bool:
        """Available once at least one day of the programme was read."""
        week = self.coordinator.schedules.get(self._preset)
        return (
            super().available
            and week is not None
            and any(day is not None for day in week)
        )

    @property
    def event(self) -> CalendarEvent | None:
        """Return the current or next heating period."""
        now = dt_util.now()
        upcoming = self._events(now, now + LOOKAHEAD)
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Return the heating periods that overlap the requested window."""
        del hass
        return self._events(start_date, end_date)

    def _events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        week = self.coordinator.schedules.get(self._preset) or []
        if not week:
            return []
        zone = dt_util.get_default_time_zone()
        events: list[CalendarEvent] = []
        day: date = dt_util.as_local(start).date()
        last = dt_util.as_local(end).date()
        while day <= last:
            for period in week[day.weekday()] or ():
                begins = datetime.combine(day, period.start, zone)
                ends = datetime.combine(day, period.end, zone)
                if begins < end and ends > start:
                    events.append(
                        CalendarEvent(
                            start=begins,
                            end=ends,
                            summary=f"{period.temperature_c} °C",
                        )
                    )
            day += timedelta(days=1)
        return events


class UserTimerCalendar(DimplexScheduleCalendar):
    """User Timer programme (the only one enabled by default: 3 calendars per
    heater crowd the calendar view)."""

    _preset = PRESET_USER_TIMER
    _unique_suffix = _attr_translation_key = "schedule_user_timer"


class HomeAllDayCalendar(DimplexScheduleCalendar):
    """Home All Day programme."""

    _preset = PRESET_HOME_ALL_DAY
    _unique_suffix = _attr_translation_key = "schedule_home_all_day"
    _attr_entity_registry_enabled_default = False


class OutAllDayCalendar(DimplexScheduleCalendar):
    """Out All Day programme."""

    _preset = PRESET_OUT_ALL_DAY
    _unique_suffix = _attr_translation_key = "schedule_out_all_day"
    _attr_entity_registry_enabled_default = False
