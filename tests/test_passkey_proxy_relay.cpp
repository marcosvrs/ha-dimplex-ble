#include <cassert>
#include <cstdint>
#include <vector>

#include "dimplex_passkey_proxy.h"

namespace {
uint32_t now_ms = 0;
struct Reply { uint8_t address[6]; bool accepted; uint32_t passkey; };
std::vector<Reply> replies;

void request(esphome::dimplex_passkey_proxy::DimplexPasskeyProxy &relay,
             const uint8_t (&address)[6]) {
  esp_ble_gap_cb_param_t param{};
  for (int i = 0; i < 6; ++i) param.ble_security.ble_req.bd_addr[i] = address[i];
  relay.gap_event_handler(ESP_GAP_BLE_PASSKEY_REQ_EVT, &param);
}
}

uint32_t millis() { return now_ms; }
int esp_ble_passkey_reply(uint8_t *address, bool accept, uint32_t passkey) {
  Reply reply{};
  for (int i = 0; i < 6; ++i) reply.address[i] = address[i];
  reply.accepted = accept;
  reply.passkey = passkey;
  replies.push_back(reply);
  return 0;
}

int main() {
  using esphome::dimplex_passkey_proxy::DimplexPasskeyProxy;
  DimplexPasskeyProxy relay;
  constexpr uint8_t first[] = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06};
  constexpr uint8_t second[] = {0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f};

  relay.arm_pairing("not-an-address");
  relay.set_pending_passkey("010203040506", "123456");
  request(relay, first);
  assert(replies.empty());

  relay.arm_pairing("010203040506");
  relay.gap_event_handler(ESP_GAP_BLE_OTHER_EVT, nullptr);
  request(relay, second);
  assert(replies.empty());
  request(relay, first);
  assert(replies.empty());  // The GAP request waits for the user's PIN.
  relay.set_pending_passkey("010203040506", "000042");
  assert(replies.size() == 1 && replies.back().accepted &&
         replies.back().passkey == 42);
  request(relay, first);
  assert(replies.size() == 1);  // The reply is one-shot.

  relay.arm_pairing("0A0B0C0D0E0F");
  relay.set_pending_passkey("0a0b0c0d0e0f", "123456");
  request(relay, second);
  assert(replies.size() == 2 && replies.back().passkey == 123456);

  relay.arm_pairing("0A0B0C0D0E0F");
  request(relay, second);
  relay.set_pending_passkey("010203040506", "654321");
  assert(replies.size() == 2);  // A PIN for another address is ignored.
  relay.set_pending_passkey("0A0B0C0D0E0F", "654321");
  assert(replies.size() == 3 && replies.back().passkey == 654321);

  relay.arm_pairing("0A0B0C0D0E0F");
  relay.set_pending_passkey("0A0B0C0D0E0F", "12x456");
  request(relay, second);
  assert(replies.size() == 3);  // An invalid PIN disarms the attempt.

  relay.arm_pairing("0A0B0C0D0E0F");
  now_ms += 60'000;
  request(relay, second);
  assert(replies.size() == 3);  // An expired GAP request is not retained.

  relay.arm_pairing("0A0B0C0D0E0F");
  now_ms += 60'000;
  relay.loop();
  relay.set_pending_passkey("0A0B0C0D0E0F", "000042");
  request(relay, second);
  assert(replies.size() == 3);  // The main loop expires an armed attempt.

  relay.arm_pairing("0A0B0C0D0E0F");
  request(relay, second);
  relay.cancel_pairing();
  relay.set_pending_passkey("0A0B0C0D0E0F", "987654");
  request(relay, second);
  assert(replies.size() == 3);  // Cancel clears a request awaiting a PIN.

  relay.arm_pairing("0A0B0C0D0E0F");
  relay.set_pending_passkey("0A0B0C0D0E0F", "987654");
  esp_ble_gap_cb_param_t auth{};
  for (int i = 0; i < 6; ++i) {
    auth.ble_security.auth_cmpl.bd_addr[i] = first[i];
  }
  relay.gap_event_handler(ESP_GAP_BLE_AUTH_CMPL_EVT, &auth);
  request(relay, second);
  assert(replies.size() == 4 && replies.back().passkey == 987654);

  relay.arm_pairing("0A0B0C0D0E0F");
  request(relay, second);
  for (int i = 0; i < 6; ++i) {
    auth.ble_security.auth_cmpl.bd_addr[i] = second[i];
  }
  relay.gap_event_handler(ESP_GAP_BLE_AUTH_CMPL_EVT, &auth);
  relay.set_pending_passkey("0A0B0C0D0E0F", "987654");
  assert(replies.size() == 4);  // Matching auth completion clears the PIN state.
}
