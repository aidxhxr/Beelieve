#include "power.h"

#include <esp_sleep.h>

#include "config.h"

namespace power {

// Survives deep sleep; reset only on power-on / hard reset.
static RTC_DATA_ATTR uint32_t s_bootCount = 0;

void onBoot() {
  ++s_bootCount;
  const esp_sleep_wakeup_cause_t cause = esp_sleep_get_wakeup_cause();
  LOGD("boot #%lu, wake cause=%d (%s)", (unsigned long)s_bootCount, (int)cause,
       cause == ESP_SLEEP_WAKEUP_TIMER ? "timer" : "power-on/reset");
  (void)cause;
}

uint32_t bootCount() { return s_bootCount; }

void deepSleep(uint32_t seconds) {
  LOGD("deep sleep for %lu s", (unsigned long)seconds);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
  esp_deep_sleep_start();
  while (true) {} // not reached; satisfies noreturn
}

} // namespace power
