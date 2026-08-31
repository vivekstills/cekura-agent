"""cekura-agent CLI.

Workflow: inspect -> plan -> diff -> integrate (dry-run default) -> verify
          -> prepare-platform -> apply-platform (staging only) -> rollback

Exit codes: 0 success, 1 failure, 2 NEEDS_HUMAN.
"""

from __future__ import annotations

import json
import os
import shlex
from dataclasses import dataclass
from pathlib import Path

import typer

from . import __version__
from .errors import AgentError, NeedsHuman
from .models import Mode
from .safety import redact

app = typer.Typer(
    add_completion=False,
    no_args_is_help=False,
    help=(
        "Autonomous Cekura integration agent for LiveKit/Pipecat Python repos: "
        "monitoring (test/observe tracing), mock tools, dynamic variables, knowledge base, "
        "Cekura platform desired-state reconciliation, verification and rollback. "
        "Run without a command to open the interactive slash-command shell."
    ),
)

MODE_HELP = "Integration mode: 'test' (Cekura simulations, track_*) or 'observe' (production monitoring, observe_*). Required; there is no 'both' default."
SHELL_COMMANDS = {
    "inspect", "plan", "diff", "integrate", "verify", "prepare-platform", "apply-platform", "rollback"
}
REPO_COMMANDS = {"inspect", "plan", "diff", "integrate", "verify", "prepare-platform", "rollback"}
MODE_COMMANDS = {"plan", "diff", "integrate", "verify", "prepare-platform"}
MODEL_COMMANDS = {"plan", "diff", "integrate"}


@dataclass
class ShellState:
    repo: Path
    mode: Mode = Mode.TEST
    model_mode: str = "fake"
    platform_mode: str = "offline"
    agent_id: int | None = None
    project_id: int | None = None


def _shell_help() -> None:
    typer.echo("""Slash commands:
  /inspect [repo]                  inspect a repository
  /plan [repo]                     build a validated integration plan
  /diff [repo]                     show the patch without writing
  /integrate [repo] [--apply]      run the integration workflow
  /verify [repo]                   verify the current integration
  /prepare-platform [repo]         emit Cekura desired state
  /apply-platform [options]        reconcile platform state
  /rollback [repo]                 restore the latest applied patch
  /use <repo>                      set the default target repository
  /mode <test|observe>             set tracing mode
  /agent <id> [project-id]         set Cekura agent and optional project
  /online                          configure live services securely
  /offline                         use fake planner and offline platform
  /status                          show shell settings (never secret values)
  /help [command]                  show shell or command help
  /exit                            close the shell

Arguments and options match the one-shot CLI. Quote paths that contain spaces.""")


def _shell_status(state: ShellState) -> None:
    typer.echo(f"repository: {state.repo}")
    typer.echo(f"tracing mode: {state.mode.value}")
    typer.echo(f"planner: {state.model_mode}")
    typer.echo(f"platform: {state.platform_mode}")
    typer.echo(f"agent id: {state.agent_id if state.agent_id is not None else 'not set'}")
    typer.echo(f"project id: {state.project_id if state.project_id is not None else 'not set'}")
    typer.echo(f"OPENROUTER_API_KEY: {'configured' if os.environ.get('OPENROUTER_API_KEY') else 'not set'}")
    typer.echo(f"CEKURA_API_KEY: {'configured' if os.environ.get('CEKURA_API_KEY') else 'not set'}")


def _prompt_secret(name: str, label: str) -> None:
    if os.environ.get(name):
        return
    value = typer.prompt(label, hide_input=True).strip()
    if not value:
        raise typer.BadParameter(f"{name} cannot be empty")
    os.environ[name] = value


def _configure_online(state: ShellState) -> None:
    if typer.confirm("Use live OpenRouter / Kimi K3?", default=True):
        _prompt_secret("OPENROUTER_API_KEY", "OpenRouter API key")
        state.model_mode = "openrouter"
    if typer.confirm("Use real Cekura staging?", default=True):
        _prompt_secret("CEKURA_API_KEY", "Cekura API key")
        state.platform_mode = "staging"
    _shell_status(state)


