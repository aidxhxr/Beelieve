#include "sensors.h"

#include <math.h>

#include <DallasTemperature.h>
#include <HX711.h>
#include <OneWire.h>
#include <Preferences.h>
#include <Wire.h>
#include <driver/i2s.h>
#include <esp_task_wdt.h>

#ifdef BROOD_SENSOR_DHT22
#include <DHT.h>
#else
#include <Adafruit_SHT31.h>
#endif

#include "config.h"

namespace sensors {

// ---------------------------------------------------------------------------
// Static sensor instances
// ---------------------------------------------------------------------------
#ifdef BROOD_SENSOR_DHT22
static DHT s_dht(PIN_DHT, DHT22);
#else
static Adafruit_SHT31 s_sht31;
static bool s_sht31Present = false;
#endif

static OneWire s_oneWire(PIN_ONEWIRE);
static DallasTemperature s_ds18b20(&s_oneWire);
static bool s_ds18b20Present = false;

static HX711 s_scale;
static Preferences s_prefs;

static const char* kPrefsNamespace = "beelieve";
static const char* kPrefKeyCal = "scale_cal";  // float, ADC counts per kg
static const char* kPrefKeyOffset = "scale_off"; // int32, raw tare offset

// ---------------------------------------------------------------------------
// begin
// ---------------------------------------------------------------------------
void begin() {
  Wire.begin(PIN_I2C_SDA, PIN_I2C_SCL);

#ifdef BROOD_SENSOR_DHT22
  s_dht.begin();
#else
  s_sht31Present = s_sht31.begin(SHT31_I2C_ADDR);
  if (!s_sht31Present) LOGE("SHT31 not found at 0x%02X", SHT31_I2C_ADDR);
#endif

  s_ds18b20.begin();
  s_ds18b20Present = s_ds18b20.getDeviceCount() > 0;
  if (!s_ds18b20Present) LOGE("DS18B20 not found on GPIO%d", PIN_ONEWIRE);
  s_ds18b20.setResolution(12);
  s_ds18b20.setWaitForConversion(true);

  s_scale.begin(PIN_HX711_DOUT, PIN_HX711_SCK);
  s_prefs.begin(kPrefsNamespace, false);
  const float cal = s_prefs.getFloat(kPrefKeyCal, HX711_DEFAULT_CAL_FACTOR);
  const long offset = (long)s_prefs.getInt(kPrefKeyOffset, 0);
  s_scale.set_scale(cal);
  s_scale.set_offset(offset);
  LOGD("scale cal=%.1f counts/kg offset=%ld", cal, offset);

  // MH-Z19 on UART2. The sensor needs ~3 min warm-up after cold power-up;
  // it stays powered across deep sleep, so cycles after the first are valid.
  Serial2.begin(9600, SERIAL_8N1, PIN_MHZ19_RX, PIN_MHZ19_TX);

  analogSetPinAttenuation(PIN_VBAT_ADC, ADC_11db);
}

// ---------------------------------------------------------------------------
// Brood temperature + humidity
// ---------------------------------------------------------------------------
static void readBrood(SensorReadings& out) {
#ifdef BROOD_SENSOR_DHT22
  const float t = s_dht.readTemperature();
  const float h = s_dht.readHumidity();
#else
  if (!s_sht31Present) return;
  const float t = s_sht31.readTemperature();
  const float h = s_sht31.readHumidity();
#endif
  if (!isnan(t) && t > -40.0f && t < 85.0f) {
    out.temp_brood_c = t;
    out.has_temp_brood = true;
  }
  if (!isnan(h) && h >= 0.0f && h <= 100.0f) {
    out.humidity_pct = h;
    out.has_humidity = true;
  }
}

// ---------------------------------------------------------------------------
// Ambient temperature (DS18B20)
// ---------------------------------------------------------------------------
static void readAmbient(SensorReadings& out) {
  if (!s_ds18b20Present) return;
  s_ds18b20.requestTemperatures();
  const float t = s_ds18b20.getTempCByIndex(0);
  if (t > -55.0f && t < 125.0f && t != DEVICE_DISCONNECTED_C) {
    out.temp_ambient_c = t;
    out.has_temp_ambient = true;
  }
}

// ---------------------------------------------------------------------------
// Weight (HX711)
// ---------------------------------------------------------------------------
static void readWeight(SensorReadings& out) {
  s_scale.power_up();
  if (!s_scale.wait_ready_timeout(1000)) {
    LOGE("HX711 not ready");
    s_scale.power_down();
    return;
  }
  const float kg = s_scale.get_units(10);
  s_scale.power_down();
  if (kg > -5.0f && kg < 300.0f) { // plausibility window for a hive on a stand
    out.weight_kg = kg;
    out.has_weight = true;
  } else {
    LOGE("HX711 implausible reading %.2f kg (check calibration)", kg);
  }
}

// ---------------------------------------------------------------------------
// Audio: INMP441 over I2S, 16 kHz for 1 s.
//
// Spectral energies are computed with a small fixed-point Goertzel bank —
// no FFT library. Each 100 Hz contract band is covered by 5 detectors spaced
// 20 Hz apart; with N = fs = 16000 every integer frequency is an exact DFT
// bin, so rectangular-window leakage between detectors is minimal. States are
// int64 with Q14 coefficients: exact, overflow-free for N=16000 16-bit input.
// ---------------------------------------------------------------------------
struct Goertzel {
  int32_t coeff_q14; // round(2*cos(2*pi*f/fs) * 2^14)
  int64_t s1;
  int64_t s2;
};

static const int kBands = 5;
static const int kDetectorsPerBand = 5;
static const int kDetectors = kBands * kDetectorsPerBand;

static inline void goertzelInit(Goertzel& g, float freqHz) {
  g.coeff_q14 =
      (int32_t)lroundf(2.0f * cosf(2.0f * (float)M_PI * freqHz / AUDIO_SAMPLE_RATE) * 16384.0f);
  g.s1 = 0;
  g.s2 = 0;
}

static inline void goertzelStep(Goertzel& g, int32_t x) {
  const int64_t s0 = (((int64_t)g.coeff_q14 * g.s1) >> 14) - g.s2 + x;
  g.s2 = g.s1;
  g.s1 = s0;
}

static inline double goertzelPower(const Goertzel& g) {
  const int64_t cross = ((int64_t)g.coeff_q14 * g.s1) >> 14;
  const double p =
      (double)g.s1 * (double)g.s1 + (double)g.s2 * (double)g.s2 - (double)cross * (double)g.s2;
  return p > 0.0 ? p : 0.0;
}

static bool i2sMicStart() {
  const i2s_config_t cfg = {
      .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX),
      .sample_rate = AUDIO_SAMPLE_RATE,
      .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT,
      .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT,
      .communication_format = I2S_COMM_FORMAT_STAND_I2S,
      .intr_alloc_flags = ESP_INTR_FLAG_LEVEL1,
      .dma_buf_count = 8,
      .dma_buf_len = 256,
      .use_apll = false,
      .tx_desc_auto_clear = false,
      .fixed_mclk = 0,
  };
  const i2s_pin_config_t pins = {
      .mck_io_num = I2S_PIN_NO_CHANGE,
      .bck_io_num = PIN_I2S_BCLK,
      .ws_io_num = PIN_I2S_WS,
      .data_out_num = I2S_PIN_NO_CHANGE,
      .data_in_num = PIN_I2S_SD,
  };
  if (i2s_driver_install(I2S_NUM_0, &cfg, 0, nullptr) != ESP_OK) return false;
  if (i2s_set_pin(I2S_NUM_0, &pins) != ESP_OK) {
    i2s_driver_uninstall(I2S_NUM_0);
    return false;
  }
  i2s_zero_dma_buffer(I2S_NUM_0);
  return true;
}

