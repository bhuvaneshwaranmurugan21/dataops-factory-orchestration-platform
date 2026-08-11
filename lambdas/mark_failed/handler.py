from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3


def handler(event: dict[str, Any], _context: Any) -> dict[str, str]:
    run_id = str(event["run_id"])
    token = int(event["fencing_token"])
    table = boto3.resource("dynamodb").Table(os.environ["RUN_LEDGER_TABLE"])
    if event.get("finalize_run") is True:
        table.update_item(
            Key={"pk": f"RUN#{run_id}", "sk": "META"},
            UpdateExpression="SET #state = :failed",
            ConditionExpression="fencing_token = :token AND #state <> :published",
            ExpressionAttributeNames={"#state": "state"},
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":published": "PUBLISHED",
                ":token": token,
            },
        )
        return {"run_id": run_id, "state": "FAILED"}
    job_id = str(event["job_id"])
    attempt = int(event["attempt"])
    table.put_item(
        Item={
            "pk": f"RUN#{run_id}",
            "sk": f"FAILURE#{job_id}#{attempt}",
            "job_id": job_id,
            "attempt": attempt,
            "fencing_token": token,
            "mandatory": bool(event["mandatory"]),
            "failure": json.dumps(event.get("failure", {}), sort_keys=True),
            "recorded_at": int(time.time()),
        },
    )
    return {"run_id": run_id, "state": "ATTEMPT_FAILED"}
