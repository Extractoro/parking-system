"""Streaming job of C09 (lab 5): entries and exits of every garage in 5-minute event-time windows.

    Kafka EVENTS_TOPIC -> JSON of the card (event_id, garage_id, plate, direction, event_time)
      -> withWatermark(event_time, WATERMARK_MINUTES)            O5.2: lateness allowed
      -> dropDuplicatesWithinWatermark(event_id)                 O5.1: horizon of deduplication = the watermark
      -> groupBy(window(event_time, WINDOW_MINUTES), garage_id)  entries = in, exits = out
      -> append: only finalized windows (window end <= watermark)
      -> foreachBatch: one upsert by (run_id, garage_id, window_start) in one transaction (O5.4)

The service event `tick` (garage_id "tick", direction "tick") goes through the watermark and the aggregation
as a group of its own and is dropped only when a batch is written. A filter on garage_id written right
after withWatermark is pushed by the optimizer below EventTimeWatermark (the predicate does not use
event_time), and then tick would not move the watermark at all (Spark 4.2, experiments/dpc_lr5_pushdown.py;
DeduplicateWithinWatermark between them stops the push today, but the job must not depend on that).

Parameters (environment): EVENTS_TOPIC, RUN_ID (checkpoint /data/checkpoints/<RUN_ID>, rows of panel_window
with this run_id), WATERMARK_MINUTES, WINDOW_MINUTES, EVENTS_FILE (the JSONL the topic is fed from: recorded
in panel_run for /v1/panel/batch), TRACK_EVENTS=1 (also store the ids of counted events, experiment O5.3).
Restart keeps the checkpoint (O5.4):
    docker compose kill stream && docker compose up -d stream
"""

import os
from datetime import UTC, datetime

import psycopg
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

TOPIC = os.environ.get("EVENTS_TOPIC", "barrier-events")
RUN_ID = os.environ.get("RUN_ID", "main")
WATERMARK_MINUTES = int(os.environ.get("WATERMARK_MINUTES", "5"))
WINDOW_MINUTES = int(os.environ.get("WINDOW_MINUTES", "5"))
EVENTS_FILE = os.environ.get("EVENTS_FILE") or None
TRACK_EVENTS = os.environ.get("TRACK_EVENTS", "0") == "1"
# partitions of the state: 4 garages + tick need few; 200 (the default) costs ~40 s per micro-batch on
# 2 cores. Fixed in the checkpoint at the first start of a run.
STATE_PARTITIONS = os.environ.get("STREAM_PARTITIONS", "4")
CHECKPOINT = f"/data/checkpoints/{RUN_ID}"
TICK = "tick"

# the event of card C09; event_time: ISO 8601 UTC with milliseconds and Z (as POST /v1/entries)
SCHEMA = StructType(
    [
        StructField("event_id", StringType()),
        StructField("garage_id", StringType()),
        StructField("plate", StringType()),
        StructField("direction", StringType()),
        StructField("event_time", StringType()),
    ]
)

UPSERT = """
INSERT INTO panel_window (run_id, garage_id, window_start, window_end, entries, exits, batch_id, written_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (run_id, garage_id, window_start) DO UPDATE
   SET window_end = EXCLUDED.window_end, entries = EXCLUDED.entries, exits = EXCLUDED.exits,
       batch_id = EXCLUDED.batch_id, written_at = now()
"""
COUNTED = """
INSERT INTO panel_event (run_id, event_id, garage_id, window_start) VALUES (%s, %s, %s, %s)
ON CONFLICT (run_id, event_id) DO NOTHING
"""


def utc(millis: int) -> datetime:
    return datetime.fromtimestamp(millis / 1000, UTC)


def register_run() -> None:
    """panel_run: the topic, the source file and the rules of this run; a restart keeps started_at."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute(
            """INSERT INTO panel_run (run_id, topic, events_file, window_minutes, watermark_minutes)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (run_id) DO UPDATE SET topic = EXCLUDED.topic, events_file = EXCLUDED.events_file,
                   window_minutes = EXCLUDED.window_minutes, watermark_minutes = EXCLUDED.watermark_minutes""",
            (RUN_ID, TOPIC, EVENTS_FILE, WINDOW_MINUTES, WATERMARK_MINUTES),
        )


def write_batch(batch: DataFrame, batch_id: int) -> None:
    """Upsert the finalized windows of one micro-batch in one transaction.

    The batch holds only windows finalized by this micro-batch (append mode): at most garages x windows
    closed by one watermark step, so collect() brings a few rows, never the events themselves.
    A repeat of the same batch_id after a restart writes the same values for the same keys: nothing grows.
    """
    rows = batch.where(F.col("garage_id") != TICK).collect()
    if not rows:
        print(f"batch {batch_id}: no finalized windows", flush=True)
        return

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn, conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            UPSERT,
            [
                (RUN_ID, r.garage_id, utc(r.start_ms), utc(r.end_ms), r.entries, r.exits, batch_id)
                for r in rows
            ],
        )
        if TRACK_EVENTS:
            cur.executemany(
                COUNTED,
                [(RUN_ID, event_id, r.garage_id, utc(r.start_ms)) for r in rows for event_id in r.event_ids],
            )

    print(f"batch {batch_id}: {len(rows)} windows of run {RUN_ID} written", flush=True)


def main() -> None:
    register_run()
    spark = (
        SparkSession.builder.appName(f"stream-{RUN_ID}")
        .config("spark.sql.shuffle.partitions", STATE_PARTITIONS)
        .getOrCreate()
    )

    events = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092"))
        .option("subscribe", TOPIC)
        .option("startingOffsets", "earliest")
        .load()
        .select(F.from_json(F.col("value").cast("string"), SCHEMA).alias("e"))
        .select("e.*")
        .withColumn("event_time", F.to_timestamp("event_time"))
    )

    counts = [
        F.count(F.when(F.col("direction") == "in", 1)).alias("entries"),
        F.count(F.when(F.col("direction") == "out", 1)).alias("exits"),
    ]
    if TRACK_EVENTS:
        counts.append(F.collect_list("event_id").alias("event_ids"))

    windows = (
        events.withWatermark("event_time", f"{WATERMARK_MINUTES} minutes")
        .dropDuplicatesWithinWatermark(["event_id"])
        .groupBy(F.window("event_time", f"{WINDOW_MINUTES} minutes"), "garage_id")
        .agg(*counts)
        .withColumn("start_ms", F.unix_millis(F.col("window.start")))
        .withColumn("end_ms", F.unix_millis(F.col("window.end")))
        .drop("window")
    )

    print(
        f"stream run={RUN_ID} topic={TOPIC} window={WINDOW_MINUTES}m watermark={WATERMARK_MINUTES}m "
        f"checkpoint={CHECKPOINT} track_events={TRACK_EVENTS}",
        flush=True,
    )
    query = (
        windows.writeStream.outputMode("append")
        .option("checkpointLocation", CHECKPOINT)
        .foreachBatch(write_batch)
        .start()
    )
    query.awaitTermination()


if __name__ == "__main__":
    main()
