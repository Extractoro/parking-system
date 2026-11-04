# One image for spark-master, the workers, the job runner and the streaming driver.
# PySpark refuses to run when the driver and the executors have different Python minor
# versions: every Spark container is built from this one file. The pyspark package ships the
# Spark distribution itself (bin/spark-submit, bin/spark-class, jars), so no separate download.
FROM python:3.14-slim-trixie

RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-21-jre-headless procps \
 && ln -s "$(dirname "$(dirname "$(readlink -f /usr/bin/java)")")" /opt/java \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    JAVA_HOME=/opt/java

COPY spark/requirements.txt /build/requirements.txt
RUN pip install --no-cache-dir -r /build/requirements.txt

ENV SPARK_HOME=/usr/local/lib/python3.14/site-packages/pyspark \
    PYSPARK_PYTHON=/usr/local/bin/python \
    PYSPARK_DRIVER_PYTHON=/usr/local/bin/python
ENV PATH="$SPARK_HOME/bin:$PATH"

# Lab 5: the Kafka source of Structured Streaming, fetched once at build time.
# The connector version must equal the PySpark version; PySpark 4.x uses Scala 2.13.
COPY spark/fetch_kafka_jars.py /build/
RUN python /build/fetch_kafka_jars.py && cp /tmp/ivy/jars/*.jar "$SPARK_HOME/jars/" && rm -rf /tmp/ivy

COPY spark/runner.py /runner/runner.py
COPY spark/jobs/ /jobs/
WORKDIR /jobs
RUN mkdir -p /data
