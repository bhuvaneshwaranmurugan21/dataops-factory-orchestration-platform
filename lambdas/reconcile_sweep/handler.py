from __future__ import annotations

import json
import os
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key


def handler(_event: dict[str, Any], _context: Any) -> dict[str, int]:
    table = boto3.resource("dynamodb").Table(os.environ["CALLBACK_TABLE"])
    response = table.query(
        IndexName="state-updated-index",
        KeyConditionExpression=Key("state").eq("RUNNING"),
        Limit=int(os.environ.get("RECONCILE_BATCH_SIZE", "100")),
    )
    function_name = os.environ["RECONCILE_FUNCTION"]
    client = boto3.client("lambda")
    for item in response.get("Items", []):
        client.invoke(
            FunctionName=function_name,
            InvocationType="Event",
            Payload=json.dumps({"dispatch_key": item["dispatch_key"]}).encode(),
        )
    return {"scheduled": len(response.get("Items", []))}
