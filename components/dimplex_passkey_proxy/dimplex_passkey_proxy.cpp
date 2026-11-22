#include "dimplex_passkey_proxy.h"

#include "esphome/core/log.h"

#include <cstddef>
#include <cstring>

namespace esphome::dimplex_passkey_proxy {
namespace {

constexpr char TAG[] = "dimplex_passkey";
constexpr char DEBUG_PREFIX[] = "[DEBUG-DIMPLEX-PIN-20261117]";
constexpr uint32_t PASSKEY_LIFETIME_MS = 60'000;

int hex_digit_(char value) {
  if (value >= '0' && value <= '9') {
    return value - '0';
  }
  if (value >= 'a' && value <= 'f') {
    return value - 'a' + 10;
  }
  if (value >= 'A' && value <= 'F') {
    return value - 'A' + 10;
  }
  return -1;
}

}  // namespace

void DimplexPasskeyProxy::arm_pairing(const char *address_hex) {
  this->clear_pairing_();

  std::array<uint8_t, 6> address{};
  if (!parse_address_(address_hex, address)) {
    ESP_LOGW(TAG, "%s pairing arm rejected: invalid address", DEBUG_PREFIX);
    return;
  }

  this->pending_address_ = address;
  this->pending_since_ = millis();
  this->state_ = PairingState::WAITING_FOR_REQUEST;
  ESP_LOGI(TAG, "%s pairing armed", DEBUG_PREFIX);
}
void DimplexPasskeyProxy::clear_all_bonds() {
  this->clear_pairing_();

  int bond_count = esp_ble_get_bond_device_num();
  if (bond_count <= 0) {
    ESP_LOGI(TAG, "%s no BLE bonds to clear", DEBUG_PREFIX);
    return;
  }

  constexpr int MAX_BONDS = 20;
  std::array<esp_ble_bond_dev_t, MAX_BONDS> bonds{};
  int list_count = bond_count < MAX_BONDS ? bond_count : MAX_BONDS;
  const esp_err_t list_result =
      esp_ble_get_bond_device_list(&list_count, bonds.data());
  ESP_LOGI(TAG, "%s BLE bond list status=%d count=%d", DEBUG_PREFIX,
           static_cast<int>(list_result), list_count);
  if (list_result != ESP_OK) {
    return;
  }
  for (int index = 0; index < list_count; ++index) {
    const esp_err_t remove_result =
        esp_ble_remove_bond_device(bonds[index].bd_addr);
    ESP_LOGI(TAG, "%s BLE bond %d removal status=%d", DEBUG_PREFIX, index,
             static_cast<int>(remove_result));
  }
}

void DimplexPasskeyProxy::clear_bond(const char *address_hex) {
  this->clear_pairing_();

  std::array<uint8_t, 6> address{};
  if (!parse_address_(address_hex, address)) {
    ESP_LOGW(TAG, "%s bond clear rejected: invalid address", DEBUG_PREFIX);
    return;
  }

  const esp_err_t direct_result = esp_ble_remove_bond_device(address.data());
  ESP_LOGI(TAG, "%s bond clear address order status=%d", DEBUG_PREFIX,
           static_cast<int>(direct_result));

  std::array<uint8_t, 6> reversed_address = address;
  for (std::size_t index = 0; index < reversed_address.size() / 2; ++index) {
    const auto opposite = reversed_address.size() - 1 - index;
    const auto value = reversed_address[index];
    reversed_address[index] = reversed_address[opposite];
    reversed_address[opposite] = value;
  }
  if (reversed_address != address) {
    const esp_err_t reversed_result =
        esp_ble_remove_bond_device(reversed_address.data());
    ESP_LOGI(TAG, "%s bond clear reverse address order status=%d", DEBUG_PREFIX,
             static_cast<int>(reversed_result));
  }
}


void DimplexPasskeyProxy::set_pending_passkey(const char *address_hex,
                                              const char *passkey) {
  std::array<uint8_t, 6> address{};
  uint32_t numeric_passkey = 0;
  if (!parse_address_(address_hex, address) ||
      !parse_passkey_(passkey, numeric_passkey)) {
    ESP_LOGW(TAG, "%s invalid pairing passkey action data", DEBUG_PREFIX);
    this->clear_pairing_();
    return;
  }
  if (this->state_ == PairingState::IDLE) {
    ESP_LOGW(TAG, "%s passkey ignored: relay is idle", DEBUG_PREFIX);
    return;
  }
  if (!this->matches_address_(address.data())) {
    ESP_LOGW(TAG, "%s passkey ignored: address did not match", DEBUG_PREFIX);
    return;
  }
  if (millis() - this->pending_since_ >= PASSKEY_LIFETIME_MS) {
    ESP_LOGW(TAG, "%s passkey ignored: relay state expired", DEBUG_PREFIX);
    this->clear_pairing_();
    return;
  }

  if (this->state_ == PairingState::REQUEST_PENDING) {
    const int result =
        esp_ble_passkey_reply(address.data(), true, numeric_passkey);
    this->state_ = PairingState::REPLY_SENT;
    ESP_LOGI(TAG, "%s passkey reply status=%d (PIN arrived second)",
             DEBUG_PREFIX, result);
    return;
  }

  this->pending_passkey_ = numeric_passkey;
  this->state_ = PairingState::PASSKEY_READY;
  ESP_LOGI(TAG, "%s PIN stored for pending request", DEBUG_PREFIX);
}

void DimplexPasskeyProxy::cancel_pairing() {
  ESP_LOGI(TAG, "%s pairing cancelled", DEBUG_PREFIX);
  this->clear_pairing_();
}


void DimplexPasskeyProxy::gap_event_handler(esp_gap_ble_cb_event_t event,
                                            esp_ble_gap_cb_param_t *param) {
  if (event == ESP_GAP_BLE_AUTH_CMPL_EVT) {
    const auto &auth = param->ble_security.auth_cmpl;
    ESP_LOGI(TAG, "%s authentication complete success=%d reason=%d bonds=%d",
             DEBUG_PREFIX, auth.success, auth.fail_reason,
             esp_ble_get_bond_device_num());
    return;
  }
  if (event == ESP_GAP_BLE_KEY_EVT) {
    ESP_LOGI(TAG, "%s security key event type=%d", DEBUG_PREFIX,
             param->ble_security.ble_key.key_type);
    return;
  }
  if (event != ESP_GAP_BLE_PASSKEY_REQ_EVT ||
      this->state_ == PairingState::IDLE) {
    return;
  }
  if (millis() - this->pending_since_ >= PASSKEY_LIFETIME_MS) {
    ESP_LOGW(TAG, "%s relay state expired before GAP event", DEBUG_PREFIX);
    this->clear_pairing_();
    return;
  }

  auto *address = param->ble_security.ble_req.bd_addr;
  if (!this->matches_address_(address)) {
    ESP_LOGW(TAG, "%s passkey request received: address did not match",
             DEBUG_PREFIX);
    return;
  }
  if (this->state_ == PairingState::REPLY_SENT) {
    return;
  }
  ESP_LOGI(TAG, "%s passkey request received for armed address", DEBUG_PREFIX);
  if (this->state_ == PairingState::PASSKEY_READY) {
    const int result =
        esp_ble_passkey_reply(address, true, this->pending_passkey_);
    this->state_ = PairingState::REPLY_SENT;
    ESP_LOGI(TAG, "%s passkey reply status=%d (PIN arrived first)",
             DEBUG_PREFIX, result);
  } else {
    this->state_ = PairingState::REQUEST_PENDING;
    ESP_LOGI(TAG, "%s waiting for HA PIN action", DEBUG_PREFIX);
  }
}

bool DimplexPasskeyProxy::parse_address_(
    const char *value, std::array<uint8_t, 6> &address) {
  if (value == nullptr || std::strlen(value) != 12) {
    return false;
  }
  for (std::size_t index = 0; index < address.size(); ++index) {
    const int high = hex_digit_(value[index * 2]);
    const int low = hex_digit_(value[index * 2 + 1]);
    if (high < 0 || low < 0) {
      return false;
    }
    address[index] = static_cast<uint8_t>((high << 4) | low);
  }
  return true;
}

bool DimplexPasskeyProxy::parse_passkey_(const char *value,
                                         uint32_t &passkey) {
  if (value == nullptr || std::strlen(value) != 6) {
    return false;
  }
  uint32_t parsed = 0;
  for (std::size_t index = 0; index < 6; ++index) {
    const char digit = value[index];
    if (digit < '0' || digit > '9') {
      return false;
    }
    parsed = parsed * 10 + static_cast<uint32_t>(digit - '0');
  }
  passkey = parsed;
  return true;
}


bool DimplexPasskeyProxy::matches_address_(const uint8_t *address) const {
  if (std::memcmp(this->pending_address_.data(), address,
                  this->pending_address_.size()) == 0) {
    return true;
  }
  for (std::size_t index = 0; index < this->pending_address_.size(); ++index) {
    if (this->pending_address_[index] !=
        address[this->pending_address_.size() - 1 - index]) {
      return false;
    }
  }
  return true;
}


void DimplexPasskeyProxy::clear_pairing_() {
  this->pending_address_.fill(0);
  this->pending_passkey_ = 0;
  this->pending_since_ = 0;
  this->state_ = PairingState::IDLE;
}

}  // namespace esphome::dimplex_passkey_proxy
