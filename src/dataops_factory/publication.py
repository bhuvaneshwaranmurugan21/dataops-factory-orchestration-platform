from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from dataops_factory.ledger import LedgerConflict, RunState, SqliteRunLedger


class SimulatedCrash(RuntimeError):
    """Injected crash used to prove cross-system publication recovery."""


@dataclass(frozen=True, slots=True)
class ActivePublication:
    pipeline_id: str
    run_id: str
    plan_digest: str
    version: int


class SqlitePublicationCatalog:
    """Local stand-in for Redshift's transactionally updated consumer catalog."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS publication_history (
                pipeline_id TEXT NOT NULL,
                run_id TEXT NOT NULL UNIQUE,
                plan_digest TEXT NOT NULL,
                version INTEGER NOT NULL,
                PRIMARY KEY (pipeline_id, version)
            );
            CREATE TABLE IF NOT EXISTS active_publication (
                pipeline_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                plan_digest TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.connection.close()

    def active(self, pipeline_id: str) -> ActivePublication | None:
        row = self.connection.execute(
            "SELECT * FROM active_publication WHERE pipeline_id = ?", (pipeline_id,)
        ).fetchone()
        if row is None:
            return None
        return ActivePublication(
            str(row["pipeline_id"]),
            str(row["run_id"]),
            str(row["plan_digest"]),
            int(row["version"]),
        )

    def activate(
        self, pipeline_id: str, run_id: str, plan_digest: str, expected_version: int
    ) -> ActivePublication:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing_run = self.connection.execute(
                "SELECT pipeline_id, run_id, plan_digest, version FROM publication_history "
                "WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing_run is not None:
                if existing_run["plan_digest"] != plan_digest:
                    raise LedgerConflict("run publication already exists with a different plan")
                publication = ActivePublication(
                    str(existing_run["pipeline_id"]),
                    str(existing_run["run_id"]),
                    str(existing_run["plan_digest"]),
                    int(existing_run["version"]),
                )
                self.connection.execute("COMMIT")
                return publication

            current = self.active(pipeline_id)
            current_version = 0 if current is None else current.version
            if current_version != expected_version:
                raise LedgerConflict(
                    "publication version conflict: "
                    f"expected {expected_version}, found {current_version}"
                )
            version = current_version + 1
            self.connection.execute(
                "INSERT INTO publication_history(pipeline_id, run_id, plan_digest, version) "
                "VALUES (?, ?, ?, ?)",
                (pipeline_id, run_id, plan_digest, version),
            )
            self.connection.execute(
                "INSERT INTO active_publication(pipeline_id, run_id, plan_digest, version) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(pipeline_id) DO UPDATE SET "
                "run_id=excluded.run_id, plan_digest=excluded.plan_digest, "
                "version=excluded.version",
                (pipeline_id, run_id, plan_digest, version),
            )
            self.connection.execute("COMMIT")
            return ActivePublication(pipeline_id, run_id, plan_digest, version)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise


class PublicationCoordinator:
    def __init__(self, ledger: SqliteRunLedger, catalog: SqlitePublicationCatalog) -> None:
        self.ledger = ledger
        self.catalog = catalog

    def publish(
        self,
        run_id: str,
        expected_catalog_version: int,
        token: int,
        crash_after_catalog_commit: bool = False,
    ) -> ActivePublication:
        run = self.ledger.run_record(run_id)
        if run["state"] != RunState.READY_TO_PUBLISH.value:
            raise LedgerConflict("run must be ready before consumer publication")
        publication = self.catalog.activate(
            str(run["pipeline_id"]),
            run_id,
            str(run["plan_digest"]),
            expected_catalog_version,
        )
        if crash_after_catalog_commit:
            raise SimulatedCrash("publisher crashed after catalog commit")
        self.ledger.mirror_publication(run_id, publication.version, publication.plan_digest, token)
        return publication

    def reconcile(self, pipeline_id: str, token: int) -> ActivePublication:
        publication = self.catalog.active(pipeline_id)
        if publication is None:
            raise LedgerConflict("catalog has no active publication")
        self.ledger.mirror_publication(
            publication.run_id,
            publication.version,
            publication.plan_digest,
            token,
        )
        return publication
