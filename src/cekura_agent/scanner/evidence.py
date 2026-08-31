"""Evidence extraction: AST-based detection of everything the plan may cite.

Every claim the planner later makes must reference an evidence id produced here.
Candidates that look relevant but must not drive integration (README placeholders,
test/example entrypoints, doc files) are still recorded — with `rejected=True` and
a reason — so reviewers can see what was considered and dismissed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from ..models import Evidence, EvidenceKind, RepoSnapshot

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
KB_SUFFIXES = {".md", ".txt", ".pdf"}
KB_DIR_HINTS = {"docs", "doc", "kb", "knowledge", "knowledge_base", "data", "documents", "faq"}
KB_EXCLUDE_NAMES = {"readme.md", "changelog.md", "contributing.md", "license.md", "code_of_conduct.md"}
TEST_PATH_HINTS = ("tests/", "test/", "examples/", "example/", "scripts/", "docs/")
RAG_IMPORT_HINTS = {"llama_index", "langchain", "chromadb", "pinecone", "faiss", "qdrant_client", "weaviate"}

LIVEKIT_TOOL_DECORATORS = {"function_tool", "ai_callable"}


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def next(self) -> str:
        self.n += 1
        return f"ev-{self.n:04d}"


def _unparse(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return "<unparseable>"


def _name_of(node: ast.expr) -> str:
    """Trailing identifier of a Name/Attribute chain: `agents.AgentSession` -> AgentSession."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _dotted(node: ast.expr) -> str:
    parts: list[str] = []
    cur: ast.expr | None = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts))


def _is_test_path(rel_path: str) -> bool:
    lowered = rel_path.lower()
    return any(lowered.startswith(h) or f"/{h}" in lowered for h in TEST_PATH_HINTS)


def _snippet(lines: list[str], lineno: int, end: int | None = None) -> str:
    end = end or lineno
    return "\n".join(lines[max(0, lineno - 1): min(len(lines), end)])[:400]


def extract_evidence(root: Path, snapshot: RepoSnapshot) -> list[Evidence]:
    counter = _Counter()
    evidence: list[Evidence] = []

    py_files = [f.path for f in snapshot.files if f.path.endswith(".py")]
    doc_files = [f for f in snapshot.files if Path(f.path).suffix.lower() in KB_SUFFIXES]

    for rel in py_files:
        path = root / rel
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            evidence.append(
                Evidence(
                    id=counter.next(), kind=EvidenceKind.OTHER, file=rel, line_start=1, line_end=1,
                    detail={"error": str(exc)}, rejected=True, reject_reason="unparseable python file",
                )
            )
            continue
        evidence.extend(_scan_python_file(rel, source, tree, counter))

    evidence.extend(_scan_docs(root, doc_files, counter))
    evidence.extend(_scan_dependencies(root, snapshot, counter))
    _mark_kb_files_referenced(evidence)
    return evidence


# ------------------------------------------------------------------ python file scan


def _scan_python_file(rel: str, source: str, tree: ast.Module, counter: _Counter) -> list[Evidence]:
    out: list[Evidence] = []
    lines = source.splitlines()
    in_test_path = _is_test_path(rel)

    def add(kind: EvidenceKind, node: ast.AST, *, symbol: str | None = None,
            detail: dict | None = None, rejected: bool = False, reject_reason: str | None = None) -> Evidence:
        line = getattr(node, "lineno", 1)
        end = getattr(node, "end_lineno", line)
        if in_test_path and not rejected and kind in {
            EvidenceKind.ENTRYPOINT, EvidenceKind.SESSION_START, EvidenceKind.PIPELINE_TASK,
        }:
            rejected, reject_reason = True, f"path looks like tests/examples: {rel}"
        ev = Evidence(
            id=counter.next(), kind=kind, file=rel, line_start=line, line_end=end,
            symbol=symbol, snippet=_snippet(lines, line, end), detail=detail or {},
            rejected=rejected, reject_reason=reject_reason,
        )
        out.append(ev)
        return ev

    # ---- imports
    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported_roots.add(alias.name.split(".")[0])
                _classify_import(alias.name, node, add)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".")[0])
            _classify_import(node.module, node, add, names=[a.name for a in node.names])
    for hint in RAG_IMPORT_HINTS & imported_roots:
        add(EvidenceKind.KB_SOURCE, tree, symbol=hint, detail={"style": "rag_import", "package": hint})

    # ---- entrypoint config references (WorkerOptions(entrypoint_fnc=...), rtc_session)
    worker_entry_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name_of(node.func) == "WorkerOptions":
            for kw in node.keywords:
                if kw.arg == "entrypoint_fnc" and isinstance(kw.value, ast.Name):
                    worker_entry_names.add(kw.value.id)
                if kw.arg == "agent_name" and isinstance(kw.value, ast.Constant):
                    add(EvidenceKind.OTHER, node, symbol="agent_name",
                        detail={"agent_name": kw.value.value, "style": "worker_options"})

    # ---- functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.extend(_scan_function(rel, node, lines, add, worker_entry_names))

    # ---- module-level string constants: placeholders
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "{{" in node.value:
            for var in dict.fromkeys(PLACEHOLDER_RE.findall(node.value)):
                add(EvidenceKind.PROMPT_PLACEHOLDER, node, symbol=var,
                    detail={"variable": var, "context": node.value[:200]})

    # ---- FunctionSchema(...) tool definitions (module or function scope)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name_of(node.func) == "FunctionSchema":
            detail: dict = {"style": "pipecat_function_schema"}
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    detail["name"] = kw.value.value
                elif kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    detail["description"] = kw.value.value
                elif kw.arg == "properties":
                    try:
                        detail["parameters"] = ast.literal_eval(kw.value)
                    except (ValueError, SyntaxError):
                        detail["parameters"] = {}
                elif kw.arg == "required":
                    try:
                        detail["required"] = ast.literal_eval(kw.value)
                    except (ValueError, SyntaxError):
                        pass
            if detail.get("name"):
                add(EvidenceKind.TOOL_DEF, node, symbol=detail["name"], detail=detail)

    # ---- direct observe API usage + existing cekura markers anywhere
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "/observability/v1/observe" in node.value:
            add(EvidenceKind.DIRECT_OBSERVE, node, detail={"url": node.value})
    if "opentelemetry" in imported_roots:
        add(EvidenceKind.OTEL, tree, detail={"style": "import"})

    return out


