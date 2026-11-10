-- C09 «Паркінги»: login of Web lab 3. Owner: Web (app_user, session).
--
-- app_user.password_hash: argon2id in the PHC string format ($argon2id$v=19$m=...,t=...,p=...$salt$hash),
-- made by scripts/hash_password.py. The default '!' is not a valid hash: a user created without a
-- password (older test users, rows of other parts) can never log in. Seed passwords: 904.
--
-- session: one row per login. The client holds the opaque token (32 random bytes, base64url);
-- the table holds only sha256(token) in hex, so a copy of the table does not give working tokens.
-- Logout deletes the row; expired rows are refused at once and deleted in batches by the publisher.

ALTER TABLE app_user ADD COLUMN password_hash text NOT NULL DEFAULT '!';

CREATE TABLE session (
    token_hash text PRIMARY KEY CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    user_id    text NOT NULL REFERENCES app_user (id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    CHECK (expires_at > created_at)
);

-- the cleanup takes the expired rows by expires_at
CREATE INDEX session_expires_at ON session (expires_at);
