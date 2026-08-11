from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]
SOURCE_PACKAGE = REPOSITORY / "src" / "dataops_factory"
HANDLERS = {
    "register_run": "register_run",
    "dispatch": "dispatch",
    "reconcile": "reconcile",
    "reconcile_sweep": "reconcile_sweep",
    "validate_manifest": "validate_manifest",
    "mark_failed": "mark_failed",
    "workload": "workload",
    "control_workload": "control_workload",
    "publish_catalog": "publish_catalog",
}


def _write_file(archive: zipfile.ZipFile, source: Path, destination: str) -> None:
    info = zipfile.ZipInfo(destination, date_time=(2024, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, source.read_bytes())


def package(output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    checksums: dict[str, str] = {}
    package_files = sorted(
        path for path in SOURCE_PACKAGE.rglob("*.py") if "__pycache__" not in path.parts
    )
    for archive_name, handler_name in HANDLERS.items():
        destination = output_dir / f"{archive_name}.zip"
        with zipfile.ZipFile(destination, "w") as archive:
            _write_file(archive, REPOSITORY / "lambdas" / handler_name / "handler.py", "handler.py")
            for source in package_files:
                relative = source.relative_to(REPOSITORY / "src")
                _write_file(archive, source, relative.as_posix())
        checksums[destination.name] = hashlib.sha256(destination.read_bytes()).hexdigest()
    (output_dir / "checksums.json").write_text(
        json.dumps(checksums, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return checksums


if __name__ == "__main__":
    package(REPOSITORY / "dist" / "lambdas")