def _classify_import(module: str, node: ast.AST, add, names: list[str] | None = None) -> None:
    root_pkg = module.split(".")[0]
    if root_pkg == "livekit":
        add(EvidenceKind.FRAMEWORK_IMPORT, node, symbol=module,
            detail={"framework": "livekit", "names": names or []})
    elif root_pkg == "pipecat":
        add(EvidenceKind.FRAMEWORK_IMPORT, node, symbol=module,
            detail={"framework": "pipecat", "names": names or []})
    elif root_pkg == "cekura":
        add(EvidenceKind.EXISTING_CEKURA, node, symbol=module,
            detail={"style": "import", "names": names or []})


def _scan_function(rel: str, fn: ast.FunctionDef | ast.AsyncFunctionDef, lines: list[str], add,
                   worker_entry_names: set[str]) -> list[Evidence]:
    out: list[Evidence] = []
    params = [a.arg for a in fn.args.args + fn.args.posonlyargs + fn.args.kwonlyargs]
    annotations = {a.arg: _name_of(a.annotation) for a in fn.args.args if a.annotation is not None}

    is_livekit_entry = (
        "JobContext" in annotations.values()
        or fn.name in worker_entry_names
        or any("rtc_session" in _dotted(d.func if isinstance(d, ast.Call) else d) for d in fn.decorator_list)
    )
    ctx_param = next((a for a, ann in annotations.items() if ann == "JobContext"), None)
    if ctx_param is None and params:
        ctx_param = params[0]

    # tool decorators (LiveKit style)
    for dec in fn.decorator_list:
        dec_name = _name_of(dec.func) if isinstance(dec, ast.Call) else _name_of(dec)
        if dec_name in LIVEKIT_TOOL_DECORATORS:
            doc = ast.get_docstring(fn) or ""
            tool_params = {
                a.arg: (_name_of(a.annotation) or "any")
                for a in fn.args.args
                if a.arg not in {"self", "ctx", "context"} and (a.annotation is None or _name_of(a.annotation) != "RunContext")
            }
            defaults_count = len(fn.args.defaults)
            required = [a.arg for a in fn.args.args[: len(fn.args.args) - defaults_count]
                        if a.arg in tool_params]
            add(EvidenceKind.TOOL_DEF, fn, symbol=fn.name, detail={
                "name": fn.name, "style": "livekit_function_tool",
                "description": doc.split("\n\n")[0].strip(),
                "parameters": tool_params, "required": required,
            })

    body_evidence: dict[str, Evidence] = {}
    pipeline_vars: dict[str, list[str]] = {}
    metadata_vars: set[str] = set()
    aggregator_seen = False

    for node in ast.walk(fn):
        # session = AgentSession(...)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            callee = _name_of(node.value.func)
            target = node.targets[0]
            target_name = target.id if isinstance(target, ast.Name) else None
            if callee == "AgentSession" and target_name:
                body_evidence["agent_session"] = add(
                    EvidenceKind.AGENT_SESSION, node, symbol=target_name,
                    detail={"session_var": target_name, "function": fn.name})
            if callee == "Pipeline" and target_name and node.value.args:
                first = node.value.args[0]
                elements = [_unparse(e) for e in first.elts] if isinstance(first, ast.List) else []
                pipeline_vars[target_name] = elements
                body_evidence["pipeline"] = add(
                    EvidenceKind.PIPELINE, node, symbol=target_name,
                    detail={"pipeline_var": target_name, "elements": elements, "function": fn.name})
            if callee == "PipelineTask" and target_name:
                pipeline_arg = _unparse(node.value.args[0]) if node.value.args else None
                kwargs = [kw.arg for kw in node.value.keywords if kw.arg]
                body_evidence["pipeline_task"] = add(
                    EvidenceKind.PIPELINE_TASK, node, symbol=target_name,
                    detail={
                        "task_var": target_name, "pipeline_arg": pipeline_arg,
                        "extra_kwargs": kwargs, "function": fn.name,
                        "function_params": params,
                        "has_custom_kwargs": bool(kwargs),
                        "is_async_function": isinstance(fn, ast.AsyncFunctionDef),
                    })
            if callee == "LLMContextAggregatorPair" or callee == "create_context_aggregator":
                aggregator_seen = True
                add(EvidenceKind.AGGREGATOR, node, symbol=callee,
                    detail={"style": callee, "function": fn.name,
                            "targets": [_unparse(t) for t in node.targets]})
            if callee in {"LLMContext", "OpenAILLMContext"} and target_name:
                body_evidence.setdefault("context", add(
                    EvidenceKind.OTHER, node, symbol=target_name,
                    detail={"context_var": target_name, "style": callee, "function": fn.name}))
            if callee == "PipecatTracer" or callee == "LiveKitTracer":
                add(EvidenceKind.EXISTING_CEKURA, node, symbol=callee,
                    detail={"style": "tracer_init", "function": fn.name})
            # dial_info = json.loads(ctx.job.metadata ...)
            if _dotted(node.value.func).endswith("json.loads") and target_name:
                arg_repr = _unparse(node.value.args[0]) if node.value.args else ""
                if ".job.metadata" in arg_repr or ".metadata" in arg_repr:
                    metadata_vars.add(target_name)
                    add(EvidenceKind.RUNTIME_INPUT, node, symbol=target_name,
                        detail={"source": "job_metadata", "var": target_name, "expr": arg_repr,
                                "function": fn.name})

        # await session.start(...)
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "start":
                receiver = _unparse(call.func.value)
                agent_arg = None
                for kw in call.keywords:
                    if kw.arg == "agent":
                        agent_arg = _unparse(kw.value)
                kwargs = [kw.arg for kw in call.keywords if kw.arg]
                body_evidence["session_start"] = add(
                    EvidenceKind.SESSION_START, node, symbol=receiver,
                    detail={"session_var": receiver, "agent_arg": agent_arg,
                            "kwargs": kwargs, "function": fn.name, "ctx_param": ctx_param})
            attr_chain = _dotted(call.func)
            if attr_chain.endswith(("track_session", "observe_session",
                                    "track_and_create_task", "observe_and_create_task",
                                    "track_pipeline", "observe_pipeline")):
                add(EvidenceKind.EXISTING_CEKURA, node, symbol=attr_chain,
                    detail={"style": "tracer_call", "function": fn.name})

        # llm.register_function("name", handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "register_function":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                add(EvidenceKind.TOOL_DEF, node, symbol=node.args[0].value, detail={
                    "name": node.args[0].value, "style": "pipecat_register_function",
                    "handler": _unparse(node.args[1]) if len(node.args) > 1 else None,
                })

        # runtime metadata reads: X.get("k") / X["k"] on json.loads(ctx.job.metadata) vars or session_data
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get":
            base = node.func.value
            base_name = base.id if isinstance(base, ast.Name) else None
            if base_name and (base_name in metadata_vars or base_name in {"session_data", "session_args", "body"}):
                if node.args and isinstance(node.args[0], ast.Constant):
                    add(EvidenceKind.RUNTIME_INPUT, node, symbol=str(node.args[0].value), detail={
                        "source": "job_metadata" if base_name in metadata_vars else "session_data",
                        "key": node.args[0].value, "var": base_name, "function": fn.name,
                    })

        # open("docs/x.md") / Path("kb/y.md").read_text()
        if isinstance(node, ast.Call):
            fname = _name_of(node.func)
            if fname in {"open", "Path"} and node.args and isinstance(node.args[0], ast.Constant) \
                    and isinstance(node.args[0].value, str):
                target_path = node.args[0].value
                if Path(target_path).suffix.lower() in KB_SUFFIXES:
                    add(EvidenceKind.KB_SOURCE, node, symbol=target_path,
                        detail={"style": "code_file_read", "path": target_path, "function": fn.name})

    # entrypoint evidence for LiveKit-shaped functions
    if is_livekit_entry:
        add(EvidenceKind.ENTRYPOINT, fn, symbol=fn.name, detail={
            "framework": "livekit", "function": fn.name, "params": params,
            "ctx_param": ctx_param, "is_async": isinstance(fn, ast.AsyncFunctionDef),
            "has_session_start": "session_start" in body_evidence,
            "session_var": body_evidence.get("session_start").detail.get("session_var") if body_evidence.get("session_start") else None,
        })
    if "pipeline_task" in body_evidence:
        task_detail = body_evidence["pipeline_task"].detail
        pipeline_elements = pipeline_vars.get(task_detail.get("pipeline_arg") or "", [])
        add(EvidenceKind.ENTRYPOINT, fn, symbol=fn.name, detail={
            "framework": "pipecat", "function": fn.name, "params": params,
            "is_async": isinstance(fn, ast.AsyncFunctionDef),
            "pipeline_elements": pipeline_elements,
            "has_aggregator_pair": aggregator_seen,
            "context_var": (body_evidence.get("context").detail.get("context_var")
                            if body_evidence.get("context") else None),
        })

    # FunctionSchema(...) at any level handled at module scan; also inside functions:
    return out