static void readAudio(SensorReadings& out) {
  if (!i2sMicStart()) {
    LOGE("I2S driver install failed");
    return;
  }

  Goertzel bank[kDetectors];
  for (int b = 0; b < kBands; ++b) {
    for (int k = 0; k < kDetectorsPerBand; ++k) {
      // Band b covers [100*(b+1), 100*(b+2)] Hz; detectors at lo+10+20k.
      const float f = 100.0f * (b + 1) + 10.0f + 20.0f * k;
      goertzelInit(bank[b * kDetectorsPerBand + k], f);
    }
  }

  static int32_t chunk[256];
  int64_t sum = 0;
  uint64_t sumSq = 0;
  int32_t peakAbs = 0;
  uint32_t collected = 0;
  uint32_t discarded = 0;
  bool ioError = false;

  while (collected < AUDIO_SAMPLE_COUNT) {
    size_t bytesRead = 0;
    if (i2s_read(I2S_NUM_0, chunk, sizeof(chunk), &bytesRead, pdMS_TO_TICKS(300)) != ESP_OK ||
        bytesRead == 0) {
      ioError = true;
      break;
    }
    const size_t n = bytesRead / sizeof(int32_t);
    for (size_t i = 0; i < n && collected < AUDIO_SAMPLE_COUNT; ++i) {
      if (discarded < AUDIO_WARMUP_SAMPLES) { // let the mic settle
        ++discarded;
        continue;
      }
      // INMP441: 24-bit data MSB-aligned in the 32-bit slot -> >>16 gives a
      // signed 16-bit sample.
      const int32_t x = chunk[i] >> 16;
      sum += x;
      sumSq += (uint64_t)((int64_t)x * x);
      const int32_t ax = x < 0 ? -x : x;
      if (ax > peakAbs) peakAbs = ax;
      for (int d = 0; d < kDetectors; ++d) goertzelStep(bank[d], x);
      ++collected;
    }
    esp_task_wdt_reset();
  }

  i2s_driver_uninstall(I2S_NUM_0);

  if (ioError || collected < AUDIO_SAMPLE_COUNT) {
    LOGE("audio capture incomplete (%lu samples)", (unsigned long)collected);
    return;
  }
  if (peakAbs < 2) {
    // Data line stuck at rail/ground — microphone almost certainly absent.
    LOGE("audio all-zero, INMP441 missing?");
    return;
  }

  // Band energies, normalized to sum 1.0.
  double bandPower[kBands];
  double total = 0.0;
  for (int b = 0; b < kBands; ++b) {
    double p = 0.0;
    for (int k = 0; k < kDetectorsPerBand; ++k)
      p += goertzelPower(bank[b * kDetectorsPerBand + k]);
    bandPower[b] = p;
    total += p;
  }
  if (total <= 0.0) return;
  for (int b = 0; b < kBands; ++b) out.audio_bands[b] = (float)(bandPower[b] / total);

  // dB SPL estimate from DC-removed RMS. INMP441 sensitivity: -26 dBFS at
  // 94 dB SPL, i.e. 94 dB SPL corresponds to RMS ≈ (32768/sqrt(2))*10^(-26/20)
  // ≈ 1161 counts in our 16-bit domain.
  const double mean = (double)sum / collected;
  const double variance = (double)sumSq / collected - mean * mean;
  const double rms = variance > 0.0 ? sqrt(variance) : 0.0;
  const double kRms94dB = 1161.0;
  out.audio_db = (float)(94.0 + 20.0 * log10((rms > 0.1 ? rms : 0.1) / kRms94dB));
  out.has_audio = true;
  LOGD("audio: %.1f dB SPL, bands %.3f/%.3f/%.3f/%.3f/%.3f", out.audio_db, out.audio_bands[0],
       out.audio_bands[1], out.audio_bands[2], out.audio_bands[3], out.audio_bands[4]);
}

