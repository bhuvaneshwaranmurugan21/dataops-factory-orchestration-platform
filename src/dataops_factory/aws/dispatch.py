from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from dataops_factory.models import AdapterType, ContractError


class AwsClient(Protocol):
    def __getattr__(self, name: str) -> Callable[..., dict[str, Any]]: ...


ClientFactory = Callable[[str, dict[str, str], str], AwsClient]

_ARN = re.compile(r"^arn:aws[a-zA-Z-]*:[a-z0-9-]+:[a-z0-9-]*:[0-9]{0,12}:.+$")


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    profile_id: str
    adapter_type: AdapterType
    role_arn: str
    region: str
    target: str
    account_label: str
    artifact_bucket: str
    manifest_bucket: str
    signer_key_arn: str
    execution_role_arn: str | None = None

    @classmethod
    def from_dict(cls, profile_id: str, raw: dict[str, object]) -> ExecutionProfile:
        try:
            adapter = AdapterType(str(raw["adapter_type"]))
            role_arn = str(raw["role_arn"])
            region = str(raw["region"])
            target = str(raw["target"])
            account_label = str(raw["account_label"])
            artifact_bucket = str(raw["artifact_bucket"])
            manifest_bucket = str(raw["manifest_bucket"])
            signer_key_arn = str(raw["signer_key_arn"])
        except KeyError as exc:
            raise ContractError(f"execution profile {profile_id} is incomplete") from exc
        if not _ARN.fullmatch(role_arn):
            raise ContractError(f"execution profile {profile_id} has an invalid role ARN")
        if not region or not target:
            raise ContractError(f"execution profile {profile_id} has an empty target or region")
        if not account_label or not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", account_label):
            raise ContractError(f"execution profile {profile_id} has an invalid account label")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", artifact_bucket):
            raise ContractError(f"execution profile {profile_id} has an invalid artifact bucket")
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{2,62}", manifest_bucket):
            raise ContractError(f"execution profile {profile_id} has an invalid manifest bucket")
        if not _ARN.fullmatch(signer_key_arn):
            raise ContractError(f"execution profile {profile_id} has an invalid signer key ARN")
        execution_role = raw.get("execution_role_arn")
        execution_role_arn = None if execution_role is None else str(execution_role)
        if adapter is AdapterType.EMR_SERVERLESS and not execution_role_arn:
            raise ContractError(f"EMR profile {profile_id} requires execution_role_arn")
        return cls(
            profile_id,
            adapter,
            role_arn,
            region,
            target,
            account_label,
            artifact_bucket,
            manifest_bucket,
            signer_key_arn,
            execution_role_arn,
        )


@dataclass(frozen=True, slots=True)
class DispatchRequest:
    run_id: str
    logical_run_key: str
    plan_digest: str
    job_id: str
    attempt: int
    fencing_token: int
    dispatch_key: str
    output_prefix: str
    artifact_digest: str
    quality_policy: tuple[str, ...]
    cost_budget_usd: float
    profile_id: str


@dataclass(frozen=True, slots=True)
class DispatchReceipt:
    adapter_type: AdapterType
    external_id: str
    profile_id: str
    result: dict[str, Any] | None = None


