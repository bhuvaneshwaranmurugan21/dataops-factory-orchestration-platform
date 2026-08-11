from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass, replace
from typing import Any, Protocol

from dataops_factory.compiler import canonical_json, sha256_digest
from dataops_factory.models import ContractError


class ManifestSigner(Protocol):
    @property
    def key_id(self) -> str: ...

    @property
    def algorithm(self) -> str: ...

    def sign_digest(self, digest: bytes) -> bytes: ...

    def verify_digest(self, digest: bytes, signature: bytes) -> bool: ...


class LocalHmacSigner:
    """Deterministic local oracle; AWS stage uses an asymmetric KMS signer."""

    def __init__(self, secret: bytes, key_id: str = "local-oracle-key-v1") -> None:
        if len(secret) < 32:
            raise ValueError("local signing secret must contain at least 32 bytes")
        self._secret = secret
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    @property
    def algorithm(self) -> str:
        return "HMAC_SHA256_LOCAL_ORACLE"

    def sign_digest(self, digest: bytes) -> bytes:
        return hmac.new(self._secret, digest, hashlib.sha256).digest()

    def verify_digest(self, digest: bytes, signature: bytes) -> bool:
        expected = self.sign_digest(digest)
        return hmac.compare_digest(expected, signature)


@dataclass(frozen=True, slots=True)
class ResultManifest:
    run_id: str
    logical_run_key: str
    plan_digest: str
    job_id: str
    attempt: int
    fencing_token: int
    workload_account: str
    engine: str
    artifact_digest: str
    input_refs: tuple[str, ...]
    output_refs: tuple[str, ...]
    row_count: int
    quality_results: tuple[tuple[str, bool], ...]
    lineage: tuple[str, ...]
    started_at: str
    completed_at: str
    cost_usd: float
    signer_key_id: str = ""
    signature_algorithm: str = ""
    signature: str = ""

    def unsigned_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "logical_run_key": self.logical_run_key,
            "plan_digest": self.plan_digest,
            "job_id": self.job_id,
            "attempt": self.attempt,
            "fencing_token": self.fencing_token,
            "workload_account": self.workload_account,
            "engine": self.engine,
            "artifact_digest": self.artifact_digest,
            "input_refs": sorted(self.input_refs),
            "output_refs": sorted(self.output_refs),
            "row_count": self.row_count,
            "quality_results": {key: value for key, value in sorted(self.quality_results)},
            "lineage": sorted(self.lineage),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "cost_usd": self.cost_usd,
        }

    @property
    def digest(self) -> str:
        return sha256_digest(self.unsigned_payload())

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.unsigned_payload(),
            "manifest_digest": self.digest,
            "signer_key_id": self.signer_key_id,
            "signature_algorithm": self.signature_algorithm,
            "signature": self.signature,
        }


def _digest_bytes(manifest: ResultManifest) -> bytes:
    return hashlib.sha256(canonical_json(manifest.unsigned_payload()).encode()).digest()


def sign_manifest(manifest: ResultManifest, signer: ManifestSigner) -> ResultManifest:
    signature = signer.sign_digest(_digest_bytes(manifest))
    return replace(
        manifest,
        signer_key_id=signer.key_id,
        signature_algorithm=signer.algorithm,
        signature=base64.b64encode(signature).decode("ascii"),
    )


def verify_manifest(manifest: ResultManifest, signer: ManifestSigner) -> None:
    if manifest.signer_key_id != signer.key_id:
        raise ContractError("manifest signer is not bound to the execution plan")
    if manifest.signature_algorithm != signer.algorithm:
        raise ContractError("manifest signature algorithm differs from the execution plan")
    if not manifest.signature:
        raise ContractError("manifest has no signature")
    if manifest.row_count < 0 or manifest.cost_usd < 0:
        raise ContractError("manifest contains a negative measurement")
    if not manifest.output_refs:
        raise ContractError("manifest contains no outputs")
    if not all(value for _, value in manifest.quality_results):
        raise ContractError("manifest failed at least one quality policy")
    try:
        signature = base64.b64decode(manifest.signature, validate=True)
    except ValueError as exc:
        raise ContractError("manifest signature is not valid base64") from exc
    if not signer.verify_digest(_digest_bytes(manifest), signature):
        raise ContractError("manifest signature verification failed")
