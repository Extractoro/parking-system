-- Index of the cursor list GET /v1/bookings (O1.5): WHERE user_id = $1 AND (created_at, id) < ($t, $id)
-- ORDER BY created_at DESC, id DESC. A separate file to compare query plans before and after (D2.7).
CREATE INDEX CONCURRENTLY IF NOT EXISTS booking_user_created_idx ON booking (user_id, created_at DESC, id DESC);
