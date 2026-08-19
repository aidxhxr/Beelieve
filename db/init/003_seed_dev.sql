-- Dev seed: demo account, one apiary, hives matching the simulator's defaults.
-- Password for demo@beelieve.kz is "demo1234" (bcrypt via API on first login is
-- not used here; hash generated with crypt for dev only).
INSERT INTO users (id, email, password_hash, full_name, locale)
VALUES ('00000000-0000-0000-0000-000000000001',
        'demo@beelieve.kz',
        crypt('demo1234', gen_salt('bf', 10)),
        'Demo Beekeeper', 'en');

INSERT INTO apiaries (id, owner_id, name, latitude, longitude, region) VALUES
('apiary-almaty-01', '00000000-0000-0000-0000-000000000001',
 'Almaty Foothills', 43.1056, 76.9927, 'Almaty Region');

INSERT INTO hives (id, apiary_id, name, hive_type, queen_year, frames, installed_at)
SELECT
    format('KZ-ALA-%s', lpad(i::text, 4, '0')),
    'apiary-almaty-01',
    format('Hive %s', i),
    CASE WHEN i % 3 = 0 THEN 'langstroth' ELSE 'dadant' END,
    2024 + (i % 3),
    10 + (i % 3) * 2,
    DATE '2025-04-15' + (i || ' days')::interval
FROM generate_series(1, 12) AS i;
