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
    # Feature specs are host-computed from evidence (deterministic), never model-invented.
    from .features import build_dynamic_variable_specs, build_kb_manifest, build_mock_tool_specs

    plan = plan.model_copy(update={
        "mock_tools": build_mock_tool_specs(inspection),
        "dynamic_variables": build_dynamic_variable_specs(inspection),
        "kb_manifest": build_kb_manifest(inspection),
    })
    return plan, PlanContext(inspection=inspection, settings=settings, ledger=ledger)


def is_already_integrated(inspection: InspectionResult) -> bool:
    return bool(inspection.evidence_map.of_kind(EvidenceKind.EXISTING_CEKURA))


# ----------------------------------------------------------------- filled in later slices


def render_patchset(repo: Path, *, mode: Mode, model_mode: str = "fake",
                    agent_id: int | None = None) -> PatchSet:
    from .patching import render_patchset as _render

    plan, ctx = make_plan(repo, mode, model_mode=model_mode, agent_id=agent_id)
    return _render(plan, ctx.inspection)


def integrate_repo(*args, **kwargs):
    raise AgentError("orchestration not wired yet (slice F)")


def verify_repo(*args, **kwargs):
    raise AgentError("verification not wired yet (slice F)")


def prepare_platform_state(
    repo: Path,
    *,
    mode: Mode,
    agent_id: int | None = None,
    project_id: int | None = None,
):
    """Build the CekuraDesiredState (agent, mock tools, dynamic variables, KB) from the repo."""
    from .config import CEKURA_DASHBOARD_URL
    from .models import CekuraAgentSpec, CekuraDesiredState

    plan, ctx = make_plan(repo, mode, agent_id=agent_id)
    inspection = ctx.inspection
    emap = inspection.evidence_map
    framework = inspection.matrix.framework.value

    prompts = [e for e in emap.of_kind(EvidenceKind.OTHER) if e.detail.get("style") == "system_prompt"]
    description = (prompts[0].detail["text"] if prompts else
                   f"{framework} voice agent (no system prompt found in code — fill in manually)")
    provider_config: dict = {"tracing_enabled": mode == Mode.TEST}
    agent_names = [e.detail.get("agent_name") for e in emap.of_kind(EvidenceKind.OTHER)
                   if e.detail.get("style") == "worker_options" and e.detail.get("agent_name")]
    if framework == "livekit" and agent_names:
        provider_config["agent_name"] = agent_names[0]

    spec = None if agent_id is not None else CekuraAgentSpec(
        name=f"{inspection.root.name} ({framework})",
        description=description,
        project=project_id,
        provider_type=framework,
        provider_config=provider_config,
    )
    return CekuraDesiredState(
        agent_id=agent_id,
        agent=spec,
        mock_tools=plan.mock_tools,
        dynamic_variables=plan.dynamic_variables,
        kb_uploads=plan.kb_manifest,
        repo_root=str(inspection.root),
        dashboard_url=(f"{CEKURA_DASHBOARD_URL}/agents/{agent_id}" if agent_id
                       else CEKURA_DASHBOARD_URL),
        notes=(f"mode={mode.value}; tracing_enabled={provider_config['tracing_enabled']}; "
               "KB entries upload only when approved=true; expect on the dashboard: "
               f"{len(plan.mock_tools)} mock tool(s), {len(plan.dynamic_variables)} dynamic variable(s)"),
    )


def apply_platform_state(
    desired_state_path: Path,
    *,
    platform_mode: str = "offline",
    apply: bool = False,
    approve_deletions: bool = False,
    base_url: str | None = None,
):
    """Reconcile the platform: GET -> exact diff -> apply once -> GET-after compare."""
    import json as _json
    import os

    from .errors import BlockedByAccess
    from .models import CekuraDesiredState
    from .platform import CekuraClient, reconcile

    state = CekuraDesiredState.model_validate(_json.loads(Path(desired_state_path).read_text()))

    if platform_mode == "offline":
        if base_url is None:
            raise BlockedByAccess(
                "PLATFORM_OFFLINE",
                "offline platform mode has no server; pass --platform-mode staging with "
                "CEKURA_API_KEY, or --base-url pointing at a local fake server",
            )
        api_key = os.environ.get("CEKURA_API_KEY") or "test-cekura-key"
    elif platform_mode == "staging":
        if base_url is not None:
            raise AgentError("--base-url override is only allowed in offline mode (tests)")
        settings = load_settings()
        api_key = settings.cekura_api_key
        if not api_key:
            raise BlockedByAccess("CEKURA_KEY_MISSING",
                                  "CEKURA_API_KEY is not set; cannot target the Cekura platform")
        base_url = settings.cekura_base_url
    else:
        raise AgentError(f"unknown --platform-mode {platform_mode!r} (expected offline|staging)")

    client = CekuraClient(base_url, api_key)
    kb_root = Path(state.repo_root) if state.repo_root else None
    return reconcile(client, state, apply=apply, approve_deletions=approve_deletions,
                     kb_files_root=kb_root)
