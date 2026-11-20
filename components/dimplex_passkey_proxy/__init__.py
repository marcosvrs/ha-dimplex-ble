"""Short-lived passkey relay for the ESPHome Bluetooth Proxy."""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import esp32_ble
from esphome.const import CONF_ID

DEPENDENCIES = ["api", "esp32_ble"]

passkey_proxy_ns = cg.esphome_ns.namespace("dimplex_passkey_proxy")
DimplexPasskeyProxy = passkey_proxy_ns.class_(
    "DimplexPasskeyProxy", cg.Component
)

CONFIG_SCHEMA = cv.Schema(
    {
        cv.GenerateID(): cv.declare_id(DimplexPasskeyProxy),
        cv.GenerateID(esp32_ble.CONF_BLE_ID): cv.use_id(esp32_ble.ESP32BLE),
    }
).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    """Register security callbacks on ESPHome's shared BLE stack."""
    var = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(var, config)
    ble = await cg.get_variable(config[esp32_ble.CONF_BLE_ID])
    esp32_ble.register_gap_event_handler(ble, var)
