# cekura-agent

Autonomous coding agent that integrates the [Cekura](https://cekura.ai) SDK into customer
**LiveKit** and **Pipecat** Python repositories and reconciles the matching **Cekura platform**
configuration — monitoring, mock tools, dynamic variables and knowledge base — with
verification, idempotence and rollback evidence at every step.

```
cekura-agent integrate /path/to/repo --mode test            # dry-run (default)
cekura-agent integrate /path/to/repo --mode observe --apply # actually patch
./demo.sh                                                   # 3-minute offline walkthrough
```

**Live-verified** (2026-08-31, real keys): Kimi K3 planning via OpenRouter; Cekura platform
registration with GET-after exact verification — dashboard agent
[22338](https://dashboard.cekura.ai/agents/22338) carries the mock tool (3 distinct
input→output mappings), both dynamic variables and the KB document extracted from the repo;
real-repo patch of `livekit-examples/outbound-caller-python` (tracer inserted before an
`asyncio.create_task(session.start(...))`). Details: `EVALUATION.md`.

## Requested capability -> where it lives

| Capability | Module | Status labels used |
|---|---|---|
| Autonomous coding agent | `cli.py`, `orchestrator.py` | per-run VerificationReport |
| OpenRouter + Kimi K3 planner | `planner/` (`--model-mode fake\|openrouter`) | fake-server tested; live canary optional |
| LiveKit adapter (test/observe) | `adapters/livekit.py` | golden fixtures + lifecycle verifier |
| Pipecat adapter (test/observe) | `adapters/pipecat.py` | golden fixtures + lifecycle verifier |
| Monitoring (tracing lifecycle) | `features/monitoring.py`, `verification/lifecycle.py` | exactly-once checks |
| Mock tools | `features/mock_tools.py` | desired-state objects; SDK auto-injects for LiveKit and Pipecat (explicit router optional) |
| Dynamic variables | `features/dynamic_vars.py` | typed source->sink mapping |
| Knowledge base | `features/knowledge_base.py` | reviewed upload manifest |
| Cekura dashboard | `platform/` (`prepare-platform`, `apply-platform`) | GET-after exact verification |
| Repo compatibility | `scanner/` | supported / blocked / NEEDS_HUMAN |
| Safety | `safety.py`, `patching.py` | boundary, redaction, dry-run, rollback |

## Result status labels

Every capability in a report carries one of:

- `IMPLEMENTED_AND_OFFLINE_VERIFIED` — feature exists and passed offline (fixture/fake-server) verification
- `LIVE_VERIFIED` — additionally proven against the live OpenRouter/Cekura staging environment
- `BLOCKED_BY_ACCESS_OR_DEPENDENCY` — implemented, but a credential or dependency is absent (stable blocker code)
- `NEEDS_HUMAN_UNSUPPORTED_TOPOLOGY` — the repo's shape is outside the supported matrix (stable reason code)
- `NOT_RUN` — intentionally not executed in this run

The agent never fabricates a successful integration: anything unproven is labelled, not claimed.

## Install

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

## Modes & safety defaults

- `--mode test|observe` is **required** — no implicit "both".
- `integrate` and `apply-platform` are **dry-run by default**; `--apply` is explicit.
- `--model-mode fake` (default) never touches the network; `openrouter` uses `OPENROUTER_API_KEY` from the environment/.env.
- `--platform-mode offline` (default) targets a local fake Cekura server in tests; `staging` requires `CEKURA_API_KEY`.
- Secrets are read from env only, redacted from all output, and scanned for before packaging.
- Every `--apply` writes a rollback manifest; `cekura-agent rollback` restores exact original hashes.

Docs: `DESIGN.md` (architecture) · `DECISIONS.md` (incl. real-API contracts discovered live) ·
`SECURITY.md` · `COMPATIBILITY.md` (supported matrix + stable reason codes) · `EVALUATION.md`
(test suite, offline matrix, live results) · `SUBMISSION_CHECKLIST.md` (exact commands) ·
`eval/matrix.md` (six CEK-8066 repos classified).
