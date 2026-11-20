#pragma once
#include <cstdint>

namespace esphome {
class Component {
 public:
  virtual ~Component() = default;
  virtual void setup() {}
  virtual void loop() {}
};
}  // namespace esphome

uint32_t millis();
