"""Slice B gates: fake plan correctness, OpenRouter client contract, semantic validation."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cekura_agent.budget import BudgetLedger
from cekura_agent.cli import app
from cekura_agent.errors import BlockedByAccess, BudgetExceeded, PlanRejected
from cekura_agent.models import ActionType, Framework, Mode
from cekura_agent.orchestrator import make_plan
from cekura_agent.planner import build_bundle, validate_plan
from cekura_agent.planner.client import OpenRouterPlanner
from cekura_agent.scanner import inspect_repo

FIXTURES = Path(__file__).parent / "fixtures"


def action_types(plan):
    return [a.action_type for a in plan.actions]


# ------------------------------------------------------------------ fake planner


def test_fake_plan_livekit_test_mode():
    plan, _ = make_plan(FIXTURES / "livekit_basic", Mode.TEST, agent_id=77)
    assert plan.framework == Framework.LIVEKIT
    assert action_types(plan) == [
        ActionType.ADD_DEPENDENCY, ActionType.INSERT_TRACER_INIT,
        ActionType.INSERT_TRACK_SESSION, ActionType.ADD_ENV_PLACEHOLDER,
    ]
    dep = plan.actions[0]
    assert dep.params["package"].startswith("cekura[livekit]>=")
    assert plan.actions[2].file == "agent.py"


def test_fake_plan_livekit_observe_mode():
    plan, _ = make_plan(FIXTURES / "livekit_basic", Mode.OBSERVE)
    assert ActionType.INSERT_OBSERVE_SESSION in action_types(plan)
    assert ActionType.INSERT_TRACK_SESSION not in action_types(plan)
    # no agent id given -> env placeholder must include CEKURA_AGENT_ID
    env_action = plan.actions[-1]
    assert "CEKURA_AGENT_ID" in env_action.params["keys"]


def test_fake_plan_pipecat_single_vs_multi():
    plan, _ = make_plan(FIXTURES / "pipecat_single", Mode.TEST)
    assert ActionType.PIPECAT_SINGLE_STEP in action_types(plan)
    plan, _ = make_plan(FIXTURES / "pipecat_custom", Mode.TEST)
    assert ActionType.PIPECAT_MULTI_STEP in action_types(plan)


def test_fake_plan_already_integrated_noop():
    plan, _ = make_plan(FIXTURES / "livekit_existing", Mode.TEST)
    assert action_types(plan) == [ActionType.ALREADY_INTEGRATED_NOOP]


def test_ambiguous_repo_exits_2_via_cli():
    runner = CliRunner()
    result = runner.invoke(app, ["plan", str(FIXTURES / "ambiguous_livekit"), "--mode", "test"])
    assert result.exit_code == 2
    assert "AMBIGUOUS_ENTRYPOINT" in result.output


def test_no_framework_repo_exits_2_via_cli():
    runner = CliRunner()
    result = runner.invoke(app, ["plan", str(FIXTURES / "readme_only"), "--mode", "observe"])
    assert result.exit_code == 2
    assert "NO_FRAMEWORK" in result.output


# ------------------------------------------------------------------ openrouter client


def _client(settings_factory, fake_openrouter, monkeypatch, **kw):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-" + "deadbeef" * 4)
    settings = settings_factory(openrouter_base_url=fake_openrouter.url, **kw)
    planner = OpenRouterPlanner(settings, BudgetLedger(settings))
    planner.retry_base_delay_s = 0.01
    return planner


def _valid_model_reply(repo: Path, mode: Mode):
    """Use the fake planner's actions as the canned model response."""
    plan, ctx = make_plan(repo, mode, agent_id=5)
    return {
        "framework": plan.framework.value,
        "mode": mode.value,
        "actions": [a.model_dump(mode="json") for a in plan.actions],
        "notes": "model notes",
    }, ctx


def test_openrouter_happy_path_sends_auth_and_model(settings_factory, fake_openrouter, monkeypatch):
    reply, ctx = _valid_model_reply(FIXTURES / "livekit_basic", Mode.TEST)
    fake_openrouter.queue_completion(reply)
    planner = _client(settings_factory, fake_openrouter, monkeypatch)
    bundle = build_bundle(ctx.inspection, Mode.TEST, 5)
    actions, notes, meta = planner.plan(bundle, ctx.inspection, Mode.TEST, 5)
    assert [a.action_type for a in actions][0] == ActionType.ADD_DEPENDENCY
    request = fake_openrouter.requests[0]
    assert request["body"]["model"] == "moonshotai/kimi-k3"
    assert request["headers"]["Authorization"].startswith("Bearer sk-or-")
    assert request["body"]["response_format"] == {"type": "json_object"}
    assert meta["usage"]["prompt_tokens"] == 1200
    assert meta["cost_usd"] > 0


