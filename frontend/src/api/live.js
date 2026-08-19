import { getToken } from './client';

const WS_URL = import.meta.env.VITE_WS_URL ?? 'ws://localhost:8000/ws';

/**
 * Subscribe to the live event stream (telemetry / prediction / alert).
 * Reconnects with exponential backoff. Returns an unsubscribe function.
 */
export function subscribeLive(onEvent) {
  let ws;
  let closed = false;
  let attempt = 0;

  const connect = () => {
    if (closed) return;
    ws = new WebSocket(`${WS_URL}?token=${getToken() ?? ''}`);
    ws.onopen = () => {
      attempt = 0;
    };
    ws.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data));
      } catch {
        /* ignore malformed frames */
      }
    };
    ws.onclose = () => {
      if (closed) return;
      attempt += 1;
      const delay = Math.min(30000, 1000 * 2 ** attempt);
      setTimeout(connect, delay);
    };
    ws.onerror = () => ws.close();
  };

  connect();

  return () => {
    closed = true;
    ws?.close();
  };
}