# ------------------------------------------------------------------ docs / deps


def _scan_docs(root: Path, doc_files, counter: _Counter) -> list[Evidence]:
    out: list[Evidence] = []
    for record in doc_files:
        rel = record.path
        name = Path(rel).name.lower()
        parts = {p.lower() for p in Path(rel).parts[:-1]}
        is_kb_location = bool(parts & KB_DIR_HINTS) and name not in KB_EXCLUDE_NAMES
        if is_kb_location:
            out.append(Evidence(
                id=counter.next(), kind=EvidenceKind.KB_SOURCE, file=rel, line_start=1, line_end=1,
                symbol=rel, detail={"style": "doc_file", "path": rel, "media_type": _media_type(rel)},
            ))
        if Path(rel).suffix.lower() in {".md", ".txt"}:
            try:
                text = (root / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for var in dict.fromkeys(PLACEHOLDER_RE.findall(text)):
                out.append(Evidence(
                    id=counter.next(), kind=EvidenceKind.PROMPT_PLACEHOLDER, file=rel,
                    line_start=1, line_end=1, symbol=var, detail={"variable": var},
                    rejected=True, reject_reason="placeholder in non-executable documentation file",
                ))
    return out


def _media_type(rel: str) -> str:
    return {".md": "text/markdown", ".txt": "text/plain", ".pdf": "application/pdf"}.get(
        Path(rel).suffix.lower(), "application/octet-stream")


def _scan_dependencies(root: Path, snapshot: RepoSnapshot, counter: _Counter) -> list[Evidence]:
    out: list[Evidence] = []
    interesting = ("livekit-agents", "livekit", "pipecat-ai", "pipecat", "cekura")
    for record in snapshot.files:
        name = Path(record.path).name.lower()
        if name.startswith("requirements") and name.endswith(".txt"):
            try:
                for i, line in enumerate((root / record.path).read_text().splitlines(), start=1):
                    stripped = line.strip()
                    pkg = re.split(r"[<>=~!\[; ]", stripped, 1)[0].lower()
                    if pkg in interesting:
                        out.append(Evidence(
                            id=counter.next(), kind=EvidenceKind.DEPENDENCY, file=record.path,
                            line_start=i, line_end=i, symbol=pkg,
                            detail={"package": pkg, "spec": stripped, "manifest": record.path}))
            except (OSError, UnicodeDecodeError):
                continue
        elif name == "pyproject.toml":
            try:
                import tomllib

                data = tomllib.loads((root / record.path).read_text())
                deps = data.get("project", {}).get("dependencies", []) or []
                for dep in deps:
                    pkg = re.split(r"[<>=~!\[; ]", dep.strip(), 1)[0].lower()
                    if pkg in interesting:
                        out.append(Evidence(
                            id=counter.next(), kind=EvidenceKind.DEPENDENCY, file=record.path,
                            line_start=1, line_end=1, symbol=pkg,
                            detail={"package": pkg, "spec": dep, "manifest": record.path}))
            except Exception:
                continue
    return out


def _mark_kb_files_referenced(evidence: list[Evidence]) -> None:
    """Cross-link: doc files read from code are higher-confidence KB sources."""
    read_paths = {
        ev.detail.get("path") for ev in evidence
        if ev.kind == EvidenceKind.KB_SOURCE and ev.detail.get("style") == "code_file_read"
    }
    for ev in evidence:
        if ev.kind == EvidenceKind.KB_SOURCE and ev.detail.get("style") == "doc_file":
            ev.detail["referenced_in_code"] = any(
                rp and (ev.file.endswith(rp) or rp.endswith(ev.file) or Path(rp).name == Path(ev.file).name)
                for rp in read_paths
            )
