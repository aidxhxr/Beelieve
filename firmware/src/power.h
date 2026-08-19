// Beelieve hive sensor node — power management (deep sleep + RTC counters).
#pragma once

#include <Arduino.h>

namespace power {

// Call once at the top of setup(): bumps the RTC-memory boot counter and logs
// the wake cause.
void onBoot();

// Number of wake cycles since cold boot (kept in RTC slow memory).
uint32_t bootCount();

// Enter deep sleep for `seconds` (timer wake). Does not return.
void deepSleep(uint32_t seconds) __attribute__((noreturn));

} // namespace power
