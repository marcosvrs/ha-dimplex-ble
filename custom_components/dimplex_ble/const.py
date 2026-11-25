"""Constants for the Dimplex BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "dimplex_ble"
MANUFACTURER = "Dimplex"
ADVERTISEMENT_SUFFIX = "<Dimplex>"

CONF_ADDRESS = "address"
CONF_PROXY_SOURCE = "proxy_source"
CONF_PASSKEY = "passkey"
# Option: hold the BLE link between polls (True) or connect per operation.
CONF_KEEP_CONNECTED = "keep_connected"
DEFAULT_KEEP_CONNECTED = True
# Option: set the heater clock to Home Assistant's time when it drifts, as the
# ConfigR app does on every connection.
CONF_SYNC_CLOCK = "sync_clock"
DEFAULT_SYNC_CLOCK = True
# Option: how long the Boost preset lasts (the heater accepts 1-240 min).
CONF_BOOST_MINUTES = "boost_minutes"
DEFAULT_BOOST_MINUTES = 60

# Fired by firmware/dimplex_pin_responder.yaml when the heater asks the proxy for
# the passkey (its PIN is on the display); ESPHome adds the proxy's device_id.
PASSKEY_REQUEST_EVENT = "esphome.dimplex_passkey_request"

DEFAULT_UPDATE_INTERVAL = timedelta(seconds=10)
# Per-poll mode connects, pairs, reads and disconnects on every update, so it
# polls less often. This A/B experiment therefore changes the interval together
# with the mode: a difference between modes may come from either.
POLL_INTERVAL_PER_POLL = 60
# Slow settings (Sound, Setback, offset) change only when someone changes them.
SETTINGS_UPDATE_INTERVAL = timedelta(minutes=5)
SCHEDULE_UPDATE_INTERVAL = timedelta(hours=6)
# A held link rechecks the heater clock this often (per-poll links check it on
# every connection), so a DST change is corrected within the hour.
CLOCK_CHECK_INTERVAL = timedelta(hours=1)
PLATFORMS = (
    Platform.CALENDAR,
    Platform.CLIMATE,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)

# Entity unique_id = "<mac>_<suffix>". The suffixes are frozen to the values
# installed entities already use (they once came from the translation keys),
# so renaming a translation key never orphans an entity again. Registry
# entries of an entry whose unique_id is not built from these are removed.
UNIQUE_ID_SUFFIXES = frozenset(
    {
        "heater",
        "away_until",
        "temperature_unit",
        "error_code",
        "operating_mode_raw",
        "rated_power",
        "lcd_temperature",
        "output_power",
        "calculated_power_consumption",
        "sound",
        "open_window_detection",
        "preemptive_heating",
        "advance",
        "setback",
        "away_temperature",
        "temperature_offset",
        "schedule_user_timer",
        "schedule_home_all_day",
        "schedule_out_all_day",
    }
)
# Never created, never removed: no commit ever used this unique_id suffix, but
# a live entity_id ends in "pre_emptive_heating" and the live entity registry
# was not inspected, so cleanup leaves such an entry alone.
UNVERIFIED_LEGACY_SUFFIXES = frozenset({"pre_emptive_heating"})
