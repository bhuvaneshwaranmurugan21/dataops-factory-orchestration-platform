from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from dataops_factory.manifest import ResultManifest


class LedgerConflict(RuntimeError):
    """A conditional state change lost a race or violated an invariant."""


class RunState(StrEnum):
    PLANNED = "PLANNED"
    LEASED = "LEASED"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    READY_TO_PUBLISH = "READY_TO_PUBLISH"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CLEANED = "CLEANED"


class AttemptState(StrEnum):
    CREATED = "CREATED"
    DISPATCHING = "DISPATCHING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


_RUN_TRANSITIONS = {
    RunState.LEASED: {RunState.RUNNING, RunState.FAILED},
    RunState.RUNNING: {RunState.VALIDATING, RunState.FAILED},
    RunState.VALIDATING: {RunState.READY_TO_PUBLISH, RunState.FAILED},
    RunState.READY_TO_PUBLISH: {RunState.PUBLISHED, RunState.FAILED},
    RunState.PUBLISHED: {RunState.CLEANED},
    RunState.FAILED: {RunState.CLEANED},
}

_ATTEMPT_TRANSITIONS = {
    AttemptState.CREATED: {AttemptState.DISPATCHING},
    AttemptState.DISPATCHING: {AttemptState.RUNNING, AttemptState.FAILED},
    AttemptState.RUNNING: {AttemptState.SUCCEEDED, AttemptState.FAILED},
}


@dataclass(frozen=True, slots=True)
class Lease:
    run_id: str
    owner: str
    lease_epoch: int
    fencing_token: int
    expires_at: float


