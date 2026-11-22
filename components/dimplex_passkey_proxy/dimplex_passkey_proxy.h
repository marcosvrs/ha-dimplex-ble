#pragma once

#include <array>
#include <cstdint>

#include "esphome/core/component.h"

#include "esp_gap_ble_api.h"

namespace esphome::dimplex_passkey_proxy {

class DimplexPasskeyProxy final : public Component {
 public:
  /// Arm a short-lived passkey relay for one panel heater before pairing starts.
  void arm_pairing(const char *address_hex);
  /// Supply the PIN after the panel heater displays it.
  void set_pending_passkey(const char *address_hex, const char *passkey);
  /// Clear all pending passkey state for this proxy.
  void cancel_pairing();
  /// Remove every stored BLE bond from this proxy before a clean repair.
  void clear_all_bonds();
  /// Remove the stored BLE bond for one panel heater before a fresh pairing.
  void clear_bond(const char *address_hex);
  /// Called from ESPHome's shared GAP event dispatcher.
  void gap_event_handler(esp_gap_ble_cb_event_t event,
                         esp_ble_gap_cb_param_t *param);

 protected:
  enum class PairingState : uint8_t {
    IDLE,
    WAITING_FOR_REQUEST,
    PASSKEY_READY,
    REQUEST_PENDING,
    REPLY_SENT,
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
