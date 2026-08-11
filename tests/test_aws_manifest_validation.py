from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from dataops_factory.aws.manifest_validation import ManifestBinding, validate_bound_manifest
from dataops_factory.models import ContractError


def binding() -> ManifestBinding:
    return ManifestBinding(
        run_id="run-1",
        logical_run_key="scheduled/2026-08-11",
        plan_digest="sha256:" + "b" * 64,
        job_id="commerce_orders_ingest",
        attempt=1,
        fencing_token=3,
        dispatch_key="run-1/commerce_orders_ingest/1/plan",
        artifact_digest="sha256:" + "a" * 64,
        workload_account="commerce-data",
        output_uri="s3://commerce-manifests/outputs/run-1/commerce_orders_ingest/1/3/",
        quality_policy=("schema", "row_count"),
        cost_budget_usd=8.0,
    )


def manifest() -> dict[str, Any]:
    expected = binding()
    return {
        "run_id": expected.run_id,
        "logical_run_key": expected.logical_run_key,
        "plan_digest": expected.plan_digest,
        "job_id": expected.job_id,
        "attempt": expected.attempt,
        "fencing_token": expected.fencing_token,
        "dispatch_key": expected.dispatch_key,
        "artifact_digest": expected.artifact_digest,
        "workload_account": expected.workload_account,
        "engine": "spark",
        "input_refs": [],
        "output_refs": [expected.output_uri + "part-00000.parquet"],
        "row_count": 100,
        "quality_results": {"schema": True, "row_count": True},
        "lineage": [],
        "cost_usd": 1.5,
        "completed_at": "2026-08-11T12:00:00+00:00",
        "manifest_digest_hex": "a" * 64,
        "signature_hex": "beef",
        "signer_key_arn": "arn:aws:kms:ap-south-1:111122223333:key/signer",
        "signing_algorithm": "ECDSA_SHA_256",
    }


def test_manifest_must_match_every_immutable_execution_binding() -> None:
    validate_bound_manifest(manifest(), binding())
    for field in ("run_id", "plan_digest", "attempt", "fencing_token", "artifact_digest"):
        changed = deepcopy(manifest())
        changed[field] = "wrong"
        with pytest.raises(ContractError, match=field):
            validate_bound_manifest(changed, binding())


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["quality_results"].update({"schema": False}),
        lambda value: value.update({"cost_usd": 8.01}),
        lambda value: value.update({"output_refs": ["s3://another-run/data"]}),
        lambda value: value.update({"extra": "field"}),
        lambda value: value.update({"engine": "shell"}),
    ],
)
def test_manifest_policy_violations_fail_closed(mutation: Any) -> None:
    changed = manifest()
    mutation(changed)
    with pytest.raises(ContractError):
        validate_bound_manifest(changed, binding())
