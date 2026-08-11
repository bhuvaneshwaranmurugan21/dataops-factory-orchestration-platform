from __future__ import annotations

import json
import struct
from pathlib import Path

import hcl2

from dataops_factory.compiler import compile_registry, load_registry

REPOSITORY = Path(__file__).resolve().parents[1]


def main() -> None:
    registry_path = REPOSITORY / "contracts" / "registry" / "jobs.json"
    compiled_path = REPOSITORY / "orchestration" / "generated" / "compiled-plan.json"
    compiled = json.loads(compiled_path.read_text(encoding="utf-8"))
    rebuilt = compile_registry(load_registry(registry_path), compiled["registry_commit"]).to_dict()
    if rebuilt != compiled:
        raise AssertionError("checked-in compiled plan is stale or non-deterministic")

    profiles = set(
        json.loads(
            (REPOSITORY / "config" / "execution_profiles.example.json").read_text(encoding="utf-8")
        )
    )
    referenced_profiles = {job["execution_profile"] for job in compiled["jobs"]}
    if not referenced_profiles <= profiles:
        raise AssertionError(
            f"missing execution profiles: {sorted(referenced_profiles - profiles)}"
        )
    if len(compiled["jobs"]) != 36 or len(compiled["accounts"]) != 5:
        raise AssertionError("reference topology must preserve 36 jobs across five accounts")
    if len(compiled["levels"]) != 12:
        raise AssertionError("reference topology must preserve the reviewed 12-level DAG")
    fixture = json.loads(
        (REPOSITORY / "config" / "stage-benchmark-job.example.json").read_text(encoding="utf-8")
    )
    jobs = {job["job_id"]: job for job in compiled["jobs"]}
    fixture_job = jobs[fixture["job_id"]]
    if fixture["plan_digest"] != compiled["plan_digest"]:
        raise AssertionError("stage benchmark fixture references a stale immutable plan")
    for field in (
        "artifact_digest",
        "quality_policy",
        "cost_budget_usd",
        "timeout_seconds",
    ):
        if fixture[field] != fixture_job[field]:
            raise AssertionError(f"stage benchmark fixture differs from JobSpec at {field}")
    if fixture["profile_id"] != fixture_job["execution_profile"]:
        raise AssertionError("stage benchmark fixture differs from the JobSpec execution profile")

    state_machine = json.loads(
        (REPOSITORY / "orchestration" / "step_functions" / "workload_execution.asl.json").read_text(
            encoding="utf-8"
        )
    )
    dispatch = state_machine["States"]["DispatchWorkload"]
    required_dispatch_contract = {
        "Resource": "arn:aws:states:::lambda:invoke.waitForTaskToken",
        "ResultPath": "$.workload_result",
        "TimeoutSecondsPath": "$.timeout_seconds",
        "HeartbeatSeconds": 120,
    }
    for field, expected in required_dispatch_contract.items():
        if dispatch.get(field) != expected:
            raise AssertionError(f"Step Functions dispatch contract changed at {field}")
    retry_errors = {
        error for policy in dispatch.get("Retry", []) for error in policy.get("ErrorEquals", [])
    }
    if "States.TaskFailed" in retry_errors:
        raise AssertionError("workload failures must not be retried as dispatcher failures")
    dag_source = (REPOSITORY / "orchestration" / "dags" / "dataops_factory.py").read_text(
        encoding="utf-8"
    )
    for fragment in (
        'attempt = "{{ ti.try_number }}"',
        'retries=job["max_attempts"] - 1',
        'retry_exponential_backoff=job["retry_class"] != "none"',
        'execution_timeout=timedelta(seconds=job["sla_seconds"])',
        "@task(trigger_rule=TriggerRule.ALL_DONE)",
        "optional_markers.get(dependency, tasks[dependency])",
        "@task(trigger_rule=TriggerRule.ONE_FAILED)",
    ):
        if fragment not in dag_source:
            raise AssertionError(
                f"Airflow no longer enforces the JobSpec retry/SLA contract: {fragment}"
            )

    for terraform_file in sorted((REPOSITORY / "infrastructure" / "terraform").rglob("*.tf")):
        with terraform_file.open(encoding="utf-8") as handle:
            hcl2.load(handle)

    image = REPOSITORY / "architecture" / "dataops-factory-architecture.png"
    with image.open("rb") as handle:
        signature = handle.read(24)
    if signature[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError("architecture artifact is not a PNG")
    width, height = struct.unpack(">II", signature[16:24])
    if (width, height) != (2048, 813):
        raise AssertionError(f"architecture artifact changed dimensions: {(width, height)}")
    print("architecture contract validated")


if __name__ == "__main__":
    main()
