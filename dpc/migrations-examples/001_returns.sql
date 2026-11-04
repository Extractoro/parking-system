-- Example schema of "Міська бібліотека", DPC (not applied: migrate reads only migrations/NNN_*.sql).
-- A return from a book drop is accepted into the queue and puts the copy on the sorting shelf.
-- Invariant: used = books on the shelf <= capacity. No CHECK (used <= capacity): the O1.4
-- experiment without synchronisation must be able to show the violation.

CREATE TABLE shelf (
  branch_id text PRIMARY KEY,
  capacity  integer NOT NULL CHECK (capacity > 0),
  used      integer NOT NULL DEFAULT 0
);

CREATE TABLE shelf_item (
  branch_id text NOT NULL REFERENCES shelf (branch_id),
  copy_id   text NOT NULL,
  return_id text NOT NULL,
  placed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (branch_id, return_id)
);

CREATE TABLE return_request (
  id           text PRIMARY KEY,
  copy_id      text NOT NULL,
  loan_id      text NOT NULL,
  branch_id    text NOT NULL REFERENCES shelf (branch_id),
  station_id   text NOT NULL,
  state        text NOT NULL CHECK (state IN ('queued', 'processing', 'done', 'rejected', 'failed')),
  reason       text,
  arrived_at   timestamptz NOT NULL DEFAULT now(),
  finished_at  timestamptz
);

CREATE INDEX return_request_state ON return_request (state);
