"""Slice C gates: compare-before-write, all-or-nothing apply, idempotence, exact rollback."""

from pathlib import Path

import pytest

from cekura_agent.errors import AgentError
from cekura_agent.models import ActionType, Mode
from cekura_agent.orchestrator import make_plan
from cekura_agent.patching import apply_patchset, render, rollback_run
from cekura_agent.safety import sha256_file


def _hashes(repo: Path) -> dict[str, str]:
    return {
        str(p.relative_to(repo)): sha256_file(p)
        for p in repo.rglob("*")
        if p.is_file() and ".cekura-agent" not in p.parts
    }


def _integrate(repo: Path, mode=Mode.TEST, agent_id=77):
    plan, ctx = make_plan(repo, mode, agent_id=agent_id)
    patchset, contents = render(plan, ctx.inspection)
    manifest = apply_patchset(repo, patchset, contents)
    return plan, patchset, manifest


def test_apply_then_second_apply_is_noop(copy_fixture):
    repo = copy_fixture("livekit_basic")
    original = _hashes(repo)

    plan, patchset, manifest = _integrate(repo)
    assert len(patchset.edits) == 3
    patched = _hashes(repo)
    assert patched != original
    assert "cekura[livekit]>=1.6.5" in (repo / "requirements.txt").read_text()

    # second run: scanner sees the integration -> noop plan -> empty patchset
    plan2, ctx2 = make_plan(repo, Mode.TEST, agent_id=77)
    assert [a.action_type for a in plan2.actions] == [ActionType.ALREADY_INTEGRATED_NOOP]
    patchset2, _ = render(plan2, ctx2.inspection)
    assert patchset2.is_noop
    assert _hashes(repo) == patched


def test_rollback_restores_exact_hashes(copy_fixture):
    repo = copy_fixture("livekit_basic")
    original = _hashes(repo)
    _integrate(repo)
    restored = rollback_run(repo)
    assert set(restored) == {".env.example", "agent.py", "requirements.txt"}
    assert _hashes(repo) == original


def test_rollback_deletes_created_files(copy_fixture):
    repo = copy_fixture("pipecat_custom")  # fixture has no requirements.txt / .env.example
    original = _hashes(repo)
    _plan, _patchset, manifest = _integrate(repo, agent_id=99)
    created = {e.file for e in manifest.entries if e.created}
    assert created == {"requirements.txt", ".env.example"}
    rollback_run(repo)
    assert _hashes(repo) == original
    assert not (repo / "requirements.txt").exists()


def test_rollback_refuses_after_manual_edit_unless_forced(copy_fixture):
    repo = copy_fixture("livekit_basic")
    original = _hashes(repo)
    _integrate(repo)
    (repo / "agent.py").write_text((repo / "agent.py").read_text() + "\n# manual edit\n")
    with pytest.raises(AgentError, match="--force"):
        rollback_run(repo)
    rollback_run(repo, force=True)
    assert _hashes(repo) == original


def test_apply_refuses_on_before_hash_mismatch(copy_fixture):
    repo = copy_fixture("livekit_basic")
    plan, ctx = make_plan(repo, Mode.TEST, agent_id=77)
    patchset, contents = render(plan, ctx.inspection)
    (repo / "agent.py").write_text((repo / "agent.py").read_text() + "\n# drift\n")
    drifted = _hashes(repo)
    with pytest.raises(AgentError, match="changed since"):
        apply_patchset(repo, patchset, contents)
    assert _hashes(repo) == drifted  # nothing was written


def test_mid_apply_failure_restores_everything(copy_fixture, monkeypatch):
    repo = copy_fixture("livekit_basic")
    original = _hashes(repo)
    plan, ctx = make_plan(repo, Mode.TEST, agent_id=77)
    patchset, contents = render(plan, ctx.inspection)

    real_write = Path.write_text

    def failing_write(self, *args, **kwargs):
        if self.name == "requirements.txt":  # last file in the sorted patchset
            raise OSError("disk full")
        return real_write(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", failing_write)
    with pytest.raises(OSError, match="disk full"):
        apply_patchset(repo, patchset, contents)
    monkeypatch.undo()
    assert _hashes(repo) == original  # partial writes rolled back
