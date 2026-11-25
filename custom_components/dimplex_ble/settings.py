"""The integration-level "Dimplex" entry: settings shared by every heater.

It holds the Dimplex sign-in (firmware updates), clock sync and the Boost
length. Heaters read it directly (no loading order); without it they use the
defaults.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_BOOST_MINUTES,
    CONF_SYNC_CLOCK,
    DEFAULT_BOOST_MINUTES,
    DEFAULT_SYNC_CLOCK,
    DOMAIN,
)

SETTINGS_UNIQUE_ID = "dimplex_settings"
CONF_REFRESH_TOKEN = "refresh_token"


def is_settings_entry(entry: ConfigEntry) -> bool:
    """Return whether ``entry`` is the integration-level entry (not a heater)."""
    return entry.unique_id == SETTINGS_UNIQUE_ID


@callback
def settings_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the integration-level entry, if it was added."""
    return hass.config_entries.async_entry_for_domain_unique_id(
        DOMAIN, SETTINGS_UNIQUE_ID
    )


@callback
def sync_clock(hass: HomeAssistant) -> bool:
    """Return whether heater clocks follow Home Assistant's time."""
    entry = settings_entry(hass)
    return bool(
        DEFAULT_SYNC_CLOCK
        if entry is None
        else entry.options.get(CONF_SYNC_CLOCK, DEFAULT_SYNC_CLOCK)
    )


@callback
def boost_minutes(hass: HomeAssistant) -> int:
    """Return how long the Boost preset heats."""
    entry = settings_entry(hass)
    return int(
        DEFAULT_BOOST_MINUTES
        if entry is None
        else entry.options.get(CONF_BOOST_MINUTES, DEFAULT_BOOST_MINUTES)
    )


@callback
def async_reload_heaters(hass: HomeAssistant) -> None:
    """Apply changed shared settings: reload every heater entry."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if not is_settings_entry(entry):
            hass.config_entries.async_schedule_reload(entry.entry_id)
