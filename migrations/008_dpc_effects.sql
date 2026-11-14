-- C09 «Паркінги»: decisions of the effect receiver (DPC lab 3). Owner: DPC.
--
-- A Ray task delivers the result of one entry request to POST /internal/effects. The receiver inserts
-- the key first (ON CONFLICT DO NOTHING) and in the same transaction locks garage_state, checks the rule,
-- writes `entry` and `occupied` and stores the decision. A repeated delivery of the same key (a retry of
-- the task after a failure) finds the row and gets the stored decision without changing the garage.
-- Rejections are stored too, so a repeat gets the same `rejected` and the same reason.

CREATE TABLE effect (
    effect_key text PRIMARY KEY,  -- = entry_request.event_id, the same in every attempt of the task
    request_id text NOT NULL REFERENCES entry_request (id),
    decision   text NOT NULL CHECK (decision IN ('accepted', 'rejected')),
    reason     text NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((decision = 'rejected') = (reason IS NOT NULL))
);
CREATE INDEX effect_request_idx ON effect (request_id);
