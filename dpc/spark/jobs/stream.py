"""Streaming job (lab 5) - skeleton: Kafka source, JSON schema, checkpoint, an empty foreachBatch.

Runs as the service stream of compose.yaml; restart keeps the checkpoint in /data (O5.4):
    docker compose kill stream && docker compose up -d stream
Yours: the event schema of your card, event-time windows and watermark (WATERMARK_MINUTES),
deduplication (O5.1) and an idempotent write of finalized results in write_batch (O5.4).
"""

import os

import psycopg
from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import StringType, StructField, StructType

CHECKPOINT = "/data/checkpoints/stream"

# TODO (O5.1): the fields of your event; event_time in UTC (ISO 8601 with Z or epoch milliseconds)
SCHEMA = StructType(
    [
        StructField("event_id", StringType()),
        StructField("event_time", StringType()),
        StructField("kind", StringType()),
    ]
)


def write_batch(batch: DataFrame, batch_id: int) -> None:
    rows = batch.collect()
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        # TODO (O5.4): upsert the finalized windows by (key, window) in one transaction
        conn.execute("SELECT 1")

    print(f"batch {batch_id}: {len(rows)} rows")


def main() -> None:
    spark = SparkSession.builder.appName("stream").getOrCreate()

    events = (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", os.environ.get("KAFKA_BOOTSTRAP", "kafka:9092"))
        .option("subscribe", os.environ.get("EVENTS_TOPIC", "events"))
        .option("startingOffsets", "earliest")
        .load()
        .select(F.from_json(F.col("value").cast("string"), SCHEMA).alias("e"))
        .select("e.*")
        .withColumn("event_time", F.to_timestamp("event_time"))
    )

    # TODO (O5.2): withWatermark("event_time", f"{WATERMARK_MINUTES} minutes"), window, aggregate
    query = (
        events.writeStream.outputMode("append")
        .option("checkpointLocation", CHECKPOINT)
        .foreachBatch(write_batch)
        .start()
    )
    query.awaitTermination()


if __name__ == "__main__":
    main()
