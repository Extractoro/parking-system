"""Batch report of C09 «Паркінги» (O4.2): sessions, average parking time and revenue per garage.

The api starts it through spark-runner:
    POST http://spark-runner:8000/jobs
    {"script": "report.py", "args": ["--facts", "/data/c09/small/history", "--dim", "/data/c09/small/garages",
                                     "--out", "/data/reports/runs/{run}"]}

  facts history(session_id, garage_id, plate, entered, minutes, amount_minor)
  JOIN  garages(garage_id, name, city, tariff_minor_per_hour)  ON garage_id
  GROUP BY garage_id, name:
    sessions = count(*), total_minutes = sum(minutes), revenue_minor = sum(amount_minor)  (64-bit integers)
    avg_minutes = total_minutes / sessions  (from the sum and the count, never an average of averages)

D4.3 atomic publication: the run writes ONLY its own folder /data/reports/runs/{run} (Parquet + rows.json),
then replaces the pointer /data/reports/current: a temporary file in the same folder + os.replace (one rename).
A run that fails at any step exits non-zero before the replace, so `current` still names the previous run.

O4.4: --join shuffle disables the broadcast join (autoBroadcastJoinThreshold = -1, also for AQE), so Spark
plans a sort-merge join with Exchange hashpartitioning(garage_id) on both sides; --aqe on|off and the skew
thresholds select the variant. --metrics writes the task metrics of every stage (the REST API of this
driver's own UI) next to the result: _metrics.json; the final physical plan goes to _plan.txt.
"""

import argparse
import json
import os
import statistics
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F
from pyspark.sql import types as T

ROW_SCHEMA = T.StructType(
    [
        T.StructField("garage_id", T.StringType(), False),
        T.StructField("name", T.StringType(), True),
        T.StructField("sessions", T.LongType(), False),
        T.StructField("total_minutes", T.LongType(), False),
        T.StructField("avg_minutes", T.DoubleType(), False),
        T.StructField("revenue_minor", T.LongType(), False),
    ]
)


def session(args: argparse.Namespace) -> SparkSession:
    aqe = (os.environ.get("SPARK_AQE", "on") if args.aqe == "env" else args.aqe) == "on"
    builder = (
        SparkSession.builder.appName(f"report join={args.join} aqe={'on' if aqe else 'off'}")
        .config("spark.sql.adaptive.enabled", aqe)
        .config("spark.sql.adaptive.skewJoin.enabled", aqe)
    )
    if args.join == "shuffle":
        # no broadcast at plan time and none at run time (AQE converts small sides to broadcast too)
        builder = builder.config("spark.sql.autoBroadcastJoinThreshold", "-1").config(
            "spark.sql.adaptive.autoBroadcastJoinThreshold", "-1"
        )
    else:
        builder = builder.config("spark.sql.autoBroadcastJoinThreshold", os.environ.get("BROADCAST_THRESHOLD", "10m"))
    if args.skew_factor:
        builder = builder.config("spark.sql.adaptive.skewJoin.skewedPartitionFactor", args.skew_factor)
    if args.skew_threshold:
        builder = builder.config("spark.sql.adaptive.skewJoin.skewedPartitionThresholdInBytes", args.skew_threshold)
    if args.force_skew:
        # the split of a skewed join part breaks the partitioning by garage_id that the aggregation after the join
        # relies on; without this flag AQE refuses the split (it would need one more Exchange) and only coalesces
        builder = builder.config("spark.sql.adaptive.forceOptimizeSkewedJoin", "true")
    if args.advisory:
        builder = builder.config("spark.sql.adaptive.advisoryPartitionSizeInBytes", args.advisory)
    if args.partitions:
        builder = builder.config("spark.sql.shuffle.partitions", args.partitions)
    return builder.getOrCreate()


def build(facts: DataFrame, dim: DataFrame) -> DataFrame:
    """Join the facts with the reference table and group by garage."""
    joined = facts.select("garage_id", "minutes", "amount_minor").join(
        dim.select("garage_id", "name"), on="garage_id", how="inner"
    )
    grouped = joined.groupBy("garage_id", "name").agg(
        F.count(F.lit(1)).cast("long").alias("sessions"),
        F.sum(F.col("minutes").cast("long")).alias("total_minutes"),
        F.sum(F.col("amount_minor").cast("long")).alias("revenue_minor"),
    )
    return grouped.select(
        "garage_id",
        "name",
        "sessions",
        "total_minutes",
        (F.col("total_minutes") / F.col("sessions")).alias("avg_minutes"),
        "revenue_minor",
    )


def explain(df: DataFrame) -> str:
    """The same text as df.explain("formatted"), as a string (after an action: the final AQE plan)."""
    return df.sparkSession._jvm.PythonSQLUtils.explainString(df._jdf.queryExecution(), "formatted")


def _get(base: str, path: str) -> Any:
    with urllib.request.urlopen(f"{base}/api/v1{path}", timeout=30) as response:
        return json.load(response)


