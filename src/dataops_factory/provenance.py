from __future__ import annotations

import hashlib
import os
import shutil
import subprocess  # nosec B404
from pathlib import Path

CANONICAL_ROOTS = ("src", "tests", "contracts")


class ProvenanceError(RuntimeError):
    """Raised when repository provenance cannot be established safely."""


# Git is the provenance authority for this repository-scoped operation.
def _git_paths(repository: Path, *arguments: str) -> tuple[Path, ...]:
    git = shutil.which("git")
    if git is None:
        raise ProvenanceError("git is required to compute repository provenance")
    # The absolute executable and all command arguments are controlled; no shell is involved.
    completed = subprocess.run(  # nosec B603
        [git, "-C", str(repository), *arguments],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ProvenanceError(f"unable to inspect canonical repository inputs: {detail}")
    return tuple(Path(os.fsdecode(item)) for item in completed.stdout.split(b"\0") if item)


def canonical_inputs(repository: Path) -> tuple[Path, ...]:
    repository = repository.resolve()
    tracked = _git_paths(repository, "ls-files", "-z", "--", *CANONICAL_ROOTS)
    if not tracked:
        raise ProvenanceError("no tracked canonical inputs were found")

    untracked = _git_paths(
        repository,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        "--",
        *CANONICAL_ROOTS,
    )
    if untracked:
        listed = ", ".join(path.as_posix() for path in sorted(untracked))
        raise ProvenanceError(f"untracked canonical inputs must be reviewed and staged: {listed}")

    for relative in tracked:
        path = repository / relative
        if path.is_symlink() or not path.is_file():
            raise ProvenanceError(f"canonical input is not a regular file: {relative.as_posix()}")
    return tuple(sorted(tracked, key=lambda path: path.as_posix()))


def implementation_digest(repository: Path) -> str:
    digest = hashlib.sha256()
    for relative in canonical_inputs(repository):
        name = relative.as_posix().encode()
        content = (repository / relative).read_bytes()
        digest.update(len(name).to_bytes(8, byteorder="big"))
        digest.update(name)
        digest.update(len(content).to_bytes(8, byteorder="big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()
