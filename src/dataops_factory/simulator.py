from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from dataops_factory.compiler import compile_registry, load_registry, write_plan
from dataops_factory.ledger import (
    AttemptState,
    Lease,
    LedgerConflict,
    RunState,
    SqliteRunLedger,
)
from dataops_factory.manifest import (
    LocalHmacSigner,
    ResultManifest,
    sign_manifest,
    verify_manifest,
)
from dataops_factory.models import CompiledPlan, ContractError, JobSpec
from dataops_factory.publication import (
    PublicationCoordinator,
    SimulatedCrash,
    SqlitePublicationCatalog,
)

_LOCAL_SECRET = b"dataops-factory-local-oracle-key-v1-32-bytes-minimum"
_BASE_TIME = 1_725_000_000.0


@dataclass(slots=True)
class SimulationContext:
    plan: CompiledPlan
    ledger: SqliteRunLedger
    catalog: SqlitePublicationCatalog
    signer: LocalHmacSigner
    run_id: str
    lease: Lease


def _fresh_file(path: Path) -> None:
    if path.exists():
        path.unlink()


def _manifest_for(
    plan: CompiledPlan,
    run_id: str,
    logical_run_key: str,
    job: JobSpec,
    token: int,
    output_prefix: str,
    row_count: int,
    cost_usd: float,
) -> ResultManifest:
    return ResultManifest(
        run_id=run_id,
        logical_run_key=logical_run_key,
        plan_digest=plan.plan_digest,
        job_id=job.job_id,
        attempt=1,
        fencing_token=token,
        workload_account=job.workload_account,
        engine=job.adapter_type.value,
        artifact_digest=job.artifact_digest,
        input_refs=tuple(f"accepted://{dependency}" for dependency in job.dependencies),
        output_refs=(f"local://{output_prefix}part-00000.json",),
        row_count=row_count,
        quality_results=tuple((policy, True) for policy in job.quality_policy),
        lineage=job.dependencies,
        started_at="2024-08-29T00:00:00Z",
        completed_at="2024-08-29T00:00:01Z",
        cost_usd=cost_usd,
    )


