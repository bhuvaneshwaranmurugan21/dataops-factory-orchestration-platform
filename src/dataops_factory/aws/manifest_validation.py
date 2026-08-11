from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dataops_factory.models import ContractError


@dataclass(frozen=True, slots=True)
class ManifestBinding:
    run_id: str
    logical_run_key: str
    plan_digest: str
    job_id: str
    attempt: int
    fencing_token: int
    dispatch_key: str
    artifact_digest: str
    workload_account: str
    output_uri: str
    quality_policy: tuple[str, ...]
    cost_budget_usd: float


_REQUIRED_FIELDS = {
    "run_id",
    "logical_run_key",
    "plan_digest",
    "job_id",
    "attempt",
    "fencing_token",
    "dispatch_key",
    "artifact_digest",
    "workload_account",
    "engine",
    "input_refs",
    "output_refs",
    "row_count",
    "quality_results",
    "lineage",
    "cost_usd",
    "completed_at",
    "manifest_digest_hex",
    "signature_hex",
    "signer_key_arn",
    "signing_algorithm",
}


def validate_bound_manifest(manifest: dict[str, Any], expected: ManifestBinding) -> None:
    """Validate semantic bindings after the immutable object's signature verifies."""

    if manifest.keys() != _REQUIRED_FIELDS:
        raise ContractError(
            "manifest schema differs: "
            f"missing={sorted(_REQUIRED_FIELDS - manifest.keys())}, "
            f"extra={sorted(manifest.keys() - _REQUIRED_FIELDS)}"
        )
    bindings: dict[str, object] = {
        "run_id": expected.run_id,
        "logical_run_key": expected.logical_run_key,
        "plan_digest": expected.plan_digest,
        "job_id": expected.job_id,
        "attempt": expected.attempt,
        "fencing_token": expected.fencing_token,
        "dispatch_key": expected.dispatch_key,
        "artifact_digest": expected.artifact_digest,
        "workload_account": expected.workload_account,
    }
    for field, value in bindings.items():
        if manifest[field] != value:
            raise ContractError(f"manifest {field} is not bound to the scheduled workload")
    if manifest["engine"] not in {"lambda", "spark", "redshift"}:
        raise ContractError("manifest engine is not allowlisted")
    if not isinstance(manifest["row_count"], int) or manifest["row_count"] < 0:
        raise ContractError("manifest row_count must be a non-negative integer")
    cost = manifest["cost_usd"]
    if not isinstance(cost, int | float) or not 0 <= float(cost) <= expected.cost_budget_usd:
        raise ContractError("manifest cost exceeds the immutable job budget")
    quality = manifest["quality_results"]
    if not isinstance(quality, dict) or any(
        not isinstance(value, bool) for value in quality.values()
    ):
        raise ContractError("manifest quality_results must contain boolean outcomes")
    missing_quality = set(expected.quality_policy) - quality.keys()
    if missing_quality or not all(quality[policy] for policy in expected.quality_policy):
        raise ContractError("manifest does not satisfy the immutable quality policy")
    output_refs = manifest["output_refs"]
    if not isinstance(output_refs, list) or not output_refs:
        raise ContractError("manifest must contain at least one output reference")
    control_ref = f"control://{expected.run_id}/{expected.job_id}"
    if not all(
        isinstance(value, str) and (value.startswith(expected.output_uri) or value == control_ref)
        for value in output_refs
    ):
        raise ContractError("manifest output escaped the attempt-isolated prefix")
