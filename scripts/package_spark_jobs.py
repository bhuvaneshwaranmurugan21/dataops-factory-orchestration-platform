from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[1]


def package(output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "spark_jobs.zip"
    with zipfile.ZipFile(destination, "w") as archive:
        for source in sorted((REPOSITORY / "spark_jobs").glob("*.py")):
            info = zipfile.ZipInfo(f"spark_jobs/{source.name}", date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, source.read_bytes())
    result = {destination.name: hashlib.sha256(destination.read_bytes()).hexdigest()}
    (output_dir / "checksums.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


if __name__ == "__main__":
    package(REPOSITORY / "dist" / "spark_jobs")