def _has_option(args: list[str], option: str) -> bool:
    return option in args or any(arg.startswith(f"{option}=") for arg in args)


def _option_value(args: list[str], option: str, default: str) -> str:
    if option in args:
        index = args.index(option)
        if index + 1 < len(args):
            return args[index + 1]
    prefix = f"{option}="
    return next((arg.removeprefix(prefix) for arg in args if arg.startswith(prefix)), default)


def _add_shell_defaults(command: str, args: list[str], state: ShellState) -> list[str]:
    prepared = list(args)
    if command in REPO_COMMANDS:
        if not prepared or prepared[0].startswith("-"):
            prepared.insert(0, str(state.repo))
        else:
            candidate = Path(prepared[0]).expanduser()
            if candidate.exists() and candidate.is_dir():
                state.repo = candidate.resolve()
    if command in MODE_COMMANDS and not _has_option(prepared, "--mode"):
        prepared.extend(["--mode", state.mode.value])
    if command in MODEL_COMMANDS and not _has_option(prepared, "--model-mode"):
        prepared.extend(["--model-mode", state.model_mode])
    if command == "integrate" and not _has_option(prepared, "--platform-mode"):
        prepared.extend(["--platform-mode", state.platform_mode])
    if command == "apply-platform" and not _has_option(prepared, "--platform-mode"):
        prepared.extend(["--platform-mode", state.platform_mode])
    if command in {"plan", "diff", "integrate", "prepare-platform"} \
            and state.agent_id is not None and not _has_option(prepared, "--agent-id"):
        prepared.extend(["--agent-id", str(state.agent_id)])
    if command in {"integrate", "prepare-platform"} \
            and state.project_id is not None and not _has_option(prepared, "--project-id"):
        prepared.extend(["--project-id", str(state.project_id)])
    return prepared


def _ensure_live_keys(command: str, args: list[str], state: ShellState) -> None:
    model_mode = _option_value(args, "--model-mode", state.model_mode)
    platform_mode = _option_value(args, "--platform-mode", state.platform_mode)
    if command in MODEL_COMMANDS and model_mode == "openrouter":
        _prompt_secret("OPENROUTER_API_KEY", "OpenRouter API key")
    if command in {"integrate", "apply-platform"} and platform_mode == "staging":
        _prompt_secret("CEKURA_API_KEY", "Cekura API key")


def _run_shell_command(command: str, args: list[str]) -> int:
    click_app = typer.main.get_command(app)
    try:
        result = click_app.main(args=[command, *args], prog_name="cekura-agent", standalone_mode=False)
        return int(result or 0)
    except typer.Exit as exc:
        return exc.exit_code
    except typer.Abort:
        typer.echo("Aborted.", err=True)
        return 1
    except Exception as exc:
        show = getattr(exc, "show", None)
        if callable(show):
            show()
            return int(getattr(exc, "exit_code", 1))
        raise