// ---------------------------------------------------------------------------
// CO2 (MH-Z19, UART "read CO2" command 0x86). Silent absence -> field omitted.
// ---------------------------------------------------------------------------
static bool mhz19ReadOnce(uint16_t& ppm) {
  static const uint8_t cmd[9] = {0xFF, 0x01, 0x86, 0x00, 0x00, 0x00, 0x00, 0x00, 0x79};
  while (Serial2.available()) Serial2.read(); // drain stale bytes
  Serial2.write(cmd, sizeof(cmd));
  Serial2.flush();

  uint8_t resp[9];
  const uint32_t start = millis();
  size_t got = 0;
  while (got < sizeof(resp) && millis() - start < 500) {
    if (Serial2.available()) resp[got++] = (uint8_t)Serial2.read();
  }
  if (got < sizeof(resp) || resp[0] != 0xFF || resp[1] != 0x86) return false;

  uint8_t checksum = 0;
  for (int i = 1; i < 8; ++i) checksum += resp[i];
  checksum = (uint8_t)(0xFF - checksum + 1);
  if (checksum != resp[8]) return false;

  ppm = (uint16_t)((resp[2] << 8) | resp[3]);
  return ppm > 0 && ppm <= 10000;
}

static void readCo2(SensorReadings& out) {
  uint16_t ppm = 0;
  for (int attempt = 0; attempt < 2; ++attempt) {
    if (mhz19ReadOnce(ppm)) {
      out.co2_ppm = ppm;
      out.has_co2 = true;
      return;
    }
    delay(100);
  }
  LOGD("MH-Z19 absent or not responding; co2_ppm omitted");
}

// ---------------------------------------------------------------------------
// Battery voltage via resistive divider on ADC1
// ---------------------------------------------------------------------------
static void readBattery(SensorReadings& out) {
  uint32_t mvSum = 0;
  for (int i = 0; i < 16; ++i) mvSum += analogReadMilliVolts(PIN_VBAT_ADC);
  const float v = (mvSum / 16.0f) * VBAT_DIVIDER_RATIO / 1000.0f;
  if (v > 1.0f && v < 6.0f) { // < 1 V means the divider is not connected
    out.battery_v = v;
    out.has_battery = true;
  }
}

