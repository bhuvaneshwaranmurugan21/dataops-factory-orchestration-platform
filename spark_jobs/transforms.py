from __future__ import annotations

from typing import Any


def standard_frame(spark: Any, run_id: str, job_id: str, rows: int = 100_000) -> Any:
    from pyspark.sql import functions as functions

    return (
        spark.range(0, rows)
        .withColumn("run_id", functions.lit(run_id))
        .withColumn("job_id", functions.lit(job_id))
        .withColumn("partition_key", functions.col("id") % 16)
    )


def heavy_frame(spark: Any, rows: int = 1_000_000, groups: int = 1_024) -> Any:
    from pyspark.sql import functions as functions

    base = spark.range(0, rows).withColumn("group_key", functions.col("id") % groups)
    return base.groupBy("group_key").agg(
        functions.count("*").alias("records"), functions.sum("id").alias("id_sum")
    )
