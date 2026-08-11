from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from dataops_factory.models import AdapterType, ContractError


class ReconcileClient(Protocol):
    def get_job_run(self, **kwargs: Any) -> dict[str, Any]: ...


class ExternalState(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    external_id: str
    state: ExternalState
    raw_state: str


def reconcile_job(
    adapter_type: AdapterType,
    client: ReconcileClient,
    external_id: str,
    target: str,
) -> ReconcileResult:
    if adapter_type is AdapterType.GLUE:
        raw = client.get_job_run(JobName=target, RunId=external_id, PredecessorsIncluded=False)
        state = str(raw["JobRun"]["JobRunState"])
        mapped = {
            "SUCCEEDED": ExternalState.SUCCEEDED,
            "FAILED": ExternalState.FAILED,
            "ERROR": ExternalState.FAILED,
            "TIMEOUT": ExternalState.FAILED,
            "STOPPED": ExternalState.FAILED,
            "STARTING": ExternalState.RUNNING,
            "RUNNING": ExternalState.RUNNING,
            "WAITING": ExternalState.RUNNING,
            "STOPPING": ExternalState.RUNNING,
        }.get(state, ExternalState.UNKNOWN)
    elif adapter_type is AdapterType.EMR_SERVERLESS:
        raw = client.get_job_run(applicationId=target, jobRunId=external_id)
        state = str(raw["jobRun"]["state"])
        mapped = {
            "SUCCESS": ExternalState.SUCCEEDED,
            "FAILED": ExternalState.FAILED,
            "CANCELLED": ExternalState.FAILED,
            "CANCELLING": ExternalState.RUNNING,
            "SCHEDULED": ExternalState.RUNNING,
            "PENDING": ExternalState.RUNNING,
            "RUNNING": ExternalState.RUNNING,
        }.get(state, ExternalState.UNKNOWN)
    else:
        raise ContractError("synchronous Lambda executions do not use reconciliation polling")
    return ReconcileResult(external_id, mapped, state)
