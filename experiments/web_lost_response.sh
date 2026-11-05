#!/usr/bin/env bash
# Experiment "lost response": the booking is committed, the answer never leaves app-a.
#
#   bash experiments/web_lost_response.sh | tee results/web-lr2/lost-response.txt
#
# 1) arm the fault after_commit_before_response (shared volume /faults, FAULTS=on in compose.yaml);
# 2) POST /v1/bookings with key K straight to app-a (:8001): the process dies after COMMIT,
#    the client gets no answer (curl: empty reply); the log of app-a has fault.hit;
# 3) the same request with K to app-b (:8002): 201 with the same booking (Idempotent-Replayed);
# 4) SQL: exactly one booking for this key and body, free of each hour went down once.
# Needs: docker compose up -d nginx (app-a, app-b, postgres), curl.
set -euo pipefail
export MSYS_NO_PATHCONV=1   # Git Bash on Windows: keep /faults/... as it is
cd "$(dirname "$0")/.."

psql() { docker compose exec -T postgres psql -U app -d app -At "$@"; }

USER_ID="lost-$(date +%s)-$RANDOM"
KEY="lost-$(cat /proc/sys/kernel/random/uuid 2>/dev/null || date +%s%N)"
DAY=$(( RANDOM % 27 + 1 )); MONTH=$(( RANDOM % 9 + 1 )); YEAR=$(( 2050 + RANDOM % 40 ))
FROM=$(printf '%04d-%02d-%02dT10:00:00+03:00' "$YEAR" "$((MONTH + 1))" "$DAY")   # Apr..Oct: UTC+3 in Kyiv
BODY="{\"level_id\":\"mall-1\",\"from_hour\":\"$FROM\",\"hours\":2,\"plate\":\"LR0001AA\"}"
FREE_SQL="SELECT coalesce(string_agg(free::text, ',' ORDER BY hour), 'no rows yet (free = 40)') FROM level_hour
          WHERE level_id = 'mall-1' AND hour >= '$FROM'::timestamptz AND hour < '$FROM'::timestamptz + interval '2 hours'"

psql -c "INSERT INTO app_user (id, login, role) VALUES ('$USER_ID', '$USER_ID', 'user')" >/dev/null
echo "== user $USER_ID, key $KEY"
echo "== body $BODY"
echo "== free of the two hours before: $(psql -c "$FREE_SQL")"

echo "== 1) arm the fault"
docker compose exec -T app-a touch /faults/after_commit_before_response
echo "docker compose exec app-a touch /faults/after_commit_before_response -> $(docker compose exec -T app-a ls /faults)"

echo "== 2) POST to app-a (:8001) with the key"
set +e
curl -sS -o /dev/null -w 'http_code=%{http_code}\n' --max-time 10 -X POST http://localhost:8001/v1/bookings \
  -H "X-User-Id: $USER_ID" -H "Idempotency-Key: $KEY" -H 'Content-Type: application/json' -d "$BODY" 2>&1
echo "curl exit code: $?"
set -e
sleep 1
echo "-- log of app-a:"
docker compose logs app-a --since 1m --no-log-prefix | grep -E '"fault.hit"|"app.started"|Started server' || true
echo "-- after the crash, before any retry: bookings of the user = $(psql -c "SELECT count(*) FROM booking WHERE user_id = '$USER_ID'"), key stored = $(psql -c "SELECT response_status FROM idempotency_key WHERE user_id = '$USER_ID' AND key = '$KEY'")"

for _ in $(seq 1 30); do curl -sf http://localhost:8001/health >/dev/null && break; sleep 1; done
echo "-- app-a is back: $(curl -s http://localhost:8001/health)"

echo "== 3) the same request with the same key to app-b (:8002)"
curl -sS -i --max-time 10 -X POST http://localhost:8002/v1/bookings \
  -H "X-User-Id: $USER_ID" -H "Idempotency-Key: $KEY" -H 'Content-Type: application/json' -d "$BODY" \
  | tr -d '\r' | grep -iE '^HTTP/|^location:|^idempotent-replayed:|^x-instance:|^\{' | sed -E 's/("id":"[^"]*").*/\1, .../'

echo "== 4) database"
echo "bookings of the user: $(psql -c "SELECT count(*) FROM booking WHERE user_id = '$USER_ID'")"
echo "bookings of this body: $(psql -c "SELECT count(*) FROM booking WHERE user_id = '$USER_ID' AND level_id = 'mall-1' AND from_hour = '$FROM' AND hours = 2 AND plate = 'LR0001AA'")"
echo "booking id in the stored answer = booking in the table: $(psql -c "SELECT k.response_body->>'id' = b.id FROM idempotency_key k JOIN booking b ON b.user_id = k.user_id WHERE k.user_id = '$USER_ID' AND k.key = '$KEY'")"
echo "free of the two hours after: $(psql -c "$FREE_SQL")"
echo "fault file left: $(docker compose exec -T app-a ls /faults | wc -l) (0 = the fault fired once and disarmed itself)"
