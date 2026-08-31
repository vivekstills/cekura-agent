# Evaluation

## Test suite
`pytest` — 99 tests: schema strictness, safety kernel, scanner accuracy on fixtures,
planner contract vs a local fake OpenRouter server, semantic-validator hostility tests,
golden transformations, lifecycle mutation tests (hook-after-start, missing agent arg,
shared pipecat tracer, missing tracing flags, register-before-task, aggregator removal,
duplicate integration, hardcoded keys, wrong mode), compare-before-write / mid-apply
failure restoration / idempotence / exact rollback, platform reconciliation vs a faithful
fake Cekura server (deletion approval, timeout-after-commit recovery without duplicates,
corrupted-state detection, 401/429/5xx, dry-run zero-mutations), offline autonomous E2E
with a loopback-only socket guard.

## Offline E2E matrix (see eval/matrix.md for the full table)
- fixtures livekit_basic / pipecat_single / pipecat_custom: dry-run → apply → verify →
  idempotent second apply (zero diff) → exact-hash rollback — PASS
- refusal fixtures: readme_only (NO_FRAMEWORK), pipecat_direct_observe
  (DIRECT_OBSERVE_PRESENT), pipecat_worker (PIPECAT_WORKER_API) — exit 2 with stable codes
- **all six CEK-8066 top picks executed** (`integrate` ran on every target, incl. the pinned
  `twilio-chatbot` wings): SUPPORTED targets (outbound-caller-python, telephony-server) passed
  the full offline E2E (15/15 checks, no-op re-apply, exact rollback); the rest produced
  structured exit-2 refusals with stable reason codes — per-target outcomes in COMPATIBILITY.md

## LIVE verification (real keys, 2026-08-31)
| What | Result |
|---|---|
| Kimi K3 planning via OpenRouter | `LIVE_VERIFIED` — moonshotai/kimi-k3, valid plans on livekit + pipecat repos; canary caught a real model quirk (omitted host-known param) now normalized. Costs: $0.0141 / $0.0226 / $0.0136 per plan |
| Cekura platform registration | `LIVE_VERIFIED` — agent **22338** (`dashboard.cekura.ai/agents/22338`): mock tool `order_lookup` with 3 distinct input→output mappings, dynamic variables `caller_name` + `patient_id`, KB `pricing_guide.md`, `tracing_enabled=true`; GET-after exact match; reconciliation idempotent on re-run |
| Real-repo code integration | outbound-caller-python patched by the full autonomous run (real Kimi plan): tracer inserted before the `asyncio.create_task(session.start(...))` statement; 15/15 checks; rollback manifest written |
| LiveKit agent creation | `BLOCKED_BY_ACCESS_OR_DEPENDENCY: PROVIDER_CONNECTION_REQUIRED` — the platform requires LiveKit credentials or a connection method; unblocks by setting `LIVEKIT_URL`/`LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET` and re-running one command |
| SDK dependency | `LIVE_VERIFIED` — cekura==1.6.5 resolvable on PyPI with both extras |
| Scenario-run E2E | `NOT_RUN` — next step; requires a runnable agent connection (never faked) |

## Budget
Cumulative model spend this build: < $0.10 of the $200 OpenRouter budget (ledger at
`~/.cekura-agent/ledger.json`; caps enforced per run and cumulatively).
