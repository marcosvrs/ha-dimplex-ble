"""Constants for the Dimplex BLE integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "dimplex_ble"
MANUFACTURER = "Dimplex"
ADVERTISEMENT_SUFFIX = "<Dimplex>"

CONF_ADDRESS = "address"
CONF_ADDRESS_TYPE = "address_type"
CONF_PROXY_SOURCE = "proxy_source"
CONF_PASSKEY = "passkey"
DEVICE_NAME = "Dimplex panel heater"
PENDING_DEVICES = "pending_devices"
CLIENTS = "clients"

DEFAULT_UPDATE_INTERVAL = timedelta(seconds=10)
SOUND_UPDATE_INTERVAL = timedelta(minutes=5)
PLATFORMS = (
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
)
