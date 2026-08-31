"""Shared AST-anchored line-surgery helpers for the framework adapters.

Edits are computed against the *current* file content (re-anchored via AST, never
trusting stale line numbers), expressed as line-range replacements, and applied
bottom-up so earlier edits cannot shift later anchors.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ..errors import AgentError


class AdapterError(AgentError):
    """The adapter could not safely anchor its transformation."""


@dataclass
class LineEdit:
    """Replace lines [start, end] (1-based, inclusive) with `lines`.

    Pure insertion *before* line N is expressed as start=N, end=N-1.
    """

    start: int
    end: int
    lines: list[str] = field(default_factory=list)

    @property
    def is_insert(self) -> bool:
        return self.end < self.start


def apply_line_edits(source: str, edits: list[LineEdit]) -> str:
    lines = source.splitlines()
    trailing_newline = source.endswith("\n")
    ordered = sorted(edits, key=lambda e: (e.start, e.end), reverse=True)
    seen_ranges: list[tuple[int, int]] = []
    for edit in ordered:
        for start, end in seen_ranges:
            if not edit.is_insert and not (edit.end < start or edit.start > end):
                raise AdapterError(f"overlapping edits at lines {edit.start}-{edit.end}")
        seen_ranges.append((edit.start, edit.end))
        idx = edit.start - 1
        if edit.is_insert:
            lines[idx:idx] = edit.lines
        else:
            lines[idx: edit.end] = edit.lines
    out = "\n".join(lines)
    return out + "\n" if trailing_newline or out else out


def parse_module(source: str, filename: str) -> ast.Module:
    try:
        return ast.parse(source)
    except SyntaxError as exc:
        raise AdapterError(f"cannot parse {filename}: {exc}") from exc


def indent_of(source_lines: list[str], lineno: int) -> str:
    line = source_lines[lineno - 1]
    return line[: len(line) - len(line.lstrip())]


def import_insertion_line(tree: ast.Module) -> int:
    """Line *before* which new imports go: after the last top-level import,
    or after the module docstring, or line 1."""
    last_import_end = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import_end = max(last_import_end, node.end_lineno or node.lineno)
    if last_import_end:
        return last_import_end + 1
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)
            and isinstance(tree.body[0].value.value, str)):
        return (tree.body[0].end_lineno or tree.body[0].lineno) + 1
    return 1


def has_plain_import(tree: ast.Module, module: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(a.name == module or a.name.startswith(module + ".")
                                                for a in node.names):
            return True
    return False


def has_from_import(tree: ast.Module, module: str, name: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module:
            if any(a.name == name for a in node.names):
                return True
    return False


def find_function(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AdapterError(f"function {name!r} not found (file changed since inspection?)")


def statement_of(node: ast.AST, fn: ast.AST) -> ast.stmt:
    """Smallest statement in `fn` that contains `node`."""
    target: ast.stmt | None = None
    for stmt in ast.walk(fn):
        if isinstance(stmt, ast.stmt) and stmt.lineno <= node.lineno <= (stmt.end_lineno or stmt.lineno):
            if target is None or stmt.lineno >= target.lineno:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    target = stmt
    if target is None:
        raise AdapterError("could not resolve containing statement")
    return target


def agent_id_expr(agent_id: int | None) -> str:
    if agent_id is not None:
        return str(int(agent_id))
    return 'int(os.getenv("CEKURA_AGENT_ID", "0"))'
