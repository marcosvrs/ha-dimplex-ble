#pragma once

inline void esphome_test_log(const char *, const char *, ...) {}

#define ESP_LOGI(tag, ...) esphome_test_log(tag, __VA_ARGS__)
#define ESP_LOGW(tag, ...) esphome_test_log(tag, __VA_ARGS__)
