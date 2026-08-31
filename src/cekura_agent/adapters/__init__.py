"""Adapters: turn a validated IntegrationPlan into concrete file contents.

`build_file_edits` returns {repo-relative path: new content}. Nothing is written
here — patching/apply is the executor's job (patching.py).
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path

from ..errors import AgentError
from ..models import ActionType, EvidenceKind, IntegrationPlan, Mode
from ..scanner import InspectionResult
from .base import AdapterError
from .livekit import integrate_livekit
from .pipecat import integrate_pipecat

__all__ = ["AdapterError", "build_file_edits", "unified_diff"]

CANONICAL_ORDER = [
    ActionType.ADD_DEPENDENCY,
    ActionType.INSERT_TRACER_INIT,
    ActionType.INSERT_TRACK_SESSION,
    ActionType.INSERT_OBSERVE_SESSION,
    ActionType.PIPECAT_SINGLE_STEP,
    ActionType.PIPECAT_MULTI_STEP,
    ActionType.ADD_ENV_PLACEHOLDER,
    ActionType.ALREADY_INTEGRATED_NOOP,
]


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=f"a/{path}", tofile=f"b/{path}",
    ))


def _read(root: Path, rel: str, pending: dict[str, str]) -> str:
    if rel in pending:
        return pending[rel]
    path = root / rel
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _add_dependency(content: str, rel: str, package: str) -> str:
    if "cekura[" in content:
        return content
    if rel.endswith(".txt") or not rel.endswith(".toml"):
        body = content.rstrip("\n")
        return (body + "\n" if body else "") + package + "\n"
    # pyproject.toml: append to [project].dependencies array, then re-validate
    match = re.search(r"(?ms)(^dependencies\s*=\s*\[)(.*?)(^\])", content)
    if not match:
        raise AdapterError(
            "pyproject.toml has no [project].dependencies array the agent can safely extend; "
            "add the dependency manually"
        )
    head, body, tail = match.groups()
    insert = f'    "{package}",\n'
    new_content = content[: match.start(3)] + insert + content[match.start(3):]
    try:
        import tomllib

        tomllib.loads(new_content)
    except Exception as exc:  # noqa: BLE001 - any parse failure must abort
        raise AdapterError(f"pyproject edit would break TOML syntax: {exc}") from exc
    del head, body
    return new_content


def _add_env_placeholders(content: str, keys: list[str]) -> str:
    lines = content.splitlines()
    present = {line.split("=", 1)[0].strip() for line in lines if "=" in line}
    additions = [f"{key}=" for key in keys if key not in present]
    if not additions:
        return content
    body = content.rstrip("\n")
    prefix = (body + "\n") if body else ""
    return prefix + "# Cekura (added by cekura-agent)\n" + "\n".join(additions) + "\n"


def build_file_edits(plan: IntegrationPlan, inspection: InspectionResult) -> dict[str, str]:
    """Compute the full post-integration content of every touched file."""
    root = inspection.root
    emap = inspection.evidence_map
    pending: dict[str, str] = {}

    actions = sorted(plan.actions, key=lambda a: CANONICAL_ORDER.index(a.action_type))
    for action in actions:
        if action.action_type == ActionType.ALREADY_INTEGRATED_NOOP:
            continue

        if action.action_type == ActionType.ADD_DEPENDENCY:
            rel = str(action.params.get("manifest") or action.file or "requirements.txt")
            pending[rel] = _add_dependency(_read(root, rel, pending), rel,
                                           str(action.params["package"]))

        elif action.action_type == ActionType.ADD_ENV_PLACEHOLDER:
            rel = str(action.params.get("file") or action.file or ".env.example")
            keys = [str(k) for k in action.params.get("keys", ["CEKURA_API_KEY"])]
            pending[rel] = _add_env_placeholders(_read(root, rel, pending), keys)

        elif action.action_type == ActionType.INSERT_TRACER_INIT:
            continue  # folded into the track/observe insertion (single anchored edit per file)

        elif action.action_type in (ActionType.INSERT_TRACK_SESSION, ActionType.INSERT_OBSERVE_SESSION):
            rel = action.file or ""
            entry = next(e for e in emap.of_kind(EvidenceKind.ENTRYPOINT)
                         if e.detail.get("framework") == "livekit" and e.file == rel)
            start = next(e for e in emap.of_kind(EvidenceKind.SESSION_START)
                         if e.file == rel and e.detail.get("function") == entry.symbol)
            mode = Mode.TEST if action.action_type == ActionType.INSERT_TRACK_SESSION else Mode.OBSERVE
            pending[rel] = integrate_livekit(
                _read(root, rel, pending), rel,
                mode=mode,
                agent_id=_plan_agent_id(plan),
                entry_function=str(entry.symbol),
                ctx_param=str(start.detail.get("ctx_param") or entry.detail.get("ctx_param") or "ctx"),
                session_var=str(start.detail["session_var"]),
                agent_arg=start.detail.get("agent_arg"),
            )

        elif action.action_type in (ActionType.PIPECAT_SINGLE_STEP, ActionType.PIPECAT_MULTI_STEP):
            rel = action.file or ""
            task = next(e for e in emap.of_kind(EvidenceKind.PIPELINE_TASK) if e.file == rel)
            entry = next(e for e in emap.of_kind(EvidenceKind.ENTRYPOINT)
                         if e.detail.get("framework") == "pipecat" and e.file == rel)
            pending[rel] = integrate_pipecat(
                _read(root, rel, pending), rel,
                mode=plan.mode,
                agent_id=_plan_agent_id(plan),
                entry_function=str(task.detail["function"]),
                task_var=str(task.detail["task_var"]),
                pipeline_arg=str(task.detail["pipeline_arg"]),
                context_var=entry.detail.get("context_var"),
                multi_step=action.action_type == ActionType.PIPECAT_MULTI_STEP,
            )
        else:  # pragma: no cover - enum is closed
            raise AgentError(f"unhandled action type {action.action_type}")

    return pending


def _plan_agent_id(plan: IntegrationPlan) -> int | None:
    for action in plan.actions:
        value = action.params.get("agent_id")
        if isinstance(value, int):
            return value
    return None
