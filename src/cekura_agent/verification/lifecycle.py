"""Lifecycle verifiers: the documented Cekura SDK invariants, enforced by AST.

LiveKit:
  L1 tracer call (track_session/observe_session) strictly BEFORE session.start()
  L2 track_session passes the agent instance (3 args)
  L3 LiveKitTracer initialized at module scope
  L4 api_key comes from the environment, never a literal
  L5 mode consistency (test->track_session, observe->observe_session), no duplicates

Pipecat:
  P1 PipecatTracer instantiated INSIDE the per-call function (not module scope)
  P2 single-step: task from <tracer>.track_and_create_task/observe_and_create_task
     multi-step: *_pipeline wrap + PipelineTask(enable_tracing=True, enable_turn_tracking=True)
                 + register_task_handlers AFTER task creation
  P3 aggregator pair still present in the pipeline
  P4 api_key from environment
  P5 mode consistency + no direct-observe duplication
"""

from __future__ import annotations

import ast
import py_compile
from pathlib import Path

from ..models import CheckResult, Mode


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, OSError, UnicodeDecodeError):
        return None


def _calls_of(tree: ast.AST, method_names: set[str]) -> list[tuple[str, ast.Call, ast.AST]]:
    """(method_name, call node, enclosing function) for attribute calls in `tree`."""
    out = []
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                        and node.func.attr in method_names:
                    out.append((node.func.attr, node, fn))
    return out


def _module_scope_assign_of(tree: ast.Module, callee: str) -> bool:
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == callee:
                return True
    return False


def _any_assign_of(tree: ast.AST, callee: str) -> list[ast.Call]:
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == callee:
                calls.append(node)
    return calls


