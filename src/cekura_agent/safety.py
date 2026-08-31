"""Safety kernel: repo boundary, hashing, secret redaction and secret scanning.

Every filesystem write the agent performs goes through `ensure_within_root`.
Every log line goes through `redact`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .errors import SafetyViolation

# Directories the scanner and patcher never descend into.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build", ".cekura-agent",
    ".idea", ".vscode", ".tox", ".eggs",
}

# Patterns that indicate credentials: (name, pattern, replacement template).
# Replacement keeps identifying prefixes (variable / header names) but drops the value.
_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("openrouter_key", re.compile(r"sk-or-v1-[a-f0-9]{16,}"), "[REDACTED]"),
    ("openai_style_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED]"),
    ("bearer_token", re.compile(r"(?i)(bearer[ \t]+)[a-z0-9._~+/=-]{16,}"), r"\1[REDACTED]"),
    (
        "api_key_assignment",
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API_KEY|API_SECRET|ACCESS_TOKEN|SECRET_KEY|AUTH_TOKEN)\b[ \t]*[:=][ \t]*['\"]?)"
            # value must contain a digit: excludes identifier chains like settings.cekura_api_key
            r"(?!os\.)(?!process\.)(?=[A-Za-z0-9._~+/-]*\d)[A-Za-z0-9._~+/-]{12,}"
        ),
        r"\1[REDACTED]",
    ),
    ("cekura_header",
     re.compile(r"(?i)(X-CEKURA-API-KEY[ \t]*[:=][ \t]*)[A-Za-z0-9._-]{8,}"), r"\1[REDACTED]"),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[REDACTED-PRIVATE-KEY]"),
]

REDACTED = "[REDACTED]"


def ensure_within_root(root: Path, candidate: Path) -> Path:
    """Resolve `candidate` and require it to live inside `root`. Raises SafetyViolation."""
    root_resolved = root.resolve()
    resolved = (root_resolved / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise SafetyViolation(f"path escapes repository root: {candidate}")
    return resolved


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def redact(text: str) -> str:
    """Replace anything credential-shaped with [REDACTED]. Applied to all logs/reports."""
    out = text
    for _name, pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def scan_text_for_secrets(text: str, origin: str = "<text>") -> list[dict[str, str]]:
    findings = []
    for name, pattern, _replacement in _SECRET_PATTERNS:
        for match in pattern.finditer(text):
            line_no = text.count("\n", 0, match.start()) + 1
            findings.append({"rule": name, "origin": origin, "line": str(line_no)})
    return findings


def scan_paths_for_secrets(paths: list[Path]) -> list[dict[str, str]]:
    """Secret scan used by the packaging gate. Binary files are skipped."""
    findings: list[dict[str, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text_for_secrets(text, origin=str(path)))
    return findings


def iter_repo_files(root: Path, *, max_file_bytes: int = 2_000_000) -> list[Path]:
    """Deterministically ordered repo file listing honouring SKIP_DIRS.

    Symlinks are skipped to prevent repository-supplied links from causing
    reads outside the approved root (e.g. a symlink to /etc/passwd or .env).
    """
    root_resolved = root.resolve()
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            resolved = path.resolve()
        except (OSError, ValueError):
            continue
        if resolved != root_resolved and root_resolved not in resolved.parents:
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
        except OSError:
            continue
        files.append(path)
    return files
