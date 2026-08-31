"""Orchestrator: wires scanner -> planner -> adapters -> verification -> platform.

Grows slice by slice; every public function is CLI-facing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import BudgetLedger
from .config import Settings, load_settings
from .errors import AgentError, NeedsHuman
from .models import CapabilityStatus, EvidenceKind, IntegrationPlan, Mode, PatchSet
from .planner import build_plan
from .scanner import InspectionResult, inspect_repo


@dataclass
class PlanContext:
    inspection: InspectionResult
    settings: Settings
    ledger: BudgetLedger


def _gate_on_matrix(inspection: InspectionResult) -> None:
    """NEEDS_HUMAN topologies stop before any planning."""
    matrix = inspection.matrix
    if matrix.decision == CapabilityStatus.SUPPORTED:
        return
    detail = "; ".join(f"{c.name}: {c.reason}" for c in matrix.capabilities
                       if c.status != CapabilityStatus.SUPPORTED)
    raise NeedsHuman(matrix.decision_reason,
                     f"repository is not in the supported matrix ({matrix.decision_reason})",
                     detail=detail)


def make_plan(
    repo: Path,
    mode: Mode,
    *,
    model_mode: str = "fake",
    agent_id: int | None = None,
    settings: Settings | None = None,
) -> tuple[IntegrationPlan, PlanContext]:
    settings = settings or load_settings(model_mode=model_mode)
    inspection = inspect_repo(repo)
    _gate_on_matrix(inspection)
    ledger = BudgetLedger(settings)
    plan = build_plan(inspection, mode, model_mode=model_mode, agent_id=agent_id,
                      settings=settings, ledger=ledger)
    return plan, PlanContext(inspection=inspection, settings=settings, ledger=ledger)


def is_already_integrated(inspection: InspectionResult) -> bool:
    return bool(inspection.evidence_map.of_kind(EvidenceKind.EXISTING_CEKURA))


# ----------------------------------------------------------------- filled in later slices


def render_patchset(repo: Path, *, mode: Mode, model_mode: str = "fake",
                    agent_id: int | None = None) -> PatchSet:
    raise AgentError("adapters not wired yet (slice C)")


def integrate_repo(*args, **kwargs):
    raise AgentError("orchestration not wired yet (slice F)")


def verify_repo(*args, **kwargs):
    raise AgentError("verification not wired yet (slice F)")


def prepare_platform_state(*args, **kwargs):
    raise AgentError("platform layer not wired yet (slice E)")


def apply_platform_state(*args, **kwargs):
    raise AgentError("platform layer not wired yet (slice E)")
