from __future__ import annotations

import pytest

from spark_jobs.common import WorkloadArguments


def valid_argv() -> list[str]:
    return [
        "--run-id",
        "run-1",
        "--logical-run-key",
        "scheduled/2026-08-11",
        "--plan-digest",
        "sha256:" + "b" * 64,
        "--job-id",
        "job_one",
        "--attempt",
        "2",
        "--fencing-token",
        "7",
        "--dispatch-key",
        "dispatch/run-1/job_one/2",
        "--output-prefix",
        "outputs/run-1/job_one/2/7/",
        "--output-uri",
        "s3://output/run-1/job_one/2/7/",
        "--artifact-digest",
        "sha256:" + "a" * 64,
        "--quality-policy",
        '["schema", "row_count"]',
        "--cost-budget-usd",
        "8.0",
        "--workload-account",
        "commerce",
        "--manifest-bucket",
        "dataops-commerce-manifests",
        "--signer-key-arn",
        "arn:aws:kms:ap-south-1:111122223333:key/signer",
    ]


def test_glue_arguments_share_the_dispatch_contract() -> None:
    arguments = WorkloadArguments.from_argv(
        ["--JOB_NAME", "managed-name", *valid_argv(), "--enable-metrics", "true"]
    )
    assert arguments.run_id == "run-1"
    assert arguments.attempt == 2
    assert arguments.fencing_token == 7
    assert arguments.workload_account == "commerce"


def test_glue_arguments_fail_closed_on_missing_or_duplicate_values() -> None:
    with pytest.raises(ValueError, match="missing workload arguments"):
        WorkloadArguments.from_argv(valid_argv()[:-2])
    with pytest.raises(ValueError, match="duplicate argument"):
        WorkloadArguments.from_argv([*valid_argv(), "--run-id", "run-2"])
    with pytest.raises(ValueError, match="missing value"):
        WorkloadArguments.from_argv([*valid_argv(), "--run-id"])
