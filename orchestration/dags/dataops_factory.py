from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow import DAG
from airflow.decorators import task
from airflow.exceptions import AirflowException
from airflow.providers.amazon.aws.operators.step_function import (
    StepFunctionStartExecutionOperator,
)
from airflow.utils.trigger_rule import TriggerRule

PLAN_PATH = Path(__file__).resolve().parents[1] / "generated" / "compiled-plan.json"
PLAN = json.loads(PLAN_PATH.read_text(encoding="utf-8"))
STATE_MACHINE_ARN = os.environ["DATAOPS_WORKLOAD_STATE_MACHINE_ARN"]
REGISTER_RUN_FUNCTION = os.environ["DATAOPS_REGISTER_RUN_FUNCTION"]
MARK_FAILED_FUNCTION = os.environ["DATAOPS_MARK_FAILED_FUNCTION"]


@task
def register_run(logical_run_key: str) -> dict[str, object]:
    import boto3

    response = boto3.client("lambda").invoke(
        FunctionName=REGISTER_RUN_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "pipeline_id": PLAN["pipeline_id"],
                "logical_run_key": logical_run_key,
                "plan_digest": PLAN["plan_digest"],
                "registry_commit": PLAN["registry_commit"],
            },
            sort_keys=True,
        ).encode(),
    )
    return json.loads(response["Payload"].read())


@task(trigger_rule=TriggerRule.ALL_DONE)
def close_optional_branch(job_id: str) -> dict[str, str]:
    return {"job_id": job_id, "disposition": "OPTIONAL_BRANCH_CLOSED"}


@task(trigger_rule=TriggerRule.ONE_FAILED)
def finalize_failed_run(registration: dict[str, object]) -> None:
    import boto3

    response = boto3.client("lambda").invoke(
        FunctionName=MARK_FAILED_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "run_id": registration["run_id"],
                "fencing_token": registration["fencing_token"],
                "finalize_run": True,
            },
            sort_keys=True,
        ).encode(),
    )
    if response.get("FunctionError"):
        raise AirflowException("failed to persist the terminal run failure")
    raise AirflowException("a mandatory branch exhausted its retry policy")


with DAG(
    dag_id="dataops_factory",
    start_date=datetime(2024, 1, 1, tzinfo=UTC),
    schedule="0 2 * * *",
    catchup=False,
    max_active_runs=4,
    default_args={"retries": 0},
    tags=["dataops", "multi-account", PLAN["plan_digest"][-12:]],
) as dag:
    registration = register_run("{{ data_interval_start | ts }}")
    tasks: dict[str, StepFunctionStartExecutionOperator] = {}
    jobs = {job["job_id"]: job for job in PLAN["jobs"]}
    for level in PLAN["levels"]:
        for job_id in level:
            job = jobs[job_id]
            attempt = "{{ ti.try_number }}"
            retry_delay = 120 if job["retry_class"] == "throttled" else 60
            tasks[job_id] = StepFunctionStartExecutionOperator(
                task_id=f"execute__{job_id}",
                state_machine_arn=STATE_MACHINE_ARN,
                name=f"{{{{ ts_nodash }}}}-{job_id}-{attempt}",
                state_machine_input=json.dumps(
                    {
                        "logical_run_key": "{{ data_interval_start | ts }}",
                        "run_id": "{{ ti.xcom_pull(task_ids='register_run')['run_id'] }}",
                        "fencing_token": (
                            "{{ ti.xcom_pull(task_ids='register_run')['fencing_token'] }}"
                        ),
                        "job_id": job_id,
                        "mandatory": job["mandatory"],
                        "attempt": attempt,
                        "dispatch_key": (
                            "{{ ti.xcom_pull(task_ids='register_run')['run_id'] }}"
                            f"/{job_id}/{attempt}/{PLAN['plan_digest']}"
                        ),
                        "output_prefix": (
                            "outputs/{{ ti.xcom_pull(task_ids='register_run')['run_id'] }}"
                            f"/{job_id}/{attempt}/"
                            "{{ ti.xcom_pull(task_ids='register_run')['fencing_token'] }}/"
                        ),
                        "artifact_digest": job["artifact_digest"],
                        "quality_policy": job["quality_policy"],
                        "cost_budget_usd": job["cost_budget_usd"],
                        "profile_id": job["execution_profile"],
                        "timeout_seconds": job["timeout_seconds"],
                        "plan_digest": PLAN["plan_digest"],
                        "registry_commit": PLAN["registry_commit"],
                    },
                    sort_keys=True,
                ),
                deferrable=True,
                retries=job["max_attempts"] - 1,
                retry_delay=timedelta(seconds=retry_delay),
                retry_exponential_backoff=job["retry_class"] != "none",
                max_retry_delay=timedelta(minutes=15),
                execution_timeout=timedelta(seconds=job["sla_seconds"]),
            )

    optional_markers = {
        job_id: close_optional_branch.override(task_id=f"optional__{job_id}")(job_id)
        for job_id, job in jobs.items()
        if not job["mandatory"]
    }
    for job_id, marker in optional_markers.items():
        tasks[job_id] >> marker

    for job_id, job in jobs.items():
        if not job["dependencies"]:
            registration >> tasks[job_id]
        for dependency in job["dependencies"]:
            optional_markers.get(dependency, tasks[dependency]) >> tasks[job_id]

    failure_finalizer = finalize_failed_run(registration)
    for job_id, job in jobs.items():
        if job["mandatory"]:
            tasks[job_id] >> failure_finalizer
