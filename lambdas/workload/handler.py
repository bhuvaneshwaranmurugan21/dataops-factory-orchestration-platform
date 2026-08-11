from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def handler(event: dict[str, Any], _context: Any) -> dict[str, str]:
    required = {
        "run_id",
        "logical_run_key",
        "plan_digest",
        "job_id",
        "attempt",
        "fencing_token",
        "dispatch_key",
        "output_prefix",
        "output_uri",
        "artifact_digest",
        "quality_policy",
        "cost_budget_usd",
        "workload_account",
        "manifest_bucket",
        "signer_key_arn",
    }
    missing = required - event.keys()
    if missing:
        raise ValueError(f"workload request is incomplete: {sorted(missing)}")
    manifest_key = (
        f"manifests/{event['run_id']}/{event['job_id']}/"
        f"{event['attempt']}/{event['fencing_token']}.json"
    )
    s3 = boto3.client("s3")
    try:
        existing = s3.head_object(Bucket=event["manifest_bucket"], Key=manifest_key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
            raise
    else:
        return {
            "manifest_bucket": str(event["manifest_bucket"]),
            "manifest_key": manifest_key,
            "manifest_version": str(existing["VersionId"]),
            "signer_key_arn": str(event["signer_key_arn"]),
            "workload_account": str(event["workload_account"]),
        }
    output_key = f"{event['output_prefix']}result.json"
    result_body = _canonical(
        {
            "run_id": event["run_id"],
            "job_id": event["job_id"],
            "classification": "LIGHTWEIGHT_GOVERNED_WORKLOAD",
        }
    )
    s3.put_object(
        Bucket=event["manifest_bucket"], Key=output_key, Body=result_body, IfNoneMatch="*"
    )
    manifest = {
        "run_id": event["run_id"],
        "logical_run_key": event["logical_run_key"],
        "plan_digest": event["plan_digest"],
        "job_id": event["job_id"],
        "attempt": int(event["attempt"]),
        "fencing_token": int(event["fencing_token"]),
        "dispatch_key": event["dispatch_key"],
        "artifact_digest": event["artifact_digest"],
        "workload_account": event["workload_account"],
        "engine": "lambda",
        "input_refs": [],
        "output_refs": [f"s3://{event['manifest_bucket']}/{output_key}"],
        "row_count": 1,
        "quality_results": {str(policy): True for policy in event["quality_policy"]},
        "lineage": [],
        "cost_usd": 0.0,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    digest = hashlib.sha256(_canonical(manifest)).digest()
    signature = boto3.client("kms").sign(
        KeyId=event["signer_key_arn"],
        Message=digest,
        MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256",
    )["Signature"]
    response = s3.put_object(
        Bucket=event["manifest_bucket"],
        Key=manifest_key,
        Body=_canonical(
            {
                **manifest,
                "manifest_digest_hex": digest.hex(),
                "signature_hex": bytes(signature).hex(),
                "signer_key_arn": event["signer_key_arn"],
                "signing_algorithm": "ECDSA_SHA_256",
            }
        ),
        ContentType="application/json",
    )
    return {
        "manifest_bucket": str(event["manifest_bucket"]),
        "manifest_key": manifest_key,
        "manifest_version": str(response["VersionId"]),
        "manifest_digest_hex": digest.hex(),
        "signer_key_arn": str(event["signer_key_arn"]),
        "workload_account": str(event["workload_account"]),
    }
