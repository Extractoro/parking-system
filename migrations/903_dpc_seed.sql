-- Demo data of DPC lab 1 (9xx - test data only).
-- spots of a garage = sum of its levels WITHOUT the test level race-test of Web lab 2 (mall = 81,
-- center = 20, decision P1). Garage dpc-race (5 spots, no levels, no tariff, so the Web catalog does
-- not list it) exists only for the experiment without synchronization (RESOURCE_MODE=unsafe).

INSERT INTO garage (id, name, tz) VALUES
    ('dpc-race', 'DPC race test (5 spots)', 'Europe/Kyiv');

INSERT INTO garage_state (garage_id, spots, occupied)
SELECT l.garage_id, sum(l.spots), 0
FROM level l
WHERE l.garage_id IN ('mall', 'center') AND l.id <> 'race-test'
GROUP BY l.garage_id;

INSERT INTO garage_state (garage_id, spots, occupied) VALUES ('dpc-race', 5, 0);
