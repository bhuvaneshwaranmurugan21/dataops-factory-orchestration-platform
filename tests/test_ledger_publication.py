from __future__ import annotations

from pathlib import Path

import pytest

from dataops_factory.ledger import LedgerConflict, RunState, SqliteRunLedger
from dataops_factory.publication import (
    PublicationCoordinator,
    SimulatedCrash,
    SqlitePublicationCatalog,
)


def ready_run(tmp_path: Path) -> tuple[SqliteRunLedger, SqlitePublicationCatalog, str, int]:
    ledger = SqliteRunLedger(tmp_path / "ledger.sqlite")
    catalog = SqlitePublicationCatalog(tmp_path / "catalog.sqlite")
    run_id = ledger.create_run("factory", "day/1", "digest", "commit", "run-1")
    lease = ledger.acquire_lease(run_id, "scheduler", 10, 5)
    ledger.transition(run_id, RunState.LEASED, RunState.RUNNING, lease.fencing_token)
    ledger.transition(run_id, RunState.RUNNING, RunState.VALIDATING, lease.fencing_token)
    ledger.transition(run_id, RunState.VALIDATING, RunState.READY_TO_PUBLISH, lease.fencing_token)
    return ledger, catalog, run_id, lease.fencing_token


def test_publication_is_idempotent(tmp_path: Path) -> None:
    ledger, catalog, run_id, token = ready_run(tmp_path)
    coordinator = PublicationCoordinator(ledger, catalog)
    first = coordinator.publish(run_id, 0, token)
    assert first.version == 1
    assert catalog.active("factory") == first
    assert ledger.run_record(run_id)["state"] == RunState.PUBLISHED.value
    ledger.mirror_publication(run_id, 1, "digest", token)
    ledger.close()
    catalog.close()


def test_crash_after_catalog_commit_is_reconciled(tmp_path: Path) -> None:
    ledger, catalog, run_id, token = ready_run(tmp_path)
    coordinator = PublicationCoordinator(ledger, catalog)
    with pytest.raises(SimulatedCrash):
        coordinator.publish(run_id, 0, token, crash_after_catalog_commit=True)
    assert ledger.run_record(run_id)["state"] == RunState.READY_TO_PUBLISH.value
    publication = coordinator.reconcile("factory", token)
    assert publication.run_id == run_id
    assert ledger.run_record(run_id)["state"] == RunState.PUBLISHED.value


def test_catalog_compare_and_swap_rejects_stale_publisher(tmp_path: Path) -> None:
    catalog = SqlitePublicationCatalog(tmp_path / "catalog.sqlite")
    first = catalog.activate("factory", "run-1", "digest-1", 0)
    assert catalog.activate("factory", "run-1", "digest-1", 0) == first
    with pytest.raises(LedgerConflict, match="version conflict"):
        catalog.activate("factory", "run-2", "digest-2", 0)
    with pytest.raises(LedgerConflict, match="different plan"):
        catalog.activate("factory", "run-1", "other", 1)


def test_invalid_publication_state_and_fence_are_rejected(tmp_path: Path) -> None:
    ledger = SqliteRunLedger(tmp_path / "ledger.sqlite")
    catalog = SqlitePublicationCatalog(tmp_path / "catalog.sqlite")
    run_id = ledger.create_run("factory", "day/1", "digest", "commit")
    lease = ledger.acquire_lease(run_id, "scheduler", 10, 5)
    with pytest.raises(LedgerConflict, match="ready"):
        PublicationCoordinator(ledger, catalog).publish(run_id, 0, lease.fencing_token)
    with pytest.raises(LedgerConflict, match="no active"):
        PublicationCoordinator(ledger, catalog).reconcile("factory", lease.fencing_token)
