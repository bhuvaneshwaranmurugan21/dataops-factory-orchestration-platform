from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from dataops_factory.models import CompiledPlan, ContractError, JobSpec, Registry


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(value).encode()).hexdigest()


def load_registry(path: Path) -> Registry:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot load registry {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ContractError("registry root must be an object")
    required = {"pipeline_id", "registry_version", "accounts", "jobs"}
    if raw.keys() != required:
        raise ContractError(
            f"registry fields differ: missing={sorted(required - raw.keys())}, "
            f"extra={sorted(raw.keys() - required)}"
        )

    pipeline_id = raw["pipeline_id"]
    version = raw["registry_version"]
    accounts_raw = raw["accounts"]
    jobs_raw = raw["jobs"]
    if not isinstance(pipeline_id, str) or not pipeline_id:
        raise ContractError("pipeline_id must be a non-empty string")
    if not isinstance(version, int) or version < 1:
        raise ContractError("registry_version must be a positive integer")
    if not isinstance(accounts_raw, list) or not all(isinstance(x, str) for x in accounts_raw):
        raise ContractError("accounts must be a list of strings")
    if len(accounts_raw) != 5 or len(set(accounts_raw)) != 5:
        raise ContractError("registry must define exactly five unique accounts")
    if not isinstance(jobs_raw, list):
        raise ContractError("jobs must be a list")

    jobs = tuple(JobSpec.from_dict(item) for item in jobs_raw if isinstance(item, dict))
    if len(jobs) != len(jobs_raw):
        raise ContractError("every jobs entry must be an object")
    job_ids = [job.job_id for job in jobs]
    if len(job_ids) != len(set(job_ids)):
        raise ContractError("job_id values must be unique")
    if len(jobs) != 36:
        raise ContractError(f"reference registry must contain 36 jobs, found {len(jobs)}")

    accounts = tuple(sorted(accounts_raw))
    known_jobs = set(job_ids)
    known_accounts = set(accounts)
    for job in jobs:
        unknown_dependencies = set(job.dependencies) - known_jobs
        if unknown_dependencies:
            raise ContractError(
                f"{job.job_id} has unknown dependencies: {sorted(unknown_dependencies)}"
            )
        if job.workload_account not in known_accounts:
            raise ContractError(f"{job.job_id} targets unknown account {job.workload_account}")

    return Registry(
        pipeline_id=pipeline_id,
        registry_version=version,
        accounts=accounts,
        jobs=tuple(sorted(jobs, key=lambda job: job.job_id)),
    )


def _topological_levels(jobs: tuple[JobSpec, ...]) -> tuple[tuple[str, ...], ...]:
    remaining = {job.job_id: set(job.dependencies) for job in jobs}
    levels: list[tuple[str, ...]] = []
    completed: set[str] = set()
    while remaining:
        ready = tuple(sorted(job_id for job_id, deps in remaining.items() if deps <= completed))
        if not ready:
            cycle_nodes = sorted(remaining)
            raise ContractError(f"dependency graph contains a cycle involving {cycle_nodes}")
        levels.append(ready)
        completed.update(ready)
        for job_id in ready:
            del remaining[job_id]
    return tuple(levels)


def compile_registry(registry: Registry, registry_commit: str) -> CompiledPlan:
    if not registry_commit or any(ch.isspace() for ch in registry_commit):
        raise ContractError("registry_commit must be a non-empty token")
    accounts = tuple(sorted(registry.accounts))
    jobs = tuple(sorted(registry.jobs, key=lambda job: job.job_id))
    levels = _topological_levels(jobs)
    unsigned = {
        "pipeline_id": registry.pipeline_id,
        "registry_version": registry.registry_version,
        "registry_commit": registry_commit,
        "accounts": list(accounts),
        "levels": [list(level) for level in levels],
        "jobs": [job.to_dict() for job in jobs],
    }
    return CompiledPlan(
        pipeline_id=registry.pipeline_id,
        registry_version=registry.registry_version,
        registry_commit=registry_commit,
        accounts=accounts,
        levels=levels,
        jobs=jobs,
        plan_digest=sha256_digest(unsigned),
    )


def write_plan(plan: CompiledPlan, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
