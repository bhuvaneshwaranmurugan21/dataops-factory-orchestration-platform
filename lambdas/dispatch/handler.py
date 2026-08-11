from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from dataops_factory.aws.dispatch import AwsDispatcher, DispatchRequest, ExecutionProfile


def _client_factory(service: str, credentials: dict[str, str], region: str) -> Any:
    return boto3.client(service, region_name=region, **credentials)


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    request_event = event["request"]
    task_token = str(event["task_token"])
    profiles_raw = json.loads(os.environ["EXECUTION_PROFILES"])
    profiles = {
        profile_id: ExecutionProfile.from_dict(profile_id, raw)
        for profile_id, raw in profiles_raw.items()
    }
    request = DispatchRequest(
        run_id=str(request_event["run_id"]),
        logical_run_key=str(request_event["logical_run_key"]),
        plan_digest=str(request_event["plan_digest"]),
        job_id=str(request_event["job_id"]),
        attempt=int(request_event["attempt"]),
        fencing_token=int(request_event["fencing_token"]),
        dispatch_key=str(request_event["dispatch_key"]),
        output_prefix=str(request_event["output_prefix"]),
        artifact_digest=str(request_event["artifact_digest"]),
        quality_policy=tuple(str(value) for value in request_event["quality_policy"]),
        cost_budget_usd=float(request_event["cost_budget_usd"]),
        profile_id=str(request_event["profile_id"]),
    )
    if request.profile_id not in profiles:
        raise ValueError("execution profile is not allowlisted")
    profile = profiles[request.profile_id]
    lease_seconds = int(os.environ.get("RUN_LEASE_SECONDS", "300"))
    boto3.resource("dynamodb").Table(os.environ["RUN_LEDGER_TABLE"]).update_item(
        Key={"pk": f"RUN#{request.run_id}", "sk": "META"},
        UpdateExpression="SET lease_expires_at = :expires, #state = :running",
        ConditionExpression="fencing_token = :fence AND plan_digest = :plan",
        ExpressionAttributeNames={"#state": "state"},
        ExpressionAttributeValues={
            ":expires": int(time.time()) + lease_seconds,
            ":running": "RUNNING",
            ":fence": request.fencing_token,
            ":plan": request.plan_digest,
        },
    )
    table = boto3.resource("dynamodb").Table(os.environ["CALLBACK_TABLE"])
    now = int(time.time())
    callback_item = {
        "dispatch_key": request.dispatch_key,
        "task_token": task_token,
        "run_id": request.run_id,
        "job_id": request.job_id,
        "attempt": request.attempt,
        "fencing_token": request.fencing_token,
        "adapter_type": profile.adapter_type.value,
        "profile_id": request.profile_id,
        "workload_account": profile.account_label,
        "manifest_bucket": profile.manifest_bucket,
        "signer_key_arn": profile.signer_key_arn,
        "state": "DISPATCHING",
        "updated_at": now,
        "expires_at": now + int(os.environ.get("CALLBACK_RETENTION_SECONDS", "604800")),
    }
    try:
        table.put_item(
            Item=callback_item,
            ConditionExpression="attribute_not_exists(dispatch_key)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"dispatch_key": request.dispatch_key}, ConsistentRead=True)[
            "Item"
        ]
        identity = {
            "run_id": request.run_id,
            "job_id": request.job_id,
            "attempt": request.attempt,
            "fencing_token": request.fencing_token,
            "profile_id": request.profile_id,
        }
        if any(existing.get(field) != value for field, value in identity.items()):
            raise ValueError("dispatch key is already bound to a different workload") from exc
        state = str(existing["state"])
        if state == "CALLBACK_SENT":
            return {
                "adapter_type": profile.adapter_type.value,
                "external_id": str(existing["external_id"]),
                "profile_id": request.profile_id,
                "callback_sent": True,
                "recovered": True,
            }
        if state == "RUNNING":
            table.update_item(
                Key={"dispatch_key": request.dispatch_key},
                UpdateExpression="SET task_token = :token, expires_at = :expires",
                ExpressionAttributeValues={
                    ":token": task_token,
                    ":expires": callback_item["expires_at"],
                },
            )
            if profile.adapter_type.value == "lambda" and "callback_output" in existing:
                boto3.client("stepfunctions").send_task_success(
                    taskToken=task_token,
                    output=str(existing["callback_output"]),
                )
                table.update_item(
                    Key={"dispatch_key": request.dispatch_key},
                    UpdateExpression="SET #state = :done REMOVE task_token",
                    ExpressionAttributeNames={"#state": "state"},
                    ExpressionAttributeValues={":done": "CALLBACK_SENT"},
                )
            return {
                "adapter_type": profile.adapter_type.value,
                "external_id": str(existing["external_id"]),
                "profile_id": request.profile_id,
                "callback_sent": profile.adapter_type.value == "lambda",
                "recovered": True,
            }
        if state not in {"DISPATCHING", "DISPATCH_FAILED"}:
            raise ValueError(f"dispatch cannot be resumed from state {state}") from exc
        table.update_item(
            Key={"dispatch_key": request.dispatch_key},
            UpdateExpression=(
                "SET task_token = :token, #state = :dispatching, updated_at = :now, "
                "expires_at = :expires"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":token": task_token,
                ":dispatching": "DISPATCHING",
                ":now": now,
                ":expires": callback_item["expires_at"],
            },
        )
    try:
        receipt = AwsDispatcher(boto3.client("sts"), _client_factory, profiles).dispatch(request)
    except Exception:
        table.update_item(
            Key={"dispatch_key": request.dispatch_key},
            UpdateExpression="SET #state = :failed",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={":failed": "DISPATCH_FAILED"},
        )
        raise
    running_values: dict[str, Any] = {
        ":running": "RUNNING",
        ":dispatching": "DISPATCHING",
        ":external_id": receipt.external_id,
        ":updated_at": int(time.time()),
    }
    running_expression = (
        "SET #state = :running, external_id = :external_id, updated_at = :updated_at"
    )
    if receipt.result is not None:
        running_expression += ", callback_output = :callback_output"
        running_values[":callback_output"] = json.dumps(receipt.result, sort_keys=True)
    table.update_item(
        Key={"dispatch_key": request.dispatch_key},
        UpdateExpression=running_expression,
        ConditionExpression="#state = :dispatching",
        ExpressionAttributeNames={"#state": "state"},
        ExpressionAttributeValues=running_values,
    )
    if receipt.adapter_type.value == "lambda":
        if receipt.result is None:
            raise ValueError("synchronous workload Lambda returned no result manifest")
        boto3.client("stepfunctions").send_task_success(
            taskToken=task_token,
            output=json.dumps(receipt.result, sort_keys=True),
        )
        table.update_item(
            Key={"dispatch_key": request.dispatch_key},
            UpdateExpression="SET #state = :done, updated_at = :updated_at REMOVE task_token",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":done": "CALLBACK_SENT",
                ":updated_at": int(time.time()),
            },
        )
    return {
        "adapter_type": receipt.adapter_type.value,
        "external_id": receipt.external_id,
        "profile_id": receipt.profile_id,
        "callback_sent": receipt.adapter_type.value == "lambda",
    }
