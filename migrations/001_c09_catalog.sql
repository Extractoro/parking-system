-- C09 «Паркінги»: catalog of garages and levels, tariff, users (Web lab 1, O1.2).
-- Owners (docs/decisions.md): garage, level, app_user - Web; tariff - DPC (coordinator, DPC lab 6).

CREATE TABLE garage (
    id   text PRIMARY KEY,
    name text NOT NULL CHECK (char_length(name) > 0),
    -- IANA zone name of the garage; timestamptz stores a moment, not a zone (O1.2)
    tz   text NOT NULL CHECK (char_length(tz) > 0)
);

CREATE TABLE level (
    id        text PRIMARY KEY,
    garage_id text NOT NULL REFERENCES garage (id),
    name      text NOT NULL CHECK (char_length(name) > 0),
    spots     int  NOT NULL CHECK (spots > 0),
    -- a closed level takes no new bookings; confirmed ones stay valid (decision B8, lab 3)
    closed    boolean NOT NULL DEFAULT false,
    UNIQUE (garage_id, name)
);
CREATE INDEX level_garage_idx ON level (garage_id);

-- Owner: DPC (the tariff manager of DPC lab 6 changes it; generation/fencing columns come then).
-- Web only reads it and copies the value into a booking at booking time.
CREATE TABLE tariff (
    garage_id      text PRIMARY KEY REFERENCES garage (id),
    minor_per_hour bigint NOT NULL CHECK (minor_per_hour > 0),
    currency       char(3) NOT NULL DEFAULT 'UAH' CHECK (currency ~ '^[A-Z]{3}$'),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Password hash is added by the migration of Web lab 3.
CREATE TABLE app_user (
    id    text PRIMARY KEY,
    login text NOT NULL UNIQUE CHECK (char_length(login) > 0),
    role  text NOT NULL CHECK (role IN ('user', 'staff'))
);
