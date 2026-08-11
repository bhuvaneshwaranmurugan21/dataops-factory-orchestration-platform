from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest

from dataops_factory.aws.dispatch import (
    AwsDispatcher,
    DispatchRequest,
    ExecutionProfile,
)
from dataops_factory.aws.kms_signer import KmsDigestSigner
from dataops_factory.aws.reconcile import ExternalState, reconcile_job
from dataops_factory.models import AdapterType, ContractError


class FakeClient:
    def __init__(self, existing_glue_run: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.existing_glue_run = existing_glue_run

    def assume_role(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("assume_role", kwargs))
        return {
            "Credentials": {
                "AccessKeyId": "key",
                "SecretAccessKey": "secret",
                "SessionToken": "token",
            }
        }

    def invoke(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("invoke", kwargs))
        return {"ResponseMetadata": {"RequestId": "lambda-1"}, "Payload": BytesIO(b"{}")}

    def start_job_run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("start_job_run", kwargs))
        return {"JobRunId": "glue-1", "jobRunId": "emr-1"}

    def get_job_runs(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_job_runs", kwargs))
        if not self.existing_glue_run:
            return {"JobRuns": []}
        return {
            "JobRuns": [{"Id": "glue-existing", "Arguments": {"--dispatch-key": "dispatch-key"}}]
        }

    def get_job_run(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_job_run", kwargs))
        if "JobName" in kwargs:
            return {"JobRun": {"JobRunState": "SUCCEEDED"}}
        return {"jobRun": {"state": "RUNNING"}}


def profile(adapter: str) -> ExecutionProfile:
    raw: dict[str, object] = {
        "adapter_type": adapter,
        "role_arn": "arn:aws:iam::111122223333:role/dataops-workload",
        "region": "ap-south-1",
        "target": "target-1",
        "account_label": "commerce-data",
        "artifact_bucket": "dataops-commerce-artifacts",
        "manifest_bucket": "dataops-commerce-manifests",
        "signer_key_arn": "arn:aws:kms:ap-south-1:111122223333:key/signer",
    }
    if adapter == "emr_serverless":
        raw["execution_role_arn"] = "arn:aws:iam::111122223333:role/emr-execution"
    return ExecutionProfile.from_dict(f"{adapter}-profile", raw)


def request(profile_id: str) -> DispatchRequest:
    return DispatchRequest(
        run_id="run-1",
        logical_run_key="scheduled/2026-08-11",
        plan_digest="sha256:" + "b" * 64,
        job_id="job_one",
        attempt=1,
        fencing_token=2,
        dispatch_key="dispatch-key",
        output_prefix="outputs/run-1/job-one/1/2/",
        artifact_digest="sha256:" + "a" * 64,
        quality_policy=("schema", "row_count"),
        cost_budget_usd=8.0,
        profile_id=profile_id,
    )


@pytest.mark.parametrize(
    ("adapter", "external_id"),
    [("lambda", "lambda-1"), ("glue", "glue-1"), ("emr_serverless", "emr-1")],
)
def test_allowlisted_dispatch_paths(adapter: str, external_id: str) -> None:
    sts = FakeClient()
    service = FakeClient()
    selected = profile(adapter)
    dispatcher = AwsDispatcher(sts, lambda *_: service, {selected.profile_id: selected})
    receipt = dispatcher.dispatch(request(selected.profile_id))
    assert receipt.external_id == external_id
    assert receipt.adapter_type.value == adapter
    assert sts.calls[0][0] == "assume_role"


def test_unlisted_and_invalid_profiles_fail_closed() -> None:
    dispatcher = AwsDispatcher(FakeClient(), lambda *_: FakeClient(), {})
    with pytest.raises(ContractError, match="not allowlisted"):
        dispatcher.dispatch(request("unknown"))
    with pytest.raises(ContractError, match="invalid role ARN"):
        ExecutionProfile.from_dict(
            "bad",
            {
                "adapter_type": "lambda",
                "role_arn": "*",
                "region": "x",
                "target": "y",
                "account_label": "test",
                "artifact_bucket": "test-artifacts",
                "manifest_bucket": "test-bucket",
                "signer_key_arn": "arn:aws:kms:ap-south-1:111122223333:key/signer",
            },
        )
    with pytest.raises(ContractError, match="requires execution_role"):
        ExecutionProfile.from_dict(
            "bad-emr",
            {
                "adapter_type": "emr_serverless",
                "role_arn": "arn:aws:iam::111122223333:role/test",
                "region": "x",
                "target": "y",
                "account_label": "test",
                "artifact_bucket": "test-artifacts",
                "manifest_bucket": "test-bucket",
                "signer_key_arn": "arn:aws:kms:ap-south-1:111122223333:key/signer",
            },
        )


def test_reconciliation_maps_managed_engine_states() -> None:
    client = FakeClient()
    glue = reconcile_job(AdapterType.GLUE, client, "glue-1", "job")
    emr = reconcile_job(AdapterType.EMR_SERVERLESS, client, "emr-1", "app")
    assert glue.state is ExternalState.SUCCEEDED
    assert emr.state is ExternalState.RUNNING
    with pytest.raises(ContractError, match="Lambda"):
        reconcile_job(AdapterType.LAMBDA, client, "lambda-1", "function")


def test_emr_dispatch_uses_profile_owned_versioned_artifacts() -> None:
    sts = FakeClient()
    service = FakeClient()
    selected = profile("emr_serverless")
    AwsDispatcher(sts, lambda *_: service, {selected.profile_id: selected}).dispatch(
        request(selected.profile_id)
    )
    _, call = service.calls[0]
    spark_submit = call["jobDriver"]["sparkSubmit"]
    assert spark_submit["entryPoint"].startswith(
        "s3://dataops-commerce-artifacts/jobs/heavy_transform.py"
    )
    assert "dataops-commerce-artifacts/jobs/spark_jobs.zip" in spark_submit["sparkSubmitParameters"]


def test_glue_dispatch_recovers_an_existing_dispatch_identity() -> None:
    sts = FakeClient()
    service = FakeClient(existing_glue_run=True)
    selected = profile("glue")
    receipt = AwsDispatcher(sts, lambda *_: service, {selected.profile_id: selected}).dispatch(
        request(selected.profile_id)
    )
    assert receipt.external_id == "glue-existing"
    assert [name for name, _ in service.calls] == ["get_job_runs"]


class FakeKms:
    def sign(self, **kwargs: Any) -> dict[str, object]:
        assert kwargs["MessageType"] == "DIGEST"
        return {"Signature": b"signature"}

    def verify(self, **kwargs: Any) -> dict[str, object]:
        return {"SignatureValid": kwargs["Signature"] == b"signature"}


def test_kms_digest_signer_contract() -> None:
    signer = KmsDigestSigner(FakeKms(), "arn:aws:kms:ap-south-1:111122223333:key/abc")
    signature = signer.sign_digest(b"digest")
    assert signer.algorithm == "ECDSA_SHA_256"
    assert signer.verify_digest(b"digest", signature)
