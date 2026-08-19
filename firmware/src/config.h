// Beelieve hive sensor node — compile-time configuration.
//
// Required identifiers/credentials come from platformio.ini build_flags or from
// include/secrets.h (template: include/secrets.h.example). Everything else has
// a sane default below and can be overridden the same way.
#pragma once

#include <Arduino.h>

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#if !defined(WIFI_SSID) || !defined(WIFI_PASS) || !defined(MQTT_HOST) || \
    !defined(HIVE_ID) || !defined(APIARY_ID)
#error "Missing configuration: define WIFI_SSID, WIFI_PASS, MQTT_HOST, HIVE_ID and APIARY_ID via build_flags or copy include/secrets.h.example to include/secrets.h"
#endif

// ---------------------------------------------------------------------------
// Identity / firmware
// ---------------------------------------------------------------------------
#ifndef FW_VERSION
#define FW_VERSION "1.0.0"
#endif

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef MQTT_USER
#define MQTT_USER "" // empty -> anonymous
#endif
#ifndef MQTT_PASS
#define MQTT_PASS ""
#endif
#ifndef MQTT_KEEPALIVE_S
#define MQTT_KEEPALIVE_S 30
#endif
// Telemetry payload is ~400 B; PubSubClient default buffer (256 B) is too small.
#ifndef MQTT_BUFFER_SIZE
#define MQTT_BUFFER_SIZE 1024
#endif

// ---------------------------------------------------------------------------
// Cycle timing / power
// ---------------------------------------------------------------------------
#ifndef TELEMETRY_INTERVAL_S
#define TELEMETRY_INTERVAL_S 600 // deep-sleep period between readings
#endif
#ifndef WDT_TIMEOUT_S
#define WDT_TIMEOUT_S 90 // hard watchdog on the whole wake cycle
#endif
#ifndef CALIBRATION_WINDOW_MS
#define CALIBRATION_WINDOW_MS 2500 // serial window after boot to enter cal console
#endif
#ifndef NTP_RESYNC_INTERVAL_S
#define NTP_RESYNC_INTERVAL_S 86400 // SNTP re-sync at most once per day
#endif
#ifndef NTP_SERVER_1
#define NTP_SERVER_1 "pool.ntp.org"
#endif
#ifndef NTP_SERVER_2
#define NTP_SERVER_2 "time.google.com"
#endif

// ---------------------------------------------------------------------------
// Networking retry policy
// ---------------------------------------------------------------------------
#ifndef WIFI_MAX_ATTEMPTS
#define WIFI_MAX_ATTEMPTS 4
#endif
#ifndef WIFI_ATTEMPT_TIMEOUT_MS
#define WIFI_ATTEMPT_TIMEOUT_MS 8000
#endif
#ifndef WIFI_BACKOFF_BASE_MS
#define WIFI_BACKOFF_BASE_MS 500 // doubles per attempt, capped at 8 s
#endif
#ifndef MQTT_MAX_ATTEMPTS
#define MQTT_MAX_ATTEMPTS 3
#endif

// ---------------------------------------------------------------------------
// Pin map (see README.md wiring table)
// ---------------------------------------------------------------------------
#define PIN_I2C_SDA 21   // SHT31 SDA
#define PIN_I2C_SCL 22   // SHT31 SCL
#define SHT31_I2C_ADDR 0x44

#define PIN_ONEWIRE 4    // DS18B20 data (4.7 kΩ pull-up to 3V3)

#define PIN_HX711_DOUT 16
#define PIN_HX711_SCK 17

#define PIN_I2S_BCLK 26  // INMP441 SCK
#define PIN_I2S_WS 25    // INMP441 WS  (L/R tied to GND -> left channel)
#define PIN_I2S_SD 33    // INMP441 SD

#define PIN_MHZ19_RX 35  // ESP32 RX2  <- MH-Z19 TX (GPIO35 is input-only: OK)
#define PIN_MHZ19_TX 32  // ESP32 TX2  -> MH-Z19 RX

#define PIN_VBAT_ADC 34  // battery divider midpoint (ADC1_CH6, input-only)
#ifndef VBAT_DIVIDER_RATIO
#define VBAT_DIVIDER_RATIO 2.0f // 100 kΩ / 100 kΩ divider
#endif

// Optional: use a DHT22 for brood temp/humidity instead of the SHT31.
// #define BROOD_SENSOR_DHT22
#ifndef PIN_DHT
#define PIN_DHT 27
#endif

// ---------------------------------------------------------------------------
// Sensors
// ---------------------------------------------------------------------------
#ifndef HX711_DEFAULT_CAL_FACTOR
#define HX711_DEFAULT_CAL_FACTOR 21500.0f // ADC counts per kg; refine via `cal`
#endif
#define AUDIO_SAMPLE_RATE 16000
#define AUDIO_SAMPLE_COUNT 16000 // 1 s capture
#define AUDIO_WARMUP_SAMPLES 3200 // ~200 ms discarded after mic power-up

// ---------------------------------------------------------------------------
// Offline buffering (LittleFS append/replay queue)
// ---------------------------------------------------------------------------
#define QUEUE_FILE "/telemetry_queue.jsonl"
#define QUEUE_TMP_FILE "/telemetry_queue.tmp"
#ifndef QUEUE_MAX_BYTES
#define QUEUE_MAX_BYTES (64 * 1024) // ≈150 readings; oldest dropped first
#endif

#define TELEMETRY_JSON_CAPACITY 768

// ---------------------------------------------------------------------------
// Logging. LOGD only compiles in with -DDEBUG; LOGE always prints so field
// units keep basic diagnosability over serial.
// ---------------------------------------------------------------------------
#ifdef DEBUG
#define LOGD(fmt, ...) Serial.printf("[%8lu][D] " fmt "\r\n", (unsigned long)millis(), ##__VA_ARGS__)
#else
#define LOGD(fmt, ...) do {} while (0)
#endif
#define LOGE(fmt, ...) Serial.printf("[%8lu][E] " fmt "\r\n", (unsigned long)millis(), ##__VA_ARGS__)
