#pragma once
#include <cstddef>
#include <cstdint>

using esp_bd_addr_t = uint8_t[6];
using esp_err_t = int;
constexpr esp_err_t ESP_OK = 0;
using esp_ble_auth_req_t = int;
using esp_ble_io_cap_t = int;
constexpr int ESP_BLE_SEC_ENCRYPT_MITM = 2;
constexpr int ESP_LE_AUTH_REQ_SC_MITM_BOND = 8;
constexpr int ESP_IO_CAP_IN = 2;
constexpr int ESP_BLE_ENC_KEY_MASK = 1;
constexpr int ESP_BLE_ID_KEY_MASK = 2;
constexpr int ESP_BLE_SM_AUTHEN_REQ_MODE = 0;
constexpr int ESP_BLE_SM_IOCAP_MODE = 1;
constexpr int ESP_BLE_SM_MAX_KEY_SIZE = 2;
constexpr int ESP_BLE_SM_SET_INIT_KEY = 3;
constexpr int ESP_BLE_SM_SET_RSP_KEY = 4;

enum esp_gap_ble_cb_event_t {
  ESP_GAP_BLE_AUTH_CMPL_EVT,
  ESP_GAP_BLE_SEC_REQ_EVT,
  ESP_GAP_BLE_PASSKEY_NOTIF_EVT,
  ESP_GAP_BLE_PASSKEY_REQ_EVT,
  ESP_GAP_BLE_KEY_EVT,
  ESP_GAP_BLE_OTHER_EVT,
};

struct esp_ble_gap_cb_param_t {
  struct {
    struct {
      uint8_t success;
      uint8_t fail_reason;
    } auth_cmpl;
    struct {
      uint8_t key_type;
    } ble_key;
    struct {
      uint8_t bd_addr[6];
    } ble_req;
  } ble_security;
};
int esp_ble_passkey_reply(uint8_t *address, bool accept, uint32_t passkey);
int esp_ble_get_bond_device_num();
esp_err_t esp_ble_gap_set_security_param(int param, void *value,
                                         uint8_t length);
esp_err_t esp_ble_set_encryption(uint8_t *address, int mode);
