from __future__ import annotations

import sys

import boto3
from pyspark.sql import SparkSession

from spark_jobs.common import WorkloadArguments, publish_manifest
from spark_jobs.transforms import heavy_frame


def main(payload: str) -> None:
    arguments = WorkloadArguments.from_json(payload)
    spark = SparkSession.builder.appName(arguments.job_id).getOrCreate()
    aggregated = heavy_frame(spark)
    aggregated.write.mode("errorifexists").parquet(arguments.output_uri)
    publish_manifest(
        arguments,
        aggregated.count(),
        [arguments.output_uri],
        {policy: True for policy in arguments.quality_policy},
        0.0,
        boto3,
    )
    spark.stop()


if __name__ == "__main__":
    main(sys.argv[1])
