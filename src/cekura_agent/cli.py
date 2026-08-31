"""cekura-agent CLI.

Workflow: inspect -> plan -> diff -> integrate (dry-run default) -> verify
          -> prepare-platform -> apply-platform (staging only) -> rollback

Exit codes: 0 success, 1 failure, 2 NEEDS_HUMAN.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .config import load_settings
from .errors import AgentError, NeedsHuman
from .models import Mode
from .safety import redact

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help=(
        "Autonomous Cekura integration agent for LiveKit/Pipecat Python repos: "
        "monitoring (test/observe tracing), mock tools, dynamic variables, knowledge base, "
        "Cekura platform desired-state reconciliation, verification and rollback."
    ),
)

MODE_HELP = "Integration mode: 'test' (Cekura simulations, track_*) or 'observe' (production monitoring, observe_*). Required; there is no 'both' default."


def _echo_json(payload: object, out: Optional[Path]) -> None:
    text = json.dumps(payload, indent=2, default=str)
    text = redact(text)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n")
        typer.echo(f"wrote {out}")
    else:
        typer.echo(text)


def _fail(exc: AgentError) -> None:
    prefix = "NEEDS_HUMAN" if isinstance(exc, NeedsHuman) else "ERROR"
    code = getattr(exc, "reason_code", None) or getattr(exc, "blocker_code", None)
    msg = f"{prefix}[{code}]: {exc.message}" if code else f"{prefix}: {exc.message}"
    typer.secho(redact(msg), err=True, fg="red" if prefix == "ERROR" else "yellow")
    if exc.detail:
        typer.secho(redact(exc.detail), err=True)
    raise typer.Exit(exc.exit_code)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"cekura-agent {__version__}")
        raise typer.Exit(0)


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", help="Print version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    del version


@app.command()
def inspect(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Path to the customer repo."),
    out: Optional[Path] = typer.Option(None, "--json", help="Write full inspection JSON here."),
) -> None:
    """Read-only repository analysis: framework, entrypoints, tools, variables, KB, existing Cekura."""
    from .scanner import inspect_repo

    try:
        result = inspect_repo(repo)
    except AgentError as exc:
        _fail(exc)
    _echo_json(result.summary(), out)


@app.command()
def plan(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    mode: Mode = typer.Option(..., "--mode", help=MODE_HELP),
    model_mode: str = typer.Option("fake", "--model-mode", help="fake | openrouter"),
    agent_id: Optional[int] = typer.Option(None, "--agent-id", help="Existing Cekura agent id."),
    out: Optional[Path] = typer.Option(None, "--out", help="Write IntegrationPlan JSON here."),
) -> None:
    """Produce and validate a constrained IntegrationPlan (no writes)."""
    from .orchestrator import make_plan

    try:
        plan_obj, _ctx = make_plan(repo, mode=mode, model_mode=model_mode, agent_id=agent_id)
    except AgentError as exc:
        _fail(exc)
    _echo_json(plan_obj.model_dump(mode="json"), out)


@app.command()
def diff(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    mode: Mode = typer.Option(..., "--mode", help=MODE_HELP),
    model_mode: str = typer.Option("fake", "--model-mode"),
    agent_id: Optional[int] = typer.Option(None, "--agent-id"),
) -> None:
    """Render the exact patch the integration would apply, without writing anything."""
    from .orchestrator import render_patchset

    try:
        patchset = render_patchset(repo, mode=mode, model_mode=model_mode, agent_id=agent_id)
    except AgentError as exc:
        _fail(exc)
    if patchset.is_noop:
        typer.echo("no changes (already integrated or nothing to do)")
    for edit in patchset.edits:
        typer.echo(edit.diff)


@app.command()
def integrate(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    mode: Mode = typer.Option(..., "--mode", help=MODE_HELP),
    apply: bool = typer.Option(False, "--apply", help="Actually write changes. Default is dry-run."),
    model_mode: str = typer.Option("fake", "--model-mode", help="fake | openrouter"),
    platform_mode: str = typer.Option("offline", "--platform-mode", help="offline | staging"),
    agent_id: Optional[int] = typer.Option(None, "--agent-id"),
    project_id: Optional[int] = typer.Option(None, "--project-id"),
    report: Optional[Path] = typer.Option(None, "--report", help="Write VerificationReport JSON here."),
    e2e: bool = typer.Option(False, "--e2e", help="Run platform E2E checks (staging only)."),
) -> None:
    """Full autonomous workflow: snapshot -> evidence -> plan -> patch -> verify -> platform prep."""
    from .orchestrator import integrate_repo

    try:
        result = integrate_repo(
            repo,
            mode=mode,
            apply=apply,
            model_mode=model_mode,
            platform_mode=platform_mode,
            agent_id=agent_id,
            project_id=project_id,
            e2e=e2e,
        )
    except AgentError as exc:
        _fail(exc)
    _echo_json(result.report.model_dump(mode="json"), report)
    typer.echo(result.human_summary())
    raise typer.Exit(result.report.exit_code)


@app.command()
def verify(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    mode: Optional[Mode] = typer.Option(None, "--mode", help="Expected mode to verify against."),
    report: Optional[Path] = typer.Option(None, "--report"),
) -> None:
    """Verify current repo state: lifecycle invariants, syntax, integration presence."""
    from .orchestrator import verify_repo

    try:
        rep = verify_repo(repo, mode=mode)
    except AgentError as exc:
        _fail(exc)
    _echo_json(rep.model_dump(mode="json"), report)
    raise typer.Exit(rep.exit_code)


@app.command("prepare-platform")
def prepare_platform(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    mode: Mode = typer.Option(..., "--mode", help=MODE_HELP),
    agent_id: Optional[int] = typer.Option(None, "--agent-id"),
    project_id: Optional[int] = typer.Option(None, "--project-id"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Emit the CekuraDesiredState (agent, mock tools, dynamic variables, KB) + dashboard URL."""
    from .orchestrator import prepare_platform_state

    try:
        state = prepare_platform_state(repo, mode=mode, agent_id=agent_id, project_id=project_id)
    except AgentError as exc:
        _fail(exc)
    _echo_json(state.model_dump(mode="json"), out)


