from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class WorkloadArguments:
    run_id: str
    logical_run_key: str
    plan_digest: str
    job_id: str
    attempt: int
    fencing_token: int
    dispatch_key: str
    output_prefix: str
    output_uri: str
    artifact_digest: str
    quality_policy: tuple[str, ...]
    cost_budget_usd: float
    workload_account: str
    manifest_bucket: str
    signer_key_arn: str

    @classmethod
    def from_json(cls, value: str) -> WorkloadArguments:
        raw = json.loads(value)
        return cls(
            run_id=str(raw["run_id"]),
            logical_run_key=str(raw["logical_run_key"]),
            plan_digest=str(raw["plan_digest"]),
            job_id=str(raw["job_id"]),
            attempt=int(raw["attempt"]),
            fencing_token=int(raw["fencing_token"]),
            dispatch_key=str(raw["dispatch_key"]),
            output_prefix=str(raw["output_prefix"]),
            output_uri=str(raw["output_uri"]),
            artifact_digest=str(raw["artifact_digest"]),
            quality_policy=tuple(str(value) for value in raw["quality_policy"]),
            cost_budget_usd=float(raw["cost_budget_usd"]),
            workload_account=str(raw["workload_account"]),
            manifest_bucket=str(raw["manifest_bucket"]),
            signer_key_arn=str(raw["signer_key_arn"]),
        )

    @classmethod
    def from_argv(cls, values: list[str] | None = None) -> WorkloadArguments:
        """Parse the explicit arguments sent by the Glue dispatcher.

        Unknown Glue-owned arguments are deliberately ignored; every application
        argument is still required exactly once.
        """

        tokens = list(sys.argv[1:] if values is None else values)
        expected = {
            "run-id",
            "logical-run-key",
            "plan-digest",
            "job-id",
            "attempt",
            "fencing-token",
            "dispatch-key",
            "output-prefix",
            "output-uri",
            "artifact-digest",
            "quality-policy",
            "cost-budget-usd",
            "workload-account",
            "manifest-bucket",
            "signer-key-arn",
        }
        parsed: dict[str, str] = {}
        position = 0
        while position < len(tokens):
            token = tokens[position]
            if not token.startswith("--"):
                position += 1
                continue
            key = token[2:]
            if position + 1 >= len(tokens) or tokens[position + 1].startswith("--"):
                if key in expected:
                    raise ValueError(f"missing value for --{key}")
                position += 1
                continue
            if key in expected:
                if key in parsed:
                    raise ValueError(f"duplicate argument --{key}")
                parsed[key] = tokens[position + 1]
            position += 2
        missing = sorted(expected - parsed.keys())
        if missing:
            raise ValueError(f"missing workload arguments: {missing}")
        normalized: dict[str, object] = {
            key.replace("-", "_"): value for key, value in parsed.items()
        }
        normalized["quality_policy"] = json.loads(str(normalized["quality_policy"]))
        return cls.from_json(json.dumps(normalized))


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def publish_manifest(
    arguments: WorkloadArguments,
    row_count: int,
    output_files: list[str],
    quality: dict[str, bool],
    cost_usd: float,
    boto3_module: Any,
) -> dict[str, str]:
    completed_at = datetime.now(UTC).isoformat()
    manifest = {
        "run_id": arguments.run_id,
        "logical_run_key": arguments.logical_run_key,
        "plan_digest": arguments.plan_digest,
        "job_id": arguments.job_id,
        "attempt": arguments.attempt,
        "fencing_token": arguments.fencing_token,
        "dispatch_key": arguments.dispatch_key,
        "artifact_digest": arguments.artifact_digest,
        "workload_account": arguments.workload_account,
        "engine": "spark",
        "input_refs": [],
        "output_refs": sorted(output_files),
        "row_count": row_count,
        "quality_results": quality,
        "lineage": [],
        "cost_usd": cost_usd,
        "completed_at": completed_at,
    }
    digest = hashlib.sha256(canonical_json(manifest)).digest()
    kms = boto3_module.client("kms")
    signature = kms.sign(
        KeyId=arguments.signer_key_arn,
        Message=digest,
        MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256",
    )["Signature"]
    key = (
        f"manifests/{arguments.run_id}/{arguments.job_id}/"
        f"{arguments.attempt}/{arguments.fencing_token}.json"
    )
    body = canonical_json(
        {
            **manifest,
            "manifest_digest_hex": digest.hex(),
            "signature_hex": bytes(signature).hex(),
            "signer_key_arn": arguments.signer_key_arn,
            "signing_algorithm": "ECDSA_SHA_256",
        }
    )
    response = boto3_module.client("s3").put_object(
        Bucket=arguments.manifest_bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
    )
    result = {
        "manifest_key": key,
        "manifest_version": str(response["VersionId"]),
        "manifest_digest_hex": digest.hex(),
        "signature_hex": bytes(signature).hex(),
        "signer_key_arn": arguments.signer_key_arn,
        "signing_algorithm": "ECDSA_SHA_256",
    }
    return result
