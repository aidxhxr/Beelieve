#include "buffer.h"

#include <LittleFS.h>
#include <esp_task_wdt.h>

#include "config.h"

namespace tqueue {

static bool s_mounted = false;

bool begin() {
  s_mounted = LittleFS.begin(true /* format on first mount */);
  if (!s_mounted) {
    LOGE("LittleFS mount failed; offline buffering disabled");
  } else {
    LOGD("LittleFS mounted, queued bytes=%u", (unsigned)pendingBytes());
  }
  return s_mounted;
}

size_t pendingBytes() {
  if (!s_mounted || !LittleFS.exists(QUEUE_FILE)) return 0;
  File f = LittleFS.open(QUEUE_FILE, "r");
  if (!f) return 0;
  const size_t size = f.size();
  f.close();
  return size;
}

// Copy `in` (from its current position) to `out` in chunks.
static void copyRemainder(File& in, File& out) {
  uint8_t buf[256];
  while (in.available()) {
    const size_t n = in.read(buf, sizeof(buf));
    if (n == 0) break;
    out.write(buf, n);
  }
}

// Ring behaviour: rewrite the queue keeping only the newest ~targetBytes,
// dropping whole lines from the front.
static void dropOldest(size_t targetBytes) {
  File in = LittleFS.open(QUEUE_FILE, "r");
  if (!in) return;
  const size_t size = in.size();
  if (size <= targetBytes) {
    in.close();
    return;
  }
  in.seek(size - targetBytes);
  in.readStringUntil('\n'); // discard the partial line at the cut point

  File out = LittleFS.open(QUEUE_TMP_FILE, "w");
  if (!out) {
    in.close();
    return;
  }
  copyRemainder(in, out);
  in.close();
  out.close();
  LittleFS.remove(QUEUE_FILE);
  LittleFS.rename(QUEUE_TMP_FILE, QUEUE_FILE);
  LOGD("queue compacted: %u -> %u bytes", (unsigned)size, (unsigned)pendingBytes());
}

bool enqueue(const char* json) {
  if (!s_mounted || json == nullptr || json[0] == '\0') return false;
  const size_t lineLen = strlen(json) + 1; // + '\n'

  if (pendingBytes() + lineLen > QUEUE_MAX_BYTES) {
    dropOldest((QUEUE_MAX_BYTES * 3) / 4);
  }

  File f = LittleFS.open(QUEUE_FILE, "a");
  if (!f) {
    LOGE("queue append open failed");
    return false;
  }
  const size_t written = f.print(json) + f.print('\n');
  f.close();
  if (written != lineLen) {
    LOGE("queue short write (%u/%u)", (unsigned)written, (unsigned)lineLen);
    return false;
  }
  LOGD("queued reading (%u bytes pending)", (unsigned)pendingBytes());
  return true;
}

size_t flush(PublishFn publishFn) {
  if (!s_mounted || publishFn == nullptr || !LittleFS.exists(QUEUE_FILE)) return 0;

  File f = LittleFS.open(QUEUE_FILE, "r");
  if (!f) return 0;

  size_t published = 0;
  String failedLine;
  bool failed = false;

  while (f.available()) {
    String line = f.readStringUntil('\n');
    line.trim();
    if (line.isEmpty()) continue;
    esp_task_wdt_reset();
    if (!publishFn(line.c_str())) {
      failedLine = line;
      failed = true;
      break;
    }
    ++published;
  }

  if (!failed) {
    f.close();
    LittleFS.remove(QUEUE_FILE);
    if (published > 0) LOGD("queue flushed: %u readings", (unsigned)published);
    return published;
  }

  // Keep the failed line plus everything after it.
  File out = LittleFS.open(QUEUE_TMP_FILE, "w");
  if (out) {
    out.print(failedLine);
    out.print('\n');
    copyRemainder(f, out);
    out.close();
    f.close();
    LittleFS.remove(QUEUE_FILE);
    LittleFS.rename(QUEUE_TMP_FILE, QUEUE_FILE);
  } else {
    f.close(); // cannot rewrite; leave the original (some duplicates possible)
  }
  LOGE("queue flush interrupted after %u readings; %u bytes remain", (unsigned)published,
       (unsigned)pendingBytes());
  return published;
}

} // namespace tqueue
