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

void DimplexPasskeyProxy::loop() {
  if (this->state_ != PairingState::IDLE &&
      millis() - this->pending_since_ >= PASSKEY_LIFETIME_MS) {
    ESP_LOGW(TAG, "%s relay state expired", DEBUG_PREFIX);
    this->clear_pairing_();
  }
}

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
    ESP_LOGI(TAG, "%s passkey reply status=%d (PIN arrived second)",
             DEBUG_PREFIX, result);
    this->clear_pairing_();
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
  if (this->state_ == PairingState::IDLE) {
    return;
  }
  if (millis() - this->pending_since_ >= PASSKEY_LIFETIME_MS) {
    ESP_LOGW(TAG, "%s relay state expired before GAP event", DEBUG_PREFIX);
    this->clear_pairing_();
    return;
  }

  if (event == ESP_GAP_BLE_SEC_REQ_EVT) {
    const bool matches =
        this->matches_address_(param->ble_security.ble_req.bd_addr);
    ESP_LOGI(TAG, "%s security request received: address_match=%s",
             DEBUG_PREFIX, matches ? "yes" : "no");
    return;
  }
  if (event == ESP_GAP_BLE_PASSKEY_NOTIF_EVT) {
    const bool matches =
        this->matches_address_(param->ble_security.key_notif.bd_addr);
    ESP_LOGI(TAG, "%s passkey notification received: address_match=%s",
             DEBUG_PREFIX, matches ? "yes" : "no");
    return;
  }
  if (event == ESP_GAP_BLE_AUTH_CMPL_EVT) {
    const bool matches =
        this->matches_address_(param->ble_security.auth_cmpl.bd_addr);
    if (matches) {
      ESP_LOGI(TAG, "%s authentication complete: success=%s fail_reason=%d",
               DEBUG_PREFIX,
               param->ble_security.auth_cmpl.success ? "yes" : "no",
               static_cast<int>(param->ble_security.auth_cmpl.fail_reason));
      this->clear_pairing_();
    } else {
      ESP_LOGI(TAG, "%s authentication complete for another address",
               DEBUG_PREFIX);
    }
    return;
  }
  if (event != ESP_GAP_BLE_PASSKEY_REQ_EVT) {
    return;
  }

  auto *address = param->ble_security.ble_req.bd_addr;
  if (!this->matches_address_(address)) {
    ESP_LOGW(TAG, "%s passkey request received: address did not match",
             DEBUG_PREFIX);
    return;
  }
  ESP_LOGI(TAG, "%s passkey request received for armed address", DEBUG_PREFIX);
  if (this->state_ == PairingState::PASSKEY_READY) {
    const uint32_t passkey = this->pending_passkey_;
    this->clear_pairing_();
    const int result = esp_ble_passkey_reply(address, true, passkey);
    ESP_LOGI(TAG, "%s passkey reply status=%d (PIN arrived first)",
             DEBUG_PREFIX, result);
  } else {
    this->state_ = PairingState::REQUEST_PENDING;
    ESP_LOGI(TAG, "%s waiting for HA PIN action", DEBUG_PREFIX);
  }
}

bool DimplexPasskeyProxy::parse_address_(const char *value,
                                         std::array<uint8_t, 6> &address) {
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

bool DimplexPasskeyProxy::parse_passkey_(const char *value, uint32_t &passkey) {
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
  return std::memcmp(this->pending_address_.data(), address,
                     this->pending_address_.size()) == 0;
}

void DimplexPasskeyProxy::clear_pairing_() {
  this->pending_address_.fill(0);
  this->pending_passkey_ = 0;
  this->pending_since_ = 0;
  this->state_ = PairingState::IDLE;
}

}  // namespace esphome::dimplex_passkey_proxy
