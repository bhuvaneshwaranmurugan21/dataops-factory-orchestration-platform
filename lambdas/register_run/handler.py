from __future__ import annotations

import os
import time
import uuid
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _run_id(pipeline_id: str, logical_key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{pipeline_id}/{logical_key}"))


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    pipeline_id = str(event["pipeline_id"])
    logical_key = str(event["logical_run_key"])
    plan_digest = str(event["plan_digest"])
    registry_commit = str(event["registry_commit"])
    owner = str(event.get("owner") or context.aws_request_id)
    run_id = _run_id(pipeline_id, logical_key)
    table = boto3.resource("dynamodb").Table(os.environ["RUN_LEDGER_TABLE"])
    now = int(time.time())
    expires = now + int(os.environ.get("RUN_LEASE_SECONDS", "300"))
    item = {
        "pk": f"RUN#{run_id}",
        "sk": "META",
        "run_id": run_id,
        "pipeline_id": pipeline_id,
        "logical_run_key": logical_key,
        "plan_digest": plan_digest,
        "registry_commit": registry_commit,
        "state": "PLANNED",
        "lease_epoch": 0,
        "fencing_token": 0,
    }
    try:
        table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"pk": item["pk"], "sk": "META"}, ConsistentRead=True)["Item"]
        if existing["plan_digest"] != plan_digest or existing["registry_commit"] != registry_commit:
            raise ValueError("logical run already exists with a different immutable plan") from exc
        if existing.get("lease_owner") == owner and int(existing.get("lease_expires_at", 0)) > now:
            return {
                "run_id": run_id,
                "fencing_token": int(existing["fencing_token"]),
                "lease_epoch": int(existing["lease_epoch"]),
            }

    try:
        result = table.update_item(
            Key={"pk": item["pk"], "sk": "META"},
            UpdateExpression=(
                "SET lease_owner = :owner, lease_expires_at = :expires, #state = :leased "
                "ADD lease_epoch :one, fencing_token :one"
            ),
            ConditionExpression=(
                "attribute_not_exists(lease_expires_at) OR lease_expires_at < :now "
                "OR lease_owner = :owner"
            ),
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":owner": owner,
                ":expires": expires,
                ":leased": "LEASED",
                ":now": now,
                ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise RuntimeError("run lease is held by another scheduler") from exc
        raise
    attributes = result["Attributes"]
    return {
        "run_id": run_id,
        "fencing_token": int(attributes["fencing_token"]),
        "lease_epoch": int(attributes["lease_epoch"]),
    }
