// Beelieve hive sensor node — sensor acquisition.
//
// Every field is optional (per docs/ARCHITECTURE.md a sensor fault must not
// drop the whole reading), so each value carries a has_* flag.
#pragma once

#include <Arduino.h>

// Ordered to match the telemetry contract keys:
// b100_200, b200_300, b300_400, b400_500, b500_600
static const char* const kAudioBandKeys[5] = {
    "b100_200", "b200_300", "b300_400", "b400_500", "b500_600"};

struct SensorReadings {
  bool has_temp_brood = false;
  float temp_brood_c = 0;

  bool has_humidity = false;
  float humidity_pct = 0;

  bool has_temp_ambient = false;
  float temp_ambient_c = 0;

  bool has_weight = false;
  float weight_kg = 0;

  bool has_audio = false;
  float audio_db = 0;       // dB SPL estimate
  float audio_bands[5] = {0, 0, 0, 0, 0}; // normalized, sum ≈ 1.0

  bool has_co2 = false;
  uint16_t co2_ppm = 0;

  bool has_battery = false;
  float battery_v = 0;
};

namespace sensors {

// Initialise buses and probes; loads scale calibration from NVS.
void begin();

// Acquire one full reading. Individual sensor failures set has_* = false.
void readAll(SensorReadings& out);

// --- scale calibration (persisted in NVS via Preferences) ---
void tare();                       // record zero offset with empty platform
bool calibrate(float known_mass_kg); // derive counts/kg from a known mass
long rawScaleReading();            // averaged raw ADC counts (diagnostics)

// Blocks up to window_ms waiting for any serial input; if a byte arrives,
// enters the interactive calibration console (tare / cal <kg> / raw / ...).
void handleSerialCalibration(uint32_t window_ms);

} // namespace sensors
