from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from dataops_factory.compiler import compile_registry, load_registry, write_plan
from dataops_factory.models import ContractError, Registry


def test_reference_registry_compiles_deterministically(registry_path: Path) -> None:
    registry = load_registry(registry_path)
    first = compile_registry(registry, "commit-a")
    shuffled = Registry(
        registry.pipeline_id,
        registry.registry_version,
        tuple(reversed(registry.accounts)),
        tuple(reversed(registry.jobs)),
    )
    second = compile_registry(shuffled, "commit-a")
    assert first.plan_digest == second.plan_digest
    assert len(first.jobs) == 36
    assert len(first.levels) == 12
    assert first.levels[-1] == ("audit_publication",)


def test_registry_commit_is_part_of_plan_identity(registry_path: Path) -> None:
    registry = load_registry(registry_path)
    first = compile_registry(registry, "a")
    second = compile_registry(registry, "b")
    assert first.plan_digest != second.plan_digest
    with pytest.raises(ContractError, match="registry_commit"):
        compile_registry(registry, "has whitespace")


def test_cycle_is_rejected(registry_path: Path) -> None:
    registry = load_registry(registry_path)
    jobs = list(registry.jobs)
    index = next(i for i, job in enumerate(jobs) if job.job_id == "register_snapshot")
    jobs[index] = replace(jobs[index], dependencies=("audit_publication",))
    cyclic = Registry(
        registry.pipeline_id, registry.registry_version, registry.accounts, tuple(jobs)
    )
    with pytest.raises(ContractError, match="cycle"):
        compile_registry(cyclic, "commit")


def test_load_rejects_unknown_dependency_and_wrong_job_count(
    registry_path: Path, tmp_path: Path
) -> None:
    raw = json.loads(registry_path.read_text())
    raw["jobs"][0]["dependencies"] = ["unknown"]
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps(raw))
    with pytest.raises(ContractError, match="unknown dependencies"):
        load_registry(invalid)

    raw["jobs"] = raw["jobs"][:-1]
    invalid.write_text(json.dumps(raw))
    with pytest.raises(ContractError, match="36 jobs"):
        load_registry(invalid)


def test_write_plan_is_stable(registry_path: Path, tmp_path: Path) -> None:
    plan = compile_registry(load_registry(registry_path), "commit")
    target = tmp_path / "nested" / "plan.json"
    write_plan(plan, target)
    written = json.loads(target.read_text())
    assert written["plan_digest"] == plan.plan_digest
    assert written["jobs"][0]["job_id"] == "activate_catalog"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_digest", "latest", "artifact digest"),
        ("max_attempts", 9, "max_attempts"),
        ("timeout_seconds", 1, "timeout_seconds"),
        ("quality_policy", [], "quality_policy"),
        ("mandatory", "yes", "mandatory"),
    ],
)
def test_jobspec_contract_edges(
    registry_path: Path, tmp_path: Path, field: str, value: object, message: str
) -> None:
    raw = json.loads(registry_path.read_text())
    raw["jobs"][0][field] = value
    invalid = tmp_path / f"{field}.json"
    invalid.write_text(json.dumps(raw))
    with pytest.raises(ContractError, match=message):
        load_registry(invalid)
