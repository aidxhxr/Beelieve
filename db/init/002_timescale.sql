-- Hypertables, continuous aggregates, compression and retention policies.
SELECT create_hypertable('sensor_readings', 'time', chunk_time_interval => INTERVAL '1 day');
SELECT create_hypertable('predictions',     'time', chunk_time_interval => INTERVAL '1 day');
SELECT create_hypertable('alerts',          'time', chunk_time_interval => INTERVAL '7 days');

CREATE INDEX sensor_readings_hive_time_idx ON sensor_readings (hive_id, time DESC);
CREATE INDEX predictions_hive_time_idx     ON predictions (hive_id, time DESC);
CREATE INDEX alerts_hive_time_idx          ON alerts (hive_id, time DESC);

-- Hourly rollup used by dashboard charts.
CREATE MATERIALIZED VIEW readings_hourly
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 hour', time) AS bucket,
    hive_id,
    avg(temp_brood_c)   AS temp_brood_c,
    avg(temp_ambient_c) AS temp_ambient_c,
    avg(humidity_pct)   AS humidity_pct,
    avg(weight_kg)      AS weight_kg,
    max(weight_kg) - min(weight_kg) AS weight_range_kg,
    avg(audio_db)       AS audio_db,
    avg(co2_ppm)        AS co2_ppm,
    min(battery_v)      AS battery_v,
    count(*)            AS n_readings
FROM sensor_readings
GROUP BY bucket, hive_id
WITH NO DATA;

CREATE MATERIALIZED VIEW readings_daily
WITH (timescaledb.continuous) AS
SELECT
    time_bucket('1 day', time) AS bucket,
    hive_id,
    avg(temp_brood_c) AS temp_brood_c,
    avg(humidity_pct) AS humidity_pct,
    avg(weight_kg)    AS weight_kg,
    last(weight_kg, time) - first(weight_kg, time) AS weight_delta_kg,
    avg(audio_db)     AS audio_db,
    count(*)          AS n_readings
FROM sensor_readings
GROUP BY bucket, hive_id
WITH NO DATA;

SELECT add_continuous_aggregate_policy('readings_hourly',
    start_offset => INTERVAL '3 hours',
    end_offset   => INTERVAL '10 minutes',
    schedule_interval => INTERVAL '10 minutes');

SELECT add_continuous_aggregate_policy('readings_daily',
    start_offset => INTERVAL '3 days',
    end_offset   => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour');

ALTER MATERIALIZED VIEW readings_hourly SET (timescaledb.materialized_only = false);
ALTER MATERIALIZED VIEW readings_daily  SET (timescaledb.materialized_only = false);

ALTER TABLE sensor_readings SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'hive_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('sensor_readings', INTERVAL '7 days');

ALTER TABLE predictions SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'hive_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('predictions', INTERVAL '7 days');

SELECT add_retention_policy('sensor_readings', INTERVAL '12 months');
SELECT add_retention_policy('predictions',     INTERVAL '12 months');
