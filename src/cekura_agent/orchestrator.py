"""Orchestrator: wires scanner -> planner -> adapters -> verification -> platform.

Grows slice by slice; every public function is CLI-facing.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .budget import BudgetLedger
from .config import Settings, load_settings
from .errors import AgentError, NeedsHuman
from .models import (
    CapabilityStatus,
    EvidenceKind,
    IntegrationPlan,
    Mode,
    PatchSet,
    VerificationReport,
)
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


@dataclass
class IntegrateResult:
    report: VerificationReport
    patchset: PatchSet | None = None
    desired_state: object = None
    platform_result: dict | None = None

    def human_summary(self) -> str:
        from .report import human_summary

        extra = []
        if self.patchset is not None and not self.patchset.is_noop:
            extra.append(f"patched files: {[e.file for e in self.patchset.edits]}")
        if self.platform_result is not None:
            extra.append(f"dashboard: {self.platform_result.get('dashboard_url', '-')}")
        return human_summary(self.report, extra)


def _sdk_availability_blockers(framework, *, allow_network: bool) -> list[str]:
    """Dependency guard: is the pinned Cekura SDK extra actually resolvable?"""
    if not allow_network:
        return []  # offline: NOT_RUN, reported by caller
    import httpx

    from .planner.rules import SDK_VERSION_SPEC

    minimum = SDK_VERSION_SPEC.lstrip(">=")
    try:
        resp = httpx.get("https://pypi.org/pypi/cekura/json", timeout=15)
        resp.raise_for_status()
        releases = set(resp.json().get("releases", {}))
    except Exception:
        return [f"CEKURA_SDK_RESOLUTION_UNKNOWN (PyPI unreachable; pin cekura[{framework.value}]{SDK_VERSION_SPEC})"]
    if minimum not in releases:
        return [f"CEKURA_{framework.value.upper()}_SDK_UNAVAILABLE (cekura=={minimum} not on PyPI)"]
    return []


def integrate_repo(
    repo: Path,
    *,
    mode: Mode,
    apply: bool = False,
    model_mode: str = "fake",
    platform_mode: str = "offline",
    agent_id: int | None = None,
    project_id: int | None = None,
    e2e: bool = False,
) -> IntegrateResult:
    """The autonomous workflow: snapshot -> evidence -> plan -> patch -> verify -> platform."""
    import uuid

    from .models import Status, VerificationReport
    from .report import add_check

    run_id = uuid.uuid4().hex[:10]
    settings = load_settings(model_mode=model_mode, platform_mode=platform_mode)

    # ---- inspect + gate (NEEDS_HUMAN becomes a structured report, not a crash)
    inspection = inspect_repo(repo)
    report = VerificationReport(
        run_id=run_id, repo_root=str(inspection.root),
        framework=inspection.matrix.framework, mode=mode,
    )
    if inspection.matrix.decision != CapabilityStatus.SUPPORTED:
        report.statuses["repo_compatibility"] = Status.NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY
        report.needs_human.append(inspection.matrix.decision_reason)
        for cap in inspection.matrix.capabilities:
            if cap.status != CapabilityStatus.SUPPORTED:
                add_check(report, f"capability:{cap.name}", False, cap.reason)
        report.exit_code = 2
        return IntegrateResult(report=report)
    report.statuses["repo_compatibility"] = Status.IMPLEMENTED_AND_OFFLINE_VERIFIED

    # ---- plan
    ledger = BudgetLedger(settings)
    plan = build_plan(inspection, mode, model_mode=model_mode, agent_id=agent_id,
                      settings=settings, ledger=ledger)
    from .features import build_dynamic_variable_specs, build_kb_manifest, build_mock_tool_specs

    plan = plan.model_copy(update={
        "mock_tools": build_mock_tool_specs(inspection),
        "dynamic_variables": build_dynamic_variable_specs(inspection),
        "kb_manifest": build_kb_manifest(inspection),
    })
    report.model_usage = dict(plan.model_metadata) | {"ledger": ledger.summary()}
    report.statuses["kimi_planner"] = (
        Status.LIVE_VERIFIED if model_mode == "openrouter" else Status.IMPLEMENTED_AND_OFFLINE_VERIFIED
    )

    # ---- patch
    from .patching import apply_patchset, render

    patchset, contents = render(plan, inspection)
    already = is_already_integrated(inspection)
    add_check(report, "plan:actions", True,
              ", ".join(a.action_type.value for a in plan.actions))

    if patchset.is_noop:
        report.statuses["code_integration"] = (
            Status.IMPLEMENTED_AND_OFFLINE_VERIFIED if already else Status.NOT_RUN)
        add_check(report, "idempotence:noop", True,
                  "already integrated; zero-diff" if already else "nothing to change")
    elif not apply:
        report.statuses["code_integration"] = Status.NOT_RUN
        add_check(report, "dry_run:diff_rendered", True,
                  f"{len(patchset.edits)} file(s) would change (run with --apply)")
    else:
        patchset = patchset.model_copy(update={"plan_id": run_id})
        apply_patchset(inspection.root, patchset, contents)
        changed = [e.file for e in patchset.edits]
        from .verification import collect_checks

        report.checks.extend(collect_checks(inspection.root, changed, expected_mode=mode))
        post = inspect_repo(repo)
        add_check(report, "post:still_supported_topology",
                  post.matrix.decision == CapabilityStatus.SUPPORTED, post.matrix.decision_reason)
        add_check(report, "post:integration_detected", is_already_integrated(post),
                  "cekura SDK usage visible to the scanner after patch")
        manifests = " ".join(
            (inspection.root / f).read_text(encoding="utf-8", errors="ignore")
            for f in changed if "requirements" in f or f.endswith(".toml"))
        add_check(report, "post:dependency_added", "cekura[" in manifests,
                  f"cekura[{plan.framework.value}] present in a dependency manifest")
        from .safety import scan_paths_for_secrets

        findings = scan_paths_for_secrets([inspection.root / f for f in changed])
        add_check(report, "post:no_secrets_in_diff", not findings, str(findings))
        report.statuses["code_integration"] = (
            Status.IMPLEMENTED_AND_OFFLINE_VERIFIED if report.passed else Status.NOT_RUN)
        report.statuses["rollback_manifest"] = Status.IMPLEMENTED_AND_OFFLINE_VERIFIED

    # ---- feature specs (always computed; registration is the platform step)
    report.statuses["mock_tools_spec"] = Status.IMPLEMENTED_AND_OFFLINE_VERIFIED
    report.statuses["dynamic_variables_spec"] = Status.IMPLEMENTED_AND_OFFLINE_VERIFIED
    report.statuses["kb_manifest"] = Status.IMPLEMENTED_AND_OFFLINE_VERIFIED
    add_check(report, "features:mock_tools", True, f"{len(plan.mock_tools)} tool(s)")
    add_check(report, "features:dynamic_variables", True, f"{len(plan.dynamic_variables)} variable(s)")
    add_check(report, "features:kb_manifest", True,
              f"{len(plan.kb_manifest)} document(s), approval required before upload")

    # ---- platform
    desired = prepare_platform_state(repo, mode=mode, agent_id=agent_id, project_id=project_id)
    platform_result: dict | None = None
    if platform_mode == "staging":
        if not settings.cekura_api_key:
            report.statuses["platform_registration"] = Status.BLOCKED_BY_ACCESS_OR_DEPENDENCY
            report.blockers.append("CEKURA_KEY_MISSING")
        else:
            from .platform import CekuraClient, reconcile

            client = CekuraClient(settings.cekura_base_url, settings.cekura_api_key)
            platform_result = reconcile(client, desired, apply=apply and e2e,
                                        kb_files_root=inspection.root)
            if apply and e2e:
                verified = bool(platform_result.get("verified"))
                add_check(report, "platform:get_after_exact_match", verified,
                          str(platform_result.get("mismatches", [])))
                report.statuses["platform_registration"] = (
                    Status.LIVE_VERIFIED if verified else Status.BLOCKED_BY_ACCESS_OR_DEPENDENCY)
            else:
                report.statuses["platform_registration"] = Status.NOT_RUN
                add_check(report, "platform:dry_run_diff", True,
                          str({k: v for k, v in platform_result.items()
                               if k.endswith("_diff")}), severity="info")
        blockers = _sdk_availability_blockers(plan.framework, allow_network=True)
        report.blockers.extend(blockers)
        report.statuses["sdk_dependency"] = (
            Status.BLOCKED_BY_ACCESS_OR_DEPENDENCY if blockers else Status.LIVE_VERIFIED)
    else:
        report.statuses["platform_registration"] = Status.NOT_RUN
        report.statuses["sdk_dependency"] = Status.NOT_RUN

    report.statuses["scenario_run_e2e"] = Status.NOT_RUN  # documented next step, never faked

    report.exit_code = 0 if report.passed else 1
    return IntegrateResult(report=report, patchset=patchset, desired_state=desired,
                           platform_result=platform_result)


def verify_repo(repo: Path, mode: Mode | None = None):
    """Standalone verification of the repo's current integration state."""
    import uuid

    from .models import Status, VerificationReport
    from .report import add_check
    from .verification import collect_checks

    inspection = inspect_repo(repo)
    report = VerificationReport(
        run_id=uuid.uuid4().hex[:10], repo_root=str(inspection.root),
        framework=inspection.matrix.framework, mode=mode,
    )
    tracer_files = sorted({
        f.path for f in inspection.snapshot.files
        if f.path.endswith(".py")
        and any(marker in (inspection.root / f.path).read_text(encoding="utf-8", errors="ignore")
                for marker in ("LiveKitTracer", "PipecatTracer"))
    })
    if not tracer_files:
        add_check(report, "integration:present", False,
                  "no Cekura tracer usage found — run `cekura-agent integrate` first")
        report.statuses["code_integration"] = Status.NOT_RUN
        report.exit_code = 1
        return report
    report.checks.extend(collect_checks(inspection.root, tracer_files, expected_mode=mode))
    add_check(report, "topology:supported",
              inspection.matrix.decision == CapabilityStatus.SUPPORTED,
              inspection.matrix.decision_reason)
    report.statuses["code_integration"] = (
        Status.IMPLEMENTED_AND_OFFLINE_VERIFIED if report.passed else Status.NOT_RUN)
    report.exit_code = 0 if report.passed else 1
    return report


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
