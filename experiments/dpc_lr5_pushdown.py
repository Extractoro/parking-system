"""DPC lab 5: where the optimizer puts a filter of the service event tick written after withWatermark.

Rate source (local Spark), every even row is "tick". Prints the optimized logical plan of two queries:
  A  withWatermark -> where(garage_id != 'tick') -> groupBy(window)
  B  withWatermark -> dropDuplicatesWithinWatermark -> where(...) -> groupBy(window)
Run inside a Spark container:
    docker cp experiments/dpc_lr5_pushdown.py parking-system-spark-master-1:/tmp/
    MSYS_NO_PATHCONV=1 docker compose exec spark-master python /tmp/dpc_lr5_pushdown.py
"""

import shutil
import time

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.master("local[1]").getOrCreate()
spark.sparkContext.setLogLevel("ERROR")
rate = spark.readStream.format("rate").load()
rate = rate.withColumn("garage_id", F.when(F.col("value") % 2 == 0, "tick").otherwise("mall"))

for name, dedup in (("A: no deduplication", False), ("B: with dropDuplicatesWithinWatermark", True)):
    df = rate.withWatermark("timestamp", "5 minutes")
    if dedup:
        df = df.dropDuplicatesWithinWatermark(["value"])
    df = df.where(F.col("garage_id") != "tick").groupBy(F.window("timestamp", "5 minutes"), "garage_id").count()
    shutil.rmtree("/tmp/cp-push", ignore_errors=True)
    query = df.writeStream.outputMode("append").format("memory").queryName("t").option(
        "checkpointLocation", "/tmp/cp-push").start()
    time.sleep(8)
    print(f"## {name}", flush=True)
    query.explain(True)
    query.stop()
