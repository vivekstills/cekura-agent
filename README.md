# cekura-agent

`cekura-agent` is a Python CLI that inspects customer LiveKit or Pipecat repositories, plans a Cekura SDK integration, applies the patch, and reconciles the Cekura dashboard — with offline dry-runs, lifecycle verification, exact rollback, and no hardcoded secrets.

It runs from repository inspection through verification and rollback: snapshot the repo, detect the framework and entrypoint, extract mock tools / dynamic variables / KB documents, plan the code change, patch, verify invariants, and prepare or apply the matching Cekura platform state.

## What the coding agent does

```text
inspect → plan → diff → integrate --apply → verify → prepare-platform → apply-platform
                                                 
```

1. **Inspect** — read-only AST analysis: framework, entrypoints, tools, prompt placeholders, runtime inputs, KB sources, existing Cekura SDK usage.
2. **Plan** — deterministic fake planner or Kimi K3 via OpenRouter produces a constrained `IntegrationPlan`. Unknown actions, repo-escaping paths, and stale fingerprints are rejected.
3. **Diff** — show the exact patch without writing.
4. **Integrate** — dry-run by default. With `--apply`: patch, compile, run lifecycle checks, write rollback manifest, prepare desired platform state.
5. **Verify** — standalone AST + syntax checks on an already patched repo.
6. **Prepare-platform** — emit `CekuraDesiredState` (agent, mock tools, dynamic variables, KB) + dashboard URL.
7. **Apply-platform** — dry-run by default. With `--apply` and `--platform-mode staging`: GET → diff → apply-once → GET-after compare.
8. **Rollback** — restore the repo to the exact SHA-256 hashes from before the patch.

## Release status, compatibility, and known limitations

| Topology | Status |
|---|---|
| LiveKit Python, one entrypoint with `AgentSession` + `session.start(...)` | SUPPORTED |
| Pipecat Python, one `PipelineTask(...)` with `LLMContextAggregatorPair` or `create_context_aggregator` | SUPPORTED |
| Already-integrated repos | SUPPORTED (no-op) |
| Multi-entrypoint / helper `JobContext` functions | SUPPORTED since the disambiguation fix |
| LiveKit JS/TS | NOT in v1 |
| Pipecat `PipelineWorker` / `WorkerRunner` API | v1 refuses with `PIPECAT_WORKER_API` |
| Pipecat without an aggregator pair | v1 refuses with `MISSING_AGGREGATOR_PAIR` |
| Multi-framework repo without `--mode` | v1 refuses with `MULTI_FRAMEWORK` |

All six CEK-8066 top-pick repos were executed. As of the latest evaluation, **four are SUPPORTED** (outbound-caller-python, QuickVoice, AIReceptionist, telephony-server) and **two remain refused with stable reason codes** (pipecat-examples Twilio phone bot → `PIPECAT_WORKER_API`; NVIDIA voice-agent-examples → `MISSING_AGGREGATOR_PAIR` / `AMBIGUOUS_ENTRYPOINT`).

## Architecture overview

The agent separates a **deterministic host** from a **planner**:

- **Host** (`scanner/`, `adapters/`, `verification/`, `patching.py`, `platform/`): AST evidence, deterministic patches, lifecycle checks, and platform client. It never trusts the model to edit a file or call an API.
- **Planner** (`planner/`): `--model-mode fake` (deterministic, no network, default) or `--model-mode openrouter` (Kimi K3). It returns JSON from a closed action vocabulary; the host validates it against evidence before execution.
- **Safety layer** (`safety.py`): repo boundary, secret redaction, path traversal blocks, no shell, no `eval` of model output.

Kimi K3 is responsible for choosing from the constrained action set and for synthesizing mock tool / variable / KB metadata. The host is responsible for applying every change and verifying it.

## Prerequisites

- Python 3.10 or newer. Developed and tested on Python 3.12.
- `git` (for repo snapshots) and a POSIX shell.
- Optional for live planning: OpenRouter API key (`OPENROUTER_API_KEY`).
- Optional for live platform sync: Cekura staging API key (`CEKURA_API_KEY`).
- Optional for live tracing / dashboard: LiveKit or Pipecat provider credentials, but these are not required for offline fixture runs.

## Installation

From a fresh clone, enter the directory you cloned into first. For example:

