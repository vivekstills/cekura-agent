"""CapabilityMatrix: stable supported / blocked / needs_human decisions with reason codes.

Reason codes are stable strings used by tests, reports and the CLI exit path:
    NO_FRAMEWORK, MULTI_FRAMEWORK, NO_ENTRYPOINT, AMBIGUOUS_ENTRYPOINT,
    NO_SESSION_START, NO_PIPELINE_TASK, DIRECT_OBSERVE_PRESENT,
    MISSING_AGGREGATOR_PAIR, ALREADY_INTEGRATED
"""

from __future__ import annotations

from ..models import (
    Capability,
    CapabilityMatrix,
    CapabilityStatus,
    Evidence,
    EvidenceKind,
    EvidenceMap,
    Framework,
)


def _framework_of(emap: EvidenceMap) -> Framework:
    frameworks = {
        ev.detail.get("framework")
        for ev in emap.of_kind(EvidenceKind.FRAMEWORK_IMPORT)
        if not _is_test_file(ev)
    }
    has_lk, has_pc = "livekit" in frameworks, "pipecat" in frameworks
    if has_lk and has_pc:
        return Framework.BOTH
    if has_lk:
        return Framework.LIVEKIT
    if has_pc:
        return Framework.PIPECAT
    return Framework.NONE


def _is_test_file(ev: Evidence) -> bool:
    lowered = ev.file.lower()
    return any(part in lowered for part in ("tests/", "test_", "examples/", "conftest"))


def active_entrypoints(emap: EvidenceMap, framework: Framework) -> list[Evidence]:
    return [
        ev for ev in emap.of_kind(EvidenceKind.ENTRYPOINT)
        if ev.detail.get("framework") == framework.value
    ]


