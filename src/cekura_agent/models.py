"""Strict, versioned data models shared by every stage of the agent.

All models reject unknown fields (`extra="forbid"`) and carry `schema_version`
so plans/reports produced by one version cannot silently drive another.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import SCHEMA_VERSION


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- enums


class Status(str, Enum):
    IMPLEMENTED_AND_OFFLINE_VERIFIED = "IMPLEMENTED_AND_OFFLINE_VERIFIED"
    LIVE_VERIFIED = "LIVE_VERIFIED"
    BLOCKED_BY_ACCESS_OR_DEPENDENCY = "BLOCKED_BY_ACCESS_OR_DEPENDENCY"
    NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY = "NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY"
    NOT_RUN = "NOT_RUN"


class Framework(str, Enum):
    LIVEKIT = "livekit"
    PIPECAT = "pipecat"
    BOTH = "both"
    NONE = "none"
    AMBIGUOUS = "ambiguous"


class Mode(str, Enum):
    TEST = "test"
    OBSERVE = "observe"


class EvidenceKind(str, Enum):
    FRAMEWORK_IMPORT = "framework_import"
    ENTRYPOINT = "entrypoint"
    AGENT_SESSION = "agent_session"
    SESSION_START = "session_start"
    PIPELINE = "pipeline"
    PIPELINE_TASK = "pipeline_task"
    AGGREGATOR = "aggregator"
    TOOL_DEF = "tool_def"
    PROMPT_PLACEHOLDER = "prompt_placeholder"
    RUNTIME_INPUT = "runtime_input"
    KB_SOURCE = "kb_source"
    EXISTING_CEKURA = "existing_cekura"
    DIRECT_OBSERVE = "direct_observe"
    OTEL = "otel"
    RECORDING = "recording"
    DEPENDENCY = "dependency"
    OTHER = "other"


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"


class ActionType(str, Enum):
    ADD_DEPENDENCY = "add_dependency"
    INSERT_TRACER_INIT = "insert_tracer_init"
    INSERT_TRACK_SESSION = "insert_track_session"
    INSERT_OBSERVE_SESSION = "insert_observe_session"
    PIPECAT_SINGLE_STEP = "pipecat_single_step"
    PIPECAT_MULTI_STEP = "pipecat_multi_step"
    ADD_ENV_PLACEHOLDER = "add_env_placeholder"
    ALREADY_INTEGRATED_NOOP = "already_integrated_noop"


# --------------------------------------------------------------------------- repo snapshot


class FileRecord(StrictModel):
    path: str  # repo-relative, POSIX separators
    sha256: str
    size: int


class RepoSnapshot(StrictModel):
    schema_version: str = SCHEMA_VERSION
    root: str
    created_at: str = Field(default_factory=utcnow)
    files: list[FileRecord]
    fingerprint: str  # sha256 over sorted (path, sha256) pairs


# --------------------------------------------------------------------------- evidence


class Evidence(StrictModel):
    id: str
    kind: EvidenceKind
    file: str
    line_start: int
    line_end: int
    symbol: str | None = None
    snippet: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    rejected: bool = False
    reject_reason: str | None = None


class EvidenceMap(StrictModel):
    schema_version: str = SCHEMA_VERSION
    snapshot_fingerprint: str
    evidence: list[Evidence]

    def by_id(self, evidence_id: str) -> Evidence | None:
        for ev in self.evidence:
            if ev.id == evidence_id:
                return ev
        return None

    def of_kind(self, kind: EvidenceKind, *, include_rejected: bool = False) -> list[Evidence]:
        return [e for e in self.evidence if e.kind == kind and (include_rejected or not e.rejected)]


# --------------------------------------------------------------------------- capabilities


class Capability(StrictModel):
    name: str
    status: CapabilityStatus
    reason: str
    evidence_ids: list[str] = Field(default_factory=list)


class CapabilityMatrix(StrictModel):
    schema_version: str = SCHEMA_VERSION
    snapshot_fingerprint: str
    framework: Framework
    language: str = "python"
    capabilities: list[Capability]
    decision: CapabilityStatus
    decision_reason: str

    def get(self, name: str) -> Capability | None:
        for cap in self.capabilities:
            if cap.name == name:
                return cap
        return None


# --------------------------------------------------------------------------- integration plan


class PlanAction(StrictModel):
    action_type: ActionType
    file: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)


class MockDataVariant(StrictModel):
    variant: str  # success | empty | error
    input: dict[str, Any]
    output: dict[str, Any]


class MockToolSpec(StrictModel):
    name: str
    description: str = ""
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    mock_data: list[MockDataVariant] = Field(default_factory=list)
    freetext_params: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class DynamicVariableSpec(StrictModel):
    name: str
    description: str
    var_type: str = "string"
    example: str = ""
    source: str = ""  # where the value originates at runtime
    sink: str = ""  # how the running agent consumes it (framework specific)
    main_agent: bool = True  # False => simulator-only, never sent to the agent under test
    evidence_ids: list[str] = Field(default_factory=list)


class KBManifestEntry(StrictModel):
    path: str
    sha256: str
    size: int
    media_type: str
    scope: str = ""  # why the agent reads this document
    owner_approval_required: bool = True
    approved: bool = False
    privacy_flags: list[str] = Field(default_factory=list)
    license: str | None = None
    freshness: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class IntegrationPlan(StrictModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str
    snapshot_fingerprint: str
    framework: Framework
    mode: Mode
    actions: list[PlanAction]
    mock_tools: list[MockToolSpec] = Field(default_factory=list)
    dynamic_variables: list[DynamicVariableSpec] = Field(default_factory=list)
    kb_manifest: list[KBManifestEntry] = Field(default_factory=list)
    notes: str = ""
    model_metadata: dict[str, Any] = Field(default_factory=dict)


# --------------------------------------------------------------------------- patching


class PatchEdit(StrictModel):
    file: str
    before_sha256: str
    after_sha256: str
    diff: str


class PatchSet(StrictModel):
    schema_version: str = SCHEMA_VERSION
    plan_id: str
    created_at: str = Field(default_factory=utcnow)
    edits: list[PatchEdit]

    @property
    def is_noop(self) -> bool:
        return all(e.before_sha256 == e.after_sha256 for e in self.edits) or not self.edits


class RollbackEntry(StrictModel):
    file: str
    original_sha256: str
    patched_sha256: str
    backup_path: str
    created: bool = False  # True when the file did not exist before the patch


class RollbackManifest(StrictModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    repo_root: str
    created_at: str = Field(default_factory=utcnow)
    entries: list[RollbackEntry]


# --------------------------------------------------------------------------- platform desired state


class CekuraAgentSpec(StrictModel):
    name: str
    description: str  # full system prompt
    project: int | None = None
    provider_type: str  # livekit | pipecat
    provider_config: dict[str, Any] = Field(default_factory=dict)  # e.g. tracing_enabled, agent_name


class CekuraDesiredState(StrictModel):
    schema_version: str = SCHEMA_VERSION
    agent_id: int | None = None  # None => create
    agent: CekuraAgentSpec | None = None
    mock_tools: list[MockToolSpec] = Field(default_factory=list)
    dynamic_variables: list[DynamicVariableSpec] = Field(default_factory=list)
    kb_uploads: list[KBManifestEntry] = Field(default_factory=list)
    repo_root: str = ""  # for resolving approved KB file paths at upload time
    dashboard_url: str = ""
    notes: str = ""


# --------------------------------------------------------------------------- verification


class CheckResult(StrictModel):
    name: str
    passed: bool
    severity: str = "error"  # error | warning | info
    detail: str = ""


class VerificationReport(StrictModel):
    schema_version: str = SCHEMA_VERSION
    run_id: str
    created_at: str = Field(default_factory=utcnow)
    repo_root: str
    framework: Framework
    mode: Mode | None = None
    statuses: dict[str, Status] = Field(default_factory=dict)  # capability -> status
    checks: list[CheckResult] = Field(default_factory=list)
    needs_human: list[str] = Field(default_factory=list)  # stable reason codes
    blockers: list[str] = Field(default_factory=list)  # stable blocker codes
    model_usage: dict[str, Any] = Field(default_factory=dict)
    exit_code: int = 0

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")
