"""Patch executor: render -> pre-validate -> backup -> write -> manifest; exact rollback.

Guarantees:
- nothing is written unless every target file still matches the content the patch
  was computed against (compare-before-write);
- originals are backed up under <repo>/.cekura-agent/backups/<run_id>/ before any write;
- a mid-apply failure restores every already-written file (all-or-nothing);
- rollback restores exact original hashes and deletes files the patch created.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .errors import AgentError
from .models import (
    IntegrationPlan,
    PatchEdit,
    PatchSet,
    RollbackEntry,
    RollbackManifest,
)
from .safety import ensure_within_root, sha256_bytes
from .scanner import InspectionResult

STATE_DIR = ".cekura-agent"


def render(plan: IntegrationPlan, inspection: InspectionResult) -> tuple[PatchSet, dict[str, str]]:
    """Compute the PatchSet plus the full new content of every touched file."""
    from .adapters import build_file_edits, unified_diff

    contents = build_file_edits(plan, inspection)
    edits: list[PatchEdit] = []
    for rel, after in sorted(contents.items()):
        target = inspection.root / rel
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        if before == after:
            continue
        edits.append(PatchEdit(
            file=rel,
            before_sha256=sha256_bytes(before.encode()),
            after_sha256=sha256_bytes(after.encode()),
            diff=unified_diff(rel, before, after),
        ))
    return PatchSet(plan_id=plan.plan_id, edits=edits), contents


def render_patchset(plan: IntegrationPlan, inspection: InspectionResult) -> PatchSet:
    return render(plan, inspection)[0]


def _runs_dir(root: Path) -> Path:
    return root / STATE_DIR / "runs"


def _backup_dir(root: Path, run_id: str) -> Path:
    return root / STATE_DIR / "backups" / run_id


def apply_patchset(root: Path, patchset: PatchSet,
                   file_contents: dict[str, str]) -> RollbackManifest:
    """Write new contents atomically-ish with backups. `file_contents` maps the
    files in `patchset` to their full new content."""
    root = root.resolve()
    run_id = patchset.plan_id

    # pre-validate every target against the hash the patch was computed from
    for edit in patchset.edits:
        target = ensure_within_root(root, Path(edit.file))
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if sha256_bytes(current.encode()) != edit.before_sha256:
            raise AgentError(
                f"refusing to apply: {edit.file} changed since the patch was computed "
                "(re-run integrate to re-plan)"
            )

    backup_dir = _backup_dir(root, run_id)
    backup_dir.mkdir(parents=True, exist_ok=True)
    entries: list[RollbackEntry] = []
    written: list[tuple[Path, Path | None]] = []  # (target, backup or None if created)

    try:
        for edit in patchset.edits:
            target = ensure_within_root(root, Path(edit.file))
            created = not target.exists()
            backup_path: Path | None = None
            if not created:
                backup_path = backup_dir / edit.file
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(file_contents[edit.file], encoding="utf-8")
            written.append((target, backup_path))
            entries.append(RollbackEntry(
                file=edit.file,
                original_sha256=edit.before_sha256,
                patched_sha256=edit.after_sha256,
                backup_path=str((backup_path or Path("")).relative_to(root)) if backup_path else "",
                created=created,
            ))
    except Exception:
        for target, backup_path in reversed(written):
            if backup_path is None:
                target.unlink(missing_ok=True)
            else:
                shutil.copy2(backup_path, target)
        raise

    manifest = RollbackManifest(run_id=run_id, repo_root=str(root), entries=entries)
    runs_dir = _runs_dir(root)
    runs_dir.mkdir(parents=True, exist_ok=True)
    (runs_dir / f"{run_id}.json").write_text(manifest.model_dump_json(indent=2))
    (runs_dir / "latest").write_text(run_id)
    return manifest


def load_manifest(root: Path, run_id: str | None = None) -> RollbackManifest:
    runs_dir = _runs_dir(root.resolve())
    if run_id is None:
        latest = runs_dir / "latest"
        if not latest.exists():
            raise AgentError("no integrate --apply run recorded for this repo")
        run_id = latest.read_text().strip()
    manifest_path = runs_dir / f"{run_id}.json"
    if not manifest_path.exists():
        raise AgentError(f"no rollback manifest for run {run_id}")
    return RollbackManifest.model_validate(json.loads(manifest_path.read_text()))


def rollback_run(root: Path, run_id: str | None = None, force: bool = False) -> list[str]:
    root = root.resolve()
    manifest = load_manifest(root, run_id)
    # verify current state matches what the patch produced (unless forced)
    if not force:
        for entry in manifest.entries:
            target = ensure_within_root(root, Path(entry.file))
            current = target.read_text(encoding="utf-8") if target.exists() else ""
            if sha256_bytes(current.encode()) != entry.patched_sha256:
                raise AgentError(
                    f"{entry.file} changed after the patch; use --force to restore anyway"
                )
    restored: list[str] = []
    for entry in manifest.entries:
        target = ensure_within_root(root, Path(entry.file))
        if entry.created:
            target.unlink(missing_ok=True)
        else:
            backup = ensure_within_root(root, Path(entry.backup_path))
            shutil.copy2(backup, target)
        restored.append(entry.file)
    runs_dir = _runs_dir(root)
    (runs_dir / f"{manifest.run_id}.json").rename(runs_dir / f"{manifest.run_id}.rolledback.json")
    latest = runs_dir / "latest"
    if latest.exists() and latest.read_text().strip() == manifest.run_id:
        latest.unlink()
    return restored
