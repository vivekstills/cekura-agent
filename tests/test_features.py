"""Slice D gates: exact tool schemas, synthetic-only mocks, routing boundary,
source->sink variables, approval-gated KB, monitoring duplication awareness."""

import json
from pathlib import Path

import pytest

from cekura_agent.errors import NeedsHuman
from cekura_agent.features import (
    CekuraMockToolRouter,
    LocalFakeMockToolRouter,
    approved_entries,
    build_dynamic_variable_specs,
    build_kb_manifest,
    build_mock_tool_specs,
    resolve_pipecat_router,
    summarize_monitoring,
    verify_mock_names,
)
from cekura_agent.models import Mode
from cekura_agent.orchestrator import make_plan
from cekura_agent.scanner import inspect_repo

FIXTURES = Path(__file__).parent / "fixtures"


# ------------------------------------------------------------------ mock tools


def test_livekit_tool_specs_match_source_exactly():
    inspection = inspect_repo(FIXTURES / "livekit_basic")
    specs = build_mock_tool_specs(inspection)
    assert [s.name for s in specs] == ["confirm_appointment", "lookup_availability"]
    lookup = next(s for s in specs if s.name == "lookup_availability")
    assert lookup.parameters_schema == {"date": {"type": "str"}}
    assert "appointment slots" in lookup.description
    confirm = next(s for s in specs if s.name == "confirm_appointment")
    assert set(confirm.parameters_schema) == {"date", "time", "notes"}
    assert confirm.freetext_params == ["notes"]
    assert verify_mock_names(specs, inspection) == []


def test_pipecat_schema_and_register_function_fold_into_one_spec():
    inspection = inspect_repo(FIXTURES / "pipecat_single")
    specs = build_mock_tool_specs(inspection)
    assert [s.name for s in specs] == ["order_lookup"]
    spec = specs[0]
    assert spec.parameters_schema == {"patient_id": {"type": "string"}}
    assert "invoice" in spec.description or "patient" in spec.description
    assert len(spec.evidence_ids) >= 2  # FunctionSchema + register_function


def test_mock_variants_are_clearly_synthetic_and_inputs_distinct():
    inspection = inspect_repo(FIXTURES / "livekit_basic")
    for spec in build_mock_tool_specs(inspection):
        assert {v.variant for v in spec.mock_data} == {"success", "empty", "error"}
        # platform contract: each input maps to exactly one output -> inputs must differ
        inputs = [json.dumps(v.input, sort_keys=True) for v in spec.mock_data]
        assert len(set(inputs)) == len(inputs), f"duplicate mock inputs in {spec.name}"
        for variant in spec.mock_data:
            assert variant.output.get("mock") is True
            for key, value in variant.input.items():
                if isinstance(value, str):
                    assert value.startswith(("MOCK", "+1555", "2026-", "1", "Mock", "mock", "0")), \
                        f"non-synthetic value {value!r} for {key}"
        error = next(v for v in spec.mock_data if v.variant == "error")
        assert "error" in error.output


def test_zero_param_tool_gets_single_variant(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "from livekit.agents import Agent, function_tool\n\n\n"
        "class A(Agent):\n"
        "    @function_tool()\n"
        "    async def end_call(self, ctx):\n"
        "        \"\"\"Hang up the call.\"\"\"\n"
        "        return 'bye'\n"
    )
    [spec] = build_mock_tool_specs(inspect_repo(repo))
    assert spec.name == "end_call"
    assert len(spec.mock_data) == 1 and spec.mock_data[0].input == {}


def test_local_fake_router_requires_exact_name():
    inspection = inspect_repo(FIXTURES / "pipecat_single")
    router = LocalFakeMockToolRouter(build_mock_tool_specs(inspection))
    assert router.invoke("order_lookup", {"patient_id": "X"})["mock"] is True
    with pytest.raises(KeyError, match="exact-name"):
        router.invoke("Order_Lookup", {})


def test_pipecat_router_activation_policy():
    # not requested -> None (no mocking, real tools untouched)
    assert resolve_pipecat_router([], env={}) is None
    # requested but unconfigured -> NEEDS_HUMAN, never a silent fallback
    with pytest.raises(NeedsHuman) as exc:
        resolve_pipecat_router([], env={"CEKURA_USE_MOCK_TOOLS": "1"})
    assert exc.value.reason_code == "PIPECAT_MOCK_ROUTING_UNCONFIGURED"
    # explicit contract -> documented Cekura endpoint router
    router = resolve_pipecat_router([], env={
        "CEKURA_USE_MOCK_TOOLS": "1",
        "CEKURA_MOCK_ENDPOINT_BASE": "https://api.cekura.ai",
    })
    assert isinstance(router, CekuraMockToolRouter)
    assert router.base_url == "https://api.cekura.ai"


