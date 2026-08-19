// Beelieve hive sensor node — main cycle.
//
// Wake -> (optional calibration console) -> read sensors -> connect WiFi/MQTT
// -> flush any offline-buffered readings -> publish current reading -> report
// status offline -> deep sleep for TELEMETRY_INTERVAL_S.
//
// Telemetry contract (docs/ARCHITECTURE.md):
//   topic  beelieve/{apiary_id}/{hive_id}/telemetry
//   status beelieve/{apiary_id}/{hive_id}/status  (LWT, retained online/offline)
// All fields other than hive_id / apiary_id / ts are optional and omitted on
// sensor fault.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <esp_task_wdt.h>

#include "buffer.h"
#include "config.h"
#include "net.h"
#include "power.h"
#include "sensors.h"

// Round to `decimals` places so serialized floats stay compact and stable.
static double roundTo(float v, int decimals) {
  double scale = 1.0;
  for (int i = 0; i < decimals; ++i) scale *= 10.0;
  return round((double)v * scale) / scale;
}

// Serialize one contract-conformant telemetry document. Returns false when no
// valid wall-clock time is available (ts is mandatory).
static bool buildTelemetryJson(const SensorReadings& r, char* out, size_t outLen) {
  char ts[32];
  if (!net::isoTimestamp(ts, sizeof(ts))) return false;

  JsonDocument doc;
  doc["hive_id"] = HIVE_ID;
  doc["apiary_id"] = APIARY_ID;
  doc["ts"] = ts;

  if (r.has_temp_brood) doc["temp_brood_c"] = roundTo(r.temp_brood_c, 1);
  if (r.has_temp_ambient) doc["temp_ambient_c"] = roundTo(r.temp_ambient_c, 1);
  if (r.has_humidity) doc["humidity_pct"] = roundTo(r.humidity_pct, 1);
  if (r.has_weight) doc["weight_kg"] = roundTo(r.weight_kg, 2);
  if (r.has_audio) {
    doc["audio_db"] = roundTo(r.audio_db, 1);
    JsonObject bands = doc["audio_bands"].to<JsonObject>();
    for (int b = 0; b < 5; ++b) bands[kAudioBandKeys[b]] = roundTo(r.audio_bands[b], 4);
  }
  if (r.has_co2) doc["co2_ppm"] = r.co2_ppm;
  if (r.has_battery) doc["battery_v"] = roundTo(r.battery_v, 2);
  doc["fw"] = FW_VERSION;

  return serializeJson(doc, out, outLen) > 0;
}

void setup() {
  Serial.begin(115200);
  delay(50);

  // Watchdog over the whole wake cycle: a hang anywhere reboots into the next
  // cycle instead of draining the battery.
  esp_task_wdt_init(WDT_TIMEOUT_S, true);
  esp_task_wdt_add(nullptr);

  power::onBoot();
  LOGD("Beelieve node %s/%s fw %s", APIARY_ID, HIVE_ID, FW_VERSION);

  tqueue::begin();
  sensors::begin();
  sensors::handleSerialCalibration(CALIBRATION_WINDOW_MS);
  esp_task_wdt_reset();

  SensorReadings readings;
  sensors::readAll(readings);
  esp_task_wdt_reset();

  const bool wifiUp = net::connectWiFi();
  bool mqttUp = false;
  if (wifiUp) {
    net::syncTimeIfNeeded();
    mqttUp = net::connectMqtt();
  }
  esp_task_wdt_reset();

  static char payload[TELEMETRY_JSON_CAPACITY];
  const bool havePayload = buildTelemetryJson(readings, payload, sizeof(payload));
  if (!havePayload) {
    // Never-synced clock (first boots without network): a reading without a
    // valid `ts` violates the contract, so it is dropped.
    LOGE("no valid wall-clock time yet; dropping this reading");
  } else {
    LOGD("payload: %s", payload);
  }

  if (mqttUp) {
    // Oldest-first: replay buffered readings before the fresh one.
    tqueue::flush(net::publishTelemetry);
    if (havePayload && !net::publishTelemetry(payload)) {
      tqueue::enqueue(payload);
    }
  } else if (havePayload) {
    tqueue::enqueue(payload);
    LOGD("offline: reading buffered (%u bytes pending)", (unsigned)tqueue::pendingBytes());
  }
  esp_task_wdt_reset();

  net::shutdown();
  power::deepSleep(TELEMETRY_INTERVAL_S);
}

void loop() {
  // Never reached: setup() ends in deep sleep and each wake restarts setup().
}