```bash
cd cekura-agent
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

If the directory name contains spaces, quote it: `cd "cekura agent"`.

Check it:

```bash
cekura-agent --version
```

Expected output:

```text
cekura-agent 0.1.0
```

## Safe environment-variable setup

Copy `.env.example` to `.env` and fill in only what you need for the mode you plan to run. Never commit `.env`.

```bash
cp .env.example .env
chmod 600 .env
```

Example `.env` for offline work:

```dotenv
# Required only for --model-mode openrouter
OPENROUTER_API_KEY=
# Required only for --platform-mode staging
CEKURA_API_KEY=
# Optional defaults
CEKURA_AGENT_ID=
CEKURA_PROJECT_ID=
```

The CLI reads `.env` automatically. All output and reports run through redaction; literal secrets cause a verification failure.

## Five-minute offline quickstart

This uses the bundled `livekit_basic` fixture and the fake planner. No network, no API keys.

```bash
# 1. make a throwaway copy of the fixture
mkdir -p /tmp/cekura-quickstart
cp -R tests/fixtures/livekit_basic /tmp/cekura-quickstart/livekit_basic

# 2. inspect (read-only)
cekura-agent inspect /tmp/cekura-quickstart/livekit_basic

# 3. plan (no writes)
cekura-agent plan /tmp/cekura-quickstart/livekit_basic --mode test --agent-id 42

# 4. diff (exact patch, no writes)
cekura-agent diff /tmp/cekura-quickstart/livekit_basic --mode test --agent-id 42

# 5. apply the patch + verify
cekura-agent integrate /tmp/cekura-quickstart/livekit_basic --mode test --agent-id 42 --apply

# 6. second apply must be a no-op
cekura-agent integrate /tmp/cekura-quickstart/livekit_basic --mode test --agent-id 42 --apply

# 7. verify independently
cekura-agent verify /tmp/cekura-quickstart/livekit_basic --mode test

# 8. emit the desired Cekura platform state
cekura-agent prepare-platform /tmp/cekura-quickstart/livekit_basic --mode test --agent-id 42 --out /tmp/desired.json

# 9. rollback to original files
cekura-agent rollback /tmp/cekura-quickstart/livekit_basic
```

Each command should exit `0`. The first `integrate --apply` prints `checks: 15 passed, 0 failed, 0 warnings` and `exit code: 0`. The second `integrate --apply` prints `checks: 5 passed, 0 failed, 0 warnings` and a no-op exit `0`.

## CLI workflow

### `inspect`

```bash
cekura-agent inspect <repo> [--json <path>]
```

Read-only report of framework, entrypoint, tools, variables, KB candidates, and existing Cekura usage.

### `plan`

```bash
cekura-agent plan <repo> --mode <test|observe> --agent-id <id> [--model-mode fake|openrouter]
```

Produces a validated `IntegrationPlan`. Default planner is `fake` (offline, deterministic).

### `diff`

```bash
cekura-agent diff <repo> --mode <test|observe> --agent-id <id>
```

Renders the exact unified diff the agent would apply. No writes.

### `integrate`

```bash
cekura-agent integrate <repo> --mode <test|observe> [OPTIONS]
```

Dry-run by default. Options:

| Option | Meaning |
|---|---|
| `--apply` | Actually write the patch. |
| `--model-mode fake` | Deterministic offline planner (default). |
| `--model-mode openrouter` | Live Kimi K3; needs `OPENROUTER_API_KEY`. |
| `--platform-mode offline` | Local fake Cekura server / no-op (default). |
| `--platform-mode staging` | Real Cekura staging; needs `CEKURA_API_KEY`. |
| `--agent-id` | Cekura agent id. |
| `--project-id` | Cekura project id when multiple projects are visible. |
| `--report <path>` | Write the `VerificationReport` JSON. |
| `--e2e` | Run platform E2E checks (staging only). |

### `verify`

```bash
cekura-agent verify <repo> --mode <test|observe> [--report <path>]
```

Standalone lifecycle verification on the current state of the repo. Fails with exit `1` if the repo is unintegrated or broken.

### `prepare-platform`

```bash
cekura-agent prepare-platform <repo> --mode <test|observe> --agent-id <id> --out <path>
```

Emits the desired platform state as JSON. No network in `offline` mode.

### `apply-platform`

```bash
cekura-agent apply-platform --desired-state <path> --platform-mode <offline|staging> [--apply]
```

Reconciles the Cekura dashboard. Dry-run by default.

- `offline` mode with `--base-url <local-fake-url>` is intended for tests.
- `staging` mode with `--apply` requires `CEKURA_API_KEY`.

### `rollback`

```bash
cekura-agent rollback <repo> [--run-id <id>] [--force]
```

Restores patched files to the exact hashes recorded before `--apply`. Without `--force`, it refuses if the repo changed under the patch.

## LiveKit and Pipecat examples

### LiveKit

```bash
cekura-agent integrate tests/fixtures/livekit_basic --mode test --agent-id 42 --apply
```

The patch inserts:

```python
from cekura.livekit import LiveKitTracer

