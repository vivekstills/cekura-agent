"""Evidence bundle + system prompt for the Kimi K3 planner.

The bundle is the ONLY repository context the model receives: redacted, minimal,
and every item carries the evidence id the plan must cite back.
"""

from __future__ import annotations

import json
from typing import Any

from ..models import EvidenceKind, Mode
from ..safety import redact
from ..scanner import InspectionResult
from .rules import allowed_actions, sdk_package

SYSTEM_PROMPT = """You are the planning component of cekura-agent, a deterministic tool that \
integrates the Cekura observability SDK into LiveKit/Pipecat Python repositories.

You DO NOT write code, run commands, or call APIs. You select from a fixed action \
vocabulary; a deterministic host performs every edit. Respond with ONE JSON object and \
nothing else, matching this schema:

{
  "framework": "livekit" | "pipecat",
  "mode": "test" | "observe",
  "actions": [
    {"action_type": "<one of allowed_actions>", "file": "<repo-relative path or null>",
     "params": {...}, "evidence_ids": ["ev-...."]}
  ],
  "notes": "<short rationale>"
}

Hard rules:
- Use ONLY action types listed in `allowed_actions` in the user message.
- Cite ONLY evidence ids present in the user message; never invent files, symbols or ids.
- `insert_track_session` / `insert_observe_session` must target the session_start evidence file
  and include both the entrypoint and session_start evidence ids.
- `pipecat_single_step` / `pipecat_multi_step` must cite the pipeline_task evidence id and
  respect `has_custom_kwargs` (custom kwargs => multi_step).
- `add_dependency` params: {"manifest": <existing manifest path or "requirements.txt">, "package": <given sdk package>}.
- `add_env_placeholder` params: {"file": ".env.example", "keys": [...]}.
- Never target production configs, delete tests, or modify business logic.
- If `already_integrated` is true, emit exactly one action: already_integrated_noop.
"""


def build_bundle(inspection: InspectionResult, mode: Mode, agent_id: int | None) -> dict[str, Any]:
    emap = inspection.evidence_map
    matrix = inspection.matrix

    def ev_view(kinds: list[EvidenceKind]) -> list[dict[str, Any]]:
        out = []
        for kind in kinds:
            for ev in emap.of_kind(kind):
                out.append({
                    "id": ev.id, "kind": ev.kind.value, "file": ev.file,
                    "line": ev.line_start, "symbol": ev.symbol, "detail": ev.detail,
                })
        return out

    already = bool(emap.of_kind(EvidenceKind.EXISTING_CEKURA))
    manifests = sorted({
        ev.detail.get("manifest") for ev in emap.of_kind(EvidenceKind.DEPENDENCY)
    } - {None}) or ["requirements.txt"]

    bundle = {
        "framework": matrix.framework.value,
        "mode": mode.value,
        "agent_id": agent_id,
        "already_integrated": already,
        "allowed_actions": sorted(a.value for a in allowed_actions(matrix.framework, mode, already)),
        "sdk_package": sdk_package(matrix.framework),
        "dependency_manifests": manifests,
        "snapshot_fingerprint": inspection.snapshot.fingerprint,
        "evidence": ev_view([
            EvidenceKind.ENTRYPOINT, EvidenceKind.SESSION_START, EvidenceKind.AGENT_SESSION,
            EvidenceKind.PIPELINE, EvidenceKind.PIPELINE_TASK, EvidenceKind.AGGREGATOR,
            EvidenceKind.DEPENDENCY, EvidenceKind.EXISTING_CEKURA,
        ]),
        "capabilities": [
            {"name": c.name, "status": c.status.value, "reason": c.reason}
            for c in matrix.capabilities
        ],
    }
    return json.loads(redact(json.dumps(bundle)))
