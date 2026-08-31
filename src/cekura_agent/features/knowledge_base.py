"""Knowledge base: discovery -> reviewed upload manifest (approval-gated).

Rule (Cekura docs): if the agent reads documents at runtime, upload them for evaluator
generation — the retrieval mechanism is irrelevant. But discovery alone NEVER uploads:
every entry needs explicit approval, and this manifest feeds Cekura's evaluator KB only.
Runtime RAG stores are never modified.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from ..models import EvidenceKind, KBManifestEntry
from ..scanner import InspectionResult

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{8,}\d")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _privacy_flags(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ["unreadable_binary"]
    flags = []
    if _EMAIL_RE.search(text):
        flags.append("possible_pii_email")
    if _SSN_RE.search(text):
        flags.append("possible_pii_ssn")
    if _PHONE_RE.search(text):
        flags.append("possible_pii_phone")
    return flags


def build_kb_manifest(inspection: InspectionResult) -> list[KBManifestEntry]:
    root = inspection.root
    emap = inspection.evidence_map
    by_path: dict[str, KBManifestEntry] = {}
    records = {f.path: f for f in inspection.snapshot.files}

    doc_evidence = [e for e in emap.of_kind(EvidenceKind.KB_SOURCE)
                    if e.detail.get("style") == "doc_file"]
    code_read_paths = {
        str(e.detail.get("path")) for e in emap.of_kind(EvidenceKind.KB_SOURCE)
        if e.detail.get("style") == "code_file_read"
    }

    for ev in doc_evidence:
        rel = str(ev.detail.get("path") or ev.file)
        record = records.get(rel)
        if record is None or rel in by_path:
            continue
        path = root / rel
        referenced = bool(ev.detail.get("referenced_in_code"))
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        by_path[rel] = KBManifestEntry(
            path=rel,
            sha256=record.sha256,
            size=record.size,
            media_type=str(ev.detail.get("media_type", "application/octet-stream")),
            scope=("read by agent code at runtime" if referenced
                   else "document directory candidate (verify the agent actually reads it)"),
            owner_approval_required=True,
            approved=False,  # discovery NEVER implies upload
            privacy_flags=_privacy_flags(path),
            license=None,
            freshness=mtime,
            evidence_ids=[ev.id],
        )

    # code reads of files that live outside doc-hint dirs still count
    for rel in sorted(code_read_paths):
        if rel in by_path or rel not in records:
            continue
        path = root / rel
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
        by_path[rel] = KBManifestEntry(
            path=rel, sha256=records[rel].sha256, size=records[rel].size,
            media_type="text/markdown" if rel.endswith(".md") else "text/plain",
            scope="read by agent code at runtime",
            owner_approval_required=True, approved=False,
            privacy_flags=_privacy_flags(path), license=None, freshness=mtime,
        )

    return [by_path[k] for k in sorted(by_path)]


def approved_entries(manifest: list[KBManifestEntry]) -> list[KBManifestEntry]:
    return [e for e in manifest if e.approved]
