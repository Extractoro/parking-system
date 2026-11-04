"""Batch report (O4.2) - skeleton: session, reading Parquet, writing the result and the plan.

The api starts it through spark-runner:
    POST http://spark-runner:8000/jobs
    {"script": "report.py", "args": ["--facts", "/data/history", "--dim", "/data/books",
                                     "--out", "/data/reports/runs/{run}"]}
Yours: the join with the reference table and the grouping of your card (DataFrame or SQL),
publishing the result for GET /v1/reports/main (D4.3: a new folder and a pointer to it).
"""

import argparse
import os
from pathlib import Path

from pyspark.sql import DataFrame, SparkSession


def session() -> SparkSession:
    return (
        SparkSession.builder.appName("report")
        .config("spark.sql.adaptive.enabled", os.environ.get("SPARK_AQE", "on") == "on")
        .config("spark.sql.adaptive.skewJoin.enabled", os.environ.get("SPARK_AQE", "on") == "on")
        .config("spark.sql.autoBroadcastJoinThreshold", os.environ.get("BROADCAST_THRESHOLD", "10m"))
        .getOrCreate()
    )


def build(facts: DataFrame, dim: DataFrame) -> DataFrame:
    # TODO (O4.2): join facts with dim and group by the keys of your card
    return facts.limit(0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", required=True)
    parser.add_argument("--dim", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--partitions", type=int, default=0)
    args = parser.parse_args()

    spark = session()
    if args.partitions:
        spark.conf.set("spark.sql.shuffle.partitions", args.partitions)

    report = build(spark.read.parquet(args.facts), spark.read.parquet(args.dim))
    report.write.mode("error").parquet(args.out)

    out = Path(args.out)
    (out / "_plan.txt").write_text(report._jdf.queryExecution().executedPlan().toString(), encoding="utf-8")
    print(f"report written to {out}")
    spark.stop()


if __name__ == "__main__":
    main()
