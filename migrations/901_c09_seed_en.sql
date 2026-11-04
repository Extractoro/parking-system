-- Demo data of C09 (9xx - test data only): the client is in English, so the names it shows are too.
-- 900 is applied and is never edited; this file renames the same rows. Ids stay the same.

UPDATE garage SET name = 'South Mall'   WHERE id = 'mall';
UPDATE garage SET name = 'City Centre'  WHERE id = 'center';

UPDATE level SET name = 'Level 1'            WHERE id = 'mall-1';
UPDATE level SET name = 'Level 2'            WHERE id = 'mall-2';
UPDATE level SET name = 'VIP level (1 spot)' WHERE id = 'mall-one';
UPDATE level SET name = 'Level 1'            WHERE id = 'center-1';
