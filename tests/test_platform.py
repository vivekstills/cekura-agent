"""Slice E gates: exact-value reconciliation vs the faithful fake Cekura server."""

import json
from pathlib import Path

import pytest

from cekura_agent.errors import AgentError, BlockedByAccess, NeedsHuman, PlatformContractError
from cekura_agent.models import Mode
from cekura_agent.orchestrator import prepare_platform_state
from cekura_agent.platform import CekuraClient, FakeCekuraServer, reconcile

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_cekura():
    server = FakeCekuraServer().start()
    yield server
    server.stop()


def _client(server, key="test-cekura-key"):
    client = CekuraClient(server.url, key)
    client.retry_delay_s = 0.01
    return client


def _desired(repo="livekit_basic", mode=Mode.TEST, agent_id=None, project_id=1):
    return prepare_platform_state(FIXTURES / repo, mode=mode, agent_id=agent_id,
                                  project_id=project_id)


# ------------------------------------------------------------------ happy path


def test_create_agent_and_reconcile_everything(fake_cekura):
    desired = _desired()
    result = reconcile(_client(fake_cekura), desired, apply=True)

    assert result["agent"]["action"] == "created"
    agent_id = result["agent"]["id"]
    agent = fake_cekura.agents[agent_id]
    assert agent["provider"]["credentials"]["config"]["tracing_enabled"] is True
    assert agent["provider"]["credentials"]["config"]["agent_name"] == "acme-scheduler"
    assert "{{customer_name}}" in agent["description"]

    assert result["mock_tools_diff"]["add"] == ["confirm_appointment", "lookup_availability"]
    assert {t["name"] for t in agent["mock_tools"]} == {"confirm_appointment", "lookup_availability"}
    assert {v["name"] for v in agent["dynamic_variables"]} == {
        "account_id", "appointment_date", "customer_name", "phone_number"}
    # KB discovered but NOT approved -> not uploaded, warned
    assert agent["knowledge_base_files"] == []
    assert any("NOT uploaded" in w for w in result["warnings"])
    assert result["verified"] is True
    assert result["dashboard_url"].endswith(f"/agents/{agent_id}")
    # auth header used on every request
    assert all(r["headers"].get("X-CEKURA-API-KEY") == "test-cekura-key"
               for r in fake_cekura.requests)


def test_kb_uploads_only_when_approved(fake_cekura):
    desired = _desired()
    desired.kb_uploads[0].approved = True
    result = reconcile(_client(fake_cekura), desired, apply=True,
                       kb_files_root=FIXTURES / "livekit_basic")
    agent = fake_cekura.agents[result["agent"]["id"]]
    assert agent["knowledge_base_files"] == ["faq.md"]
    assert result["verified"] is True


# ------------------------------------------------------------------ deletion safety


def _seed_with_legacy_tool(server):
    return server.seed_agent(mock_tools=[{
        "name": "legacy_tool", "description": "kept by ops",
        "mock_data": [{"input": {}, "output": {"ok": True}}], "freetext_params": [],
    }])


def test_unintended_deletion_blocked(fake_cekura):
    agent = _seed_with_legacy_tool(fake_cekura)
    desired = _desired(agent_id=agent["id"])
    with pytest.raises(NeedsHuman) as exc:
        reconcile(_client(fake_cekura), desired, apply=True)
    assert exc.value.reason_code == "DELETION_REQUIRES_APPROVAL"
    assert {t["name"] for t in fake_cekura.agents[agent["id"]]["mock_tools"]} == {"legacy_tool"}


def test_dry_run_reports_deletion_without_blocking(fake_cekura):
    agent = _seed_with_legacy_tool(fake_cekura)
    desired = _desired(agent_id=agent["id"])
    result = reconcile(_client(fake_cekura), desired, apply=False)
    assert result["mock_tools_diff"]["delete"] == ["legacy_tool"]
    assert any("approve-deletions" in w for w in result["warnings"])
    assert fake_cekura.mutation_count() == 0  # dry-run performed only GETs


def test_approved_deletion_removes_exactly(fake_cekura):
    agent = _seed_with_legacy_tool(fake_cekura)
    desired = _desired(agent_id=agent["id"])
    result = reconcile(_client(fake_cekura), desired, apply=True, approve_deletions=True)
    names = {t["name"] for t in fake_cekura.agents[agent["id"]]["mock_tools"]}
    assert names == {"confirm_appointment", "lookup_availability"}
    assert result["verified"] is True


