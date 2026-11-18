#pragma once
#include <cstdint>

namespace esphome {
class Component {
 public:
  virtual ~Component() = default;
  virtual void loop() {}
};
}

uint32_t millis();
