from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def handler(event: dict[str, Any], _context: Any) -> dict[str, str]:
    allowlist = set(json.loads(os.environ["CONTROL_JOB_ALLOWLIST"]))
    job_id = str(event["job_id"])
    if job_id not in allowlist:
        raise ValueError("control workload is not allowlisted")
    key = f"manifests/{event['run_id']}/{job_id}/{event['attempt']}/{event['fencing_token']}.json"
    bucket = os.environ["CONTROL_MANIFEST_BUCKET"]
    s3 = boto3.client("s3")
    try:
        existing = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"404", "NoSuchKey", "NotFound"}:
            raise
    else:
        return {
            "manifest_bucket": bucket,
            "manifest_key": key,
            "manifest_version": str(existing["VersionId"]),
            "signer_key_arn": os.environ["CONTROL_SIGNER_KEY_ARN"],
            "workload_account": "control-plane",
        }
    manifest = {
        "run_id": event["run_id"],
        "logical_run_key": event["logical_run_key"],
        "plan_digest": event["plan_digest"],
        "job_id": job_id,
        "attempt": int(event["attempt"]),
        "fencing_token": int(event["fencing_token"]),
        "dispatch_key": event["dispatch_key"],
        "artifact_digest": event["artifact_digest"],
        "workload_account": "control-plane",
        "engine": "lambda",
        "input_refs": [],
        "output_refs": [f"control://{event['run_id']}/{job_id}"],
        "row_count": 1,
        "quality_results": {str(policy): True for policy in event["quality_policy"]},
        "lineage": [],
        "cost_usd": 0.0,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    digest = hashlib.sha256(_canonical(manifest)).digest()
    signer_key_arn = os.environ["CONTROL_SIGNER_KEY_ARN"]
    signature = boto3.client("kms").sign(
        KeyId=signer_key_arn,
        Message=digest,
        MessageType="DIGEST",
        SigningAlgorithm="ECDSA_SHA_256",
    )["Signature"]
    response = s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=_canonical(
            {
                **manifest,
                "manifest_digest_hex": digest.hex(),
                "signature_hex": bytes(signature).hex(),
                "signer_key_arn": signer_key_arn,
                "signing_algorithm": "ECDSA_SHA_256",
            }
        ),
        ContentType="application/json",
    )
    return {
        "manifest_bucket": bucket,
        "manifest_key": key,
        "manifest_version": str(response["VersionId"]),
        "manifest_digest_hex": digest.hex(),
        "signer_key_arn": signer_key_arn,
        "workload_account": "control-plane",
    }
