-- Invariant 1 over the whole database: hours where confirmed bookings exceed the spots of the level,
-- and hours where level_hour.free does not equal spots minus confirmed bookings.
--   docker compose exec -T postgres psql -U app -d app < experiments/web_invariant_check.sql
WITH taken AS (
    SELECT b.level_id, h AS hour, count(*) AS bookings
    FROM booking b
    CROSS JOIN LATERAL generate_series(b.from_hour, b.expires_at - interval '1 hour', interval '1 hour') h
    WHERE b.status = 'confirmed'
    GROUP BY b.level_id, h
)
SELECT l.id AS level, count(*) FILTER (WHERE t.bookings > l.spots) AS hours_over_spots,
       count(*) AS booked_hours, max(t.bookings) AS max_bookings_in_hour, l.spots
FROM taken t JOIN level l ON l.id = t.level_id
GROUP BY l.id, l.spots
ORDER BY l.id;

SELECT lh.level_id AS level, count(*) AS hours_free_mismatch
FROM level_hour lh JOIN level l ON l.id = lh.level_id
LEFT JOIN LATERAL (
    SELECT count(*) AS n FROM booking b
    WHERE b.level_id = lh.level_id AND b.status = 'confirmed' AND b.from_hour <= lh.hour AND lh.hour < b.expires_at
) c ON true
WHERE lh.free <> l.spots - c.n
GROUP BY lh.level_id
ORDER BY lh.level_id;
