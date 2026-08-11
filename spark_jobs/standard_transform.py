from __future__ import annotations

import sys

import boto3
from pyspark.sql import SparkSession

from spark_jobs.common import WorkloadArguments, publish_manifest
from spark_jobs.transforms import standard_frame


def main(values: list[str] | None = None) -> None:
    arguments = WorkloadArguments.from_argv(values)
    spark = SparkSession.builder.appName(arguments.job_id).getOrCreate()
    frame = standard_frame(spark, arguments.run_id, arguments.job_id)
    frame.write.mode("errorifexists").partitionBy("partition_key").parquet(arguments.output_uri)
    publish_manifest(
        arguments,
        frame.count(),
        [arguments.output_uri],
        {policy: True for policy in arguments.quality_policy},
        0.0,
        boto3,
    )
    spark.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
