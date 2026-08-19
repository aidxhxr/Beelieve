#include "net.h"

#include <PubSubClient.h>
#include <WiFi.h>
#include <esp_task_wdt.h>
#include <time.h>

#include "config.h"

namespace net {

static WiFiClient s_tcp;
static PubSubClient s_mqtt(s_tcp);

static char s_topicTelemetry[160];
static char s_topicStatus[160];
static char s_clientId[64];

// Epoch of the last successful SNTP sync; survives deep sleep. The ESP32 RTC
// keeps system time running through deep sleep once it has been set, so we
// only re-sync when this is stale.
static RTC_DATA_ATTR time_t s_lastNtpSync = 0;

// Any time before 2025-01-01 means "never synced".
static const time_t kMinValidEpoch = 1735689600;

static void buildTopics() {
  if (s_topicTelemetry[0] != '\0') return;
  snprintf(s_topicTelemetry, sizeof(s_topicTelemetry), "beelieve/%s/%s/telemetry", APIARY_ID,
           HIVE_ID);
  snprintf(s_topicStatus, sizeof(s_topicStatus), "beelieve/%s/%s/status", APIARY_ID, HIVE_ID);
  snprintf(s_clientId, sizeof(s_clientId), "beelieve-%s-%04X", HIVE_ID,
           (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
}

// ---------------------------------------------------------------------------
// WiFi
// ---------------------------------------------------------------------------
bool connectWiFi() {
  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);

  uint32_t backoff = WIFI_BACKOFF_BASE_MS;
  for (int attempt = 1; attempt <= WIFI_MAX_ATTEMPTS; ++attempt) {
    LOGD("WiFi attempt %d/%d -> %s", attempt, WIFI_MAX_ATTEMPTS, WIFI_SSID);
    WiFi.disconnect(true);
    delay(50);
    WiFi.begin(WIFI_SSID, WIFI_PASS);

    const uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_ATTEMPT_TIMEOUT_MS) {
      delay(100);
      esp_task_wdt_reset();
    }
    if (WiFi.status() == WL_CONNECTED) {
      LOGD("WiFi up, ip=%s rssi=%d dBm", WiFi.localIP().toString().c_str(), WiFi.RSSI());
      return true;
    }

    if (attempt < WIFI_MAX_ATTEMPTS) {
      LOGD("WiFi failed (status=%d), backoff %lu ms", WiFi.status(), (unsigned long)backoff);
      const uint32_t waitStart = millis();
      while (millis() - waitStart < backoff) {
        delay(100);
        esp_task_wdt_reset();
      }
      backoff = min<uint32_t>(backoff * 2, 8000);
    }
  }
  LOGE("WiFi connect failed after %d attempts", WIFI_MAX_ATTEMPTS);
  return false;
}

// ---------------------------------------------------------------------------
// SNTP
// ---------------------------------------------------------------------------
bool timeValid() { return time(nullptr) >= kMinValidEpoch; }

bool syncTimeIfNeeded() {
  const time_t now = time(nullptr);
  if (timeValid() && s_lastNtpSync != 0 && (now - s_lastNtpSync) < NTP_RESYNC_INTERVAL_S) {
    return true; // RTC-kept time is fresh enough
  }
  if (WiFi.status() != WL_CONNECTED) return timeValid();

  LOGD("SNTP sync via %s / %s", NTP_SERVER_1, NTP_SERVER_2);
  configTime(0, 0, NTP_SERVER_1, NTP_SERVER_2); // UTC, no DST
  const uint32_t start = millis();
  while (!timeValid() && millis() - start < 15000) {
    delay(200);
    esp_task_wdt_reset();
  }
  if (timeValid()) {
    s_lastNtpSync = time(nullptr);
    LOGD("SNTP synced, epoch=%ld", (long)s_lastNtpSync);
    return true;
  }
  LOGE("SNTP sync timed out");
  return timeValid();
}

bool isoTimestamp(char* buf, size_t len) {
  if (!timeValid()) return false;
  const time_t now = time(nullptr);
  struct tm utc;
  gmtime_r(&now, &utc);
  return strftime(buf, len, "%Y-%m-%dT%H:%M:%SZ", &utc) > 0;
}

// ---------------------------------------------------------------------------
// MQTT
// ---------------------------------------------------------------------------
bool mqttConnected() { return s_mqtt.connected(); }

bool connectMqtt() {
  if (WiFi.status() != WL_CONNECTED) return false;
  buildTopics();

  s_mqtt.setServer(MQTT_HOST, MQTT_PORT);
  s_mqtt.setBufferSize(MQTT_BUFFER_SIZE);
  s_mqtt.setKeepAlive(MQTT_KEEPALIVE_S);
  s_mqtt.setSocketTimeout(10);

  const char* user = (MQTT_USER[0] != '\0') ? MQTT_USER : nullptr;
  const char* pass = (MQTT_USER[0] != '\0') ? MQTT_PASS : nullptr;

  for (int attempt = 1; attempt <= MQTT_MAX_ATTEMPTS; ++attempt) {
    LOGD("MQTT attempt %d/%d -> %s:%d as %s", attempt, MQTT_MAX_ATTEMPTS, MQTT_HOST, MQTT_PORT,
         s_clientId);
    // LWT: retained `offline` at QoS 1 on the status topic.
    if (s_mqtt.connect(s_clientId, user, pass, s_topicStatus, 1, true, "offline", true)) {
      s_mqtt.publish(s_topicStatus, "online", true);
      s_mqtt.loop();
      LOGD("MQTT connected, status=online (retained)");
      return true;
    }
    LOGD("MQTT connect failed, rc=%d", s_mqtt.state());
    const uint32_t waitStart = millis();
    while (millis() - waitStart < (uint32_t)(1000 * attempt)) {
      delay(100);
      esp_task_wdt_reset();
    }
  }
  LOGE("MQTT connect failed after %d attempts (rc=%d)", MQTT_MAX_ATTEMPTS, s_mqtt.state());
  return false;
}

bool publishTelemetry(const char* json) {
  if (!s_mqtt.connected()) return false;
  buildTopics();
  const bool ok = s_mqtt.publish(s_topicTelemetry, json);
  s_mqtt.loop();
  if (!ok) LOGE("MQTT publish failed (%u bytes)", (unsigned)strlen(json));
  return ok;
}

// ---------------------------------------------------------------------------
// shutdown
// ---------------------------------------------------------------------------
void shutdown() {
  if (s_mqtt.connected()) {
    // The node is entering deep sleep on purpose: report `offline` explicitly,
    // since a clean DISCONNECT suppresses the LWT.
    s_mqtt.publish(s_topicStatus, "offline", true);
    // Give the TCP stack a moment to flush outgoing packets.
    for (int i = 0; i < 5; ++i) {
      s_mqtt.loop();
      delay(50);
    }
    s_mqtt.disconnect();
  }
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  LOGD("network shut down");
}

} // namespace net