def stage_metrics(spark: SparkSession) -> list[dict[str, Any]]:
    """Task metrics of every complete stage of this application (as dpc/tools/spark_stages.py)."""
    base, app = spark.sparkContext.uiWebUrl, spark.sparkContext.applicationId
    if not base:
        return []
    result = []
    for stage in _get(base, f"/applications/{app}/stages"):
        if stage["status"] != "COMPLETE":
            continue
        tasks, offset = [], 0
        path = f"/applications/{app}/stages/{stage['stageId']}/{stage['attemptId']}/taskList"
        while True:
            page = _get(base, f"{path}?offset={offset}&length=1000")
            tasks.extend(t for t in page if t.get("status") == "SUCCESS")
            if len(page) < 1000:
                break
            offset += len(page)
        if not tasks:
            continue
        durations = [t["duration"] for t in tasks]
        shuffle_bytes = [
            t.get("taskMetrics", {}).get("shuffleReadMetrics", {}).get("remoteBytesRead", 0)
            + t.get("taskMetrics", {}).get("shuffleReadMetrics", {}).get("localBytesRead", 0)
            for t in tasks
        ]
        shuffle_records = [t.get("taskMetrics", {}).get("shuffleReadMetrics", {}).get("recordsRead", 0) for t in tasks]
        busy = [d for d, r in zip(durations, shuffle_records, strict=True) if r > 0]
        result.append(
            {
                "stage": stage["stageId"],
                "attempt": stage["attemptId"],
                "name": stage["name"],
                "tasks": len(tasks),
                "tasks_with_shuffle_read": len(busy),
                "duration_ms": stage.get("executorRunTime"),
                "submitted": stage.get("submissionTime"),
                "completed": stage.get("completionTime"),
                "task_ms_max": max(durations),
                "task_ms_median": statistics.median(durations),
                "task_ms_median_nonempty": statistics.median(busy) if busy else 0,
                "shuffle_read_bytes_total": sum(shuffle_bytes),
                "shuffle_read_bytes_max": max(shuffle_bytes),
                "shuffle_read_bytes_median_nonempty": statistics.median([b for b in shuffle_bytes if b > 0])
                if any(shuffle_bytes)
                else 0,
                "shuffle_read_records_max": max(shuffle_records),
                "task_ms": sorted(durations, reverse=True),
                "task_shuffle_records": sorted(shuffle_records, reverse=True),
                "executors": sorted({t.get("executorId") for t in tasks}),
            }
        )
    return result


def publish(pointer: Path, record: dict[str, Any]) -> None:
    """D4.3: write the new pointer next to the old one, then one atomic rename over it."""
    pointer.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer.with_name(f".{pointer.name}.{record['run']}.tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    with tmp.open("rb") as f:
        os.fsync(f.fileno())
    os.replace(tmp, pointer)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--dim", required=True)
    parser.add_argument("--out", required=True, help="folder of this run, /data/reports/runs/{run}")
    parser.add_argument("--pointer", default="/data/reports/current", help="'' = do not publish")
    parser.add_argument("--partitions", type=int, default=0, help="spark.sql.shuffle.partitions (0 = default)")
    parser.add_argument("--join", choices=["auto", "shuffle"], default="auto")
    parser.add_argument("--aqe", choices=["env", "on", "off"], default="env", help="env = SPARK_AQE of the container")
    parser.add_argument("--skew-factor", default="", help="skewJoin.skewedPartitionFactor")
    parser.add_argument("--skew-threshold", default="", help="skewJoin.skewedPartitionThresholdInBytes, e.g. 32m")
    parser.add_argument("--advisory", default="", help="adaptive.advisoryPartitionSizeInBytes, e.g. 16m")
    parser.add_argument("--force-skew", action="store_true", help="adaptive.forceOptimizeSkewedJoin = true")
    parser.add_argument("--metrics", action="store_true", help="write _metrics.json with the stage task metrics")
    args = parser.parse_args()

    out = Path(args.out)
    run = out.name
    spark = session(args)

    started = time.monotonic()
    report = build(spark.read.parquet(args.facts), spark.read.parquet(args.dim))
    rows = sorted((row.asDict() for row in report.collect()), key=lambda r: r["garage_id"])
    seconds = round(time.monotonic() - started, 3)

    # the run's own folder: Parquet (for other Spark jobs) and rows.json (for the api); mode error = never reuse
    spark.createDataFrame(rows, ROW_SCHEMA).coalesce(1).write.mode("error").parquet(str(out))
    (out / "_plan.txt").write_text(explain(report), encoding="utf-8")
    record = {
        "run": run,
        "path": str(out),
        "facts": args.facts,
        "dim": args.dim,
        "join": args.join,
        "aqe": spark.conf.get("spark.sql.adaptive.enabled"),
        "partitions": spark.conf.get("spark.sql.shuffle.partitions"),
        "seconds": seconds,
        "written_at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "rows": rows,
    }
    (out / "rows.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.metrics:
        metrics = {"run": run, "app": spark.sparkContext.applicationId, "seconds": seconds, "stages": stage_metrics(spark)}
        (out / "_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    if args.pointer:
        publish(Path(args.pointer), {k: record[k] for k in ("run", "path", "facts", "dim", "join", "written_at")})
    print(f"report {run}: {len(rows)} rows in {seconds} s, written to {out}, published={bool(args.pointer)}")
    spark.stop()


if __name__ == "__main__":
    main()
