"""Slice A gates: CLI exposes the workflow, dry-run defaults, modes explicit."""

from typer.testing import CliRunner

from cekura_agent.cli import app

runner = CliRunner()


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
