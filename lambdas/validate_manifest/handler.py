from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import boto3

from dataops_factory.aws.dispatch import ExecutionProfile
from dataops_factory.aws.manifest_validation import ManifestBinding, validate_bound_manifest


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Fail closed unless the exact locked object version and KMS signature verify."""

    workload_result = event["workload_result"]
    profiles_raw = json.loads(os.environ["EXECUTION_PROFILES"])
    profile_id = str(event["profile_id"])
    if profile_id not in profiles_raw:
        raise ValueError("execution profile is not allowlisted")
    profile = ExecutionProfile.from_dict(profile_id, profiles_raw[profile_id])
    bucket = str(workload_result["manifest_bucket"])
    signer_key_arn = str(workload_result["signer_key_arn"])
    if bucket not in set(json.loads(os.environ["MANIFEST_BUCKETS"])):
        raise ValueError("manifest bucket is not bound to the control-plane allowlist")
    if signer_key_arn not in set(json.loads(os.environ["SIGNER_KEY_ARNS"])):
        raise ValueError("manifest signer is not bound to the control-plane allowlist")
    if bucket != profile.manifest_bucket or signer_key_arn != profile.signer_key_arn:
        raise ValueError("manifest location or signer differs from the execution profile")
    response = boto3.client("s3").get_object(
        Bucket=bucket,
        Key=str(workload_result["manifest_key"]),
        VersionId=str(workload_result["manifest_version"]),
    )
    if response.get("ObjectLockMode") not in {"GOVERNANCE", "COMPLIANCE"}:
        raise ValueError("accepted manifest version is not protected by Object Lock")
    manifest = json.loads(response["Body"].read())
    signed_fields = {
        "manifest_digest_hex",
        "signature_hex",
        "signer_key_arn",
        "signing_algorithm",
    }
    unsigned = {key: value for key, value in manifest.items() if key not in signed_fields}
    digest = hashlib.sha256(_canonical(unsigned)).digest()
    if digest.hex() != manifest["manifest_digest_hex"]:
        raise ValueError("manifest digest differs from its content")
    if manifest["signer_key_arn"] != signer_key_arn:
        raise ValueError("manifest was signed by an unexpected key")
    verification = boto3.client("kms").verify(
        KeyId=signer_key_arn,
        Message=digest,
        MessageType="DIGEST",
        Signature=bytes.fromhex(manifest["signature_hex"]),
        SigningAlgorithm=manifest["signing_algorithm"],
    )
    if not verification["SignatureValid"]:
        raise ValueError("manifest signature validation failed")
    binding = ManifestBinding(
        run_id=str(event["run_id"]),
        logical_run_key=str(event["logical_run_key"]),
        plan_digest=str(event["plan_digest"]),
        job_id=str(event["job_id"]),
        attempt=int(event["attempt"]),
        fencing_token=int(event["fencing_token"]),
        dispatch_key=str(event["dispatch_key"]),
        artifact_digest=str(event["artifact_digest"]),
        workload_account=profile.account_label,
        output_uri=f"s3://{profile.manifest_bucket}/{event['output_prefix']}",
        quality_policy=tuple(str(value) for value in event["quality_policy"]),
        cost_budget_usd=float(event["cost_budget_usd"]),
    )
    validate_bound_manifest(manifest, binding)
    table_name = os.environ["RUN_LEDGER_TABLE"]
    boto3.client("dynamodb").transact_write_items(
        TransactItems=[
            {
                "ConditionCheck": {
                    "TableName": table_name,
                    "Key": {
                        "pk": {"S": f"RUN#{binding.run_id}"},
                        "sk": {"S": "META"},
                    },
                    "ConditionExpression": (
                        "fencing_token = :fence AND plan_digest = :plan "
                        "AND #state IN (:leased, :running)"
                    ),
                    "ExpressionAttributeNames": {"#state": "state"},
                    "ExpressionAttributeValues": {
                        ":fence": {"N": str(binding.fencing_token)},
                        ":plan": {"S": binding.plan_digest},
                        ":leased": {"S": "LEASED"},
                        ":running": {"S": "RUNNING"},
                    },
                }
            },
            {
                "Put": {
                    "TableName": table_name,
                    "Item": {
                        "pk": {"S": f"RUN#{binding.run_id}"},
                        "sk": {"S": f"JOB#{binding.job_id}"},
                        "manifest_digest": {"S": digest.hex()},
                        "manifest_bucket": {"S": bucket},
                        "manifest_key": {"S": str(workload_result["manifest_key"])},
                        "manifest_version": {"S": str(workload_result["manifest_version"])},
                        "fencing_token": {"N": str(binding.fencing_token)},
                        "accepted_at": {"N": str(int(time.time()))},
                    },
                    "ConditionExpression": (
                        "attribute_not_exists(pk) OR "
                        "(manifest_digest = :digest AND fencing_token = :fence)"
                    ),
                    "ExpressionAttributeValues": {
                        ":digest": {"S": digest.hex()},
                        ":fence": {"N": str(binding.fencing_token)},
                    },
                }
            },
        ],
    )
    return {
        "validated": True,
        "job_id": binding.job_id,
        "manifest_version": str(workload_result["manifest_version"]),
        "manifest_digest_hex": digest.hex(),
    }
