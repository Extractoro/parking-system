-- C09 «Паркінги»: idempotency keys of state-changing requests (Web lab 2). Owner: Web.
--
-- One row per (user, operation, key) holds the FINAL answer of the first request with this key:
-- a repeat with the same body gets the stored answer and does no work again; another body -> 422.
-- The row is written in the same transaction as the domain change, so both are committed together.
-- "In progress" is not a row: it is the advisory lock pg_try_advisory_xact_lock(...) held by the
-- transaction that runs the operation; a second request with the key cannot take it -> 409.

CREATE TABLE idempotency_key (
    user_id          text NOT NULL REFERENCES app_user (id),
    operation        text NOT NULL CHECK (char_length(operation) > 0),
    key              text NOT NULL CHECK (char_length(key) BETWEEN 1 AND 200),
    -- sha256 of method + route template + canonical JSON of the body
    request_hash     text NOT NULL CHECK (char_length(request_hash) = 64),
    state            text NOT NULL CHECK (state IN ('completed')),
    response_status  int  NOT NULL CHECK (response_status BETWEEN 200 AND 599),
    response_body    jsonb NOT NULL,
    response_headers jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    expires_at       timestamptz NOT NULL,
    PRIMARY KEY (user_id, operation, key),
    CHECK (expires_at > created_at)
);
-- the cleanup of the publisher deletes expired rows in batches
CREATE INDEX idempotency_key_expires_idx ON idempotency_key (expires_at);
