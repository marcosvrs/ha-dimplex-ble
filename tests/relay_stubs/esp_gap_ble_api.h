#pragma once
#include <cstdint>

enum esp_gap_ble_cb_event_t {
  ESP_GAP_BLE_AUTH_CMPL_EVT,
  ESP_GAP_BLE_SEC_REQ_EVT,
  ESP_GAP_BLE_PASSKEY_NOTIF_EVT,
  ESP_GAP_BLE_PASSKEY_REQ_EVT,
  ESP_GAP_BLE_OTHER_EVT,
};

struct esp_ble_gap_cb_param_t {
  struct {
    struct {
      uint8_t bd_addr[6];
      bool success;
      uint8_t fail_reason;
    } auth_cmpl;
    struct {
      uint8_t bd_addr[6];
      uint32_t passkey;
    } key_notif;
    struct {
      uint8_t bd_addr[6];
    } ble_req;
  } ble_security;
};

int esp_ble_passkey_reply(uint8_t *address, bool accept, uint32_t passkey);