def build_matrix(fingerprint: str, emap: EvidenceMap) -> CapabilityMatrix:
    framework = _framework_of(emap)
    caps: list[Capability] = []
    decision = CapabilityStatus.SUPPORTED
    decision_reason = "OK"

    def cap(name: str, status: CapabilityStatus, reason: str, evidence: list[Evidence] = ()) -> None:
        caps.append(Capability(name=name, status=status, reason=reason,
                               evidence_ids=[e.id for e in evidence]))

    def demote(status: CapabilityStatus, reason: str) -> None:
        nonlocal decision, decision_reason
        if decision == CapabilityStatus.SUPPORTED:
            decision, decision_reason = status, reason

    # framework
    fw_evidence = emap.of_kind(EvidenceKind.FRAMEWORK_IMPORT)
    if framework == Framework.NONE:
        cap("framework", CapabilityStatus.NEEDS_HUMAN, "NO_FRAMEWORK: no livekit/pipecat imports in code")
        demote(CapabilityStatus.NEEDS_HUMAN, "NO_FRAMEWORK")
    elif framework == Framework.BOTH:
        cap("framework", CapabilityStatus.NEEDS_HUMAN,
            "MULTI_FRAMEWORK: both livekit and pipecat present; specify the target entrypoint", fw_evidence)
        demote(CapabilityStatus.NEEDS_HUMAN, "MULTI_FRAMEWORK")
    else:
        cap("framework", CapabilityStatus.SUPPORTED, f"{framework.value} detected", fw_evidence)

    # monitoring (entrypoint + lifecycle anchor)
    if framework in (Framework.LIVEKIT, Framework.PIPECAT):
        entries = active_entrypoints(emap, framework)
        if not entries:
            code = "NO_ENTRYPOINT"
            cap("monitoring", CapabilityStatus.NEEDS_HUMAN,
                f"{code}: no deployable {framework.value} entrypoint found")
            demote(CapabilityStatus.NEEDS_HUMAN, code)
        elif len(entries) > 1:
            code = "AMBIGUOUS_ENTRYPOINT"
            locs = ", ".join(f"{e.file}:{e.line_start}" for e in entries)
            cap("monitoring", CapabilityStatus.NEEDS_HUMAN, f"{code}: multiple candidates ({locs})", entries)
            demote(CapabilityStatus.NEEDS_HUMAN, code)
        else:
            entry = entries[0]
            if framework == Framework.LIVEKIT and not entry.detail.get("has_session_start"):
                cap("monitoring", CapabilityStatus.NEEDS_HUMAN,
                    "NO_SESSION_START: entrypoint does not call session.start()", [entry])
                demote(CapabilityStatus.NEEDS_HUMAN, "NO_SESSION_START")
            elif framework == Framework.PIPECAT and not entry.detail.get("has_aggregator_pair"):
                cap("monitoring", CapabilityStatus.NEEDS_HUMAN,
                    "MISSING_AGGREGATOR_PAIR: pipeline lacks LLM user/assistant aggregators; "
                    "the Cekura SDK would silently disable itself", [entry])
                demote(CapabilityStatus.NEEDS_HUMAN, "MISSING_AGGREGATOR_PAIR")
            else:
                cap("monitoring", CapabilityStatus.SUPPORTED,
                    f"entrypoint {entry.symbol} at {entry.file}:{entry.line_start}", [entry])

    # pipecat needs a PipelineTask to wrap
    if framework == Framework.PIPECAT:
        tasks = emap.of_kind(EvidenceKind.PIPELINE_TASK)
        worker_style = [t for t in tasks if t.detail.get("style") == "pipeline_worker"]
        task_style = [t for t in tasks if t.detail.get("style") != "pipeline_worker"]
        if not tasks:
            cap("pipeline_task", CapabilityStatus.NEEDS_HUMAN,
                "NO_PIPELINE_TASK: pipecat imported but no PipelineTask construction found")
            demote(CapabilityStatus.NEEDS_HUMAN, "NO_PIPELINE_TASK")
        elif worker_style and not task_style:
            cap("pipeline_task", CapabilityStatus.NEEDS_HUMAN,
                "PIPECAT_WORKER_API: repo uses the newer PipelineWorker/WorkerRunner API; the "
                "documented Cekura SDK integration targets PipelineTask — verify SDK support "
                "before integrating", worker_style)
            demote(CapabilityStatus.NEEDS_HUMAN, "PIPECAT_WORKER_API")
        else:
            style = "multi_step" if task_style[0].detail.get("has_custom_kwargs") else "single_step"
            cap("pipeline_task", CapabilityStatus.SUPPORTED, f"{style} integration applicable", task_style)

    # tools -> mock tools
    tools = emap.of_kind(EvidenceKind.TOOL_DEF)
    tool_names = sorted({t.detail.get("name") or t.symbol for t in tools})
    if tools:
        note = "" if framework != Framework.PIPECAT else " (pipecat: requires explicit mock-tool routing)"
        cap("mock_tools", CapabilityStatus.SUPPORTED, f"{len(tool_names)} tool(s): {', '.join(tool_names)}{note}", tools)
    else:
        cap("mock_tools", CapabilityStatus.SUPPORTED, "no tools detected; nothing to mock")

    # dynamic variables
    placeholders = emap.of_kind(EvidenceKind.PROMPT_PLACEHOLDER)
    runtime_inputs = emap.of_kind(EvidenceKind.RUNTIME_INPUT)
    if placeholders or runtime_inputs:
        names = sorted({p.symbol for p in placeholders} | {r.symbol for r in runtime_inputs if r.symbol})
        cap("dynamic_variables", CapabilityStatus.SUPPORTED,
            f"{len(names)} variable(s): {', '.join(names)}", placeholders + runtime_inputs)
    else:
        cap("dynamic_variables", CapabilityStatus.SUPPORTED, "none detected")

    # knowledge base
    kb = emap.of_kind(EvidenceKind.KB_SOURCE)
    kb_files = sorted({e.detail.get("path") or e.file for e in kb if e.detail.get("style") != "rag_import"})
    if kb:
        cap("knowledge_base", CapabilityStatus.SUPPORTED, f"{len(kb_files)} candidate document(s)", kb)
    else:
        cap("knowledge_base", CapabilityStatus.SUPPORTED, "no runtime documents detected")

    # existing integration / conflicts
    existing = emap.of_kind(EvidenceKind.EXISTING_CEKURA)
    if existing:
        cap("already_integrated", CapabilityStatus.SUPPORTED,
            "ALREADY_INTEGRATED: cekura SDK usage present; integrate becomes a no-op", existing)
    direct = emap.of_kind(EvidenceKind.DIRECT_OBSERVE)
    if direct:
        cap("direct_observe_conflict", CapabilityStatus.NEEDS_HUMAN,
            "DIRECT_OBSERVE_PRESENT: repo posts to /observability/v1/observe/ directly; "
            "SDK + direct API on the same session creates duplicate records — migration decision required",
            direct)
        demote(CapabilityStatus.NEEDS_HUMAN, "DIRECT_OBSERVE_PRESENT")

    return CapabilityMatrix(
        snapshot_fingerprint=fingerprint, framework=framework,
        capabilities=caps, decision=decision, decision_reason=decision_reason,
    )
