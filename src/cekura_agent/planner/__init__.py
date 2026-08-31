"""Planner package: evidence bundle -> (fake | Kimi K3 via OpenRouter) -> validated IntegrationPlan."""

from __future__ import annotations

import uuid

from ..budget import BudgetLedger
from ..config import Settings
from ..errors import AgentError
from ..models import IntegrationPlan, Mode
from ..scanner import InspectionResult
from .client import OpenRouterPlanner, PlannerOutput
from .fake import FakePlanner
from .prompts import SYSTEM_PROMPT, build_bundle
from .rules import allowed_actions, sdk_package
from .validate import validate_plan

__all__ = [
    "FakePlanner", "OpenRouterPlanner", "PlannerOutput", "SYSTEM_PROMPT",
    "allowed_actions", "build_bundle", "build_plan", "sdk_package", "validate_plan",
]


def build_plan(
    inspection: InspectionResult,
    mode: Mode,
    *,
    model_mode: str,
    agent_id: int | None,
    settings: Settings,
    ledger: BudgetLedger | None = None,
) -> IntegrationPlan:
    """Run the selected planner and return a semantically validated IntegrationPlan."""
    bundle = build_bundle(inspection, mode, agent_id)
    if model_mode == "fake":
        actions, notes, meta = FakePlanner().plan(bundle, inspection, mode, agent_id)
    elif model_mode == "openrouter":
        if ledger is None:
            ledger = BudgetLedger(settings)
        actions, notes, meta = OpenRouterPlanner(settings, ledger).plan(bundle, inspection, mode, agent_id)
    else:
        raise AgentError(f"unknown --model-mode {model_mode!r} (expected fake|openrouter)")

    # Host-known values are normalized, never trusted to model echo: the model may omit
    # them, but if it *contradicts* them the validator still rejects.
    from ..models import ActionType

    for action in actions:
        if action.action_type in (ActionType.PIPECAT_SINGLE_STEP, ActionType.PIPECAT_MULTI_STEP):
            action.params.setdefault("mode", mode.value)
        if action.action_type in (ActionType.PIPECAT_SINGLE_STEP, ActionType.PIPECAT_MULTI_STEP,
                                  ActionType.INSERT_TRACER_INIT):
            if agent_id is not None and action.params.get("agent_id") in (None, 0):
                action.params["agent_id"] = agent_id

    plan = IntegrationPlan(
        plan_id=uuid.uuid4().hex[:12],
        snapshot_fingerprint=inspection.snapshot.fingerprint,
        framework=inspection.matrix.framework,
        mode=mode,
        actions=actions,
        notes=notes,
        model_metadata=meta,
    )
    validate_plan(plan, inspection, mode)
    return plan
