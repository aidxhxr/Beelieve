# Beelieve API

FastAPI backend for the Beelieve dashboard. Async psycopg3 against TimescaleDB,
JWT (HS256) auth, and a WebSocket that fans out live Kafka events
(`hive.telemetry.raw`, `hive.predictions`, `hive.alerts`) to each user's hives.

Configuration is env-only — see the repo root `.env.example`
(`DATABASE_URL`, `JWT_SECRET`, `JWT_EXPIRES_MIN`, `CORS_ORIGINS`,
`KAFKA_BOOTSTRAP_SERVERS`, optional `RECOMMENDER_URL`).

## Endpoints

| Method | Path | Description |
|---|---|---|
| POST | `/auth/register` | Create account, returns JWT + user |
| POST | `/auth/login` | Email + password → JWT |
| GET | `/auth/me` | Current user profile |
| GET | `/apiaries` | User's apiaries with hive counts |
| GET | `/apiaries/{id}/hives` | Hives in one apiary |
| GET | `/hives` | All hives + latest reading, latest prediction, open alert count |
| GET | `/hives/{id}` | Hive detail incl. metadata |
| GET | `/hives/{id}/readings?hours=24&resolution=raw\|hourly\|daily` | Sensor data (raw table or continuous aggregates) |
| GET | `/hives/{id}/predictions?hours=72` | Model scores |
| GET | `/hives/{id}/alerts?limit=50` | Recent alerts |
| POST | `/alerts/ack` | Acknowledge alert `{hive_id, time}` |
| GET | `/hives/{id}/recommendations?limit=10` | Stored recommendations |
| POST | `/hives/{id}/recommendations/refresh` | Proxy to Mistral recommender (502 if down) |
| GET | `/overview` | Fleet stats: counts, alerts by severity, avg health, 7d weight trend |
| GET | `/healthz` | DB health check |
| WS | `/ws?token=JWT` | Live events `{"type": "telemetry\|prediction\|alert", "data": {...}}` |

All routes except `/auth/login`, `/auth/register` and `/healthz` require
`Authorization: Bearer <JWT>`. Users see only apiaries/hives they own; admins
see all.

## Run

```sh
uvicorn app.main:app --host 0.0.0.0 --port 8000   # or docker build .
pytest tests/                                      # pure-logic tests, no DB
```