def test_unapproved_deletion_entries_are_preserved_on_apply(fake_cekura):
    # if the caller applies without approval and without the legacy tool, we merge it back
    agent = _seed_with_legacy_tool(fake_cekura)
    desired = _desired(agent_id=agent["id"])
    with pytest.raises(NeedsHuman):
        reconcile(_client(fake_cekura), desired, apply=True)
    # explicit approval path is the only way to drop it; verified merge otherwise
    result = reconcile(_client(fake_cekura), desired, apply=False)
    assert "legacy_tool" in result["mock_tools_diff"]["delete"]


# ------------------------------------------------------------------ failure semantics


def test_wrong_agent_id_fails(fake_cekura):
    desired = _desired(agent_id=999)
    with pytest.raises(AgentError, match="not found"):
        reconcile(_client(fake_cekura), desired, apply=False)


def test_bad_key_unauthorized(fake_cekura):
    desired = _desired(agent_id=1)
    with pytest.raises(BlockedByAccess) as exc:
        reconcile(_client(fake_cekura, key="wrong-key"), desired, apply=False)
    assert exc.value.blocker_code == "CEKURA_UNAUTHORIZED"


def test_get_retries_on_429_and_5xx(fake_cekura):
    agent = fake_cekura.seed_agent()
    fake_cekura.error_queue = [429, 503]
    client = _client(fake_cekura)
    fetched = client.get_agent(agent["id"])  # two failures then success
    assert fetched["id"] == agent["id"]
    assert len(fake_cekura.requests) == 3


def test_timeout_after_commit_recovers_without_duplicate(fake_cekura):
    agent = fake_cekura.seed_agent()
    desired = _desired(agent_id=agent["id"])
    fake_cekura.hiccup_next_mutation = True  # first mutation commits, then 504
    result = reconcile(_client(fake_cekura), desired, apply=True)
    assert any("recovered from timeout" in w for w in result["warnings"])
    patches = [r for r in fake_cekura.requests if r["method"] == "PATCH"]
    assert len(patches) == 1  # never retried the possibly-committed PATCH
    assert result["verified"] is True


def test_corrupted_platform_state_fails_exact_verification(fake_cekura):
    agent = fake_cekura.seed_agent()
    desired = _desired(agent_id=agent["id"])
    fake_cekura.corrupt_next_write = True
    with pytest.raises(PlatformContractError, match="exactly match"):
        reconcile(_client(fake_cekura), desired, apply=True)


# ------------------------------------------------------------------ CLI guards


def test_apply_platform_cli_guards(tmp_path, monkeypatch, fake_cekura):
    from typer.testing import CliRunner

    from cekura_agent.cli import app

    desired = _desired(agent_id=fake_cekura.seed_agent()["id"])
    state_file = tmp_path / "desired.json"
    state_file.write_text(json.dumps(desired.model_dump(mode="json")))
    runner = CliRunner()

    # offline without base-url -> blocked
    result = runner.invoke(app, ["apply-platform", "--desired-state", str(state_file)])
    assert result.exit_code == 1 and "PLATFORM_OFFLINE" in result.output

    # staging refuses base-url override
    result = runner.invoke(app, ["apply-platform", "--desired-state", str(state_file),
                                 "--platform-mode", "staging", "--base-url", fake_cekura.url])
    assert result.exit_code == 1 and "only allowed in offline" in result.output

    # staging without key -> blocked (empty key prevents .env pickup)
    monkeypatch.setenv("CEKURA_API_KEY", "")
    result = runner.invoke(app, ["apply-platform", "--desired-state", str(state_file),
                                 "--platform-mode", "staging"])
    assert result.exit_code == 1 and "CEKURA_KEY_MISSING" in result.output

    # offline + local fake base-url works end to end (dry-run: GETs only)
    monkeypatch.setenv("CEKURA_API_KEY", "test-cekura-key")
    result = runner.invoke(app, ["apply-platform", "--desired-state", str(state_file),
                                 "--base-url", fake_cekura.url])
    assert result.exit_code == 0
    assert '"applied": false' in result.output
