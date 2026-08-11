from __future__ import annotations

import pytest

from spark_jobs.transforms import heavy_frame, standard_frame

pyspark_sql = pytest.importorskip("pyspark.sql")


@pytest.fixture(scope="module")
def spark() -> object:
    session = (
        pyspark_sql.SparkSession.builder.master("local[2]")
        .appName("dataops-factory-contract-tests")
        .config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )
    yield session
    session.stop()


@pytest.mark.spark
def test_standard_transform_preserves_rows_and_partition_contract(spark: object) -> None:
    frame = standard_frame(spark, "run-1", "job-one", rows=1_000)
    assert frame.count() == 1_000
    assert frame.select("partition_key").distinct().count() == 16
    assert frame.select("run_id").distinct().first()[0] == "run-1"


@pytest.mark.spark
def test_heavy_transform_preserves_count_and_sum_invariants(spark: object) -> None:
    frame = heavy_frame(spark, rows=10_000, groups=128)
    aggregates = frame.agg({"records": "sum", "id_sum": "sum"}).first()
    assert frame.count() == 128
    assert aggregates["sum(records)"] == 10_000
    assert aggregates["sum(id_sum)"] == sum(range(10_000))
