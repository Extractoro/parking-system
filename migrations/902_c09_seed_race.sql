-- Demo data of C09 (9xx - test data only): level race-test with one spot for the concurrency
-- experiment of Web lab 2. The mode without protection (INVARIANT_MODE=unprotected) changes
-- ONLY this level, so the experiment cannot break the hours of the demo levels.

INSERT INTO level (id, garage_id, name, spots) VALUES
    ('race-test', 'mall', 'Race test (1 spot)', 1);
