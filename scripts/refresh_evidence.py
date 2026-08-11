from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path

from dataops_factory.provenance import implementation_digest
from dataops_factory.simulator import run_failure_lab, simulate

REPOSITORY = Path(__file__).resolve().parents[1]
REGISTRY = REPOSITORY / "contracts" / "registry" / "jobs.json"
EVIDENCE = REPOSITORY / "evidence" / "verified-local"


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dataops-evidence-") as temporary:
        root = Path(temporary)
        simulation = simulate(REGISTRY, root / "simulation")
        failure_lab = run_failure_lab(REGISTRY, root / "failure-lab")
    _write(EVIDENCE / "simulation-summary.json", simulation)
    _write(EVIDENCE / "failure-lab-summary.json", failure_lab)
    _write(
        EVIDENCE / "provenance.json",
        {
            "classification": "REPRODUCIBLE_LOCAL_EVIDENCE",
            "implementation_digest": implementation_digest(REPOSITORY),
            "registry_sha256": "sha256:" + hashlib.sha256(REGISTRY.read_bytes()).hexdigest(),
            "commands": [
                "make evidence",
                "make verify",
            ],
            "boundary": (
                "No AWS deployment or managed-service throughput claim is inferred from "
                "this evidence."
            ),
        },
    )


if __name__ == "__main__":
    main()
