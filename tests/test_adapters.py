"""Slice C gates: golden diffs, lifecycle invariants, hostile mutations."""

from pathlib import Path

import pytest

from cekura_agent.models import Mode
from cekura_agent.orchestrator import make_plan
from cekura_agent.patching import render
from cekura_agent.verification import check_livekit_file, check_pipecat_file, collect_checks

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN = Path(__file__).parent / "golden"

CASES = [
    ("livekit_basic", Mode.TEST, 77, "livekit_basic_test"),
    ("livekit_basic", Mode.OBSERVE, None, "livekit_basic_observe"),
    ("pipecat_single", Mode.TEST, 88, "pipecat_single_test"),
    ("pipecat_custom", Mode.TEST, 99, "pipecat_custom_test"),
    ("pipecat_single", Mode.OBSERVE, 88, "pipecat_single_observe"),
]


def _render_case(fixture: str, mode: Mode, agent_id):
    plan, ctx = make_plan(FIXTURES / fixture, mode, agent_id=agent_id)
    return render(plan, ctx.inspection)


@pytest.mark.parametrize("fixture,mode,agent_id,golden_name", CASES)
def test_golden_transformations(fixture, mode, agent_id, golden_name):
    _patchset, contents = _render_case(fixture, mode, agent_id)
    golden_dir = GOLDEN / golden_name
    expected_files = {p.name.replace("__", "/"): p for p in golden_dir.iterdir()}
    assert set(contents) == set(expected_files), "touched file set drifted from golden"
    for rel, content in contents.items():
        assert content == expected_files[rel].read_text(), f"golden drift in {golden_name}/{rel}"


@pytest.mark.parametrize("fixture,mode,agent_id,golden_name", CASES)
def test_patched_output_passes_lifecycle(fixture, mode, agent_id, golden_name, tmp_path):
    _patchset, contents = _render_case(fixture, mode, agent_id)
    for rel, content in contents.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    checks = collect_checks(tmp_path, list(contents), expected_mode=mode)
    failures = [c for c in checks if not c.passed and c.severity == "error"]
    assert not failures, failures


# ------------------------------------------------------------------ hostile mutations


def _golden_text(case: str, filename: str) -> str:
    return (GOLDEN / case / filename).read_text()


def _check_file(tmp_path, content: str, checker, expected_mode):
    target = tmp_path / "mutant.py"
    target.write_text(content)
    return checker(target, expected_mode)


def _failed(checks, name_part: str):
    return [c for c in checks if name_part in c.name and not c.passed]


def test_mutation_tracer_after_session_start(tmp_path):
    good = _golden_text("livekit_basic_test", "agent.py")
    swapped = good.replace(
        "await cekura_tracer.track_session(ctx, session, assistant)\n\n"
        "    await session.start(room=ctx.room, agent=assistant)",
        "await session.start(room=ctx.room, agent=assistant)\n\n"
        "    await cekura_tracer.track_session(ctx, session, assistant)",
    )
    assert swapped != good
    checks = _check_file(tmp_path, swapped, check_livekit_file, Mode.TEST)
    assert _failed(checks, "tracer_before_start")


def test_mutation_track_session_missing_agent(tmp_path):
    good = _golden_text("livekit_basic_test", "agent.py")
    mutant = good.replace("track_session(ctx, session, assistant)", "track_session(ctx, session)")
    checks = _check_file(tmp_path, mutant, check_livekit_file, Mode.TEST)
    assert _failed(checks, "track_session_has_agent")


def test_mutation_livekit_tracer_inside_function(tmp_path):
    content = (
        "import os\n"
        "from cekura.livekit import LiveKitTracer\n\n"
        "async def entrypoint(ctx):\n"
        "    cekura_tracer = LiveKitTracer(api_key=os.getenv('CEKURA_API_KEY'), agent_id=1)\n"
        "    await cekura_tracer.track_session(ctx, session, agent)\n"
        "    await session.start(room=ctx.room, agent=agent)\n"
    )
    checks = _check_file(tmp_path, content, check_livekit_file, Mode.TEST)
    assert _failed(checks, "module_scope_init")