def _write_output(root: Path, prefix: str, job: JobSpec, row_count: int) -> None:
    path = root / prefix / "part-00000.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "job_id": job.job_id,
                "adapter_type": job.adapter_type.value,
                "row_count": row_count,
                "classification": "SYNTHETIC_LOCAL_ORACLE",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def simulate(registry_path: Path, work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = work_dir / "ledger.sqlite"
    catalog_path = work_dir / "catalog.sqlite"
    _fresh_file(ledger_path)
    _fresh_file(catalog_path)
    plan = compile_registry(load_registry(registry_path), "local-reference-v1")
    write_plan(plan, work_dir / "compiled-plan.json")
    ledger = SqliteRunLedger(ledger_path)
    catalog = SqlitePublicationCatalog(catalog_path)
    signer = LocalHmacSigner(_LOCAL_SECRET)
    logical_key = "scheduled/2024-08-29"
    run_id = ledger.create_run(
        plan.pipeline_id, logical_key, plan.plan_digest, plan.registry_commit, "local-run-0001"
    )
    lease = ledger.acquire_lease(run_id, "airflow-scheduler-a", _BASE_TIME, 300)
    ledger.transition(run_id, RunState.LEASED, RunState.RUNNING, lease.fencing_token)

    jobs_by_id = {job.job_id: job for job in plan.jobs}
    manifests: list[ResultManifest] = []
    for level_number, level in enumerate(plan.levels):
        for ordinal, job_id in enumerate(level):
            job = jobs_by_id[job_id]
            accepted = ledger.accepted_job_ids(run_id)
            missing_dependencies = set(job.dependencies) - accepted
            if missing_dependencies:
                raise LedgerConflict(
                    f"compiler released {job_id} before {sorted(missing_dependencies)}"
                )
            dispatch_key = f"{run_id}/{job_id}/1/{plan.plan_digest}"
            prefix = ledger.create_attempt(run_id, job_id, 1, lease.fencing_token, dispatch_key)
            ledger.transition_attempt(
                run_id,
                job_id,
                1,
                AttemptState.CREATED,
                AttemptState.DISPATCHING,
                lease.fencing_token,
            )
            ledger.transition_attempt(
                run_id,
                job_id,
                1,
                AttemptState.DISPATCHING,
                AttemptState.RUNNING,
                lease.fencing_token,
                external_id=f"local-{job.adapter_type.value}-{job_id}",
            )
            row_count = 10_000 + level_number * 1_000 + ordinal
            _write_output(work_dir, prefix, job, row_count)
            ledger.transition_attempt(
                run_id,
                job_id,
                1,
                AttemptState.RUNNING,
                AttemptState.SUCCEEDED,
                lease.fencing_token,
            )
            unit_cost = {"lambda": 0.001, "glue": 0.08, "emr_serverless": 0.2}[
                job.adapter_type.value
            ]
            manifest = sign_manifest(
                _manifest_for(
                    plan,
                    run_id,
                    logical_key,
                    job,
                    lease.fencing_token,
                    prefix,
                    row_count,
                    unit_cost,
                ),
                signer,
            )
            verify_manifest(manifest, signer)
            ledger.accept_manifest(manifest)
            manifests.append(manifest)

    ledger.transition(run_id, RunState.RUNNING, RunState.VALIDATING, lease.fencing_token)
    mandatory_jobs = {job.job_id for job in plan.jobs if job.mandatory}
    ledger.assert_required_jobs(run_id, mandatory_jobs)
    ledger.transition(run_id, RunState.VALIDATING, RunState.READY_TO_PUBLISH, lease.fencing_token)
    publication = PublicationCoordinator(ledger, catalog).publish(
        run_id, expected_catalog_version=0, token=lease.fencing_token
    )
    adapter_counts = Counter(job.adapter_type.value for job in plan.jobs)
    summary: dict[str, Any] = {
        "classification": "VERIFIED_LOCAL_SIMULATION",
        "pipeline_id": plan.pipeline_id,
        "run_id": run_id,
        "plan_digest": plan.plan_digest,
        "registry_commit": plan.registry_commit,
        "jobs_planned": len(plan.jobs),
        "jobs_accepted": len(ledger.accepted_job_ids(run_id)),
        "dependency_levels": len(plan.levels),
        "accounts_modelled": len(plan.accounts),
        "adapter_counts": dict(sorted(adapter_counts.items())),
        "publication": {
            "run_id": publication.run_id,
            "version": publication.version,
            "consumer_state": ledger.run_record(run_id)["state"],
        },
        "audit_events": ledger.audit_count(run_id),
        "manifest_digest_count": len({manifest.digest for manifest in manifests}),
        "limitations": [
            "Logical accounts are modelled locally; no AWS account was contacted.",
            "HMAC proves the signing contract locally; AWS stage requires asymmetric KMS.",
            "Synthetic row counts are correctness fixtures, not throughput measurements.",
            "SQLite models DynamoDB and Redshift boundaries; "
            "managed-service behaviour is stage-gated.",
        ],
    }
    (work_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (work_dir / "manifests.jsonl").write_text(
        "".join(json.dumps(manifest.to_dict(), sort_keys=True) + "\n" for manifest in manifests),
        encoding="utf-8",
    )
    ledger.close()
    catalog.close()
    return summary


def _context(root: Path, registry_path: Path, case_name: str) -> SimulationContext:
    case_root = root / case_name
    case_root.mkdir(parents=True, exist_ok=True)
    ledger_path = case_root / "ledger.sqlite"
    catalog_path = case_root / "catalog.sqlite"
    _fresh_file(ledger_path)
    _fresh_file(catalog_path)
    plan = compile_registry(load_registry(registry_path), "local-reference-v1")
    ledger = SqliteRunLedger(ledger_path)
    catalog = SqlitePublicationCatalog(catalog_path)
    run_id = ledger.create_run(
        plan.pipeline_id,
        f"trial/{case_name}",
        plan.plan_digest,
        plan.registry_commit,
        f"run-{case_name}",
    )
    lease = ledger.acquire_lease(run_id, "scheduler-a", _BASE_TIME, 10)
    ledger.transition(run_id, RunState.LEASED, RunState.RUNNING, lease.fencing_token)
    return SimulationContext(plan, ledger, catalog, LocalHmacSigner(_LOCAL_SECRET), run_id, lease)


def _first_job(context: SimulationContext) -> JobSpec:
    return context.plan.jobs[0]


def _complete_job(context: SimulationContext, job: JobSpec | None = None) -> ResultManifest:
    selected = job or _first_job(context)
    prefix = context.ledger.create_attempt(
        context.run_id,
        selected.job_id,
        1,
        context.lease.fencing_token,
        f"dispatch/{context.run_id}/{selected.job_id}/1",
    )
    context.ledger.transition_attempt(
        context.run_id,
        selected.job_id,
        1,
        AttemptState.CREATED,
        AttemptState.DISPATCHING,
        context.lease.fencing_token,
    )
    context.ledger.transition_attempt(
        context.run_id,
        selected.job_id,
        1,
        AttemptState.DISPATCHING,
        AttemptState.RUNNING,
        context.lease.fencing_token,
        external_id="external-1",
    )
    context.ledger.transition_attempt(
        context.run_id,
        selected.job_id,
        1,
        AttemptState.RUNNING,
        AttemptState.SUCCEEDED,
        context.lease.fencing_token,
    )
    manifest = sign_manifest(
        _manifest_for(
            context.plan,
            context.run_id,
            f"trial/{context.run_id}",
            selected,
            context.lease.fencing_token,
            prefix,
            100,
            0.01,
        ),
        context.signer,
    )
    verify_manifest(manifest, context.signer)
    return manifest


def _expect(exception: type[Exception], action: Callable[[], object]) -> None:
    try:
        action()
    except exception:
        return
    raise AssertionError(f"expected {exception.__name__}")


def run_failure_lab(registry_path: Path, work_dir: Path) -> dict[str, Any]:
    work_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, str]] = []

    def trial(name: str, action: Callable[[SimulationContext], None]) -> None:
        context = _context(work_dir, registry_path, name)
        try:
            action(context)
        except Exception as exc:
            results.append({"name": name, "status": "failed", "detail": str(exc)})
        else:
            results.append({"name": name, "status": "passed", "detail": "invariant held"})
        finally:
            context.ledger.close()
            context.catalog.close()

    def duplicate_run(c: SimulationContext) -> None:
        same = c.ledger.create_run(
            c.plan.pipeline_id,
            "trial/duplicate_logical_run",
            c.plan.plan_digest,
            c.plan.registry_commit,
            "different-candidate",
        )
        if same != c.run_id:
            raise AssertionError("duplicate logical run did not return its existing identity")

    trial("duplicate_logical_run", duplicate_run)
    trial(
        "immutable_plan_conflict",
        lambda c: _expect(
            LedgerConflict,
            lambda: c.ledger.create_run(
                c.plan.pipeline_id,
                "trial/immutable_plan_conflict",
                "sha256:" + "f" * 64,
                c.plan.registry_commit,
            ),
        ),
    )
    trial(
        "competing_lease",
        lambda c: _expect(
            LedgerConflict,
            lambda: c.ledger.acquire_lease(c.run_id, "scheduler-b", _BASE_TIME + 1, 10),
        ),
    )

    def expired_takeover(c: SimulationContext) -> None:
        new = c.ledger.acquire_lease(c.run_id, "scheduler-b", _BASE_TIME + 11, 10)
        if new.fencing_token <= c.lease.fencing_token:
            raise AssertionError("lease takeover did not advance the fencing token")

    trial("expired_lease_takeover", expired_takeover)

    def stale_heartbeat(c: SimulationContext) -> None:
        c.ledger.acquire_lease(c.run_id, "scheduler-b", _BASE_TIME + 11, 10)
        _expect(
            LedgerConflict,
            lambda: c.ledger.heartbeat(c.lease, _BASE_TIME + 12, 10),
        )

    trial("stale_heartbeat", stale_heartbeat)

    def stale_run_transition(c: SimulationContext) -> None:
        c.ledger.acquire_lease(c.run_id, "scheduler-b", _BASE_TIME + 11, 10)
        _expect(
            LedgerConflict,
            lambda: c.ledger.transition(
                c.run_id, RunState.RUNNING, RunState.VALIDATING, c.lease.fencing_token
            ),
        )

    trial("stale_run_transition", stale_run_transition)

    def duplicate_attempt(c: SimulationContext) -> None:
        job = _first_job(c)
        first = c.ledger.create_attempt(c.run_id, job.job_id, 1, c.lease.fencing_token, "key")
        second = c.ledger.create_attempt(c.run_id, job.job_id, 1, c.lease.fencing_token, "key")
        if first != second:
            raise AssertionError("duplicate attempt did not preserve its output prefix")

    trial("duplicate_attempt", duplicate_attempt)

    def conflicting_dispatch(c: SimulationContext) -> None:
        job = _first_job(c)
        c.ledger.create_attempt(c.run_id, job.job_id, 1, c.lease.fencing_token, "key-a")
        _expect(
            LedgerConflict,
            lambda: c.ledger.create_attempt(
                c.run_id, job.job_id, 1, c.lease.fencing_token, "key-b"
            ),
        )

    trial("conflicting_dispatch_identity", conflicting_dispatch)

    def illegal_attempt_transition(c: SimulationContext) -> None:
        job = _first_job(c)
        c.ledger.create_attempt(c.run_id, job.job_id, 1, c.lease.fencing_token, "key")
        _expect(
            LedgerConflict,
            lambda: c.ledger.transition_attempt(
                c.run_id,
                job.job_id,
                1,
                AttemptState.CREATED,
                AttemptState.SUCCEEDED,
                c.lease.fencing_token,
            ),
        )

    trial("illegal_attempt_transition", illegal_attempt_transition)

    def stale_attempt_transition(c: SimulationContext) -> None:
        job = _first_job(c)
        c.ledger.create_attempt(c.run_id, job.job_id, 1, c.lease.fencing_token, "key")
        new_lease = c.ledger.acquire_lease(c.run_id, "scheduler-b", _BASE_TIME + 11, 10)
        _expect(
            LedgerConflict,
            lambda: c.ledger.transition_attempt(
                c.run_id,
                job.job_id,
                1,
                AttemptState.CREATED,
                AttemptState.DISPATCHING,
                new_lease.fencing_token,
            ),
        )

    trial("stale_attempt_transition", stale_attempt_transition)

    def tampered_manifest(c: SimulationContext) -> None:
        manifest = _complete_job(c)
        tampered = replace(manifest, row_count=manifest.row_count + 1)
        _expect(ContractError, lambda: verify_manifest(tampered, c.signer))

    trial("tampered_manifest", tampered_manifest)

    def unbound_signer(c: SimulationContext) -> None:
        manifest = _complete_job(c)
        other = LocalHmacSigner(_LOCAL_SECRET, "different-key")
        _expect(ContractError, lambda: verify_manifest(manifest, other))

    trial("unbound_manifest_signer", unbound_signer)

    def stale_manifest(c: SimulationContext) -> None:
        manifest = _complete_job(c)
        c.ledger.acquire_lease(c.run_id, "scheduler-b", _BASE_TIME + 11, 10)
        _expect(LedgerConflict, lambda: c.ledger.accept_manifest(manifest))

    trial("stale_manifest_fence", stale_manifest)

    def duplicate_manifest(c: SimulationContext) -> None:
        manifest = _complete_job(c)
        c.ledger.accept_manifest(manifest)
        c.ledger.accept_manifest(manifest)
        if c.ledger.accepted_job_ids(c.run_id) != {manifest.job_id}:
            raise AssertionError("duplicate manifest created a second accepted effect")

    trial("duplicate_manifest", duplicate_manifest)

    def conflicting_manifest(c: SimulationContext) -> None:
        manifest = _complete_job(c)
        c.ledger.accept_manifest(manifest)
        alternate = sign_manifest(replace(manifest, row_count=101, signature=""), c.signer)
        _expect(LedgerConflict, lambda: c.ledger.accept_manifest(alternate))

    trial("conflicting_accepted_result", conflicting_manifest)
    trial(
        "mandatory_fanin_block",
        lambda c: _expect(
            LedgerConflict,
            lambda: c.ledger.assert_required_jobs(c.run_id, {"missing-mandatory-job"}),
        ),
    )

    def publisher_crash(c: SimulationContext) -> None:
        c.ledger.transition(c.run_id, RunState.RUNNING, RunState.VALIDATING, c.lease.fencing_token)
        c.ledger.transition(
            c.run_id, RunState.VALIDATING, RunState.READY_TO_PUBLISH, c.lease.fencing_token
        )
        coordinator = PublicationCoordinator(c.ledger, c.catalog)
        _expect(
            SimulatedCrash,
            lambda: coordinator.publish(
                c.run_id, 0, c.lease.fencing_token, crash_after_catalog_commit=True
            ),
        )
        if c.ledger.run_record(c.run_id)["state"] != RunState.READY_TO_PUBLISH.value:
            raise AssertionError("simulated publisher crash changed the run state")
        coordinator.reconcile(c.plan.pipeline_id, c.lease.fencing_token)
        if c.ledger.run_record(c.run_id)["state"] != RunState.PUBLISHED.value:
            raise AssertionError("publication reconciliation did not converge")

    trial("publisher_crash_after_catalog_commit", publisher_crash)

    def publication_race(c: SimulationContext) -> None:
        c.ledger.transition(c.run_id, RunState.RUNNING, RunState.VALIDATING, c.lease.fencing_token)
        c.ledger.transition(
            c.run_id, RunState.VALIDATING, RunState.READY_TO_PUBLISH, c.lease.fencing_token
        )
        PublicationCoordinator(c.ledger, c.catalog).publish(c.run_id, 0, c.lease.fencing_token)
        second = c.ledger.create_run(
            c.plan.pipeline_id,
            "trial/publication_race_second",
            c.plan.plan_digest,
            c.plan.registry_commit,
            "run-publication-race-second",
        )
        second_lease = c.ledger.acquire_lease(second, "scheduler-b", _BASE_TIME, 10)
        c.ledger.transition(second, RunState.LEASED, RunState.RUNNING, second_lease.fencing_token)
        c.ledger.transition(
            second, RunState.RUNNING, RunState.VALIDATING, second_lease.fencing_token
        )
        c.ledger.transition(
            second, RunState.VALIDATING, RunState.READY_TO_PUBLISH, second_lease.fencing_token
        )
        _expect(
            LedgerConflict,
            lambda: PublicationCoordinator(c.ledger, c.catalog).publish(
                second, 0, second_lease.fencing_token
            ),
        )

    trial("concurrent_publication_version", publication_race)
    trial(
        "quality_gate_rejection",
        lambda c: _expect(
            ContractError,
            lambda: verify_manifest(
                sign_manifest(
                    replace(
                        _complete_job(c),
                        quality_results=(("schema", False),),
                        signature="",
                    ),
                    c.signer,
                ),
                c.signer,
            ),
        ),
    )

    passed = sum(result["status"] == "passed" for result in results)
    report: dict[str, Any] = {
        "classification": "VERIFIED_LOCAL_FAILURE_LAB",
        "passed": passed,
        "failed": len(results) - passed,
        "trials": results,
        "limitations": [
            "The failure lab validates local state-machine invariants, "
            "not AWS service availability.",
            "Cross-account IAM, KMS Object Lock and managed-engine recovery remain stage-gated.",
        ],
    }
    (work_dir / "summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if report["failed"]:
        raise AssertionError(f"{report['failed']} failure-lab trials failed")
    return report
