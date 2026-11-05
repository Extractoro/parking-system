-- C09 «Паркінги»: occupancy of garages, entry requests and entries (DPC lab 1). Owner: DPC.
--
-- Invariant: occupied = cars inside (entries 'in' - entries 'out' of the garage) and occupied <= spots.
-- garage_state.spots = sum of level.spots of the garage without the test level race-test (decision P1).
-- Web reads `entry` to show a booking as 'entered' (decision B3); DPC only reads `booking`.

CREATE TABLE garage_state (
    garage_id text PRIMARY KEY REFERENCES garage (id),
    spots     int NOT NULL CHECK (spots > 0),
    occupied  int NOT NULL CHECK (occupied >= 0 AND occupied <= spots)
);

-- One row per accepted POST /v1/entries. Rejected with 429/503 requests are not stored.
CREATE TABLE entry_request (
    id          text PRIMARY KEY,
    -- the operation id of the barrier (or of Web in lab 4): a repeat with the same event_id is the same request
    event_id    text NOT NULL UNIQUE,
    garage_id   text NOT NULL REFERENCES garage_state (garage_id),
    plate       text NOT NULL,
    direction   text NOT NULL CHECK (direction IN ('in', 'out')),
    event_time  timestamptz NOT NULL,
    booking_id  text NULL,
    state       text NOT NULL CHECK (state IN ('queued', 'processing', 'done', 'rejected', 'failed')),
    -- token of the domain rule (full, booking-invalid, already-entered, not-inside, ...) or of the error
    reason      text NULL,
    detail      text NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    started_at  timestamptz NULL,
    finished_at timestamptz NULL,
    CHECK ((state IN ('done', 'rejected', 'failed')) = (finished_at IS NOT NULL))
);
CREATE INDEX entry_request_garage_received_idx ON entry_request (garage_id, received_at DESC);
CREATE INDEX entry_request_open_idx ON entry_request (state) WHERE state IN ('queued', 'processing');

-- The domain effect: one accepted entry or exit. Key event_id (lab 3 adds the effect receiver by this key).
CREATE TABLE entry (
    event_id   text PRIMARY KEY,
    request_id text NOT NULL REFERENCES entry_request (id),
    garage_id  text NOT NULL REFERENCES garage_state (garage_id),
    plate      text NOT NULL,
    direction  text NOT NULL CHECK (direction IN ('in', 'out')),
    booking_id text NULL,
    event_time timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- the second entry by the same booking is impossible (decision B5)
CREATE UNIQUE INDEX entry_booking_in_uq ON entry (booking_id) WHERE booking_id IS NOT NULL AND direction = 'in';
CREATE INDEX entry_garage_plate_idx ON entry (garage_id, plate);
CREATE INDEX entry_garage_direction_idx ON entry (garage_id, direction);
