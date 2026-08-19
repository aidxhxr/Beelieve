// Beelieve hive sensor node — WiFi, SNTP and MQTT.
#pragma once

#include <Arduino.h>

namespace net {

// WiFi STA connect with exponential backoff. Returns true when associated.
bool connectWiFi();

// MQTT connect with LWT `offline` (retained) on the status topic; on success
// publishes retained `online` there. Requires WiFi.
bool connectMqtt();
bool mqttConnected();

// Publish one telemetry JSON payload to beelieve/{apiary}/{hive}/telemetry.
// NOTE: PubSubClient cannot emit MQTT QoS 1 PUBLISH packets; at-least-once
// delivery is achieved at the application layer instead — a reading is only
// removed from the LittleFS queue after publish() confirms the packet was
// written to the socket (see main.cpp / buffer.cpp).
bool publishTelemetry(const char* json);

// SNTP sync, rate-limited via an RTC-memory timestamp (system time persists
// across deep sleep once set). Returns true when system time is valid.
bool syncTimeIfNeeded();
bool timeValid();

// Format current UTC time as ISO-8601 ("2026-08-18T12:00:00Z").
// Returns false when system time has never been synced.
bool isoTimestamp(char* buf, size_t len);

// Publish retained `offline` status, disconnect cleanly, power the radio down.
void shutdown();

} // namespace net
