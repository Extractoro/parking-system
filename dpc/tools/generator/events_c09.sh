#!/bin/sh
# Lab 5: the event sets of card C09 into the volume data (run by the one-off service generator):
#   docker compose run --rm --entrypoint sh generator /tools/generator/events_c09.sh
# Writes /data/events/{normal,duplicates,late}.jsonl (the same base events, seed of events.c09.json)
# and a copy of the control example of the kit with the key renamed gate_id -> garage_id.
set -eu
OUT=${OUT:-/data/events}
mkdir -p "$OUT"
for scenario in normal duplicates late; do
    python /tools/generator/generate_events.py /tools/generator/events.c09.json \
        --extend /tools/generator/columns.c09.py --scenario "$scenario" --out "$OUT/$scenario.jsonl"
done
sed 's/"gate_id"/"garage_id"/' /tools/generator/control/events.jsonl > "$OUT/control.jsonl"
echo "$OUT/control.jsonl: $(wc -l < "$OUT/control.jsonl") lines (control example, key garage_id)"
