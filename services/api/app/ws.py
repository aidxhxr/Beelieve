"""WebSocket live event stream.

A confluent-kafka consumer (group "api-ws", latest offset) runs in a dedicated
thread, subscribed to hive.telemetry.raw / hive.predictions / hive.alerts.
Events are handed to the asyncio loop via loop.call_soon_threadsafe into an
asyncio.Queue; a dispatcher task fans them out to connected sockets, filtered
by hive ownership loaded at connect time.

Envelope sent to clients: {"type": "telemetry|prediction|alert", "data": {...}}.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.db import get_pool
from app.security import TokenError, decode_access_token

logger = logging.getLogger("beelieve.api.ws")

router = APIRouter()

PING_INTERVAL_S = 30.0
EVENT_QUEUE_MAXSIZE = 1000

TOPIC_EVENT_TYPES: dict[str, str] = {
    "hive.telemetry.raw": "telemetry",
    "hive.predictions": "prediction",
    "hive.alerts": "alert",
}


class ConnectionManager:
    """Tracks live sockets and the hive ids each one is allowed to see."""

    def __init__(self) -> None:
        # allowed hive ids per socket; None means "all hives" (admin).
        self._clients: dict[WebSocket, set[str] | None] = {}

    def register(self, websocket: WebSocket, hive_ids: set[str] | None) -> None:
        self._clients[websocket] = hive_ids

    def unregister(self, websocket: WebSocket) -> None:
        self._clients.pop(websocket, None)

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, event_type: str, hive_id: str | None, data: dict[str, Any]) -> None:
        """Send one event to every socket allowed to see this hive."""
        if not self._clients:
            return
        message = {"type": event_type, "data": data}
        stale: list[WebSocket] = []
        for websocket, allowed in list(self._clients.items()):
            if hive_id is not None and allowed is not None and hive_id not in allowed:
                continue
            try:
                await websocket.send_json(message)
            except Exception:
                stale.append(websocket)
        for websocket in stale:
            self.unregister(websocket)


class KafkaEventBridge:
    """Runs a confluent-kafka Consumer in a thread and forwards decoded events
    to the asyncio loop via call_soon_threadsafe."""

    def __init__(
        self,
        bootstrap_servers: str,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[tuple[str, str | None, dict[str, Any]]],
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._loop = loop
        self._queue = queue
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kafka-ws-consumer", daemon=True)
        self._thread.start()
        logger.info("kafka event bridge started", extra={"topics": list(TOPIC_EVENT_TYPES)})

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None
        logger.info("kafka event bridge stopped")

    def _run(self) -> None:
        from confluent_kafka import Consumer, KafkaError

        consumer = Consumer(
            {
                "bootstrap.servers": self._bootstrap_servers,
                "group.id": "api-ws",
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
            }
        )
        consumer.subscribe(list(TOPIC_EVENT_TYPES))
        try:
            while not self._stop.is_set():
                message = consumer.poll(1.0)
                if message is None:
                    continue
                error = message.error()
                if error is not None:
                    if error.code() != KafkaError._PARTITION_EOF:
                        logger.warning("kafka consumer error", extra={"error": str(error)})
                    continue
                event_type = TOPIC_EVENT_TYPES.get(message.topic() or "")
                if event_type is None:
                    continue
                raw_value = message.value()
                if raw_value is None:
                    continue
                try:
                    payload = json.loads(raw_value)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    logger.warning(
                        "dropping undecodable kafka message",
                        extra={"topic": message.topic()},
                    )
                    continue
                if not isinstance(payload, dict):
                    continue
                key = message.key()
                hive_id = payload.get("hive_id") or (key.decode("utf-8") if key else None)
                self._loop.call_soon_threadsafe(self._enqueue, event_type, hive_id, payload)
        except Exception:
            logger.exception("kafka event bridge crashed")
        finally:
            consumer.close()

    def _enqueue(self, event_type: str, hive_id: str | None, payload: dict[str, Any]) -> None:
        """Runs on the event loop. Drops the oldest event under backpressure."""
        try:
            self._queue.put_nowait((event_type, hive_id, payload))
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                self._queue.put_nowait((event_type, hive_id, payload))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.warning("event queue full; dropping event", extra={"type": event_type})


async def dispatch_events(
    queue: asyncio.Queue[tuple[str, str | None, dict[str, Any]]],
    manager: ConnectionManager,
) -> None:
    """Lifespan task: drain the event queue and fan out to sockets."""
    while True:
        event_type, hive_id, payload = await queue.get()
        try:
            await manager.broadcast(event_type, hive_id, payload)
        except Exception:
            logger.exception("broadcast failed", extra={"type": event_type})
        finally:
            queue.task_done()


async def _load_allowed_hive_ids(user_id: UUID) -> set[str]:
    pool = get_pool()
    async with pool.connection() as conn:
        cur = await conn.execute(
            """
            SELECT h.id
            FROM hives h
            JOIN apiaries a ON a.id = h.apiary_id
            WHERE a.owner_id = %(user_id)s
            """,
            {"user_id": user_id},
        )
        rows = await cur.fetchall()
    return {row["id"] for row in rows}


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)) -> None:
    """Authenticated live event stream for the user's hives."""
    try:
        claims = decode_access_token(token)
        user_id = UUID(str(claims["sub"]))
    except (TokenError, KeyError, ValueError):
        # Accept then close with a policy-violation code so the client sees
        # a clean close frame rather than a failed handshake.
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="invalid token")
        return

    role = str(claims.get("role", "beekeeper"))
    allowed: set[str] | None
    if role == "admin":
        allowed = None
    else:
        try:
            allowed = await _load_allowed_hive_ids(user_id)
        except Exception:
            logger.exception("failed to load hive ownership for ws connect")
            await websocket.accept()
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="server error")
            return

    manager: ConnectionManager = websocket.app.state.ws_manager
    await websocket.accept()
    manager.register(websocket, allowed)
    logger.info(
        "ws connected",
        extra={"user_id": str(user_id), "clients": manager.client_count},
    )
    try:
        while True:
            try:
                text = await asyncio.wait_for(websocket.receive_text(), timeout=PING_INTERVAL_S)
            except TimeoutError:
                # Keepalive: application-level ping; any client message counts
                # as liveness, and a dead peer will fail the send.
                await websocket.send_json({"type": "ping", "data": {}})
                continue
            stripped = text.strip()
            if stripped == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
                continue
            try:
                incoming = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(incoming, dict) and incoming.get("type") == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
            # Any other client message (e.g. {"type": "pong"}) is a liveness
            # signal and needs no reply.
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        manager.unregister(websocket)
        logger.info(
            "ws disconnected",
            extra={"user_id": str(user_id), "clients": manager.client_count},
        )
