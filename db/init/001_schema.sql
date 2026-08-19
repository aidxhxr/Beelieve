-- Beelieve core schema. Runs once on first container start (docker-entrypoint-initdb.d).
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL DEFAULT '',
    locale        TEXT NOT NULL DEFAULT 'en',   -- en | ru | kk
    role          TEXT NOT NULL DEFAULT 'beekeeper' CHECK (role IN ('beekeeper', 'admin')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE apiaries (
    id         TEXT PRIMARY KEY,               -- e.g. apiary-almaty-01
    owner_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    latitude   DOUBLE PRECISION,
    longitude  DOUBLE PRECISION,
    region     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE hives (
    id            TEXT PRIMARY KEY,            -- e.g. KZ-ALA-0042
    apiary_id     TEXT NOT NULL REFERENCES apiaries(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    hive_type     TEXT NOT NULL DEFAULT 'dadant',
    queen_year    SMALLINT,
    frames        SMALLINT,
    installed_at  DATE,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    last_seen_at  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX hives_apiary_idx ON hives (apiary_id);

CREATE TABLE sensor_readings (
    time            TIMESTAMPTZ NOT NULL,
    hive_id         TEXT NOT NULL,
    temp_brood_c    REAL,
    temp_ambient_c  REAL,
    humidity_pct    REAL,
    weight_kg       REAL,
    audio_db        REAL,
    audio_b100_200  REAL,
    audio_b200_300  REAL,
    audio_b300_400  REAL,
    audio_b400_500  REAL,
    audio_b500_600  REAL,
    co2_ppm         REAL,
    battery_v       REAL,
    fw              TEXT
);

CREATE TABLE predictions (
    time          TIMESTAMPTZ NOT NULL,
    hive_id       TEXT NOT NULL,
    model_version TEXT NOT NULL,
    swarm_risk    REAL NOT NULL,
    health_score  REAL NOT NULL,
    is_anomaly    BOOLEAN NOT NULL DEFAULT FALSE,
    anomaly_kind  TEXT NOT NULL DEFAULT 'none',
    anomaly_score REAL NOT NULL DEFAULT 0
);

CREATE TABLE alerts (
    time     TIMESTAMPTZ NOT NULL,
    hive_id  TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('critical', 'warning', 'info')),
    kind     TEXT NOT NULL,
    message  TEXT NOT NULL,
    source   TEXT NOT NULL CHECK (source IN ('ml', 'rule')),
    acked    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE recommendations (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hive_id    TEXT NOT NULL REFERENCES hives(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locale     TEXT NOT NULL DEFAULT 'en',
    model_id   TEXT NOT NULL,
    priority   SMALLINT NOT NULL DEFAULT 3,    -- 1 = act now … 5 = FYI
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    context    JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX recommendations_hive_idx ON recommendations (hive_id, created_at DESC);
