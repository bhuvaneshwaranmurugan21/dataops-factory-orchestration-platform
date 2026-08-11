from __future__ import annotations

import argparse
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def execute_one(
    lambda_client: Any,
    step_functions: Any,
    register_function: str,
    state_machine_arn: str,
    fixture: dict[str, Any],
    batch_id: str,
    ordinal: int,
) -> dict[str, Any]:
    logical_key = f"benchmark/{batch_id}/{ordinal:05d}"
    registration = lambda_client.invoke(
        FunctionName=register_function,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "pipeline_id": fixture["pipeline_id"],
                "logical_run_key": logical_key,
                "plan_digest": fixture["plan_digest"],
                "registry_commit": fixture["registry_commit"],
                "owner": f"stage-benchmark-{batch_id}",
            },
            sort_keys=True,
        ).encode(),
    )
    payload = json.loads(registration["Payload"].read())
    run_id = str(payload["run_id"])
    fence = int(payload["fencing_token"])
    job_id = str(fixture["job_id"])
    request = {
        **fixture,
        "logical_run_key": logical_key,
        "run_id": run_id,
        "fencing_token": fence,
        "attempt": 1,
        "dispatch_key": f"{run_id}/{job_id}/1/{fixture['plan_digest']}",
        "output_prefix": f"outputs/{run_id}/{job_id}/1/{fence}/",
    }
    request.pop("pipeline_id")
    name = f"bench-{batch_id}-{ordinal:05d}-{uuid4().hex[:8]}"
    execution_arn = step_functions.start_execution(
        stateMachineArn=state_machine_arn,
        name=name,
        input=json.dumps(request, sort_keys=True),
    )["executionArn"]
    started = time.monotonic()
    while True:
        response = step_functions.describe_execution(executionArn=execution_arn)
        status = str(response["status"])
        if status in {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}:
            return {
                "ordinal": ordinal,
                "execution_arn": execution_arn,
                "status": status,
                "duration_seconds": round(time.monotonic() - started, 3),
            }
        time.sleep(2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a real AWS workload-stage benchmark")
    parser.add_argument("--register-function", required=True)
    parser.add_argument("--state-machine-arn", required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--executions", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.executions < 1 or args.concurrency < 1:
        raise ValueError("executions and concurrency must be positive")
    import boto3

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    batch_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lambda_client = boto3.client("lambda")
    step_functions = boto3.client("stepfunctions")
    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                execute_one,
                lambda_client,
                step_functions,
                args.register_function,
                args.state_machine_arn,
                fixture,
                batch_id,
                ordinal,
            )
            for ordinal in range(args.executions)
        ]
        for future in as_completed(futures):
            results.append(future.result())
    durations = [float(result["duration_seconds"]) for result in results]
    summary = {
        "classification": "VERIFIED_STAGE_EXECUTION",
        "generated_at": datetime.now(UTC).isoformat(),
        "batch_id": batch_id,
        "fixture": fixture,
        "executions": len(results),
        "concurrency": args.concurrency,
        "succeeded": sum(result["status"] == "SUCCEEDED" for result in results),
        "duration_seconds": {
            "median": round(statistics.median(durations), 3),
            "p95": round(_percentile(durations, 0.95), 3),
            "max": round(max(durations), 3),
        },
        "results": sorted(results, key=lambda result: result["ordinal"]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if summary["succeeded"] != summary["executions"]:
        raise RuntimeError("at least one stage benchmark execution failed")


if __name__ == "__main__":
    main()
