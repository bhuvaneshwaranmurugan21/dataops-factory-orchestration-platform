from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ContractError(ValueError):
    """Raised when a registry or runtime contract is invalid."""


class AdapterType(StrEnum):
    LAMBDA = "lambda"
    GLUE = "glue"
    EMR_SERVERLESS = "emr_serverless"


class RetryClass(StrEnum):
    TRANSIENT = "transient"
    THROTTLED = "throttled"
    NONE = "none"


_JOB_ID = re.compile(r"^[a-z][a-z0-9_]{2,62}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")
    return value


def _require_string_list(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ContractError(f"{field} must be a list of strings")
    if len(value) != len(set(value)):
        raise ContractError(f"{field} contains duplicates")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_id: str
    owner: str
    workload_account: str
    adapter_type: AdapterType
    execution_profile: str
    artifact_digest: str
    dependencies: tuple[str, ...]
    mandatory: bool
    timeout_seconds: int
    retry_class: RetryClass
    max_attempts: int
    idempotency_scope: str
    sla_seconds: int
    cost_budget_usd: float
    quality_policy: tuple[str, ...]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> JobSpec:
        required = {
            "job_id",
            "owner",
            "workload_account",
            "adapter_type",
            "execution_profile",
            "artifact_digest",
            "dependencies",
            "mandatory",
            "timeout_seconds",
            "retry_class",
            "max_attempts",
            "idempotency_scope",
            "sla_seconds",
            "cost_budget_usd",
            "quality_policy",
        }
        missing = required - raw.keys()
        extra = raw.keys() - required
        if missing or extra:
            raise ContractError(
                f"JobSpec fields differ: missing={sorted(missing)}, extra={sorted(extra)}"
            )

        job_id = _require_string(raw["job_id"], "job_id")
        if not _JOB_ID.fullmatch(job_id):
            raise ContractError(f"invalid job_id: {job_id}")

        artifact_digest = _require_string(raw["artifact_digest"], "artifact_digest")
        if not _SHA256.fullmatch(artifact_digest):
            raise ContractError(f"invalid artifact digest for {job_id}")

        dependencies = _require_string_list(raw["dependencies"], "dependencies")
        if job_id in dependencies:
            raise ContractError(f"{job_id} cannot depend on itself")

        mandatory = raw["mandatory"]
        if not isinstance(mandatory, bool):
            raise ContractError(f"mandatory must be a boolean for {job_id}")

        timeout_seconds = raw["timeout_seconds"]
        max_attempts = raw["max_attempts"]
        sla_seconds = raw["sla_seconds"]
        cost_budget = raw["cost_budget_usd"]
        if not isinstance(timeout_seconds, int) or not 30 <= timeout_seconds <= 86_400:
            raise ContractError(f"timeout_seconds out of range for {job_id}")
        if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise ContractError(f"max_attempts out of range for {job_id}")
        if not isinstance(sla_seconds, int) or sla_seconds < timeout_seconds:
            raise ContractError(f"sla_seconds must be >= timeout_seconds for {job_id}")
        if not isinstance(cost_budget, int | float) or not 0 < float(cost_budget) <= 500:
            raise ContractError(f"cost_budget_usd out of range for {job_id}")

        try:
            adapter = AdapterType(_require_string(raw["adapter_type"], "adapter_type"))
            retry = RetryClass(_require_string(raw["retry_class"], "retry_class"))
        except ValueError as exc:
            raise ContractError(f"invalid enum value for {job_id}: {exc}") from exc

        if retry is RetryClass.NONE and max_attempts != 1:
            raise ContractError(f"non-retryable job {job_id} must have max_attempts=1")

        quality_policy = _require_string_list(raw["quality_policy"], "quality_policy")
        if not quality_policy:
            raise ContractError(f"quality_policy cannot be empty for {job_id}")

        return cls(
            job_id=job_id,
            owner=_require_string(raw["owner"], "owner"),
            workload_account=_require_string(raw["workload_account"], "workload_account"),
            adapter_type=adapter,
            execution_profile=_require_string(raw["execution_profile"], "execution_profile"),
            artifact_digest=artifact_digest,
            dependencies=dependencies,
            mandatory=mandatory,
            timeout_seconds=timeout_seconds,
            retry_class=retry,
            max_attempts=max_attempts,
            idempotency_scope=_require_string(raw["idempotency_scope"], "idempotency_scope"),
            sla_seconds=sla_seconds,
            cost_budget_usd=float(cost_budget),
            quality_policy=quality_policy,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "owner": self.owner,
            "workload_account": self.workload_account,
            "adapter_type": self.adapter_type.value,
            "execution_profile": self.execution_profile,
            "artifact_digest": self.artifact_digest,
            "dependencies": sorted(self.dependencies),
            "mandatory": self.mandatory,
            "timeout_seconds": self.timeout_seconds,
            "retry_class": self.retry_class.value,
            "max_attempts": self.max_attempts,
            "idempotency_scope": self.idempotency_scope,
            "sla_seconds": self.sla_seconds,
            "cost_budget_usd": self.cost_budget_usd,
            "quality_policy": sorted(self.quality_policy),
        }


@dataclass(frozen=True, slots=True)
class Registry:
    pipeline_id: str
    registry_version: int
    accounts: tuple[str, ...]
    jobs: tuple[JobSpec, ...]


@dataclass(frozen=True, slots=True)
class CompiledPlan:
    pipeline_id: str
    registry_version: int
    registry_commit: str
    accounts: tuple[str, ...]
    levels: tuple[tuple[str, ...], ...]
    jobs: tuple[JobSpec, ...]
    plan_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "registry_version": self.registry_version,
            "registry_commit": self.registry_commit,
            "accounts": list(self.accounts),
            "levels": [list(level) for level in self.levels],
            "jobs": [job.to_dict() for job in self.jobs],
            "plan_digest": self.plan_digest,
        }