def test_openrouter_retries_on_429(settings_factory, fake_openrouter, monkeypatch):
    reply, ctx = _valid_model_reply(FIXTURES / "livekit_basic", Mode.TEST)
    fake_openrouter.queue(429, {"error": "rate limited"})
    fake_openrouter.queue_completion(reply)
    planner = _client(settings_factory, fake_openrouter, monkeypatch)
    bundle = build_bundle(ctx.inspection, Mode.TEST, 5)
    actions, _, _ = planner.plan(bundle, ctx.inspection, Mode.TEST, 5)
    assert actions and len(fake_openrouter.requests) == 2


def test_openrouter_malformed_json_rejected(settings_factory, fake_openrouter, monkeypatch):
    _, ctx = _valid_model_reply(FIXTURES / "livekit_basic", Mode.TEST)
    fake_openrouter.queue_completion("this is not json at all")
    planner = _client(settings_factory, fake_openrouter, monkeypatch)
    bundle = build_bundle(ctx.inspection, Mode.TEST, 5)
    with pytest.raises(PlanRejected):
        planner.plan(bundle, ctx.inspection, Mode.TEST, 5)


def test_openrouter_extra_fields_rejected(settings_factory, fake_openrouter, monkeypatch):
    reply, ctx = _valid_model_reply(FIXTURES / "livekit_basic", Mode.TEST)
    reply["shell_command"] = "rm -rf /"  # the model cannot smuggle capabilities
    fake_openrouter.queue_completion(reply)
    planner = _client(settings_factory, fake_openrouter, monkeypatch)
    bundle = build_bundle(ctx.inspection, Mode.TEST, 5)
    with pytest.raises(PlanRejected):
        planner.plan(bundle, ctx.inspection, Mode.TEST, 5)


def test_openrouter_missing_key_blocked(settings_factory, fake_openrouter):
    settings = settings_factory(openrouter_base_url=fake_openrouter.url)
    planner = OpenRouterPlanner(settings, BudgetLedger(settings))
    with pytest.raises(BlockedByAccess) as exc:
        planner.plan({}, None, Mode.TEST, None)
    assert exc.value.blocker_code == "OPENROUTER_KEY_MISSING"


def test_openrouter_budget_cap(settings_factory, fake_openrouter, monkeypatch):
    planner = _client(settings_factory, fake_openrouter, monkeypatch, per_run_cost_cap_usd=0.000001)
    with pytest.raises(BudgetExceeded):
        planner.plan({"big": "x" * 100}, None, Mode.TEST, None)


# ------------------------------------------------------------------ semantic validator


def test_validator_rejects_hostile_plans():
    inspection = inspect_repo(FIXTURES / "livekit_basic")
    plan, _ = make_plan(FIXTURES / "livekit_basic", Mode.TEST, agent_id=5)

    stale = plan.model_copy(update={"snapshot_fingerprint": "0" * 64})
    with pytest.raises(PlanRejected, match="stale"):
        validate_plan(stale, inspection, Mode.TEST)

    wrong_mode = plan.model_copy(update={"mode": Mode.OBSERVE})
    with pytest.raises(PlanRejected, match="mode"):
        validate_plan(wrong_mode, inspection, Mode.TEST)

    evil_path = plan.model_copy(deep=True)
    evil_path.actions[2].file = "../../etc/passwd"
    with pytest.raises(PlanRejected, match="outside the repository"):
        validate_plan(evil_path, inspection, Mode.TEST)

    ghost_evidence = plan.model_copy(deep=True)
    ghost_evidence.actions[2].evidence_ids = ["ev-9999"]
    with pytest.raises(PlanRejected, match="unknown evidence"):
        validate_plan(ghost_evidence, inspection, Mode.TEST)

    test_edit = plan.model_copy(deep=True)
    test_edit.actions[0].file = "tests/test_agent.py"
    test_edit.actions[0].params["manifest"] = "tests/test_agent.py"
    with pytest.raises(PlanRejected, match="test files"):
        validate_plan(test_edit, inspection, Mode.TEST)

    observe_in_test = plan.model_copy(deep=True)
    observe_in_test.actions[2].action_type = ActionType.INSERT_OBSERVE_SESSION
    with pytest.raises(PlanRejected, match="not allowed"):
        validate_plan(observe_in_test, inspection, Mode.TEST)


def test_validator_rejects_rejected_evidence_citation():
    inspection = inspect_repo(FIXTURES / "livekit_basic")
    plan, _ = make_plan(FIXTURES / "livekit_basic", Mode.TEST, agent_id=5)
    rejected_ids = [e.id for e in inspection.evidence_map.evidence if e.rejected]
    assert rejected_ids, "fixture should produce rejected evidence (README placeholder)"
    bad = plan.model_copy(deep=True)
    bad.actions[1].evidence_ids = [rejected_ids[0]]
    with pytest.raises(PlanRejected, match="rejected evidence"):
        validate_plan(bad, inspection, Mode.TEST)