cekura_tracer = LiveKitTracer(
    api_key=os.getenv("CEKURA_API_KEY"),
    agent_id=42,
)
```

at module scope and:

```python
await cekura_tracer.track_session(ctx, session, agent)
```

immediately before the statement that executes `session.start(...)`.

### Pipecat

```bash
cekura-agent integrate tests/fixtures/pipecat_single --mode test --agent-id 42 --apply
```

For a `PipelineTask` without extra kwargs, the patch replaces it with `tracer.track_and_create_task(...)`. For a `PipelineTask` with custom kwargs, the patch wraps the pipeline with `tracer.track_pipeline(...)` and preserves the original `PipelineTask(enable_tracing=True, enable_turn_tracking=True, ...)` plus `register_task_handlers`.

## Mock tools, dynamic variables, monitoring, and KB

| Feature | Where it lives | How it works |
|---|---|---|
| **Monitoring** | `src/cekura_agent/features/monitoring.py` + `verification/lifecycle.py` | Deterministic AST lifecycle checks (`tracer_before_start`, `track_session_has_agent`, `module_scope_init`, `api_key_from_env`, `mode_consistent`, `tracer_exactly_once`). |
| **Mock tools** | `src/cekura_agent/features/mock_tools.py` | Extracts `@function_tool` / `FunctionSchema` definitions and builds desired-state specs with synthetic success / empty / error variants. The Cekura SDK auto-injects mocks for LiveKit and Pipecat by default; an explicit `CekuraMockToolRouter` is available only when both `CEKURA_USE_MOCK_TOOLS=1` and `CEKURA_MOCK_ENDPOINT_BASE` are set. |
| **Dynamic variables** | `src/cekura_agent/features/dynamic_vars.py` | `{{placeholder}}` in prompts and structural runtime reads from `ctx.job.metadata` / `session_data`. Maps each variable to framework-exact source and sink. |
| **Knowledge base** | `src/cekura_agent/features/knowledge_base.py` | Discovers runtime-read documents, computes SHA-256, size, media type, and privacy flags. KB uploads are approval-gated and capped at 2 MB. |

## Test, lint, security, idempotence, rollback, and end-to-end suites

All commands are run from the repository root with the virtual environment active.

```bash
# lint
ruff check src tests

# unit + integration tests
python -m pytest
```

Expected: `ruff` clean, `pytest` 104 passed.

```bash
# security scan helpers (run as a group; grep is silent when clean)
grep -R "shell=True" src tests || true
grep -R "eval(" src tests || true
grep -R "pickle" src tests || true
grep -R "verify=False" src tests || true
```

```bash
# evaluation matrix against bundled fixtures and cloned real repos (read-only by default)
python eval/run_matrix.py
```

```bash
# build artifact sanity check
python -m build --sdist --wheel
```

## Three-minute demo

```bash
./demo.sh
```

`demo.sh` runs the full offline sequence: inspect → plan → diff → integrate --apply → prepare-platform → verify → no-op second apply → rollback → safe refusal. It uses a fresh temporary copy of `tests/fixtures/livekit_basic` and the fake planner.

## Verifying Cekura API and dashboard state

### Local fake Cekura server (offline / tests only)

The `offline` platform mode is wired into the test suite through `FakeCekuraServer`. Manual `apply-platform --platform-mode offline --base-url ...` requires a running local fake server and a pre-existing matching agent; it is not part of the quickstart. Run the platform tests instead:

```bash
python -m pytest tests/test_platform.py
```

### Real Cekura staging

```bash
export CEKURA_API_KEY=...

# prepare and sync
cekura-agent prepare-platform /tmp/cekura-quickstart/livekit_basic --mode test --agent-id 42 --out /tmp/desired.json
cekura-agent apply-platform --desired-state /tmp/desired.json --platform-mode staging --apply
```

Then open the dashboard URL printed by `prepare-platform` (example from live runs: `https://dashboard.cekura.ai/agents/22338`) and confirm the agent shows the mock tool, dynamic variables, and KB document.

