"""Verification: syntax + lifecycle invariants for changed files."""

from __future__ import annotations

from pathlib import Path

from ..models import CheckResult, Mode
from .lifecycle import check_livekit_file, check_pipecat_file, check_syntax

__all__ = ["check_livekit_file", "check_pipecat_file", "check_syntax", "collect_checks"]


def collect_checks(root: Path, files: list[str], expected_mode: Mode | None = None) -> list[CheckResult]:
    py_paths = [root / f for f in files if f.endswith(".py")]
    results = check_syntax(py_paths)
    for path in py_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "LiveKitTracer" in text:
            results.extend(check_livekit_file(path, expected_mode))
        if "PipecatTracer" in text:
            results.extend(check_pipecat_file(path, expected_mode))
    return results
