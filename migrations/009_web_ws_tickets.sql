-- C09 «Паркінги»: one-time tickets of the WebSocket channel (Web lab 5). Owner: Web (ws_ticket).
--
-- ws_ticket: POST /v1/ws-tickets (login required) stores sha256 of a random ticket, the user and the
--   sha256 of the session token it was issued for, valid 30 s. A gateway redeems it before accept() with ONE
--   UPDATE ... WHERE used_at IS NULL AND expires_at > now() RETURNING: of two connections with the same
--   ticket only one gets the row. The ticket itself travels in ?ticket= of the WebSocket URL, so it must be
--   short-lived and one-time; the session token never appears in a URL. Expired rows are deleted by the
--   cleanup loop of the publisher.
-- booking_ending_idx: the worker looks for confirmed bookings that end within 15 minutes once a minute
--   (notification "booking ends in 15 minutes").

CREATE TABLE ws_ticket (
    ticket_hash        text PRIMARY KEY,
    user_id            text NOT NULL REFERENCES app_user (id),
    session_token_hash text NOT NULL,
    expires_at         timestamptz NOT NULL,
    used_at            timestamptz
);
CREATE INDEX ws_ticket_expires_idx ON ws_ticket (expires_at);

CREATE INDEX booking_ending_idx ON booking (expires_at) WHERE status = 'confirmed';
