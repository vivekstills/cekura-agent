"""LiveKit adapter: documented Cekura tracing transformations.

Test mode    -> module-scope LiveKitTracer + `await tracer.track_session(ctx, session, agent)`
Observe mode -> module-scope LiveKitTracer + `await tracer.observe_session(ctx, session)`
Both are inserted immediately BEFORE `session.start(...)` (after = silent no-op per docs).
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
    statement_of,
)

TRACER_VAR = "cekura_tracer"


def _find_session_start(fn: ast.AST, session_var: str) -> ast.stmt:
    """The statement executing `<session_var>.start(...)` — awaited, wrapped in
    asyncio.create_task(...), or plain. The tracer insert goes before this statement."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "start":
            try:
                receiver = ast.unparse(node.func.value)
            except Exception:  # pragma: no cover
                receiver = ""
            if receiver == session_var:
                return statement_of(node, fn)
    raise AdapterError(f"{session_var}.start(...) not found — file drifted since inspection")


def integrate_livekit(
    source: str,
    filename: str,
    *,
    mode: Mode,
    agent_id: int | None,
    entry_function: str,
    ctx_param: str,
    session_var: str,
    agent_arg: str | None,
) -> str:
    tree = parse_module(source, filename)
    lines = source.splitlines()
    edits: list[LineEdit] = []

    # 1. imports + module-scope tracer init, as ONE insert so ordering is stable
    header_lines: list[str] = []
    if not has_plain_import(tree, "os"):
        header_lines.append("import os")
    if not has_from_import(tree, "cekura.livekit", "LiveKitTracer"):
        header_lines.append("from cekura.livekit import LiveKitTracer")
    header_lines += [
        "",
        "# Cekura tracing (added by cekura-agent)",
        f"{TRACER_VAR} = LiveKitTracer(",
        '    api_key=os.getenv("CEKURA_API_KEY"),',
        f"    agent_id={agent_id_expr(agent_id)},",
        ")",
    ]
    anchor = import_insertion_line(tree)
    edits.append(LineEdit(start=anchor, end=anchor - 1, lines=header_lines))

    # 3. tracer call strictly BEFORE await session.start(...)
    fn = find_function(tree, entry_function)
    start_stmt = _find_session_start(fn, session_var)
    indent = indent_of(lines, start_stmt.lineno)
    if mode == Mode.TEST:
        if not agent_arg:
            raise AdapterError(
                "track_session requires the agent instance, but session.start() has no agent= argument"
            )
        call = f"await {TRACER_VAR}.track_session({ctx_param}, {session_var}, {agent_arg})"
    else:
        call = f"await {TRACER_VAR}.observe_session({ctx_param}, {session_var})"
    edits.append(LineEdit(
        start=start_stmt.lineno, end=start_stmt.lineno - 1,
        lines=[f"{indent}# Cekura: must be called before session.start()", f"{indent}{call}", ""],
    ))

    return apply_line_edits(source, edits)
