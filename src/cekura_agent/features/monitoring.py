"""Monitoring planner: explicit test/observe semantics + existing-instrumentation detection."""

from __future__ import annotations

from ..models import EvidenceKind, Framework, Mode
from ..scanner import InspectionResult


def summarize_monitoring(inspection: InspectionResult, mode: Mode) -> dict:
    emap = inspection.evidence_map
    framework = inspection.matrix.framework
    method = {
        (Framework.LIVEKIT, Mode.TEST): "track_session (mock tools auto-injected; chat mode auto)",
        (Framework.LIVEKIT, Mode.OBSERVE): "observe_session (dual-channel audio via LiveKit egress; "
                                           "requires LiveKit creds on the Cekura agent)",
        (Framework.PIPECAT, Mode.TEST): "track_and_create_task / track_pipeline (mock tools auto-injected)",
        (Framework.PIPECAT, Mode.OBSERVE): "observe_and_create_task / observe_pipeline "
                                           "(dual-channel audio)",
    }.get((framework, mode), "n/a")
    return {
        "mode": mode.value,
        "sdk_method": method,
        "captures": ("transcripts, tool calls, metrics, session logs"
                     + (", dual-channel audio" if mode == Mode.OBSERVE else ", mock tools")),
        "existing_otel": bool(emap.of_kind(EvidenceKind.OTEL)),
        "existing_recording": bool(emap.of_kind(EvidenceKind.RECORDING)),
        "existing_direct_observe": bool(emap.of_kind(EvidenceKind.DIRECT_OBSERVE)),
        "existing_cekura_sdk": bool(emap.of_kind(EvidenceKind.EXISTING_CEKURA)),
        "exactly_once_policy": "one tracer call per entrypoint; SDK + direct observe API is refused",
        "kill_switches": ["CEKURA_TRACING_ENABLED", "CEKURA_MOCK_TOOLS_ENABLED",
                          "CEKURA_OBSERVABILITY_ENABLED", "CEKURA_TRACER_ENABLED"],
    }
