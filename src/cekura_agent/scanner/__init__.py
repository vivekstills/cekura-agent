"""Repository intelligence: snapshot -> evidence -> capability matrix."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import CapabilityMatrix, EvidenceKind, EvidenceMap, RepoSnapshot
from .capability import active_entrypoints, build_matrix
from .evidence import extract_evidence
from .scan import take_snapshot

__all__ = ["InspectionResult", "inspect_repo", "active_entrypoints"]


@dataclass
class InspectionResult:
    root: Path
    snapshot: RepoSnapshot
    evidence_map: EvidenceMap
    matrix: CapabilityMatrix

    def summary(self) -> dict:
        emap = self.evidence_map
        tools = sorted({
            (e.detail.get("name") or e.symbol)
            for e in emap.of_kind(EvidenceKind.TOOL_DEF)
        })
        variables = sorted(
            {e.symbol for e in emap.of_kind(EvidenceKind.PROMPT_PLACEHOLDER)}
            | {e.symbol for e in emap.of_kind(EvidenceKind.RUNTIME_INPUT) if e.symbol}
        )
        kb_files = sorted({
            e.detail.get("path") or e.file
            for e in emap.of_kind(EvidenceKind.KB_SOURCE)
            if e.detail.get("style") in {"doc_file", "code_file_read"}
        })
        entries = [
            {"file": e.file, "line": e.line_start, "function": e.symbol,
             "framework": e.detail.get("framework")}
            for e in emap.of_kind(EvidenceKind.ENTRYPOINT)
        ]
        rejected = [
            {"id": e.id, "kind": e.kind.value, "file": e.file, "reason": e.reject_reason}
            for e in emap.evidence if e.rejected
        ]
        return {
            "root": str(self.root),
            "fingerprint": self.snapshot.fingerprint,
            "framework": self.matrix.framework.value,
            "decision": self.matrix.decision.value,
            "decision_reason": self.matrix.decision_reason,
            "capabilities": [
                {"name": c.name, "status": c.status.value, "reason": c.reason}
                for c in self.matrix.capabilities
            ],
            "entrypoints": entries,
            "tools": tools,
            "dynamic_variables": variables,
            "kb_files": kb_files,
            "already_integrated": bool(emap.of_kind(EvidenceKind.EXISTING_CEKURA)),
            "evidence_count": len(emap.evidence),
            "rejected_evidence": rejected,
        }


def inspect_repo(root: Path) -> InspectionResult:
    root = root.resolve()
    snapshot = take_snapshot(root)
    evidence = extract_evidence(root, snapshot)
    emap = EvidenceMap(snapshot_fingerprint=snapshot.fingerprint, evidence=evidence)
    matrix = build_matrix(snapshot.fingerprint, emap)
    return InspectionResult(root=root, snapshot=snapshot, evidence_map=emap, matrix=matrix)
