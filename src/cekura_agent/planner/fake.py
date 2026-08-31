"""Deterministic planner: builds the reference IntegrationPlan without any model call.

Used for offline runs (`--model-mode fake`) and as the semantic baseline the
OpenRouter path is validated against.
"""

from __future__ import annotations

from typing import Any

from ..errors import NeedsHuman
from ..models import ActionType, EvidenceKind, Framework, Mode, PlanAction
from ..scanner import InspectionResult
from .rules import sdk_package


class FakePlanner:
    name = "fake"

    def plan(self, bundle: dict[str, Any], inspection: InspectionResult, mode: Mode,
             agent_id: int | None) -> tuple[list[PlanAction], str, dict[str, Any]]:
        emap = inspection.evidence_map
        framework = inspection.matrix.framework
        meta = {"planner": "fake", "model": None, "cost_usd": 0.0}

        if emap.of_kind(EvidenceKind.EXISTING_CEKURA):
            return (
                [PlanAction(action_type=ActionType.ALREADY_INTEGRATED_NOOP,
                            evidence_ids=[e.id for e in emap.of_kind(EvidenceKind.EXISTING_CEKURA)])],
                "cekura SDK already present; no code changes",
                meta,
            )

        manifests = bundle["dependency_manifests"]
        actions = [PlanAction(
            action_type=ActionType.ADD_DEPENDENCY,
            file=manifests[0],
            params={"manifest": manifests[0], "package": sdk_package(framework)},
            evidence_ids=[e.id for e in emap.of_kind(EvidenceKind.DEPENDENCY)][:3],
        )]

        if framework == Framework.LIVEKIT:
            [entry] = [e for e in emap.of_kind(EvidenceKind.ENTRYPOINT)
                       if e.detail.get("framework") == "livekit"]
            [start] = [e for e in emap.of_kind(EvidenceKind.SESSION_START)
                       if e.detail.get("function") == entry.symbol]
            actions.append(PlanAction(
                action_type=ActionType.INSERT_TRACER_INIT, file=entry.file,
                params={"agent_id": agent_id, "framework": "livekit"},
                evidence_ids=[entry.id],
            ))
            tracer_action = (ActionType.INSERT_TRACK_SESSION if mode == Mode.TEST
                             else ActionType.INSERT_OBSERVE_SESSION)
            actions.append(PlanAction(
                action_type=tracer_action, file=start.file,
                params={}, evidence_ids=[entry.id, start.id],
            ))
        elif framework == Framework.PIPECAT:
            [task] = emap.of_kind(EvidenceKind.PIPELINE_TASK)
            entries = [e for e in emap.of_kind(EvidenceKind.ENTRYPOINT)
                       if e.detail.get("framework") == "pipecat"]
            action_type = (ActionType.PIPECAT_MULTI_STEP if task.detail.get("has_custom_kwargs")
                           else ActionType.PIPECAT_SINGLE_STEP)
            actions.append(PlanAction(
                action_type=action_type, file=task.file,
                params={"mode": mode.value, "agent_id": agent_id},
                evidence_ids=[task.id] + [e.id for e in entries],
            ))
        else:  # pragma: no cover - guarded upstream by the capability matrix
            raise NeedsHuman("NO_FRAMEWORK", "no supported framework to plan for")

        env_keys = ["CEKURA_API_KEY"] + ([] if agent_id else ["CEKURA_AGENT_ID"])
        actions.append(PlanAction(
            action_type=ActionType.ADD_ENV_PLACEHOLDER, file=".env.example",
            params={"file": ".env.example", "keys": env_keys},
        ))
        return actions, f"deterministic {framework.value}/{mode.value} integration plan", meta
