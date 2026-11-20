#pragma once
#include <cstdint>

using esp_gatt_status_t = int;
using esp_gatt_if_t = uint16_t;
constexpr esp_gatt_status_t ESP_GATT_OK = 0;

enum esp_gattc_cb_event_t {
  ESP_GATTC_CONNECT_EVT,
  ESP_GATTC_OPEN_EVT,
  ESP_GATTC_DISCONNECT_EVT,
  ESP_GATTC_OTHER_EVT,
};

struct esp_ble_gattc_cb_param_t {
  struct {
    uint8_t remote_bda[6];
  } connect;
  struct {
    esp_gatt_status_t status;
    uint16_t conn_id;
    uint8_t remote_bda[6];
    uint16_t mtu;
  } open;
  struct {
    uint8_t remote_bda[6];
  } disconnect;
};
