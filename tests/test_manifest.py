from __future__ import annotations

from dataclasses import replace

import pytest

from dataops_factory.manifest import (
    LocalHmacSigner,
    ResultManifest,
    sign_manifest,
    verify_manifest,
)
from dataops_factory.models import ContractError


def manifest() -> ResultManifest:
    return ResultManifest(
        run_id="run-1",
        logical_run_key="scheduled/1",
        plan_digest="sha256:" + "1" * 64,
        job_id="job_one",
        attempt=1,
        fencing_token=2,
        workload_account="commerce-data",
        engine="glue",
        artifact_digest="sha256:" + "2" * 64,
        input_refs=("s3://input/b", "s3://input/a"),
        output_refs=("s3://output/a",),
        row_count=10,
        quality_results=(("schema", True), ("rows", True)),
        lineage=("source_b", "source_a"),
        started_at="2024-01-01T00:00:00Z",
        completed_at="2024-01-01T00:01:00Z",
        cost_usd=0.1,
    )


def test_sign_verify_and_canonical_digest() -> None:
    signer = LocalHmacSigner(b"x" * 32)
    signed = sign_manifest(manifest(), signer)
    verify_manifest(signed, signer)
    reordered = replace(
        manifest(),
        input_refs=tuple(reversed(manifest().input_refs)),
        lineage=tuple(reversed(manifest().lineage)),
    )
    assert reordered.digest == manifest().digest
    assert signed.to_dict()["manifest_digest"] == signed.digest


@pytest.mark.parametrize(
    "changed",
    [
        {"row_count": 11},
        {"cost_usd": 0.2},
        {"output_refs": ("s3://other",)},
    ],
)
def test_tampering_is_rejected(changed: dict[str, object]) -> None:
    signer = LocalHmacSigner(b"x" * 32)
    signed = sign_manifest(manifest(), signer)
    with pytest.raises(ContractError, match="verification failed"):
        verify_manifest(replace(signed, **changed), signer)


def test_policy_and_signer_fail_closed() -> None:
    signer = LocalHmacSigner(b"x" * 32)
    with pytest.raises(ValueError, match="32 bytes"):
        LocalHmacSigner(b"short")
    failed = sign_manifest(replace(manifest(), quality_results=(("schema", False),)), signer)
    with pytest.raises(ContractError, match="quality"):
        verify_manifest(failed, signer)
    signed = sign_manifest(manifest(), signer)
    with pytest.raises(ContractError, match="not bound"):
        verify_manifest(signed, LocalHmacSigner(b"x" * 32, "other"))


def test_missing_and_malformed_signatures_are_rejected() -> None:
    signer = LocalHmacSigner(b"x" * 32)
    unsigned = replace(
        manifest(), signer_key_id=signer.key_id, signature_algorithm=signer.algorithm
    )
    with pytest.raises(ContractError, match="no signature"):
        verify_manifest(unsigned, signer)
    malformed = replace(unsigned, signature="not base64%%")
    with pytest.raises(ContractError, match="base64"):
        verify_manifest(malformed, signer)