class AwsDispatcher:
    def __init__(
        self,
        sts_client: AwsClient,
        client_factory: ClientFactory,
        profiles: dict[str, ExecutionProfile],
    ) -> None:
        self._sts = sts_client
        self._client_factory = client_factory
        self._profiles = profiles

    def dispatch(self, request: DispatchRequest) -> DispatchReceipt:
        try:
            profile = self._profiles[request.profile_id]
        except KeyError as exc:
            raise ContractError(f"profile is not allowlisted: {request.profile_id}") from exc
        credentials = self._assume_role(profile, request)
        payload = {
            "run_id": request.run_id,
            "logical_run_key": request.logical_run_key,
            "plan_digest": request.plan_digest,
            "job_id": request.job_id,
            "attempt": request.attempt,
            "fencing_token": request.fencing_token,
            "dispatch_key": request.dispatch_key,
            "output_prefix": request.output_prefix,
            "output_uri": f"s3://{profile.manifest_bucket}/{request.output_prefix}",
            "artifact_digest": request.artifact_digest,
            "quality_policy": list(request.quality_policy),
            "cost_budget_usd": request.cost_budget_usd,
            "workload_account": profile.account_label,
            "manifest_bucket": profile.manifest_bucket,
            "signer_key_arn": profile.signer_key_arn,
        }
        if profile.adapter_type is AdapterType.LAMBDA:
            client = self._client_factory("lambda", credentials, profile.region)
            response = client.invoke(
                FunctionName=profile.target,
                InvocationType="RequestResponse",
                Payload=json.dumps(payload, sort_keys=True).encode(),
            )
            if response.get("FunctionError"):
                raise RuntimeError(f"workload Lambda failed: {response['FunctionError']}")
            external_id = str(response["ResponseMetadata"]["RequestId"])
            response_payload = response.get("Payload")
            if response_payload is None:
                payload_bytes = b"{}"
            elif isinstance(response_payload, bytes | bytearray):
                payload_bytes = bytes(response_payload)
            else:
                reader = getattr(response_payload, "read", None)
                if not callable(reader):
                    raise RuntimeError("workload Lambda returned an unreadable payload")
                payload_bytes = bytes(reader())
            result = json.loads(payload_bytes.decode())
        elif profile.adapter_type is AdapterType.GLUE:
            client = self._client_factory("glue", credentials, profile.region)
            arguments = {
                f"--{key.replace('_', '-')}": (
                    json.dumps(value, sort_keys=True)
                    if isinstance(value, list | dict)
                    else str(value)
                )
                for key, value in payload.items()
            }
            existing = client.get_job_runs(JobName=profile.target, MaxResults=100)
            matching_runs = [
                run
                for run in existing.get("JobRuns", [])
                if run.get("Arguments", {}).get("--dispatch-key") == request.dispatch_key
            ]
            if matching_runs:
                external_id = str(matching_runs[0]["Id"])
            else:
                response = client.start_job_run(JobName=profile.target, Arguments=arguments)
                external_id = str(response["JobRunId"])
            result = None
        else:
            client = self._client_factory("emr-serverless", credentials, profile.region)
            if profile.execution_role_arn is None:
                raise ContractError("EMR execution profile lost its required runtime role")
            response = client.start_job_run(
                applicationId=profile.target,
                executionRoleArn=profile.execution_role_arn,
                clientToken=request.dispatch_key,
                jobDriver={
                    "sparkSubmit": {
                        "entryPoint": (f"s3://{profile.artifact_bucket}/jobs/heavy_transform.py"),
                        "entryPointArguments": [json.dumps(payload, sort_keys=True)],
                        "sparkSubmitParameters": (
                            f"--py-files s3://{profile.artifact_bucket}/jobs/spark_jobs.zip"
                        ),
                    }
                },
                executionTimeoutMinutes=120,
                name=f"{request.job_id}-attempt-{request.attempt}",
            )
            external_id = str(response["jobRunId"])
            result = None
        return DispatchReceipt(profile.adapter_type, external_id, profile.profile_id, result)

    def _assume_role(self, profile: ExecutionProfile, request: DispatchRequest) -> dict[str, str]:
        response = self._sts.assume_role(
            RoleArn=profile.role_arn,
            RoleSessionName=f"dataops-{request.job_id[:32]}-{request.attempt}",
            ExternalId=request.dispatch_key,
            DurationSeconds=900,
        )
        raw = response["Credentials"]
        return {
            "aws_access_key_id": str(raw["AccessKeyId"]),
            "aws_secret_access_key": str(raw["SecretAccessKey"]),
            "aws_session_token": str(raw["SessionToken"]),
        }
