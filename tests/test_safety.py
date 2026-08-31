"""Slice A gates: traversal fails, secrets are redacted and detected."""

from pathlib import Path

import pytest

from cekura_agent.errors import SafetyViolation
from cekura_agent.safety import (
    ensure_within_root,
    iter_repo_files,
    redact,
    scan_paths_for_secrets,
    scan_text_for_secrets,
)

FAKE_KEY = "sk-or-v1-" + "ab12" * 8


def test_traversal_rejected(tmp_path: Path):
    with pytest.raises(SafetyViolation):
        ensure_within_root(tmp_path, Path("../outside.txt"))
    with pytest.raises(SafetyViolation):
        ensure_within_root(tmp_path, Path("/etc/passwd"))
    inside = ensure_within_root(tmp_path, Path("sub/file.py"))
    assert str(inside).startswith(str(tmp_path.resolve()))


def test_redaction():
    assert FAKE_KEY not in redact(f"calling with {FAKE_KEY} now")
    assert "[REDACTED]" in redact(f"key={FAKE_KEY}")
    assert "X-CEKURA-API-KEY" in redact("X-CEKURA-API-KEY: abcd1234efgh")
    assert "abcd1234efgh" not in redact("X-CEKURA-API-KEY: abcd1234efgh")


def test_secret_scan_finds_planted_key(tmp_path: Path):
    clean = tmp_path / "clean.py"
    clean.write_text("api_key = os.getenv('CEKURA_API_KEY')\n")
    dirty = tmp_path / "dirty.py"
    dirty.write_text(f'OPENROUTER_API_KEY = "{FAKE_KEY}"\n')
    findings = scan_paths_for_secrets([clean, dirty])
    assert any(f["origin"] == str(dirty) for f in findings)
    assert not any(f["origin"] == str(clean) for f in findings)
    assert scan_text_for_secrets("nothing here") == []


def test_iter_repo_files_skips_dirs(tmp_path: Path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("x")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "junk.js").write_text("x")
    (tmp_path / "app.py").write_text("print('hi')\n")
    files = iter_repo_files(tmp_path)
    assert [f.name for f in files] == ["app.py"]
