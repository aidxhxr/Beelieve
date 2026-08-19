// Beelieve hive sensor node — offline telemetry buffer.
//
// A simple append/replay queue: one JSON payload per line in a LittleFS file.
// When the file would exceed QUEUE_MAX_BYTES the oldest lines are dropped
// (ring behaviour). On reconnect the queue is replayed oldest-first and only
// successfully published lines are removed.
#pragma once

#include <Arduino.h>

namespace tqueue {

typedef bool (*PublishFn)(const char* json);

// Mount LittleFS (formatting on first use). Returns false when FS unusable.
bool begin();

// Append one payload line; drops oldest entries first when full.
bool enqueue(const char* json);

// Replay queued payloads oldest-first through publishFn; stops at the first
// failure and keeps the unsent remainder. Returns number published.
size_t flush(PublishFn publishFn);

// Number of buffered payload bytes (0 when queue empty).
size_t pendingBytes();

} // namespace tqueue