def _interactive_shell() -> None:
    from .config import load_settings

    load_settings()
    state = ShellState(repo=Path.cwd().resolve())
    typer.secho(f"cekura-agent {__version__} interactive shell", bold=True)
    typer.echo("Offline mode is active. Type /online for live services, /help for commands, /exit to quit.")
    if typer.confirm("Configure live services now?", default=False):
        _configure_online(state)
    while True:
        try:
            line = input("cekura-agent> ").strip()
        except EOFError:
            typer.echo()
            return
        except KeyboardInterrupt:
            typer.echo("\nUse /exit to quit.")
            continue
        if not line:
            continue
        if not line.startswith("/"):
            typer.echo("Commands start with '/'. Type /help.", err=True)
            continue
        try:
            tokens = shlex.split(line[1:])
        except ValueError as exc:
            typer.echo(f"Invalid command: {exc}", err=True)
            continue
        if not tokens:
            continue
        command = tokens[0].replace("_", "-")
        args = tokens[1:]
        if command in {"exit", "quit"}:
            return
        if command == "help":
            if args:
                _run_shell_command(args[0].replace("_", "-"), ["--help"])
            else:
                _shell_help()
            continue
        if command == "status":
            _shell_status(state)
            continue
        if command == "use":
            if not args:
                typer.echo("Usage: /use <repo>", err=True)
                continue
            repo = Path(args[0]).expanduser().resolve()
            if not repo.is_dir():
                typer.echo(f"Repository directory not found: {repo}", err=True)
                continue
            state.repo = repo
            typer.echo(f"repository: {repo}")
            continue
        if command == "mode":
            if len(args) != 1 or args[0] not in {Mode.TEST.value, Mode.OBSERVE.value}:
                typer.echo("Usage: /mode <test|observe>", err=True)
                continue
            state.mode = Mode(args[0])
            typer.echo(f"tracing mode: {state.mode.value}")
            continue
        if command == "agent":
            try:
                state.agent_id = int(args[0])
                state.project_id = int(args[1]) if len(args) > 1 else None
            except (IndexError, ValueError):
                typer.echo("Usage: /agent <id> [project-id]", err=True)
                continue
            typer.echo(f"agent id: {state.agent_id}")
            continue
        if command == "online":
            try:
                _configure_online(state)
            except (typer.Abort, typer.BadParameter) as exc:
                typer.echo(str(exc), err=True)
            continue
        if command == "offline":
            state.model_mode = "fake"
            state.platform_mode = "offline"
            typer.echo("offline mode enabled")
            continue
        if command not in SHELL_COMMANDS:
            typer.echo(f"Unknown command: /{command}. Type /help.", err=True)
            continue
        prepared = _add_shell_defaults(command, args, state)
        try:
            _ensure_live_keys(command, prepared, state)
        except (typer.Abort, typer.BadParameter) as exc:
            typer.echo(str(exc), err=True)
            continue
        exit_code = _run_shell_command(command, prepared)
        if exit_code:
            typer.echo(f"command exit code: {exit_code}", err=True)


def _echo_json(payload: object, out: Path | None) -> None:
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


@app.callback(invoke_without_command=True)
def _root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", help="Print version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    del version
    if ctx.invoked_subcommand is None:
        _interactive_shell()


@app.command()
def inspect(
    repo: Path = typer.Argument(..., exists=True, file_okay=False, help="Path to the customer repo."),
    out: Path | None = typer.Option(None, "--json", help="Write full inspection JSON here."),
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
    agent_id: int | None = typer.Option(None, "--agent-id", help="Existing Cekura agent id."),
    out: Path | None = typer.Option(None, "--out", help="Write IntegrationPlan JSON here."),
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
    agent_id: int | None = typer.Option(None, "--agent-id"),
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
        typer.echo(redact(edit.diff))


@app.command()
def integrate(
    repo: Path = typer.Argument(..., exists=True, file_okay=False),
    mode: Mode = typer.Option(..., "--mode", help=MODE_HELP),
    apply: bool = typer.Option(False, "--apply", help="Actually write changes. Default is dry-run."),
    model_mode: str = typer.Option("fake", "--model-mode", help="fake | openrouter"),
    platform_mode: str = typer.Option("offline", "--platform-mode", help="offline | staging"),
    agent_id: int | None = typer.Option(None, "--agent-id"),
    project_id: int | None = typer.Option(None, "--project-id"),
    report: Path | None = typer.Option(None, "--report", help="Write VerificationReport JSON here."),
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
    mode: Mode | None = typer.Option(None, "--mode", help="Expected mode to verify against."),
    report: Path | None = typer.Option(None, "--report"),
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
    agent_id: int | None = typer.Option(None, "--agent-id"),
    project_id: int | None = typer.Option(None, "--project-id"),
    out: Path | None = typer.Option(None, "--out"),
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
    base_url: str | None = typer.Option(None, "--base-url", help="Override API base (tests only)."),
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
    run_id: str | None = typer.Option(None, "--run-id", help="Defaults to the latest run."),
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