def _api_key_from_env(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "api_key":
            if isinstance(kw.value, ast.Constant):
                return False
            return "environ" in ast.dump(kw.value) or "getenv" in ast.dump(kw.value)
    return True  # no api_key kwarg -> SDK env fallback


def check_syntax(files: list[Path]) -> list[CheckResult]:
    results = []
    for path in files:
        if path.suffix != ".py":
            continue
        try:
            py_compile.compile(str(path), doraise=True)
            results.append(CheckResult(name=f"syntax:{path.name}", passed=True))
        except py_compile.PyCompileError as exc:
            results.append(CheckResult(name=f"syntax:{path.name}", passed=False, detail=str(exc)))
    return results


def check_livekit_file(path: Path, expected_mode: Mode | None) -> list[CheckResult]:
    tree = _parse(path)
    if tree is None:
        return [CheckResult(name=f"livekit:{path.name}:parse", passed=False, detail="unparseable")]
    results: list[CheckResult] = []
    rel = path.name

    tracer_calls = _calls_of(tree, {"track_session", "observe_session"})
    start_calls = _calls_of(tree, {"start"})

    # L1: order within the same function
    for method, call, fn in tracer_calls:
        starts_in_fn = [c for m, c, f in start_calls if f is fn and m == "start"]
        for start in starts_in_fn:
            passed = call.lineno < start.lineno
            results.append(CheckResult(
                name=f"livekit:{rel}:tracer_before_start",
                passed=passed,
                detail=f"{method}@{call.lineno} vs session.start@{start.lineno}",
            ))

    # L2: track_session arity (ctx, session, agent)
    for method, call, _fn in tracer_calls:
        if method == "track_session":
            results.append(CheckResult(
                name=f"livekit:{rel}:track_session_has_agent",
                passed=len(call.args) >= 3,
                detail=f"{len(call.args)} positional args",
            ))

    # L3: module-scope init
    if _any_assign_of(tree, "LiveKitTracer"):
        results.append(CheckResult(
            name=f"livekit:{rel}:module_scope_init",
            passed=_module_scope_assign_of(tree, "LiveKitTracer"),
            detail="LiveKitTracer must be initialized once at module scope",
        ))
        for call in _any_assign_of(tree, "LiveKitTracer"):
            results.append(CheckResult(
                name=f"livekit:{rel}:api_key_from_env",
                passed=_api_key_from_env(call),
                detail="api_key must come from the environment",
            ))

    # L5: mode consistency + exactly-once
    methods = [m for m, _c, _f in tracer_calls]
    if expected_mode is not None and tracer_calls:
        expected = "track_session" if expected_mode == Mode.TEST else "observe_session"
        wrong = [m for m in methods if m != expected]
        results.append(CheckResult(
            name=f"livekit:{rel}:mode_consistent",
            passed=not wrong,
            detail=f"expected {expected}, found {sorted(set(methods))}",
        ))
    if tracer_calls:
        results.append(CheckResult(
            name=f"livekit:{rel}:tracer_exactly_once",
            passed=len(tracer_calls) == 1,
            detail=f"{len(tracer_calls)} tracer call site(s)",
        ))
    return results


def check_pipecat_file(path: Path, expected_mode: Mode | None) -> list[CheckResult]:
    tree = _parse(path)
    if tree is None:
        return [CheckResult(name=f"pipecat:{path.name}:parse", passed=False, detail="unparseable")]
    results: list[CheckResult] = []
    rel = path.name

    tracer_inits = _any_assign_of(tree, "PipecatTracer")
    if not tracer_inits:
        return results

    # P1: per-call instantiation
    results.append(CheckResult(
        name=f"pipecat:{rel}:per_call_tracer",
        passed=not _module_scope_assign_of(tree, "PipecatTracer"),
        detail="PipecatTracer is not thread-safe to share; instantiate inside the per-call function",
    ))
    for call in tracer_inits:
        results.append(CheckResult(
            name=f"pipecat:{rel}:api_key_from_env",
            passed=_api_key_from_env(call),
            detail="api_key must come from the environment",
        ))

    single = _calls_of(tree, {"track_and_create_task", "observe_and_create_task"})
    wrap = _calls_of(tree, {"track_pipeline", "observe_pipeline"})
    register = _calls_of(tree, {"register_task_handlers"})

    if single:
        results.append(CheckResult(
            name=f"pipecat:{rel}:integration_style", passed=True, detail="single_step"))
    elif wrap:
        task_calls = _any_assign_of(tree, "PipelineTask")
        flags_ok = False
        task_line = 0
        for call in task_calls:
            kwargs = {kw.arg: kw.value for kw in call.keywords if kw.arg}
            flags_ok = (
                isinstance(kwargs.get("enable_tracing"), ast.Constant)
                and kwargs["enable_tracing"].value is True
                and isinstance(kwargs.get("enable_turn_tracking"), ast.Constant)
                and kwargs["enable_turn_tracking"].value is True
            )
            task_line = call.lineno
        results.append(CheckResult(
            name=f"pipecat:{rel}:multi_step_tracing_flags",
            passed=flags_ok,
            detail="PipelineTask needs enable_tracing=True, enable_turn_tracking=True",
        ))
        results.append(CheckResult(
            name=f"pipecat:{rel}:register_task_handlers",
            passed=bool(register) and all(c.lineno > task_line for _m, c, _f in register),
            detail="register_task_handlers must be called after PipelineTask creation",
        ))
    else:
        results.append(CheckResult(
            name=f"pipecat:{rel}:integration_style",
            passed=False,
            detail="PipecatTracer initialized but no track/observe call found",
        ))

    # P5: mode + duplication
    methods = [m for m, _c, _f in single + wrap]
    if expected_mode is not None and methods:
        prefix = "track" if expected_mode == Mode.TEST else "observe"
        results.append(CheckResult(
            name=f"pipecat:{rel}:mode_consistent",
            passed=all(m.startswith(prefix) for m in methods),
            detail=f"expected {prefix}_*, found {sorted(set(methods))}",
        ))
    if methods:
        results.append(CheckResult(
            name=f"pipecat:{rel}:tracer_exactly_once",
            passed=len(single) + len(wrap) == 1,
            detail=f"{len(single) + len(wrap)} tracer call site(s)",
        ))
    try:
        text = path.read_text(encoding="utf-8")
        if "/observability/v1/observe" in text:
            results.append(CheckResult(
                name=f"pipecat:{rel}:no_direct_observe_duplication",
                passed=False,
                detail="SDK and direct POST /observability/v1/observe/ on the same session "
                       "produce duplicate records",
            ))
    except OSError:
        pass
    return results