// ---------------------------------------------------------------------------
// readAll
// ---------------------------------------------------------------------------
void readAll(SensorReadings& out) {
  out = SensorReadings{};
  readBrood(out);
  esp_task_wdt_reset();
  readAmbient(out); // DS18B20 12-bit conversion blocks ~750 ms
  esp_task_wdt_reset();
  readWeight(out);
  esp_task_wdt_reset();
  readAudio(out); // ~1.2 s capture
  esp_task_wdt_reset();
  readCo2(out);
  readBattery(out);
  LOGD("readings: brood=%d amb=%d hum=%d wt=%d audio=%d co2=%d bat=%d", out.has_temp_brood,
       out.has_temp_ambient, out.has_humidity, out.has_weight, out.has_audio, out.has_co2,
       out.has_battery);
}

// ---------------------------------------------------------------------------
// Scale calibration (persisted to NVS)
// ---------------------------------------------------------------------------
void tare() {
  s_scale.power_up();
  if (!s_scale.wait_ready_timeout(1000)) {
    Serial.println("[cal] HX711 not ready");
    return;
  }
  s_scale.tare(15);
  s_prefs.putInt(kPrefKeyOffset, (int32_t)s_scale.get_offset());
  Serial.printf("[cal] tare done, offset=%ld\r\n", s_scale.get_offset());
}

bool calibrate(float known_mass_kg) {
  if (known_mass_kg <= 0.0f) return false;
  s_scale.power_up();
  if (!s_scale.wait_ready_timeout(1000)) {
    Serial.println("[cal] HX711 not ready");
    return false;
  }
  const double raw = s_scale.read_average(15);
  const double delta = raw - (double)s_scale.get_offset();
  if (fabs(delta) < 100.0) {
    Serial.println("[cal] no signal above tare — is the mass on the platform?");
    return false;
  }
  const float factor = (float)(delta / known_mass_kg);
  s_scale.set_scale(factor);
  s_prefs.putFloat(kPrefKeyCal, factor);
  Serial.printf("[cal] calibrated: %.1f counts/kg (%.3f kg reference)\r\n", factor, known_mass_kg);
  return true;
}

long rawScaleReading() {
  s_scale.power_up();
  if (!s_scale.wait_ready_timeout(1000)) return 0;
  return s_scale.read_average(10);
}

// ---------------------------------------------------------------------------
// Serial calibration console
// ---------------------------------------------------------------------------
static void printCalHelp() {
  Serial.println("Beelieve calibration console — commands:");
  Serial.println("  tare        record zero offset (platform empty)");
  Serial.println("  cal <kg>    calibrate with a known mass, e.g. `cal 2.000`");
  Serial.println("  raw         print averaged raw HX711 counts");
  Serial.println("  weight      print current weight using stored calibration");
  Serial.println("  info        show stored calibration factor and offset");
  Serial.println("  exit        leave console and resume normal cycle");
}

static void calConsole() {
  printCalHelp();
  String line;
  uint32_t lastActivity = millis();
  while (millis() - lastActivity < 5UL * 60UL * 1000UL) { // 5 min idle timeout
    esp_task_wdt_reset();
    if (!Serial.available()) {
      delay(20);
      continue;
    }
    line = Serial.readStringUntil('\n');
    line.trim();
    lastActivity = millis();
    if (line.isEmpty()) continue;

    if (line == "exit") {
      Serial.println("[cal] bye");
      return;
    } else if (line == "tare") {
      tare();
    } else if (line.startsWith("cal")) {
      const float kg = line.substring(3).toFloat();
      if (kg <= 0.0f) {
        Serial.println("[cal] usage: cal <mass_in_kg>");
      } else {
        calibrate(kg);
      }
    } else if (line == "raw") {
      Serial.printf("[cal] raw=%ld\r\n", rawScaleReading());
    } else if (line == "weight") {
      s_scale.power_up();
      if (s_scale.wait_ready_timeout(1000)) {
        Serial.printf("[cal] weight=%.3f kg\r\n", s_scale.get_units(10));
      } else {
        Serial.println("[cal] HX711 not ready");
      }
    } else if (line == "info") {
      Serial.printf("[cal] factor=%.1f counts/kg offset=%ld\r\n", s_scale.get_scale(),
                    s_scale.get_offset());
    } else {
      printCalHelp();
    }
  }
  Serial.println("[cal] idle timeout, resuming");
}

void handleSerialCalibration(uint32_t window_ms) {
  Serial.printf("[cal] send any key within %lu ms for calibration console\r\n",
                (unsigned long)window_ms);
  const uint32_t start = millis();
  while (millis() - start < window_ms) {
    if (Serial.available()) {
      while (Serial.available()) Serial.read(); // swallow the wake byte(s)
      calConsole();
      return;
    }
    delay(10);
  }
}

} // namespace sensors