# ------------------------------------------------------------------ dynamic variables


def test_dynamic_variables_sources_and_sinks():
    inspection = inspect_repo(FIXTURES / "livekit_basic")
    specs = build_dynamic_variable_specs(inspection)
    names = [s.name for s in specs]
    assert names == ["account_id", "appointment_date", "customer_name", "phone_number"]
    assert "example_var" not in names  # README placeholder was rejected evidence

    placeholder = next(s for s in specs if s.name == "customer_name")
    assert "get_simulation_data" in placeholder.sink
    assert "EMPTY object for phone" in placeholder.sink
    assert placeholder.example and placeholder.main_agent

    runtime = next(s for s in specs if s.name == "phone_number")
    assert "job_metadata" in runtime.source
    assert "ctx.job.metadata" in runtime.sink


def test_variable_collision_merges_sources(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "agent.py").write_text(
        "import json\n"
        "from livekit.agents import AgentSession, JobContext\n\n"
        'PROMPT = "Confirm the visit on {{date}}."\n\n\n'
        "async def entrypoint(ctx: JobContext):\n"
        "    info = json.loads(ctx.job.metadata or '{}')\n"
        "    date = info.get('date')\n"
        "    session = AgentSession()\n"
        "    await session.start(room=ctx.room, agent=None)\n"
    )
    specs = build_dynamic_variable_specs(inspect_repo(repo))
    assert [s.name for s in specs] == ["date"]
    assert "ALSO read structurally" in specs[0].description
    assert "prompt placeholder" in specs[0].source and "job_metadata" in specs[0].source


def test_pipecat_variable_sink_is_session_data():
    inspection = inspect_repo(FIXTURES / "pipecat_single")
    specs = build_dynamic_variable_specs(inspection)
    caller = next(s for s in specs if s.name == "caller_name")
    assert "session_data.get('caller_name')" in caller.sink
    assert "main_agent_variables" in caller.sink


# ------------------------------------------------------------------ knowledge base


def test_kb_manifest_is_discovery_not_upload():
    inspection = inspect_repo(FIXTURES / "livekit_basic")
    manifest = build_kb_manifest(inspection)
    assert [e.path for e in manifest] == ["docs/faq.md"]
    entry = manifest[0]
    assert entry.approved is False and entry.owner_approval_required is True
    assert "runtime" in entry.scope  # load_faq() opens it in code
    assert entry.sha256 and entry.size > 0 and entry.freshness
    assert approved_entries(manifest) == []  # nothing uploads without approval


def test_kb_privacy_flags_on_sensitive_document(copy_fixture):
    repo = copy_fixture("pipecat_single")
    (repo / "kb" / "pricing_guide.md").write_text(
        "# Guide\nContact billing@riverline.example.com or +1 (415) 555-0100.\n"
    )
    manifest = build_kb_manifest(inspect_repo(repo))
    entry = next(e for e in manifest if e.path == "kb/pricing_guide.md")
    assert "possible_pii_email" in entry.privacy_flags
    assert "possible_pii_phone" in entry.privacy_flags


def test_rag_imports_are_indicators_not_uploads(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "bot.py").write_text(
        "import langchain\n"
        "from pipecat.pipeline.pipeline import Pipeline\n"
        "from pipecat.pipeline.task import PipelineTask\n\n\n"
        "async def run_bot(transport, runner_args):\n"
        "    pipeline = Pipeline([transport.input(), transport.output()])\n"
        "    task = PipelineTask(pipeline)\n"
    )
    manifest = build_kb_manifest(inspect_repo(repo))
    assert manifest == []  # a vector-store import is evidence, not an uploadable file


# ------------------------------------------------------------------ monitoring


def test_monitoring_summary_modes_and_existing_instrumentation():
    livekit = inspect_repo(FIXTURES / "livekit_basic")
    summary = summarize_monitoring(livekit, Mode.TEST)
    assert "track_session" in summary["sdk_method"]
    assert summary["existing_direct_observe"] is False

    observe = summarize_monitoring(livekit, Mode.OBSERVE)
    assert "observe_session" in observe["sdk_method"]
    assert "audio" in observe["captures"]

    direct = inspect_repo(FIXTURES / "pipecat_direct_observe")
    assert summarize_monitoring(direct, Mode.OBSERVE)["existing_direct_observe"] is True


# ------------------------------------------------------------------ plan enrichment


def test_plan_carries_all_feature_specs():
    plan, _ = make_plan(FIXTURES / "livekit_basic", Mode.TEST, agent_id=77)
    assert {t.name for t in plan.mock_tools} == {"confirm_appointment", "lookup_availability"}
    assert len(plan.dynamic_variables) == 4
    assert [k.path for k in plan.kb_manifest] == ["docs/faq.md"]
