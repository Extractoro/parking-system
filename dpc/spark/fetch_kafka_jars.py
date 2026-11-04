"""Build step of the Spark image: download the Kafka connector matching the installed PySpark.

Spark resolves `spark.jars.packages` with Ivy into /tmp/ivy/jars; the Dockerfile copies them
into $SPARK_HOME/jars, so no download happens when the cluster runs.
"""

import pyspark
from pyspark.sql import SparkSession

SCALA = "2.13"  # PySpark 4.x is built with Scala 2.13

package = f"org.apache.spark:spark-sql-kafka-0-10_{SCALA}:{pyspark.__version__}"
spark = (
    SparkSession.builder.master("local[1]")
    .appName("fetch-kafka-jars")
    .config("spark.jars.packages", package)
    .config("spark.jars.ivy", "/tmp/ivy")
    .getOrCreate()
)
spark.stop()
print(f"fetched {package}")