class SqliteRunLedger:
    """Executable local correctness oracle for the DynamoDB run-ledger contract."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                pipeline_id TEXT NOT NULL,
                logical_run_key TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                registry_commit TEXT NOT NULL,
                state TEXT NOT NULL,
                lease_owner TEXT,
                lease_epoch INTEGER NOT NULL DEFAULT 0,
                fencing_token INTEGER NOT NULL DEFAULT 0,
                lease_expires_at REAL,
                UNIQUE (pipeline_id, logical_run_key)
            );
            CREATE TABLE IF NOT EXISTS attempts (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                job_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                state TEXT NOT NULL,
                fencing_token INTEGER NOT NULL,
                dispatch_key TEXT NOT NULL,
                external_id TEXT,
                output_prefix TEXT NOT NULL UNIQUE,
                manifest_digest TEXT,
                PRIMARY KEY (run_id, job_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS accepted_jobs (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                job_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                manifest_digest TEXT NOT NULL,
                PRIMARY KEY (run_id, job_id)
            );
            CREATE TABLE IF NOT EXISTS publication_mirror (
                run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
                publication_version INTEGER NOT NULL,
                plan_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            """
        )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        else:
            self.connection.execute("COMMIT")

    def close(self) -> None:
        self.connection.close()

    def _audit(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO audit_events(run_id, event_type, payload) VALUES (?, ?, ?)",
            (run_id, event_type, json.dumps(payload, sort_keys=True)),
        )

    def create_run(
        self,
        pipeline_id: str,
        logical_run_key: str,
        plan_digest: str,
        registry_commit: str,
        run_id: str | None = None,
    ) -> str:
        candidate = run_id or str(uuid4())
        with self._transaction():
            existing = self.connection.execute(
                "SELECT run_id, plan_digest, registry_commit FROM runs "
                "WHERE pipeline_id = ? AND logical_run_key = ?",
                (pipeline_id, logical_run_key),
            ).fetchone()
            if existing is not None:
                changed_plan = existing["plan_digest"] != plan_digest
                changed_commit = existing["registry_commit"] != registry_commit
                if changed_plan or changed_commit:
                    raise LedgerConflict(
                        "logical run already exists with a different immutable plan"
                    )
                return str(existing["run_id"])
            self.connection.execute(
                "INSERT INTO runs(run_id, pipeline_id, logical_run_key, plan_digest, "
                "registry_commit, state) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    candidate,
                    pipeline_id,
                    logical_run_key,
                    plan_digest,
                    registry_commit,
                    RunState.PLANNED.value,
                ),
            )
            self._audit(candidate, "RUN_CREATED", {"logical_run_key": logical_run_key})
        return candidate

    def acquire_lease(self, run_id: str, owner: str, now: float, ttl_seconds: float) -> Lease:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        with self._transaction():
            row = self._run_row(run_id)
            expiry = row["lease_expires_at"]
            if expiry is not None and float(expiry) > now:
                if row["lease_owner"] == owner:
                    return Lease(
                        run_id,
                        owner,
                        int(row["lease_epoch"]),
                        int(row["fencing_token"]),
                        float(expiry),
                    )
                raise LedgerConflict("run lease is held by another owner")
            if row["state"] not in {
                RunState.PLANNED.value,
                RunState.LEASED.value,
                RunState.RUNNING.value,
            }:
                raise LedgerConflict(f"cannot acquire lease in state {row['state']}")
            epoch = int(row["lease_epoch"]) + 1
            token = int(row["fencing_token"]) + 1
            expires = now + ttl_seconds
            next_state = (
                RunState.LEASED.value if row["state"] == RunState.PLANNED.value else row["state"]
            )
            self.connection.execute(
                "UPDATE runs SET lease_owner = ?, lease_epoch = ?, fencing_token = ?, "
                "lease_expires_at = ?, state = ? WHERE run_id = ?",
                (owner, epoch, token, expires, next_state, run_id),
            )
            self._audit(run_id, "LEASE_ACQUIRED", {"owner": owner, "fencing_token": token})
            return Lease(run_id, owner, epoch, token, expires)

    def heartbeat(self, lease: Lease, now: float, ttl_seconds: float) -> Lease:
        expires = now + ttl_seconds
        with self._transaction():
            cursor = self.connection.execute(
                "UPDATE runs SET lease_expires_at = ? WHERE run_id = ? AND lease_owner = ? "
                "AND fencing_token = ? AND lease_expires_at > ?",
                (expires, lease.run_id, lease.owner, lease.fencing_token, now),
            )
            if cursor.rowcount != 1:
                raise LedgerConflict("heartbeat rejected for stale or expired lease")
            self._audit(lease.run_id, "LEASE_HEARTBEAT", {"expires_at": expires})
        return Lease(lease.run_id, lease.owner, lease.lease_epoch, lease.fencing_token, expires)

    def transition(self, run_id: str, expected: RunState, target: RunState, token: int) -> None:
        if target not in _RUN_TRANSITIONS.get(expected, set()):
            raise LedgerConflict(f"illegal run transition {expected} -> {target}")
        with self._transaction():
            cursor = self.connection.execute(
                "UPDATE runs SET state = ? WHERE run_id = ? AND state = ? AND fencing_token = ?",
                (target.value, run_id, expected.value, token),
            )
            if cursor.rowcount != 1:
                raise LedgerConflict("run transition rejected by state or fencing token")
            self._audit(run_id, "RUN_TRANSITION", {"from": expected.value, "to": target.value})

    def create_attempt(
        self, run_id: str, job_id: str, attempt: int, token: int, dispatch_key: str
    ) -> str:
        output_prefix = f"outputs/{run_id}/{job_id}/{attempt}/{token}/"
        with self._transaction():
            row = self._run_row(run_id)
            if int(row["fencing_token"]) != token or row["state"] != RunState.RUNNING.value:
                raise LedgerConflict("attempt creation rejected by run state or fencing token")
            try:
                self.connection.execute(
                    "INSERT INTO attempts(run_id, job_id, attempt, state, fencing_token, "
                    "dispatch_key, output_prefix) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        job_id,
                        attempt,
                        AttemptState.CREATED.value,
                        token,
                        dispatch_key,
                        output_prefix,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                existing = self.connection.execute(
                    "SELECT dispatch_key, output_prefix FROM attempts "
                    "WHERE run_id = ? AND job_id = ? AND attempt = ?",
                    (run_id, job_id, attempt),
                ).fetchone()
                if existing is None or existing["dispatch_key"] != dispatch_key:
                    raise LedgerConflict("attempt identity conflict") from exc
                return str(existing["output_prefix"])
            self._audit(run_id, "ATTEMPT_CREATED", {"job_id": job_id, "attempt": attempt})
        return output_prefix

    def transition_attempt(
        self,
        run_id: str,
        job_id: str,
        attempt: int,
        expected: AttemptState,
        target: AttemptState,
        token: int,
        external_id: str | None = None,
    ) -> None:
        if target not in _ATTEMPT_TRANSITIONS.get(expected, set()):
            raise LedgerConflict(f"illegal attempt transition {expected} -> {target}")
        with self._transaction():
            cursor = self.connection.execute(
                "UPDATE attempts SET state = ?, external_id = COALESCE(?, external_id) "
                "WHERE run_id = ? AND job_id = ? AND attempt = ? AND state = ? "
                "AND fencing_token = ?",
                (
                    target.value,
                    external_id,
                    run_id,
                    job_id,
                    attempt,
                    expected.value,
                    token,
                ),
            )
            if cursor.rowcount != 1:
                raise LedgerConflict("attempt transition rejected")
            self._audit(
                run_id,
                "ATTEMPT_TRANSITION",
                {"job_id": job_id, "attempt": attempt, "from": expected, "to": target},
            )

    def accept_manifest(self, manifest: ResultManifest) -> None:
        with self._transaction():
            row = self._run_row(manifest.run_id)
            if row["plan_digest"] != manifest.plan_digest:
                raise LedgerConflict("manifest plan digest differs from run plan")
            if int(row["fencing_token"]) != manifest.fencing_token:
                raise LedgerConflict("manifest carries a stale fencing token")
            attempt_row = self.connection.execute(
                "SELECT state, fencing_token, manifest_digest FROM attempts "
                "WHERE run_id = ? AND job_id = ? AND attempt = ?",
                (manifest.run_id, manifest.job_id, manifest.attempt),
            ).fetchone()
            if attempt_row is None or attempt_row["state"] != AttemptState.SUCCEEDED.value:
                raise LedgerConflict("only a successful known attempt can be accepted")
            if int(attempt_row["fencing_token"]) != manifest.fencing_token:
                raise LedgerConflict("attempt and manifest fencing tokens differ")
            existing = self.connection.execute(
                "SELECT attempt, manifest_digest FROM accepted_jobs "
                "WHERE run_id = ? AND job_id = ?",
                (manifest.run_id, manifest.job_id),
            ).fetchone()
            if existing is not None:
                if (
                    int(existing["attempt"]) == manifest.attempt
                    and existing["manifest_digest"] == manifest.digest
                ):
                    return
                raise LedgerConflict("job already has a different accepted result")
            self.connection.execute(
                "INSERT INTO accepted_jobs(run_id, job_id, attempt, manifest_digest) "
                "VALUES (?, ?, ?, ?)",
                (manifest.run_id, manifest.job_id, manifest.attempt, manifest.digest),
            )
            self.connection.execute(
                "UPDATE attempts SET manifest_digest = ? WHERE run_id = ? AND job_id = ? "
                "AND attempt = ?",
                (manifest.digest, manifest.run_id, manifest.job_id, manifest.attempt),
            )
            self._audit(
                manifest.run_id,
                "MANIFEST_ACCEPTED",
                {"job_id": manifest.job_id, "digest": manifest.digest},
            )

    def accepted_job_ids(self, run_id: str) -> set[str]:
        rows = self.connection.execute(
            "SELECT job_id FROM accepted_jobs WHERE run_id = ?", (run_id,)
        ).fetchall()
        return {str(row["job_id"]) for row in rows}

    def assert_required_jobs(self, run_id: str, required_job_ids: set[str]) -> None:
        missing = required_job_ids - self.accepted_job_ids(run_id)
        if missing:
            raise LedgerConflict(f"mandatory job results are missing: {sorted(missing)}")

    def mirror_publication(self, run_id: str, version: int, plan_digest: str, token: int) -> None:
        with self._transaction():
            row = self._run_row(run_id)
            if row["plan_digest"] != plan_digest:
                raise LedgerConflict("publication plan digest differs from run plan")
            if int(row["fencing_token"]) != token:
                raise LedgerConflict("publication rejected by fencing token")
            if row["state"] == RunState.PUBLISHED.value:
                existing = self.connection.execute(
                    "SELECT publication_version, plan_digest FROM publication_mirror "
                    "WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
                if (
                    existing is not None
                    and int(existing["publication_version"]) == version
                    and existing["plan_digest"] == plan_digest
                ):
                    return
                raise LedgerConflict("published run has a conflicting catalog mirror")
            if row["state"] != RunState.READY_TO_PUBLISH.value:
                raise LedgerConflict("only a ready run can mirror publication")
            self.connection.execute(
                "INSERT INTO publication_mirror(run_id, publication_version, plan_digest) "
                "VALUES (?, ?, ?)",
                (run_id, version, plan_digest),
            )
            self.connection.execute(
                "UPDATE runs SET state = ? WHERE run_id = ?",
                (RunState.PUBLISHED.value, run_id),
            )
            self._audit(run_id, "PUBLICATION_MIRRORED", {"version": version})

    def run_record(self, run_id: str) -> dict[str, Any]:
        return dict(self._run_row(run_id))

    def audit_count(self, run_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("audit count query returned no aggregate row")
        return int(row["count"])

    def _run_row(self, run_id: str) -> sqlite3.Row:
        row = self.connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise LedgerConflict(f"unknown run_id: {run_id}")
        return cast(sqlite3.Row, row)
