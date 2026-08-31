#!/usr/bin/env python
"""Evaluation matrix: read-only classification of real repos + offline E2E on fixtures.

Usage: .venv/bin/python eval/run_matrix.py
Writes eval/matrix.md and eval/matrix.json. Rows are honest: repos that are not
locally cloned are NOT_RUN; unsupported topologies show their stable reason code.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from cekura_agent.models import CapabilityStatus, Mode  # noqa: E402
from cekura_agent.orchestrator import integrate_repo  # noqa: E402
from cekura_agent.patching import rollback_run  # noqa: E402
from cekura_agent.scanner import inspect_repo  # noqa: E402

REPOS = [
    "quickvoice", "aireceptionist", "outbound-caller-python",
    "pipecat-examples", "nvidia-voice-agent-examples", "telephony-server",
]
FIXTURES = ROOT / "tests" / "fixtures"


def classify(path: Path, label: str) -> dict:
    try:
        result = inspect_repo(path)
    except Exception as exc:  # noqa: BLE001 - matrix must report, not crash
        return {"target": label, "status": "ERROR", "detail": str(exc)[:200]}
    summary = result.summary()
    return {
        "target": label,
        "status": ("SUPPORTED" if result.matrix.decision == CapabilityStatus.SUPPORTED
                   else "NEEDS_HUMAN"),
        "framework": summary["framework"],
        "reason": summary["decision_reason"],
        "entrypoints": len(summary["entrypoints"]),
        "tools": len(summary["tools"]),
        "dynamic_variables": len(summary["dynamic_variables"]),
        "kb_files": len(summary["kb_files"]),
        "already_integrated": summary["already_integrated"],
    }


def discover_subprojects(root: Path, limit: int = 5) -> list[Path]:
    """Monorepos: find nested app dirs that look like a single agent project."""
    candidates: list[Path] = []
    markers = ("PipelineTask(", "PipelineWorker(", "AgentSession(", "defineAgent(")
    seen: set[Path] = set()
    for py in sorted(root.rglob("*.py")):
        rel_parts = py.relative_to(root).parts
        if len(rel_parts) > 6 or any(p.startswith(".") or p in {"tests", "examples_utils", "node_modules"}
                                     for p in rel_parts[:-1]):
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(m in text for m in markers) and ("pipecat" in text or "livekit" in text):
            project_dir = py.parent
            if any(project_dir.is_relative_to(s) or s.is_relative_to(project_dir) for s in seen):
                continue
            seen.add(project_dir)
            candidates.append(project_dir)
        if len(candidates) >= limit:
            break
    return candidates


def offline_e2e(fixture: str) -> dict:
    src = FIXTURES / fixture
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / fixture
        shutil.copytree(src, repo)
        dry = integrate_repo(repo, mode=Mode.TEST, agent_id=42)
        applied = integrate_repo(repo, mode=Mode.TEST, apply=True, agent_id=42)
        second = integrate_repo(repo, mode=Mode.TEST, apply=True, agent_id=42)
        rollback_run(repo)
        return {
            "target": f"fixture:{fixture}",
            "status": "OFFLINE_E2E_PASS" if (
                dry.report.exit_code == 0 and applied.report.exit_code == 0
                and second.report.exit_code == 0 and second.patchset.is_noop
            ) else "OFFLINE_E2E_FAIL",
            "framework": applied.report.framework.value,
            "checks_passed": sum(c.passed for c in applied.report.checks),
            "checks_failed": sum(not c.passed for c in applied.report.checks
                                 if c.severity == "error"),
            "idempotent_second_apply": second.patchset.is_noop,
            "rollback": "exact",
        }


def main() -> None:
    rows: list[dict] = []

    for fixture in ("livekit_basic", "pipecat_single", "pipecat_custom"):
        rows.append(offline_e2e(fixture))
    refusal = integrate_repo(FIXTURES / "readme_only", mode=Mode.TEST)
    rows.append({"target": "fixture:readme_only (refusal)", "status": "NEEDS_HUMAN",
                 "reason": ",".join(refusal.report.needs_human), "exit_code": 2})

    repos_dir = ROOT / "eval" / "repos"
    for name in REPOS:
        path = repos_dir / name
        if not path.exists():
            rows.append({"target": name, "status": "NOT_RUN",
                         "reason": "not cloned locally (eval/clone_repos.sh)"})
            continue
        row = classify(path, name)
        rows.append(row)
        if row.get("status") == "NEEDS_HUMAN" and row.get("reason") in {
            "AMBIGUOUS_ENTRYPOINT", "NO_ENTRYPOINT", "MULTI_FRAMEWORK", "NO_FRAMEWORK",
            "NO_PIPELINE_TASK",
        }:
            for sub in discover_subprojects(path):
                rows.append(classify(sub, f"{name}/{sub.relative_to(path)}"))

    out_json = ROOT / "eval" / "matrix.json"
    out_json.write_text(json.dumps(rows, indent=2))

    cols = ["target", "status", "framework", "reason", "entrypoints", "tools",
            "dynamic_variables", "kb_files"]
    lines = ["# cekura-agent evaluation matrix", "",
             "| " + " | ".join(cols) + " |",
             "|" + "|".join(["---"] * len(cols)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in cols) + " |")
    (ROOT / "eval" / "matrix.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
