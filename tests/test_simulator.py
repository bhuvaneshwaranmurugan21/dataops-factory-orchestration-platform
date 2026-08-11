from __future__ import annotations

import json
from pathlib import Path

from dataops_factory.cli import main
from dataops_factory.simulator import run_failure_lab, simulate


def test_complete_local_simulation(registry_path: Path, tmp_path: Path) -> None:
    result = simulate(registry_path, tmp_path / "simulation")
    assert result["classification"] == "VERIFIED_LOCAL_SIMULATION"
    assert result["jobs_planned"] == 36
    assert result["jobs_accepted"] == 36
    assert result["accounts_modelled"] == 5
    assert result["publication"]["consumer_state"] == "PUBLISHED"
    assert result["manifest_digest_count"] == 36
    lines = (tmp_path / "simulation" / "manifests.jsonl").read_text().splitlines()
    assert len(lines) == 36
    assert all(json.loads(line)["signature"] for line in lines)


def test_failure_lab_executes_all_trials(registry_path: Path, tmp_path: Path) -> None:
    result = run_failure_lab(registry_path, tmp_path / "failure")
    assert result["failed"] == 0
    assert result["passed"] >= 18
    assert {trial["name"] for trial in result["trials"]} >= {
        "publisher_crash_after_catalog_commit",
        "stale_manifest_fence",
        "concurrent_publication_version",
        "tampered_manifest",
    }


def test_cli_compile_and_simulate(registry_path: Path, tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    assert main(["compile", "--registry", str(registry_path), "--output", str(plan)]) == 0
    assert json.loads(plan.read_text())["plan_digest"].startswith("sha256:")
    assert (
        main(
            [
                "simulate",
                "--registry",
                str(registry_path),
                "--work-dir",
                str(tmp_path / "cli-sim"),
            ]
        )
        == 0
    )
