#include <array>
#include <cassert>
#include <cstdint>
#include <vector>
#include "dimplex_passkey_proxy.h"

namespace {
uint32_t now_ms = 0;
struct Reply {
  uint8_t address[6];
  bool accepted;
  uint32_t passkey;
};
std::vector<Reply> replies;

void request(esphome::dimplex_passkey_proxy::DimplexPasskeyProxy &relay,
             const uint8_t (&address)[6]) {
  esp_ble_gap_cb_param_t param{};
  for (int i = 0; i < 6; ++i) {
    param.ble_security.ble_req.bd_addr[i] = address[i];
  }
  relay.gap_event_handler(ESP_GAP_BLE_PASSKEY_REQ_EVT, &param);
}

}  // namespace

uint32_t millis() { return now_ms; }

int esp_ble_passkey_reply(uint8_t *address, bool accept, uint32_t passkey) {
  Reply reply{};
  for (int i = 0; i < 6; ++i) {
    reply.address[i] = address[i];
  }
  reply.accepted = accept;
  reply.passkey = passkey;
  replies.push_back(reply);
  return 0;
}

int esp_ble_get_bond_device_num() { return 0; }
int esp_ble_get_bond_device_list(int *, esp_ble_bond_dev_t *) { return 0; }

int esp_ble_remove_bond_device(uint8_t *) { return 0; }




int main() {
  using esphome::dimplex_passkey_proxy::DimplexPasskeyProxy;
  DimplexPasskeyProxy relay;
  constexpr uint8_t first[] = {0x01, 0x02, 0x03, 0x04, 0x05, 0x06};
  constexpr uint8_t second[] = {0x0a, 0x0b, 0x0c, 0x0d, 0x0e, 0x0f};
  constexpr uint8_t first_reversed[] = {0x06, 0x05, 0x04, 0x03, 0x02, 0x01};

  relay.arm_pairing("not-an-address");
  relay.set_pending_passkey("010203040506", "123456");
  request(relay, first);
  assert(replies.empty());


  relay.arm_pairing("010203040506");
  request(relay, second);
  assert(replies.empty());
  request(relay, first);
  assert(replies.empty());
  relay.set_pending_passkey("010203040506", "000042");
  assert(replies.size() == 1 && replies.back().passkey == 42);
  request(relay, first);
  assert(replies.size() == 1);

  relay.arm_pairing("010203040506");
  request(relay, first_reversed);
  relay.set_pending_passkey("010203040506", "000043");
  assert(replies.size() == 2 && replies.back().passkey == 43);

  relay.arm_pairing("0A0B0C0D0E0F");
  relay.set_pending_passkey("0a0b0c0d0e0f", "123456");
  request(relay, second);
  assert(replies.size() == 3 && replies.back().passkey == 123456);

  relay.arm_pairing("0A0B0C0D0E0F");
  request(relay, second);
  relay.set_pending_passkey("010203040506", "654321");
  assert(replies.size() == 3);
  relay.set_pending_passkey("0A0B0C0D0E0F", "654321");
  assert(replies.size() == 4 && replies.back().passkey == 654321);

  relay.arm_pairing("0A0B0C0D0E0F");
  relay.set_pending_passkey("0A0B0C0D0E0F", "12x456");
  request(relay, second);
  assert(replies.size() == 4);

  relay.arm_pairing("0A0B0C0D0E0F");
  now_ms += 60'000;
  request(relay, second);
  assert(replies.size() == 4);

  relay.arm_pairing("0A0B0C0D0E0F");
  request(relay, second);
  relay.cancel_pairing();
  relay.set_pending_passkey("0A0B0C0D0E0F", "987654");
  request(relay, second);
  assert(replies.size() == 4);
  request(relay, first);
  assert(replies.size() == 4);
}
