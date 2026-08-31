"""Slice F gates: autonomous end-to-end workflow, refusal, idempotence, rollback,
no external network in offline mode."""

import json
import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cekura_agent.cli import app
from cekura_agent.models import Mode, Status
from cekura_agent.orchestrator import integrate_repo, verify_repo
from cekura_agent.patching import rollback_run
from cekura_agent.safety import sha256_file

FIXTURES = Path(__file__).parent / "fixtures"
runner = CliRunner()


def _hashes(repo: Path):
    return {
        str(p.relative_to(repo)): sha256_file(p)
        for p in repo.rglob("*") if p.is_file() and ".cekura-agent" not in p.parts
    }


@pytest.fixture
def no_external_network(monkeypatch):
    """Offline gate: any non-loopback connection attempt fails the test."""
    real_connect = socket.socket.connect

    def guarded(self, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"external network attempted in offline mode: {address}")
        return real_connect(self, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded)


@pytest.mark.parametrize("fixture,framework", [("livekit_basic", "livekit"),
                                               ("pipecat_single", "pipecat"),
                                               ("pipecat_custom", "pipecat")])
def test_offline_autonomous_matrix(copy_fixture, no_external_network, fixture, framework):
    repo = copy_fixture(fixture)
    original = _hashes(repo)

    # 1) dry-run: no writes
    result = integrate_repo(repo, mode=Mode.TEST, agent_id=42)
    assert result.report.exit_code == 0
    assert _hashes(repo) == original
    assert result.report.statuses["code_integration"] == Status.NOT_RUN

    # 2) apply: full offline pipeline, all error-severity checks pass
    result = integrate_repo(repo, mode=Mode.TEST, apply=True, agent_id=42)
    assert result.report.exit_code == 0, result.report.model_dump()
    assert result.report.statuses["code_integration"] == Status.IMPLEMENTED_AND_OFFLINE_VERIFIED
    assert result.report.statuses["kimi_planner"] == Status.IMPLEMENTED_AND_OFFLINE_VERIFIED
    assert result.report.framework.value == framework
    patched = _hashes(repo)
    assert patched != original

    # 3) standalone verify agrees
    assert verify_repo(repo, mode=Mode.TEST).exit_code == 0

    # 4) idempotence: second apply is a no-op
    result2 = integrate_repo(repo, mode=Mode.TEST, apply=True, agent_id=42)
    assert result2.report.exit_code == 0
    assert result2.patchset.is_noop
    assert _hashes(repo) == patched

    # 5) exact rollback
    rollback_run(repo)
    assert _hashes(repo) == original


def test_refusal_fixture_exits_2(no_external_network):
    result = integrate_repo(FIXTURES / "readme_only", mode=Mode.TEST)
    assert result.report.exit_code == 2
    assert result.report.needs_human == ["NO_FRAMEWORK"]
    assert result.report.statuses["repo_compatibility"] == Status.NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY


def test_direct_observe_conflict_refused_not_skipped(no_external_network):
    result = integrate_repo(FIXTURES / "pipecat_direct_observe", mode=Mode.OBSERVE)
    assert result.report.exit_code == 2
    assert "DIRECT_OBSERVE_PRESENT" in result.report.needs_human


def test_platform_staging_without_key_is_blocked(copy_fixture, monkeypatch, no_external_network):
    monkeypatch.setenv("CEKURA_API_KEY", "")
    repo = copy_fixture("livekit_basic")
    result = integrate_repo(repo, mode=Mode.TEST, platform_mode="staging", agent_id=42)
    assert result.report.statuses["platform_registration"] == Status.BLOCKED_BY_ACCESS_OR_DEPENDENCY
    assert "CEKURA_KEY_MISSING" in result.report.blockers
    assert result.report.exit_code == 0  # blocked-by-access is honest, not a failure


def test_cli_integrate_writes_report(copy_fixture, tmp_path):
    repo = copy_fixture("livekit_basic")
    report_path = tmp_path / "report.json"
    result = runner.invoke(app, [
        "integrate", str(repo), "--mode", "test", "--agent-id", "42",
        "--report", str(report_path),
    ])
    assert result.exit_code == 0, result.output
    payload = json.loads(report_path.read_text())
    assert payload["statuses"]["repo_compatibility"] == "IMPLEMENTED_AND_OFFLINE_VERIFIED"
    assert "scenario_run_e2e" in payload["statuses"]  # never silently dropped


def test_verify_on_unintegrated_repo_fails_with_guidance():
    report = verify_repo(FIXTURES / "livekit_basic")
    assert report.exit_code == 1
    assert any("integrate" in c.detail for c in report.checks if not c.passed)
