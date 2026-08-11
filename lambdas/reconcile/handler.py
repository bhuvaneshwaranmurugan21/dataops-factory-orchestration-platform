from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3

from dataops_factory.aws.dispatch import ExecutionProfile
from dataops_factory.aws.reconcile import ExternalState, reconcile_job


def _credentials(profile: ExecutionProfile, dispatch_key: str) -> dict[str, str]:
    raw = boto3.client("sts").assume_role(
        RoleArn=profile.role_arn,
        RoleSessionName="dataops-reconciler",
        ExternalId=dispatch_key,
        DurationSeconds=900,
    )["Credentials"]
    return {
        "aws_access_key_id": str(raw["AccessKeyId"]),
        "aws_secret_access_key": str(raw["SecretAccessKey"]),
        "aws_session_token": str(raw["SessionToken"]),
    }


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    """Reconcile missing events from the authoritative managed-engine API."""

    dispatch_key = str(event["dispatch_key"])
    table = boto3.resource("dynamodb").Table(os.environ["CALLBACK_TABLE"])
    item = table.get_item(Key={"dispatch_key": dispatch_key}, ConsistentRead=True).get("Item")
    if item is None:
        raise KeyError("unknown dispatch key")
    boto3.resource("dynamodb").Table(os.environ["RUN_LEDGER_TABLE"]).update_item(
        Key={"pk": f"RUN#{item['run_id']}", "sk": "META"},
        UpdateExpression="SET lease_expires_at = :expires",
        ConditionExpression="fencing_token = :fence",
        ExpressionAttributeValues={
            ":expires": int(time.time()) + int(os.environ.get("RUN_LEASE_SECONDS", "300")),
            ":fence": int(item["fencing_token"]),
        },
    )
    profiles_raw = json.loads(os.environ["EXECUTION_PROFILES"])
    profile = ExecutionProfile.from_dict(item["profile_id"], profiles_raw[item["profile_id"]])
    step_functions = boto3.client("stepfunctions")
    if profile.adapter_type.value == "lambda":
        if "callback_output" not in item:
            return {"dispatch_key": dispatch_key, "state": "AMBIGUOUS_RETRY_REQUIRED"}
        step_functions.send_task_success(
            taskToken=item["task_token"], output=str(item["callback_output"])
        )
        table.update_item(
            Key={"dispatch_key": dispatch_key},
            UpdateExpression="SET #state = :done, updated_at = :now REMOVE task_token",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":done": "CALLBACK_SENT", ":now": int(time.time())},
        )
        return {"dispatch_key": dispatch_key, "state": "CALLBACK_SENT"}
    credentials = _credentials(profile, dispatch_key)
    service = "glue" if profile.adapter_type.value == "glue" else "emr-serverless"
    client = boto3.client(service, region_name=profile.region, **credentials)
    result = reconcile_job(profile.adapter_type, client, item["external_id"], profile.target)
    if result.state is ExternalState.RUNNING:
        step_functions.send_task_heartbeat(taskToken=item["task_token"])
        table.update_item(
            Key={"dispatch_key": dispatch_key},
            UpdateExpression="SET updated_at = :now",
            ExpressionAttributeValues={":now": int(time.time())},
        )
    elif result.state is ExternalState.SUCCEEDED:
        manifest_key = (
            f"manifests/{item['run_id']}/{item['job_id']}/"
            f"{item['attempt']}/{item['fencing_token']}.json"
        )
        manifest = boto3.client("s3", region_name=profile.region, **credentials).head_object(
            Bucket=profile.manifest_bucket, Key=manifest_key
        )
        output = {
            "dispatch_key": dispatch_key,
            "external_id": item["external_id"],
            "manifest_bucket": profile.manifest_bucket,
            "manifest_key": manifest_key,
            "manifest_version": manifest["VersionId"],
            "signer_key_arn": profile.signer_key_arn,
            "workload_account": profile.account_label,
        }
        step_functions.send_task_success(
            taskToken=item["task_token"], output=json.dumps(output, sort_keys=True)
        )
        table.update_item(
            Key={"dispatch_key": dispatch_key},
            UpdateExpression="SET #state = :done, updated_at = :now REMOVE task_token",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":done": "CALLBACK_SENT", ":now": int(time.time())},
        )
    elif result.state is ExternalState.FAILED:
        step_functions.send_task_failure(
            taskToken=item["task_token"],
            error="ManagedWorkloadFailed",
            cause=f"{profile.adapter_type.value}:{result.raw_state}",
        )
        table.update_item(
            Key={"dispatch_key": dispatch_key},
            UpdateExpression="SET #state = :failed, updated_at = :now REMOVE task_token",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":failed": "FAILED", ":now": int(time.time())},
        )
    return {"dispatch_key": dispatch_key, "state": result.state.value}
