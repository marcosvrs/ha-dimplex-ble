#pragma once

#include <array>
#include <cstdint>

#include "esphome/core/component.h"
#include "esp_gap_ble_api.h"

namespace esphome::dimplex_passkey_proxy {

class DimplexPasskeyProxy final : public Component {
 public:
  void loop() override;

  /// Arm pairing for one address before the proxy requests a BLE connection.
  void arm_pairing(const char *address_hex);
  /// Supply the PIN after the heater displays it.
  void set_pending_passkey(const char *address_hex, const char *passkey);
  /// Clear all pairing state for this proxy.
  void cancel_pairing();

  /// Called from ESPHome's main-loop GAP event dispatcher.
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param);

 protected:
  enum class PairingState : uint8_t {
    IDLE,
    WAITING_FOR_REQUEST,
    PASSKEY_READY,
    REQUEST_PENDING,
  };

  static bool parse_address_(const char *value, std::array<uint8_t, 6> &address);
  static bool parse_passkey_(const char *value, uint32_t &passkey);
  bool matches_address_(const uint8_t *address) const;
  void clear_pairing_();

  std::array<uint8_t, 6> pending_address_{};
  uint32_t pending_passkey_{0};
  uint32_t pending_since_{0};
  PairingState state_{PairingState::IDLE};
};

}  // namespace esphome::dimplex_passkey_proxy