## Expected output and exit codes

| Exit code | Meaning |
|---|---|
| `0` | Success, or blocked-by-access with an honest status. |
| `1` | Verification / runtime failure. |
| `2` | `NEEDS_HUMAN` — the repo topology is unsupported. A stable reason code is printed. |

Example `NEEDS_HUMAN`:

```bash
cekura-agent integrate tests/fixtures/readme_only --mode test
echo "EXIT: $?"
```

Expected output ends with:

```text
NEEDS_HUMAN: NO_FRAMEWORK
exit code: 2
EXIT: 2
```

## Security model and safe defaults

- **Dry-run default**: `integrate` and `apply-platform` do not write unless `--apply` is passed.
- **No model file access**: the planner returns JSON only; the host validates and executes.
- **Path containment**: plans cannot escape the target repo or touch test/example files.
- **Secrets from env only**: generated code uses `os.getenv`; literals fail lifecycle checks. `.env` is gitignored.
- **Redaction**: API keys and tokens are redacted from reports and output.
- **Exact rollback**: per-run manifest and backups inside `<repo>/.cekura-agent/`.
- **Deletion approval**: platform deletions require `--approve-deletions`.

See `SECURITY.md` and `security/SECURITY_TEST_MATRIX.md` for the full test matrix.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `NO_FRAMEWORK` | The repo has no `livekit` or `pipecat` Python imports. README text is ignored. |
| `AMBIGUOUS_ENTRYPOINT` | Multiple candidate entrypoints; use a subproject path or ensure helpers with `JobContext` do not call `session.start()`. |
| `NO_SESSION_START` | LiveKit entrypoint does not call `session.start()`. |
| `MISSING_AGGREGATOR_PAIR` | Pipecat `PipelineTask` lacks `LLMContextAggregatorPair` or `create_context_aggregator().user()/.assistant()`. |
| `PIPECAT_WORKER_API` | The repo uses `PipelineWorker`/`WorkerRunner`; v1 targets `PipelineTask`. |
| `CEKURA_KEY_MISSING` / `CEKURA_UNAUTHORIZED` | Staging platform calls need a valid `CEKURA_API_KEY`. |
| `OPENROUTER_KEY_MISSING` / `OPENROUTER_UNAUTHORIZED` | `--model-mode openrouter` needs `OPENROUTER_API_KEY`. |
| `PROVIDER_CONNECTION_REQUIRED` | Platform agent creation needs LiveKit/Pipecat provider credentials. |
| `rollback` refuses | Files changed after the patch; use `--force` only if you accept losing those changes. |
| `verify` exits `1` | Repo is unintegrated or patched state fails lifecycle invariants. |
| Tests fail with `ModuleNotFoundError` | Run `pip install -e '.[dev]'` from the repo root. |

## Documentation links

- `DEMO.md` — one-page live demo script and dashboard walkthrough.
- `DESIGN.md` — pipeline, scanner, planner, adapters, verification, platform, and trust boundaries.
- `SECURITY.md` — secrets, boundaries, budget, and rollback.
- `COMPATIBILITY.md` — supported topologies and all stable reason / blocker codes.
- `EVALUATION.md` — test suite, live results, and top-pick outcomes.
- `SUBMISSION_CHECKLIST.md` — exact release commands and what to check before handing off.

## Honest remaining limitations and next steps

- **PipelineWorker / WorkerRunner**: the v1 Pipecat adapter targets `PipelineTask`. `PipelineWorker`-based repos (Twilio phone bot outbound, AWS samples) are refused with `PIPECAT_WORKER_API`. Supporting them needs a confirmed Cekura SDK contract for that runner.
- **NVIDIA voice-agent-examples**: the WebRTC/WebSocket subprojects lack an aggregator pair and are refused with `MISSING_AGGREGATOR_PAIR`. The `nat_agent` subproject has two transport entrypoints and is `AMBIGUOUS_ENTRYPOINT`.
- **Scenario-run E2E**: the `scenario_run_e2e` capability is reported `NOT_RUN`; it requires a runnable agent connection and is never faked.
- **LiveKit JS/TS**: out of v1 scope; only Python LiveKit and Python Pipecat are supported.
- **Type-checking**: `mypy src` currently reports errors in 11 files; this is a known release-audit item and should be fixed before claiming full production type safety.
- **Key rotation**: the OpenRouter and Cekura trial keys shared during development should be rotated after the trial.