@app.command("apply-platform")
def apply_platform(
    desired_state: Path = typer.Option(..., "--desired-state", exists=True, dir_okay=False),
    platform_mode: str = typer.Option("offline", "--platform-mode", help="offline | staging"),
    apply: bool = typer.Option(False, "--apply", help="Actually call the API. Default is dry-run."),
    approve_deletions: bool = typer.Option(False, "--approve-deletions"),
    base_url: Optional[str] = typer.Option(None, "--base-url", help="Override API base (tests only)."),
) -> None:
    """Reconcile Cekura platform state: GET -> exact diff -> apply once -> GET-after compare."""
    from .orchestrator import apply_platform_state

    try:
        outcome = apply_platform_state(
            desired_state,
            platform_mode=platform_mode,
            apply=apply,
            approve_deletions=approve_deletions,
            base_url=base_url,
        )
    except AgentError as exc:
        _fail(exc)
    _echo_json(outcome, None)


@app.command()
def rollback(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    run_id: Optional[str] = typer.Option(None, "--run-id", help="Defaults to the latest run."),
    force: bool = typer.Option(False, "--force", help="Restore even if files changed since the patch."),
) -> None:
    """Restore files exactly as they were before an integrate --apply run."""
    from .patching import rollback_run

    try:
        restored = rollback_run(repo, run_id=run_id, force=force)
    except AgentError as exc:
        _fail(exc)
    for path in restored:
        typer.echo(f"restored {path}")
    typer.echo("rollback complete")


def main() -> None:  # console_scripts entrypoint
    app()


if __name__ == "__main__":
    main()