def test_mutation_hardcoded_api_key(tmp_path):
    good = _golden_text("livekit_basic_test", "agent.py")
    mutant = good.replace('api_key=os.getenv("CEKURA_API_KEY")', 'api_key="sk-hardcoded-key-123456"')
    checks = _check_file(tmp_path, mutant, check_livekit_file, Mode.TEST)
    assert _failed(checks, "api_key_from_env")


def test_mutation_wrong_mode(tmp_path):
    good = _golden_text("livekit_basic_test", "agent.py")
    checks = _check_file(tmp_path, good, check_livekit_file, Mode.OBSERVE)
    assert _failed(checks, "mode_consistent")


def test_mutation_duplicate_tracer_calls(tmp_path):
    good = _golden_text("livekit_basic_test", "agent.py")
    mutant = good.replace(
        "await cekura_tracer.track_session(ctx, session, assistant)",
        "await cekura_tracer.track_session(ctx, session, assistant)\n"
        "    await cekura_tracer.track_session(ctx, session, assistant)",
    )
    checks = _check_file(tmp_path, mutant, check_livekit_file, Mode.TEST)
    assert _failed(checks, "tracer_exactly_once")


def test_mutation_pipecat_shared_tracer(tmp_path):
    content = (
        "import os\n"
        "from cekura.pipecat import PipecatTracer\n\n"
        "cekura_tracer = PipecatTracer(api_key=os.getenv('CEKURA_API_KEY'), agent_id=1)\n\n"
        "async def run_bot(transport, runner_args):\n"
        "    task = cekura_tracer.track_and_create_task(pipeline, context)\n"
    )
    checks = _check_file(tmp_path, content, check_pipecat_file, Mode.TEST)
    assert _failed(checks, "per_call_tracer")


def test_mutation_pipecat_missing_tracing_flags(tmp_path):
    good = _golden_text("pipecat_custom_test", "bot.py")
    mutant = good.replace("        enable_tracing=True,\n", "")
    checks = _check_file(tmp_path, mutant, check_pipecat_file, Mode.TEST)
    assert _failed(checks, "multi_step_tracing_flags")


def test_mutation_pipecat_register_before_task(tmp_path):
    content = (
        "import os\n"
        "from cekura.pipecat import PipecatTracer\n\n"
        "async def run_bot(transport, runner_args):\n"
        "    cekura_tracer = PipecatTracer(api_key=os.getenv('CEKURA_API_KEY'), agent_id=1)\n"
        "    pipeline = cekura_tracer.track_pipeline(pipeline, context)\n"
        "    task = cekura_tracer.register_task_handlers(task, transport=transport)\n"
        "    task = PipelineTask(pipeline, enable_tracing=True, enable_turn_tracking=True)\n"
    )
    checks = _check_file(tmp_path, content, check_pipecat_file, Mode.TEST)
    assert _failed(checks, "register_task_handlers")


def test_mutation_pipecat_direct_observe_duplication(tmp_path):
    good = _golden_text("pipecat_single_observe", "bot.py")
    mutant = good + '\nOBSERVE_URL = "https://api.cekura.ai/observability/v1/observe/"\n'
    checks = _check_file(tmp_path, mutant, check_pipecat_file, Mode.OBSERVE)
    assert _failed(checks, "no_direct_observe_duplication")


def test_mutation_aggregator_removed_detected_by_scanner(copy_fixture):
    from cekura_agent.scanner import inspect_repo

    repo = copy_fixture("pipecat_single")
    bot = repo / "bot.py"
    content = bot.read_text().replace("            assistant_aggregator,\n", "")
    bot.write_text(content)
    result = inspect_repo(repo)
    assert result.matrix.decision_reason == "MISSING_AGGREGATOR_PAIR"
