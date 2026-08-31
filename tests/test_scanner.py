"""Slice B gates: scanner cites exact evidence; README text has no effect; ambiguity surfaces."""

from pathlib import Path

from cekura_agent.models import CapabilityStatus, EvidenceKind, Framework
from cekura_agent.scanner import inspect_repo

FIXTURES = Path(__file__).parent / "fixtures"


def test_livekit_basic_full_inspection():
    result = inspect_repo(FIXTURES / "livekit_basic")
    assert result.matrix.framework == Framework.LIVEKIT
    assert result.matrix.decision == CapabilityStatus.SUPPORTED

    emap = result.evidence_map
    [entry] = emap.of_kind(EvidenceKind.ENTRYPOINT)
    assert entry.file == "agent.py" and entry.symbol == "entrypoint"
    assert entry.detail["ctx_param"] == "ctx"
    assert entry.detail["has_session_start"] is True

    [start] = emap.of_kind(EvidenceKind.SESSION_START)
    assert start.detail["session_var"] == "session"
    assert start.detail["agent_arg"] == "assistant"

    tools = {e.detail["name"] for e in emap.of_kind(EvidenceKind.TOOL_DEF)}
    assert tools == {"lookup_availability", "confirm_appointment"}
    lookup = next(e for e in emap.of_kind(EvidenceKind.TOOL_DEF) if e.detail["name"] == "lookup_availability")
    assert lookup.detail["parameters"] == {"date": "str"}

    placeholders = {e.symbol for e in emap.of_kind(EvidenceKind.PROMPT_PLACEHOLDER)}
    assert placeholders == {"customer_name", "account_id", "appointment_date"}
    rejected = [e for e in emap.of_kind(EvidenceKind.PROMPT_PLACEHOLDER, include_rejected=True) if e.rejected]
    assert any(e.symbol == "example_var" and "non-executable" in e.reject_reason for e in rejected)

    runtime = emap.of_kind(EvidenceKind.RUNTIME_INPUT)
    assert any(e.detail.get("key") == "phone_number" for e in runtime)

    kb = [e for e in emap.of_kind(EvidenceKind.KB_SOURCE) if e.detail.get("style") == "doc_file"]
    assert any(e.detail["path"] == "docs/faq.md" and e.detail["referenced_in_code"] for e in kb)

    deps = {e.detail["package"] for e in emap.of_kind(EvidenceKind.DEPENDENCY)}
    assert "livekit-agents" in deps


def test_pipecat_single_step_detection():
    result = inspect_repo(FIXTURES / "pipecat_single")
    assert result.matrix.framework == Framework.PIPECAT
    assert result.matrix.decision == CapabilityStatus.SUPPORTED

    emap = result.evidence_map
    [task] = emap.of_kind(EvidenceKind.PIPELINE_TASK)
    assert task.detail["has_custom_kwargs"] is False
    [entry] = emap.of_kind(EvidenceKind.ENTRYPOINT)
    assert entry.detail["has_aggregator_pair"] is True
    assert entry.detail["context_var"] == "context"
    assert "transport.input()" in entry.detail["pipeline_elements"][0]

    tools = {e.detail["name"] for e in emap.of_kind(EvidenceKind.TOOL_DEF)}
    assert tools == {"order_lookup"}
    assert result.matrix.get("pipeline_task").reason.startswith("single_step")


def test_pipecat_custom_kwargs_forces_multi_step():
    result = inspect_repo(FIXTURES / "pipecat_custom")
    [task] = result.evidence_map.of_kind(EvidenceKind.PIPELINE_TASK)
    assert task.detail["has_custom_kwargs"] is True
    assert set(task.detail["extra_kwargs"]) == {"params", "idle_timeout_secs"}
    assert result.matrix.get("pipeline_task").reason.startswith("multi_step")


def test_readme_only_repo_has_no_framework():
    result = inspect_repo(FIXTURES / "readme_only")
    assert result.matrix.framework == Framework.NONE
    assert result.matrix.decision == CapabilityStatus.NEEDS_HUMAN
    assert result.matrix.decision_reason == "NO_FRAMEWORK"
    assert result.evidence_map.of_kind(EvidenceKind.FRAMEWORK_IMPORT) == []


def test_both_frameworks_needs_human():
    result = inspect_repo(FIXTURES / "both_frameworks")
    assert result.matrix.framework == Framework.BOTH
    assert result.matrix.decision_reason == "MULTI_FRAMEWORK"


def test_ambiguous_entrypoints_needs_human():
    result = inspect_repo(FIXTURES / "ambiguous_livekit")
    assert result.matrix.decision_reason == "AMBIGUOUS_ENTRYPOINT"


def test_jobcontext_helper_is_not_ambiguous():
    result = inspect_repo(FIXTURES / "livekit_helper_not_entrypoint")
    assert result.matrix.framework == Framework.LIVEKIT
    assert result.matrix.decision == CapabilityStatus.SUPPORTED
    entries = result.evidence_map.of_kind(EvidenceKind.ENTRYPOINT)
    assert [e.symbol for e in entries] == ["entrypoint"]


def test_unsupported_topology_needs_human():
    result = inspect_repo(FIXTURES / "unsupported_topology")
    assert result.matrix.framework == Framework.PIPECAT
    assert result.matrix.decision == CapabilityStatus.NEEDS_HUMAN
    assert result.matrix.decision_reason in {"NO_PIPELINE_TASK", "NO_ENTRYPOINT"}


def test_direct_observe_conflict_flagged():
    result = inspect_repo(FIXTURES / "pipecat_direct_observe")
    assert result.matrix.decision_reason == "DIRECT_OBSERVE_PRESENT"
    assert result.evidence_map.of_kind(EvidenceKind.DIRECT_OBSERVE)


def test_existing_integration_detected():
    result = inspect_repo(FIXTURES / "livekit_existing")
    assert result.summary()["already_integrated"] is True
    cap = result.matrix.get("already_integrated")
    assert cap is not None and "ALREADY_INTEGRATED" in cap.reason


def test_pipecat_worker_api_flagged_distinctly():
    result = inspect_repo(FIXTURES / "pipecat_worker")
    assert result.matrix.framework == Framework.PIPECAT
    assert result.matrix.decision == CapabilityStatus.NEEDS_HUMAN
    assert result.matrix.decision_reason == "PIPECAT_WORKER_API"
    [task] = result.evidence_map.of_kind(EvidenceKind.PIPELINE_TASK)
    assert task.detail["style"] == "pipeline_worker"
