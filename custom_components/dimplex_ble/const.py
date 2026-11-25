"""Constants for the Dimplex BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "dimplex_ble"
MANUFACTURER = "Dimplex"
ADVERTISEMENT_SUFFIX = "<Dimplex>"

CONF_ADDRESS = "address"
CONF_PROXY_SOURCE = "proxy_source"
CONF_PASSKEY = "passkey"
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

# Every update connects, reads and disconnects, like the Dimplex app: the heaters
# are not made for long-held Bluetooth links (README, Reliability first).
UPDATE_INTERVAL = timedelta(seconds=60)
# Slow settings (Sound, Setback, offset) change only when someone changes them.
SETTINGS_UPDATE_INTERVAL = timedelta(minutes=5)
SCHEDULE_UPDATE_INTERVAL = timedelta(hours=6)
PLATFORMS = (
    Platform.CALENDAR,
    Platform.CLIMATE,
    Platform.DATETIME,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)

# Entity unique_id = "<mac>_<suffix>". The suffixes are fixed, independent of
# the translation keys, so renaming a key never changes an entity's identity.
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
