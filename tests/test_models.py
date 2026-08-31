"""Slice A gates: strict schemas reject unknown fields, versions are stamped."""

import pytest
from pydantic import ValidationError

from cekura_agent import SCHEMA_VERSION
from cekura_agent.models import (
    Evidence,
    EvidenceKind,
    EvidenceMap,
    Framework,
    IntegrationPlan,
    Mode,
    PatchSet,
    VerificationReport,
)


def _evidence(**over):
    base = dict(id="ev1", kind=EvidenceKind.ENTRYPOINT, file="agent.py", line_start=1, line_end=2)
    base.update(over)
    return Evidence(**base)


def test_unknown_field_rejected_everywhere():
    with pytest.raises(ValidationError):
        Evidence(id="x", kind=EvidenceKind.OTHER, file="a", line_start=1, line_end=1, bogus=True)
    with pytest.raises(ValidationError):
        IntegrationPlan(
            plan_id="p", snapshot_fingerprint="f", framework=Framework.LIVEKIT,
            mode=Mode.TEST, actions=[], surprise="nope",
        )


def test_schema_version_stamped():
    plan = IntegrationPlan(
        plan_id="p", snapshot_fingerprint="f", framework=Framework.PIPECAT, mode=Mode.OBSERVE, actions=[]
    )
    assert plan.schema_version == SCHEMA_VERSION
    assert PatchSet(plan_id="p", edits=[]).schema_version == SCHEMA_VERSION


def test_evidence_map_lookup_skips_rejected():
    emap = EvidenceMap(
        snapshot_fingerprint="f",
        evidence=[_evidence(), _evidence(id="ev2", rejected=True, reject_reason="readme only")],
    )
    assert emap.by_id("ev2").rejected is True
    assert [e.id for e in emap.of_kind(EvidenceKind.ENTRYPOINT)] == ["ev1"]


def test_report_pass_semantics():
    from cekura_agent.models import CheckResult

    rep = VerificationReport(run_id="r", repo_root="/tmp/x", framework=Framework.LIVEKIT)
    rep.checks.append(CheckResult(name="ok", passed=True))
    rep.checks.append(CheckResult(name="warn", passed=False, severity="warning"))
    assert rep.passed  # warnings do not fail the report
    rep.checks.append(CheckResult(name="bad", passed=False))
    assert not rep.passed
