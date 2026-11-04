-- Demo data of C09 (9xx - test data only). Values are referenced by the examples of web/openapi.yaml
-- and by checks.yaml: 2 users + 1 staff; level mall-one has 1 spot for the race (example `scarce`).

INSERT INTO garage (id, name, tz) VALUES
    ('mall',   'ТЦ Південний', 'Europe/Kyiv'),
    ('center', 'Центр',        'Europe/Kyiv');

INSERT INTO level (id, garage_id, name, spots) VALUES
    ('mall-1',   'mall',   'Рівень 1',             40),
    ('mall-2',   'mall',   'Рівень 2',             40),
    ('mall-one', 'mall',   'Рівень VIP (1 місце)',  1),
    ('center-1', 'center', 'Рівень 1',             20);

-- kopecks per hour
INSERT INTO tariff (garage_id, minor_per_hour, currency) VALUES
    ('mall',   3000, 'UAH'),
    ('center', 4000, 'UAH');

INSERT INTO app_user (id, login, role) VALUES
    ('u-1', 'user1',  'user'),
    ('u-2', 'user2',  'user'),
    ('s-1', 'staff1', 'staff');
