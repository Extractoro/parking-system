-- C09 «Паркінги»: long operation "open the barrier", transactional outbox, processed messages and the
-- notification feed (Web lab 4). Owner: Web (operation, outbox, processed_message, notification_seq,
-- notification).
--
-- operation: one row per POST /v1/bookings/{id}/entries; pending -> running -> succeeded | failed.
--   At most one live (pending, running) or succeeded entry operation per booking: a failed one may be
--   started again with a new key.
-- outbox: events written in the transaction that changes the domain state; the publisher sends them
--   to Kafka (topic = outbox.topic, key = msg_key) and sets published_at after the broker confirmed.
--   payload is the whole CloudEvents envelope, so a repeated send keeps the event id. seq keeps the
--   order of the rows of one transaction (their created_at is the same now()).
-- processed_message: the pair (consumer, event id) of a handled message, written in the transaction of
--   the result of that message.
-- notification_seq / notification: the feed of a user. A row is added only by
--   notifications.add_notification in the transaction of its cause: the counter row of the user is locked,
--   so positions of one user are given in commit order without gaps (needed by lab 5, O5.3).
--   UNIQUE (user_id, source_event_id): a repeated event gives no second notification.

CREATE TABLE operation (
    id         text PRIMARY KEY,
    user_id    text NOT NULL REFERENCES app_user (id),
    kind       text NOT NULL CHECK (kind IN ('entry')),
    booking_id text NOT NULL REFERENCES booking (id),
    state      text NOT NULL CHECK (state IN ('pending', 'running', 'succeeded', 'failed')),
    version    int NOT NULL DEFAULT 1 CHECK (version >= 1),
    result     jsonb,
    error      text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((state = 'failed') = (error IS NOT NULL))
);
CREATE UNIQUE INDEX operation_entry_live_uq ON operation (booking_id)
    WHERE kind = 'entry' AND state IN ('pending', 'running', 'succeeded');
CREATE INDEX operation_user_idx ON operation (user_id, created_at DESC);

CREATE TABLE outbox (
    id           text PRIMARY KEY,
    seq          bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    topic        text NOT NULL,
    msg_key      text NOT NULL,
    type         text NOT NULL,
    payload      jsonb NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    published_at timestamptz
);
CREATE INDEX outbox_unpublished_idx ON outbox (seq) WHERE published_at IS NULL;

CREATE TABLE processed_message (
    consumer     text NOT NULL,
    event_id     text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (consumer, event_id)
);

CREATE TABLE notification_seq (
    user_id       text PRIMARY KEY REFERENCES app_user (id),
    last_position bigint NOT NULL DEFAULT 0 CHECK (last_position >= 0)
);

CREATE TABLE notification (
    user_id         text NOT NULL REFERENCES app_user (id),
    position        bigint NOT NULL CHECK (position >= 1),
    id              text NOT NULL UNIQUE,
    type            text NOT NULL,
    payload         jsonb NOT NULL,
    source_event_id text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, position),
    UNIQUE (user_id, source_event_id)
);
