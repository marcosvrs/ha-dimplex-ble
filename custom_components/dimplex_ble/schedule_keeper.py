"""Keep a heater's timer programmes safe: a backup copy and changes not yet saved.

The heater runs its programmes on its own, so a change only has to reach it
once. If the heater cannot be reached, the change waits here (on disk, across
restarts) and is written after the next successful poll. The backup is seeded
from the first complete read and then follows only the changes saved through
Home Assistant, so a heater reset to its factory schedule never overwrites it.
"""

from __future__ import annotations

from datetime import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .protocol import SCHEDULE_DAYS, SchedulePeriod

STORAGE_VERSION = 1
SAVE_DELAY_S = 1

type Day = tuple[SchedulePeriod, ...]


def pending_issue_id(entry_id: str) -> str:
    """Return the repair issue id of one entry's waiting schedule change."""
    return f"schedule_pending_{entry_id}"


def day_to_json(periods: Day) -> list[list[Any]]:
    """Encode one day as ``[["06:30", "08:00", 21], ...]``."""
    return [[f"{p.start:%H:%M}", f"{p.end:%H:%M}", p.temperature_c] for p in periods]


def _day_from_json(raw: list[list[Any]]) -> Day:
    return tuple(
        SchedulePeriod(time.fromisoformat(start), time.fromisoformat(end), temp)
        for start, end, temp in raw
    )


class ScheduleKeeper:
    """The backup copy and the pending changes of one heater."""

    def __init__(self, hass: HomeAssistant, entry_id: str, name: str) -> None:
        """Bind to one config entry's storage file and repair issue."""
        self._hass = hass
        self._entry_id = entry_id
        self._name = name
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}.schedules"
        )
        # Programme -> 7 days (Monday first); a day is None while unknown.
        self.backup: dict[str, list[Day | None]] = {}
        # Programme -> day -> periods still to be written to the heater.
        self.pending: dict[str, dict[int, Day]] = {}

    async def async_load(self) -> None:
        """Load the backup and the pending changes saved before a restart."""
        data = await self._store.async_load() or {}
        self.backup = {
            preset: [None if day is None else _day_from_json(day) for day in week]
            for preset, week in data.get("backup", {}).items()
        }
        self.pending = {
            preset: {int(day): _day_from_json(periods) for day, periods in days.items()}
            for preset, days in data.get("pending", {}).items()
        }
        self._update_issue()

    def note_read(self, preset: str, week: list[Day | None]) -> None:
        """Seed the backup from a complete read: only days it does not know yet.

        Days saved through Home Assistant are never replaced by a read, so a
        reset heater cannot overwrite them. A partial read seeds nothing: its
        missing days could later be filled from a reset heater, mixing weeks.
        """
        if any(periods is None for periods in week):
            return
        backup = self.backup.setdefault(preset, [None] * SCHEDULE_DAYS)
        filled = False
        for index, periods in enumerate(week):
            if backup[index] is None and periods is not None:
                backup[index] = periods
                filled = True
        if filled:
            self._save()

    def note_saved(self, preset: str, day: int, periods: Day) -> None:
        """A day was written and verified: back it up, drop it from pending."""
        week = self.backup.setdefault(preset, [None] * SCHEDULE_DAYS)
        week[day] = periods
        days = self.pending.get(preset, {})
        days.pop(day, None)
        if not days:
            self.pending.pop(preset, None)
        self._save()

    def drop(self, preset: str, day: int) -> None:
        """Forget a pending day the heater refused (it would never be kept)."""
        days = self.pending.get(preset, {})
        if days.pop(day, None) is not None:
            if not days:
                self.pending.pop(preset, None)
            self._save()

    def queue(self, preset: str, days: dict[int, Day]) -> None:
        """Keep days for later: a newer change of the same day replaces it."""
        self.pending.setdefault(preset, {}).update(days)
        self._save()

    def _save(self) -> None:
        self._store.async_delay_save(self._data, SAVE_DELAY_S)
        self._update_issue()

    def _data(self) -> dict[str, Any]:
        return {
            "backup": {
                preset: [None if day is None else day_to_json(day) for day in week]
                for preset, week in self.backup.items()
            },
            "pending": {
                preset: {
                    str(day): day_to_json(periods) for day, periods in days.items()
                }
                for preset, days in self.pending.items()
            },
        }

    def _update_issue(self) -> None:
        """Show a waiting change as a repair until the heater has it."""
        issue_id = pending_issue_id(self._entry_id)
        if not self.pending:
            ir.async_delete_issue(self._hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self._hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="schedule_pending",
            translation_placeholders={"name": self._name},
        )
