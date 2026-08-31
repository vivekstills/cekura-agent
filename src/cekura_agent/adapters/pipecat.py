"""Pipecat adapter: documented Cekura tracing transformations.

Single-step (PipelineTask has no custom kwargs):
    task = tracer.track_and_create_task(pipeline, context, runner_args=..., transport=...)
Multi-step (custom kwargs must be preserved):
    pipeline = tracer.track_pipeline(pipeline, context, runner_args=...)
    task = PipelineTask(pipeline, <original kwargs>, enable_tracing=True, enable_turn_tracking=True)
    task = tracer.register_task_handlers(task, transport=...)

The tracer is instantiated INSIDE the per-call function (PipecatTracer is not
thread-safe to share across concurrent sessions).
"""

from __future__ import annotations

import ast

from ..models import Mode
from .base import (
    AdapterError,
    LineEdit,
    agent_id_expr,
    apply_line_edits,
    find_function,
    has_from_import,
    has_plain_import,
    import_insertion_line,
    indent_of,
    parse_module,
)

TRACER_VAR = "cekura_tracer"


def _find_task_assign(fn: ast.AST, task_var: str) -> ast.Assign:
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            if name == "PipelineTask" and node.targets and isinstance(node.targets[0], ast.Name) \
                    and node.targets[0].id == task_var:
                return node
    raise AdapterError(f"{task_var} = PipelineTask(...) not found — file drifted since inspection")


def _detect_kwarg_expr(fn: ast.AST, params: list[str], candidates: tuple[str, ...]) -> str | None:
    """Pick the local name for runner_args / transport if present in the function."""
    for cand in candidates:
        if cand in params:
            return cand
    assigned = {
        t.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    for cand in candidates:
        if cand in assigned:
            return cand
    return None


def integrate_pipecat(
    source: str,
    filename: str,
    *,
    mode: Mode,
    agent_id: int | None,
    entry_function: str,
    task_var: str,
    pipeline_arg: str,
    context_var: str | None,
    multi_step: bool,
) -> str:
    tree = parse_module(source, filename)
    lines = source.splitlines()
    edits: list[LineEdit] = []

    # 1. imports
    import_lines: list[str] = []
    if not has_plain_import(tree, "os"):
        import_lines.append("import os")
    if not has_from_import(tree, "cekura.pipecat", "PipecatTracer"):
        import_lines.append("from cekura.pipecat import PipecatTracer")
    if import_lines:
        anchor = import_insertion_line(tree)
        edits.append(LineEdit(start=anchor, end=anchor - 1, lines=import_lines))

    fn = find_function(tree, entry_function)
    params = [a.arg for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs]
    task_assign = _find_task_assign(fn, task_var)
    indent = indent_of(lines, task_assign.lineno)

    if context_var is None:
        raise AdapterError(
            "no LLM context variable detected; the Cekura Pipecat SDK requires the LLMContext "
            "used by the aggregator pair"
        )
    runner_args_expr = _detect_kwarg_expr(fn, params, ("runner_args", "runner_arguments"))
    transport_expr = _detect_kwarg_expr(fn, params, ("transport",))

    method = "track" if mode == Mode.TEST else "observe"
    tracer_init = [
        f"{indent}# Cekura tracing (added by cekura-agent) — per-call tracer: not thread-safe to share",
        f"{indent}{TRACER_VAR} = PipecatTracer(",
        f'{indent}    api_key=os.getenv("CEKURA_API_KEY"),',
        f"{indent}    agent_id={agent_id_expr(agent_id)},",
        f"{indent})",
    ]

    start, end = task_assign.lineno, task_assign.end_lineno or task_assign.lineno

    if not multi_step:
        call_kwargs = ""
        if runner_args_expr:
            call_kwargs += f", runner_args={runner_args_expr}"
        if transport_expr:
            call_kwargs += f", transport={transport_expr}"
        replacement = tracer_init + [
            f"{indent}{task_var} = {TRACER_VAR}.{method}_and_create_task(",
            f"{indent}    {pipeline_arg}, {context_var}{call_kwargs},",
            f"{indent})",
        ]
        edits.append(LineEdit(start=start, end=end, lines=replacement))
    else:
        original_call = task_assign.value
        assert isinstance(original_call, ast.Call)
        existing_kwargs = {kw.arg for kw in original_call.keywords if kw.arg}
        kwarg_srcs = [ast.unparse(kw) for kw in original_call.keywords]
        if "enable_tracing" not in existing_kwargs:
            kwarg_srcs.append("enable_tracing=True")
        if "enable_turn_tracking" not in existing_kwargs:
            kwarg_srcs.append("enable_turn_tracking=True")

        wrap_kwargs = f", runner_args={runner_args_expr}" if runner_args_expr else ""
        register_kwargs = f", transport={transport_expr}" if transport_expr else ""
        rebuilt_task = [f"{indent}{task_var} = PipelineTask("]
        rebuilt_task.append(f"{indent}    {pipeline_arg},")
        for kw_src in kwarg_srcs:
            rebuilt_task.append(f"{indent}    {kw_src},")
        rebuilt_task.append(f"{indent})")

        replacement = tracer_init + [
            f"{indent}{pipeline_arg} = {TRACER_VAR}.{method}_pipeline(",
            f"{indent}    {pipeline_arg}, {context_var}{wrap_kwargs},",
            f"{indent})",
            *rebuilt_task,
            f"{indent}{task_var} = {TRACER_VAR}.register_task_handlers({task_var}{register_kwargs})",
        ]
        edits.append(LineEdit(start=start, end=end, lines=replacement))

    return apply_line_edits(source, edits)
