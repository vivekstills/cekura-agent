"""Semantic plan validation: nothing enters the executor that the evidence does not support."""

from __future__ import annotations

from pathlib import PurePosixPath

from .. import SCHEMA_VERSION
from ..errors import PlanRejected
from ..models import ActionType, EvidenceKind, Framework, IntegrationPlan, Mode
from ..scanner import InspectionResult
from .rules import allowed_actions

# Action types allowed to reference files that do not exist yet (they create them).
MAY_CREATE_FILE = {ActionType.ADD_DEPENDENCY, ActionType.ADD_ENV_PLACEHOLDER}

REQUIRED_EVIDENCE = {
    ActionType.INSERT_TRACK_SESSION: {EvidenceKind.ENTRYPOINT, EvidenceKind.SESSION_START},
    ActionType.INSERT_OBSERVE_SESSION: {EvidenceKind.ENTRYPOINT, EvidenceKind.SESSION_START},
    ActionType.PIPECAT_SINGLE_STEP: {EvidenceKind.PIPELINE_TASK},
    ActionType.PIPECAT_MULTI_STEP: {EvidenceKind.PIPELINE_TASK},
    ActionType.INSERT_TRACER_INIT: {EvidenceKind.ENTRYPOINT},
}


def _check_path(plan_file: str, snapshot_paths: set[str], may_create: bool) -> None:
    p = PurePosixPath(plan_file)
    if p.is_absolute() or ".." in p.parts:
        raise PlanRejected(f"plan references a path outside the repository: {plan_file}")
    lowered = plan_file.lower()
    if lowered.startswith(("tests/", "test/")) or "/tests/" in lowered:
        raise PlanRejected(f"plan may not modify test files: {plan_file}")
    if plan_file not in snapshot_paths and not may_create:
        raise PlanRejected(f"plan references a file not present in the snapshot: {plan_file}")


def validate_plan(plan: IntegrationPlan, inspection: InspectionResult, mode: Mode) -> None:
    matrix = inspection.matrix
    emap = inspection.evidence_map
    snapshot_paths = {f.path for f in inspection.snapshot.files}

    if plan.schema_version != SCHEMA_VERSION:
        raise PlanRejected(f"plan schema_version {plan.schema_version} != {SCHEMA_VERSION}")
    if plan.snapshot_fingerprint != inspection.snapshot.fingerprint:
        raise PlanRejected("plan is stale: snapshot fingerprint mismatch")
    if plan.framework != matrix.framework or plan.framework not in (Framework.LIVEKIT, Framework.PIPECAT):
        raise PlanRejected(f"plan framework {plan.framework.value} does not match detected "
                           f"{matrix.framework.value}")
    if plan.mode != mode:
        raise PlanRejected(f"plan mode {plan.mode.value} does not match requested {mode.value}")

    already = bool(emap.of_kind(EvidenceKind.EXISTING_CEKURA))
    allowed = allowed_actions(plan.framework, mode, already)
    if not plan.actions:
        raise PlanRejected("plan contains no actions")

    tracer_actions = [a for a in plan.actions if a.action_type in {
        ActionType.INSERT_TRACK_SESSION, ActionType.INSERT_OBSERVE_SESSION,
        ActionType.PIPECAT_SINGLE_STEP, ActionType.PIPECAT_MULTI_STEP,
    }]
    if not already and len(tracer_actions) != 1:
        raise PlanRejected(f"plan must contain exactly one tracer action, found {len(tracer_actions)}")

    for action in plan.actions:
        if action.action_type not in allowed:
            raise PlanRejected(
                f"action {action.action_type.value} not allowed for "
                f"{plan.framework.value}/{mode.value} (already_integrated={already})"
            )
        if action.file is not None:
            _check_path(action.file, snapshot_paths, action.action_type in MAY_CREATE_FILE)
        needed = REQUIRED_EVIDENCE.get(action.action_type, set())
        seen_kinds = set()
        for ev_id in action.evidence_ids:
            ev = emap.by_id(ev_id)
            if ev is None:
                raise PlanRejected(f"action cites unknown evidence id {ev_id}")
            if ev.rejected:
                raise PlanRejected(f"action cites rejected evidence {ev_id} ({ev.reject_reason})")
            seen_kinds.add(ev.kind)
        missing = needed - seen_kinds
        if missing:
            raise PlanRejected(
                f"action {action.action_type.value} missing required evidence kinds: "
                f"{sorted(k.value for k in missing)}"
            )
        # anchor actions must target the file their evidence lives in
        if action.action_type in REQUIRED_EVIDENCE and action.file:
            anchor_files = {emap.by_id(e).file for e in action.evidence_ids if emap.by_id(e)}
            if action.file not in anchor_files:
                raise PlanRejected(
                    f"action {action.action_type.value} targets {action.file} but its evidence "
                    f"lives in {sorted(anchor_files)}"
                )
        if action.action_type == ActionType.ADD_DEPENDENCY:
            pkg = str(action.params.get("package", ""))
            if not pkg.startswith(f"cekura[{plan.framework.value}]"):
                raise PlanRejected(f"dependency action must add cekura[{plan.framework.value}]..., got {pkg!r}")
        if action.action_type in {ActionType.PIPECAT_SINGLE_STEP, ActionType.PIPECAT_MULTI_STEP}:
            if action.params.get("mode") != mode.value:
                raise PlanRejected("pipecat action mode param must equal the requested mode")
            task_evs = [emap.by_id(e) for e in action.evidence_ids
                        if emap.by_id(e) and emap.by_id(e).kind == EvidenceKind.PIPELINE_TASK]
            for task_ev in task_evs:
                custom = bool(task_ev.detail.get("has_custom_kwargs"))
                if custom and action.action_type == ActionType.PIPECAT_SINGLE_STEP:
                    raise PlanRejected("PipelineTask has custom kwargs; single_step is not applicable")
