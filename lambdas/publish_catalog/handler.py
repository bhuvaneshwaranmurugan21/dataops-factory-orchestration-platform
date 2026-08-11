from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any

import boto3

from dataops_factory.aws.redshift_publication import RedshiftDataPublisher


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _accepted_jobs(run_id: str) -> set[str]:
    table = boto3.resource("dynamodb").Table(os.environ["RUN_LEDGER_TABLE"])
    request: dict[str, Any] = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :job)",
        "ExpressionAttributeValues": {":pk": f"RUN#{run_id}", ":job": "JOB#"},
        "ProjectionExpression": "sk",
        "ConsistentRead": True,
    }
    accepted: set[str] = set()
    while True:
        response = table.query(**request)
        accepted.update(str(item["sk"])[4:] for item in response.get("Items", []))
        key = response.get("LastEvaluatedKey")
        if key is None:
            return accepted
        request["ExclusiveStartKey"] = key


def handler(event: dict[str, Any], _context: Any) -> dict[str, str]:
    job_id = str(event["job_id"])
    if job_id not in {"activate_catalog", "audit_publication"}:
        raise ValueError("publication adapter only accepts catalog publication jobs")
    required = set(json.loads(os.environ["REQUIRED_PREPUBLICATION_JOBS"]))
    if job_id == "audit_publication":
        required.add("activate_catalog")
    missing = required - _accepted_jobs(str(event["run_id"]))
    if missing:
        raise ValueError(f"publication blocked by missing accepted jobs: {sorted(missing)}")

    publisher = RedshiftDataPublisher(
        boto3.client("redshift-data"),
        os.environ["REDSHIFT_WORKGROUP_NAME"],
        os.environ["REDSHIFT_DATABASE"],
        secret_arn=os.environ["REDSHIFT_SECRET_ARN"],
    )
    if job_id == "activate_catalog":
        publication = publisher.activate(
            os.environ["PIPELINE_ID"],
            str(event["run_id"]),
            str(event["plan_digest"]),
        )
    else:
        publication = publisher.active(os.environ["PIPELINE_ID"])
        if publication.run_id != event["run_id"] or publication.plan_digest != event["plan_digest"]:
            raise ValueError("publication audit found a different active run or plan")
    output_key = f"{event['output_prefix']}publication-{job_id}.json"
    bucket = os.environ["CONTROL_MANIFEST_BUCKET"]
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=output_key,
        Body=_canonical(
            {
                "pipeline_id": publication.pipeline_id,
                "run_id": publication.run_id,
                "plan_digest": publication.plan_digest,
                "publication_version": publication.version,
            }
        ),
        ContentType="application/json",
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
        "workload_account": "control-plane",
        "engine": "redshift",
        "input_refs": [],
        "output_refs": [f"s3://{bucket}/{output_key}"],
        "row_count": 1,
        "quality_results": {str(policy): True for policy in event["quality_policy"]},
        "lineage": [f"publication-version:{publication.version}"],
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
    manifest_key = (
        f"manifests/{event['run_id']}/{event['job_id']}/"
        f"{event['attempt']}/{event['fencing_token']}.json"
    )
    response = s3.put_object(
        Bucket=bucket,
        Key=manifest_key,
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
        "manifest_key": manifest_key,
        "manifest_version": str(response["VersionId"]),
        "manifest_digest_hex": digest.hex(),
        "signer_key_arn": signer_key_arn,
        "workload_account": "control-plane",
    }
