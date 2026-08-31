"""RepoSnapshot: deterministic hash-stamped view of the repository."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..models import FileRecord, RepoSnapshot
from ..safety import iter_repo_files, sha256_file


def take_snapshot(root: Path) -> RepoSnapshot:
    root = root.resolve()
    records: list[FileRecord] = []
    for path in iter_repo_files(root):
        rel = path.relative_to(root).as_posix()
        records.append(FileRecord(path=rel, sha256=sha256_file(path), size=path.stat().st_size))
    fp = hashlib.sha256("\n".join(f"{r.path}\0{r.sha256}" for r in records).encode()).hexdigest()
    return RepoSnapshot(root=str(root), files=records, fingerprint=fp)
