"""Slice A gates: CLI exposes the workflow, dry-run defaults, modes explicit."""

from pathlib import Path

from typer.testing import CliRunner

from cekura_agent.cli import app

runner = CliRunner()
FIXTURES = Path(__file__).parent / "fixtures"


def test_help_exposes_workflow():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ["inspect", "plan", "diff", "integrate", "verify", "prepare-platform", "apply-platform", "rollback"]:
        assert cmd in result.output


def test_mode_is_required_and_restricted():
    result = runner.invoke(app, ["plan", "."])
    assert result.exit_code != 0  # --mode required, no default
    result = runner.invoke(app, ["integrate", ".", "--mode", "both"])
    assert result.exit_code != 0  # 'both' is not a mode


def test_integrate_is_dry_run_by_default():
    result = runner.invoke(app, ["integrate", "--help"])
    assert "--apply" in result.output
    assert "dry-run" in result.output


def test_unknown_option_fails():
    result = runner.invoke(app, ["inspect", ".", "--frobnicate"])
    assert result.exit_code != 0


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "cekura-agent" in result.output


def test_no_args_opens_interactive_shell_with_slash_inspect():
    fixture = FIXTURES / "livekit_basic"
    result = runner.invoke(app, input=f'n\n/inspect "{fixture!s}"\n/exit\n')
    assert result.exit_code == 0, result.output
    assert "interactive shell" in result.output
    assert '"framework": "livekit"' in result.output
    assert '"decision": "supported"' in result.output


def test_shell_defaults_apply_to_slash_plan():
    fixture = FIXTURES / "pipecat_single"
    result = runner.invoke(app, input=f'n\n/use "{fixture!s}"\n/agent 42\n/plan\n/exit\n')
    assert result.exit_code == 0, result.output
    assert '"framework": "pipecat"' in result.output
    assert '"mode": "test"' in result.output
    assert '"planner": "fake"' in result.output


def test_shell_keeps_running_after_needs_human():
    fixture = FIXTURES / "readme_only"
    result = runner.invoke(app, input=f'n\n/integrate "{fixture!s}"\n/status\n/exit\n')
    assert result.exit_code == 0, result.output
    assert "NEEDS_HUMAN: NO_FRAMEWORK" in result.output
    assert "command exit code: 2" in result.output
    assert "tracing mode: test" in result.output


def test_shell_securely_configures_live_services(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("CEKURA_API_KEY", "")
    result = runner.invoke(
        app,
        input="y\ny\ntest-openrouter-secret\ny\ntest-cekura-secret\n/status\n/exit\n",
    )
    assert result.exit_code == 0, result.output
    assert "planner: openrouter" in result.output
    assert "platform: staging" in result.output
    assert "test-openrouter-secret" not in result.output
    assert "test-cekura-secret" not in result.output
    assert "OPENROUTER_API_KEY: configured" in result.output
    assert "CEKURA_API_KEY: configured" in result.output
