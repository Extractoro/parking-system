-- C09 «Паркінги»: free spots per level and hour, bookings (Web lab 1, O1.2). Owner: Web.
--
-- Invariant 1: in every hour the bookings of a level do not exceed its spots
--   -> level_hour.free = spots - active bookings of that hour, CHECK (free >= 0).
-- Invariant 2: a booking of N hours takes all N hours or none
--   -> all level_hour rows change in the transaction that inserts the booking.

CREATE TABLE level_hour (
    level_id text NOT NULL REFERENCES level (id),
    -- start of the hour; rows are created lazily by the booking transaction (free = level.spots)
    hour     timestamptz NOT NULL CHECK (date_trunc('hour', hour) = hour),
    free     int NOT NULL CHECK (free >= 0),
    PRIMARY KEY (level_id, hour)
);

CREATE TABLE booking (
    id                    text PRIMARY KEY,
    user_id               text NOT NULL REFERENCES app_user (id),
    level_id              text NOT NULL REFERENCES level (id),
    plate                 text NOT NULL CHECK (char_length(plate) BETWEEN 2 AND 12),
    -- interval [from_hour, from_hour + hours * 1 hour)
    from_hour             timestamptz NOT NULL CHECK (date_trunc('hour', from_hour) = from_hour),
    hours                 int NOT NULL CHECK (hours BETWEEN 1 AND 24),
    -- tariff at booking time (kopecks per hour); a partial hour is a full hour, so price = tariff * hours
    tariff_minor_per_hour bigint NOT NULL CHECK (tariff_minor_per_hour > 0),
    price_minor           bigint NOT NULL,
    currency              char(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
    -- 'entered' is not stored: Web derives it from the entries of DPC (decision B3)
    status                text NOT NULL CHECK (status IN ('held', 'confirmed', 'cancelled', 'expired')),
    version               int NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at            timestamptz NOT NULL DEFAULT now(),
    -- end of the interval: lab 4 uses it for 'expired' and the "ends in 15 minutes" notification
    expires_at            timestamptz NOT NULL,
    CONSTRAINT booking_price_check CHECK (price_minor = tariff_minor_per_hour * hours),
    CONSTRAINT booking_expires_check CHECK (expires_at = from_hour + hours * interval '1 hour')
);
CREATE INDEX booking_level_from_idx ON booking (level_id, from_hour);
